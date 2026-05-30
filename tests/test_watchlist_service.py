"""WatchlistService (app/portfolio/watchlist.py) birim testleri.

In-memory SQLite üzerinde gerçek CRUD akışı test edilir. ``session_factory``
sözleşmesi: her çağrıda yeni bir Session döner. Test ortamında aynı in-memory
SQLite engine'e bağlı yeni session'lar açan bir fabrika kullanırız (verinin
testler arasında paylaşımı için).

Async API testleri ``@pytest.mark.asyncio`` ile çalışır.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.portfolio.watchlist import WatchlistService, WatchlistItemView
from app.db.models import (
    Instrument,
    PriceHistory,
    Recommendation,
    Watchlist,
    WatchlistItem,
)


# ---------------------------------------------------------------------------
# Yardımcı session factory: sync_engine üzerinden her çağrıda yeni Session
# ---------------------------------------------------------------------------


@pytest.fixture
def session_factory(sync_engine):
    """Her çağrıda yeni sync Session döndüren factory."""
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
def seed_instruments(sync_engine):
    """3 örnek instrument seed et (AAPL, MSFT, GOOG)."""
    from sqlalchemy.orm import Session

    with Session(sync_engine) as s:
        for ticker, name in [("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOG", "Alphabet")]:
            s.add(Instrument(ticker=ticker, name=name, exchange="NASDAQ"))
        s.commit()


# ---------------------------------------------------------------------------
# Create / delete
# ---------------------------------------------------------------------------


class TestWatchlistCRUD:
    @pytest.mark.asyncio
    async def test_create_returns_unique_ids(self, session_factory):
        svc = WatchlistService(session_factory)
        id1 = await svc.create("Uzun Vade")
        id2 = await svc.create("Uzun Vade")  # aynı isim — yine de farklı id
        assert id1 != id2
        assert id1 > 0 and id2 > 0

    @pytest.mark.asyncio
    async def test_create_rejects_empty_name(self, session_factory):
        svc = WatchlistService(session_factory)
        with pytest.raises(ValueError):
            await svc.create("")
        with pytest.raises(ValueError):
            await svc.create("   ")

    @pytest.mark.asyncio
    async def test_delete_cascades_items(self, session_factory, seed_instruments):
        """Watchlist silinirse ilişkili watchlist_items CASCADE ile düşmeli."""
        from sqlalchemy.orm import Session

        svc = WatchlistService(session_factory)
        wl_id = await svc.create("Tech")
        await svc.add_item(wl_id, "AAPL")
        await svc.add_item(wl_id, "MSFT")

        # Şimdi sil
        await svc.delete(wl_id)

        with Session(session_factory.kw["bind"]) as s:
            items = (
                s.query(WatchlistItem).filter(WatchlistItem.watchlist_id == wl_id).all()
            )
            assert items == []


# ---------------------------------------------------------------------------
# add_item / remove_item
# ---------------------------------------------------------------------------


class TestAddRemoveItem:
    @pytest.mark.asyncio
    async def test_add_item_missing_instrument_raises_lookup(self, session_factory, seed_instruments):
        svc = WatchlistService(session_factory)
        wl_id = await svc.create("test")
        with pytest.raises(LookupError):
            await svc.add_item(wl_id, "NOSUCH")

    @pytest.mark.asyncio
    async def test_add_item_missing_watchlist_raises_lookup(self, session_factory, seed_instruments):
        svc = WatchlistService(session_factory)
        with pytest.raises(LookupError):
            await svc.add_item(9999, "AAPL")

    @pytest.mark.asyncio
    async def test_add_item_idempotent(self, session_factory, seed_instruments):
        """Aynı (wl, ticker) ikinci çağrı yeni satır eklemez."""
        svc = WatchlistService(session_factory)
        wl_id = await svc.create("dup")
        id1 = await svc.add_item(wl_id, "AAPL")
        id2 = await svc.add_item(wl_id, "AAPL")
        assert id1 == id2

        items = await svc.list_items(wl_id)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_remove_item_missing_is_noop(self, session_factory, seed_instruments):
        svc = WatchlistService(session_factory)
        wl_id = await svc.create("rm")
        # remove non-existent — exception YOK
        await svc.remove_item(wl_id, instrument_id=9999)

    @pytest.mark.asyncio
    async def test_remove_item_deletes_existing(self, session_factory, seed_instruments):
        from sqlalchemy.orm import Session

        svc = WatchlistService(session_factory)
        wl_id = await svc.create("rm2")
        await svc.add_item(wl_id, "AAPL")

        # AAPL'in instrument_id'sini al
        with Session(session_factory.kw["bind"]) as s:
            instr = s.query(Instrument).filter(Instrument.ticker == "AAPL").one()
            aapl_id = int(instr.id)

        await svc.remove_item(wl_id, aapl_id)

        items = await svc.list_items(wl_id)
        assert items == []


# ---------------------------------------------------------------------------
# list_items + reorder
# ---------------------------------------------------------------------------


class TestListItemsAndReorder:
    @pytest.mark.asyncio
    async def test_list_items_sorted_by_sort_order(self, session_factory, seed_instruments):
        svc = WatchlistService(session_factory)
        wl_id = await svc.create("order")
        await svc.add_item(wl_id, "AAPL")
        await svc.add_item(wl_id, "MSFT")
        await svc.add_item(wl_id, "GOOG")

        items = await svc.list_items(wl_id)
        # Eklenme sırasına göre sort_order 0,1,2 olmalı.
        assert [i.ticker for i in items] == ["AAPL", "MSFT", "GOOG"]
        assert [i.sort_order for i in items] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_reorder_updates_sort_order(self, session_factory, seed_instruments):
        from sqlalchemy.orm import Session

        svc = WatchlistService(session_factory)
        wl_id = await svc.create("ro")
        await svc.add_item(wl_id, "AAPL")
        await svc.add_item(wl_id, "MSFT")
        await svc.add_item(wl_id, "GOOG")

        with Session(session_factory.kw["bind"]) as s:
            ids = {
                i.ticker: int(i.id)
                for i in s.query(Instrument).filter(Instrument.ticker.in_(["AAPL", "MSFT", "GOOG"])).all()
            }
        # Yeni sıra: GOOG, AAPL, MSFT
        await svc.reorder(wl_id, [ids["GOOG"], ids["AAPL"], ids["MSFT"]])

        items = await svc.list_items(wl_id)
        assert [i.ticker for i in items] == ["GOOG", "AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# with_quotes=True (price_history + recommendation join)
# ---------------------------------------------------------------------------


class TestListItemsWithQuotes:
    @pytest.mark.asyncio
    async def test_with_quotes_populates_price_and_recommendation(
        self, session_factory, seed_instruments
    ):
        """Son price_history + son recommendation join edilir."""
        from sqlalchemy.orm import Session

        svc = WatchlistService(session_factory)
        wl_id = await svc.create("q")
        await svc.add_item(wl_id, "AAPL")

        with Session(session_factory.kw["bind"]) as s:
            aapl = s.query(Instrument).filter(Instrument.ticker == "AAPL").one()
            aapl_id = int(aapl.id)
            # Birkaç price_history satırı (sonuncusu en yeni)
            s.add_all([
                PriceHistory(
                    instrument_id=aapl_id,
                    source="yfinance",
                    timestamp=datetime(2026, 5, 26, tzinfo=timezone.utc),
                    open=180.0, high=185.0, low=179.0, close=184.0, volume=1000,
                ),
                PriceHistory(
                    instrument_id=aapl_id,
                    source="yfinance",
                    timestamp=datetime(2026, 5, 27, tzinfo=timezone.utc),
                    open=184.0, high=190.0, low=183.0, close=189.5,
                    verified_close=189.7, volume=2000,
                ),
            ])
            # Birkaç recommendation (sonuncusu en yeni)
            s.add_all([
                Recommendation(
                    instrument_id=aapl_id,
                    generated_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
                    action="HOLD", timeframe="short", confidence=50.0,
                    summary="x",
                ),
                Recommendation(
                    instrument_id=aapl_id,
                    generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
                    action="BUY", timeframe="short", confidence=80.0,
                    summary="y",
                ),
            ])
            s.commit()

        items = await svc.list_items(wl_id, with_quotes=True)

        assert len(items) == 1
        v = items[0]
        # verified_close 189.7 olmalı (varsa onu kullan)
        assert v.current_price == Decimal("189.7")
        assert v.volume == 2000
        assert v.bot_action == "BUY"
        assert v.bot_confidence == pytest.approx(80.0, abs=1e-6)
