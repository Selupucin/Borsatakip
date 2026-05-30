"""``DataCollector`` birim testleri.

Doğrulanan davranışlar:
1. ``fetch_all_quotes`` paralel çalışır (yavaş source diğerlerini bekletmez).
2. Bir source ``SourceUnavailableError`` fırlatınca diğerleri etkilenmez.
3. 5 art arda hata -> ``disable_source`` çağrılır, DB'de ``is_active=False``.
4. ``failed_requests`` / ``total_requests`` doğru artar.
5. ``last_success`` başarılı çağrıda güncellenir.
6. ``save_quotes`` UPSERT (ON CONFLICT DO NOTHING) ile çakışmaları yutar.
   (SQLite testinde ``pg_insert``'in ``on_conflict_do_nothing`` çağrısı,
   SQLite'ın ``INSERT OR IGNORE`` semantiğine yakın bir davranış üretmek için
   patched ``insert``'le test edilir.)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from app.data.base_source import (
    BaseSource,
    Quote,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
)
from app.data.collector import DataCollector
from app.db.models import DataSource, Instrument, PriceHistory


# ---------------------------------------------------------------------------
# Yardımcı: SQLite'a uyumlu UPSERT için collector'ı yamalama
# ---------------------------------------------------------------------------


@pytest.fixture
def patch_pg_insert_for_sqlite(monkeypatch):
    """`pg_insert` çağrılarını SQLite-uyumlu hale getir.

    DataCollector ve PriceComparator PostgreSQL'in
    ``insert(...).on_conflict_do_nothing(...)`` API'sini kullanır. SQLite'ta
    bu metod yoktur. Test ortamı için yamalama:
    - ``pg_insert`` -> SQLite ``insert``
    - ``on_conflict_do_nothing`` -> ``prefix_with('OR IGNORE')`` (INSERT OR IGNORE)
    """
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from app.data import collector as col_mod

    # SQLAlchemy 1.4+'da sqlite_insert ZATEN on_conflict_do_nothing destekler.
    # Sadece pg_insert ismini sqlite_insert ile değiştirmek yeterli — values()
    # chain'i de bozulmaz.
    monkeypatch.setattr(col_mod, "pg_insert", sqlite_insert)
    return sqlite_insert


# ---------------------------------------------------------------------------
# Yardımcı: konfigüre edilebilir mock source
# ---------------------------------------------------------------------------


def _make_source(name: str, *, quote=None, exc: Exception | None = None, delay: float = 0.0):
    """name'i set edilmiş concrete BaseSource alt sınıfı döndür.

    Abstract metodlar sınıf body'sinde tanımlanmalı; sonradan atama abstract
    durumunu değiştirmez.
    """

    async def _fetch_ohlcv(self, ticker, start, end, interval="1d"):
        return []

    async def _fetch_quote(self, ticker):
        if delay:
            await asyncio.sleep(delay)
        if exc:
            raise exc
        return quote or Quote(
            ticker=ticker,
            price=Decimal("100"),
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            source=name,
        )

    cls = type(
        f"_Src_{name}",
        (BaseSource,),
        {
            "name": name,
            "fetch_ohlcv": _fetch_ohlcv,
            "fetch_quote": _fetch_quote,
        },
    )
    return cls()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDataCollectorParallel:
    @pytest.mark.asyncio
    async def test_fetch_all_quotes_runs_in_parallel(
        self, async_session_factory, patch_pg_insert_for_sqlite
    ):
        """2 hızlı + 1 yavaş (100ms) source toplam < 200ms'de bitmeli."""
        s1 = _make_source("fast_a", delay=0.0)
        s2 = _make_source("fast_b", delay=0.0)
        s3 = _make_source("slow_c", delay=0.1)

        collector = DataCollector([s1, s2, s3], async_session_factory)

        start = time.perf_counter()
        quotes = await collector.fetch_all_quotes("AAPL")
        elapsed = time.perf_counter() - start

        assert len(quotes) == 3
        # Seri çalışsaydı 100ms+ olurdu; paralelde << 200ms
        assert elapsed < 0.2, f"Paralel değil — geçti: {elapsed*1000:.1f}ms"

    @pytest.mark.asyncio
    async def test_one_source_failure_does_not_break_others(
        self, async_session_factory, patch_pg_insert_for_sqlite
    ):
        ok1 = _make_source("ok1")
        broken = _make_source("broken", exc=SourceUnavailableError("boom"))
        ok2 = _make_source("ok2")

        collector = DataCollector([ok1, broken, ok2], async_session_factory)
        quotes = await collector.fetch_all_quotes("AAPL")

        # Sadece 2 başarılı dönmeli
        assert len(quotes) == 2
        assert {q.source for q in quotes} == {"ok1", "ok2"}


class TestDataCollectorFailureTracking:
    @pytest.mark.asyncio
    async def test_five_consecutive_failures_disable_source(
        self, async_session_factory, patch_pg_insert_for_sqlite, clean_settings
    ):
        """5 art arda hata -> kaynak DB'de is_active=False."""
        broken = _make_source("flaky", exc=SourceUnavailableError("nope"))
        collector = DataCollector([broken], async_session_factory)

        for _ in range(5):
            await collector.fetch_all_quotes("AAPL")

        # 'flaky' inactive olmalı
        assert "flaky" in collector._inactive

        async with async_session_factory() as session:
            ds = (
                await session.execute(
                    select(DataSource).where(DataSource.name == "flaky")
                )
            ).scalar_one()
            assert ds.is_active is False
            assert ds.failed_requests == 5
            assert ds.total_requests == 5

    @pytest.mark.asyncio
    async def test_rate_limit_does_not_count_as_failure(
        self, async_session_factory, patch_pg_insert_for_sqlite, clean_settings
    ):
        """RateLimitError failure sayılmaz, kaynak aktif kalır."""
        rl = _make_source("av", exc=RateLimitError("429"))
        collector = DataCollector([rl], async_session_factory)

        for _ in range(10):
            await collector.fetch_all_quotes("AAPL")

        assert "av" not in collector._inactive

        async with async_session_factory() as session:
            ds = (
                await session.execute(select(DataSource).where(DataSource.name == "av"))
            ).scalar_one()
            # total_requests artmış olmalı, failed_requests artmamalı
            assert ds.failed_requests == 0
            assert ds.is_active is True

    @pytest.mark.asyncio
    async def test_success_updates_last_success_and_resets_failure_counter(
        self, async_session_factory, patch_pg_insert_for_sqlite, clean_settings
    ):
        """Başarı: ``last_success`` güncellenir, peş peşe failure sayacı sıfırlanır."""
        # İlk 2 fail sonra başarı
        attempts = {"i": 0}

        async def _fq(self, ticker):
            attempts["i"] += 1
            if attempts["i"] <= 2:
                raise SourceUnavailableError("first two fail")
            return Quote(
                ticker=ticker,
                price=Decimal("100"),
                timestamp=datetime.now(tz=timezone.utc),
                source="mix",
            )

        async def _foh(self, *a, **kw):
            return []

        cls = type(
            "_Src_mix",
            (BaseSource,),
            {"name": "mix", "fetch_quote": _fq, "fetch_ohlcv": _foh},
        )
        src = cls()
        collector = DataCollector([src], async_session_factory)

        # 2 fail
        await collector.fetch_all_quotes("X")
        await collector.fetch_all_quotes("X")
        assert collector._consecutive_failures["mix"] == 2

        # 1 success
        await collector.fetch_all_quotes("X")
        assert collector._consecutive_failures["mix"] == 0

        async with async_session_factory() as session:
            ds = (
                await session.execute(select(DataSource).where(DataSource.name == "mix"))
            ).scalar_one()
            assert ds.last_success is not None
            assert ds.failed_requests == 2
            assert ds.total_requests == 3


class TestDataCollectorPersistence:
    @pytest.mark.asyncio
    async def test_save_quotes_writes_price_history_rows(
        self, async_session_factory, patch_pg_insert_for_sqlite
    ):
        """``save_quotes`` -> ``price_history`` tablosuna satır yazar."""
        # Önce bir instrument yarat
        async with async_session_factory() as session:
            instr = Instrument(ticker="AAPL", name="Apple")
            session.add(instr)
            await session.commit()
            instrument_id = instr.id

        collector = DataCollector([], async_session_factory)
        quotes = [
            Quote(
                ticker="AAPL",
                price=Decimal("150"),
                timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
                source="yfinance",
                volume=1000,
            ),
            Quote(
                ticker="AAPL",
                price=Decimal("150.10"),
                timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
                source="stooq",
                volume=1100,
            ),
        ]
        n = await collector.save_quotes(quotes, instrument_id=instrument_id)
        assert n == 2

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(PriceHistory).where(
                        PriceHistory.instrument_id == instrument_id
                    )
                )
            ).scalars().all()
            assert len(rows) == 2
            sources = {r.source for r in rows}
            assert sources == {"yfinance", "stooq"}

    @pytest.mark.asyncio
    async def test_save_quotes_upsert_does_not_duplicate(
        self, async_session_factory, patch_pg_insert_for_sqlite
    ):
        """Aynı (instr,source,ts) için ikinci save no-op olmalı (UPSERT)."""
        async with async_session_factory() as session:
            instr = Instrument(ticker="AAPL")
            session.add(instr)
            await session.commit()
            instrument_id = instr.id

        collector = DataCollector([], async_session_factory)
        q = Quote(
            ticker="AAPL",
            price=Decimal("150"),
            timestamp=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            source="yfinance",
        )
        await collector.save_quotes([q], instrument_id=instrument_id)
        # İkinci kez: çakışma -> sessizce yutulmalı (toplam yine 1 satır)
        await collector.save_quotes([q], instrument_id=instrument_id)

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(PriceHistory).where(PriceHistory.instrument_id == instrument_id)
                )
            ).scalars().all()
            assert len(rows) == 1

    @pytest.mark.asyncio
    async def test_disable_source_marks_db_inactive(
        self, async_session_factory, patch_pg_insert_for_sqlite
    ):
        collector = DataCollector([], async_session_factory)
        await collector.disable_source("yfinance", "test")
        assert "yfinance" in collector._inactive

        async with async_session_factory() as session:
            ds = (
                await session.execute(
                    select(DataSource).where(DataSource.name == "yfinance")
                )
            ).scalar_one()
            assert ds.is_active is False

    @pytest.mark.asyncio
    async def test_get_active_sources_excludes_inactive(
        self, async_session_factory, patch_pg_insert_for_sqlite
    ):
        a = _make_source("a")
        b = _make_source("b")
        collector = DataCollector([a, b], async_session_factory)
        await collector.disable_source("a", "manual")
        active = collector.get_active_sources()
        assert len(active) == 1
        assert active[0].name == "b"
