"""NewsAggregator (app/analysis/news_aggregator.py) birim testleri.

In-memory SQLite ile gerçek aggregate sorgusu çalıştırılır. ``session_factory``
sözleşmesi: çağrıldığında yeni bir Session döndürmesi yeterli; testte
``conftest.py``'deki ``db_session`` fixture'ından bir factory üretilir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.analysis.news_aggregator import NewsAggregator
from app.db.models import Instrument, NewsFeed


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _seed_instrument(session, ticker: str = "AAPL") -> int:
    instr = Instrument(ticker=ticker, name=ticker)
    session.add(instr)
    session.flush()
    return int(instr.id)


def _seed_news(
    session,
    instrument_id: int,
    *,
    sentiment_score,
    published_at: datetime,
    language: str = "en",
    title: str | None = None,
) -> None:
    n = NewsFeed(
        instrument_id=instrument_id,
        source="test",
        title=title or "haber",
        url=f"https://example.com/{instrument_id}/{published_at.isoformat()}",
        published_at=published_at,
        language=language,
        sentiment="positive" if (sentiment_score or 0) > 0 else "negative",
        sentiment_score=sentiment_score,
    )
    session.add(n)


def _factory_from_session(session):
    """Aynı session'ı her çağrıda döndüren basit factory.

    ``aggregate_sentiment`` factory()'yi her çağrıda yeni session bekler ama
    test ortamında her seferinde aynı in-memory connection'a bağlı session'ı
    kullanmak güvenli — DB içeriği test boyunca paylaşılır. Ayrıca
    ``aggregate_sentiment`` ``getattr(session, 'close', None)`` ile çağırır;
    bunu no-op'a çevirmek için custom wrapper döneriz.
    """

    class _Wrap:
        def __init__(self, inner):
            self.inner = inner

        def execute(self, *a, **kw):
            return self.inner.execute(*a, **kw)

        def close(self):
            # Gerçek session'ı kapatma — fixture sonunda rollback edecek.
            pass

    return lambda: _Wrap(session)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestAggregateSentiment:
    @pytest.mark.asyncio
    async def test_mean_calculation_skips_null_scores(self, db_session):
        """5 haber: skorlar (0.5, 0.6, -0.2, 0.3, NULL); mean=(0.5+0.6-0.2+0.3)/4=0.3."""
        instr_id = _seed_instrument(db_session)
        now = datetime.now(tz=timezone.utc)

        scores = [0.5, 0.6, -0.2, 0.3, None]
        for i, s in enumerate(scores):
            _seed_news(
                db_session,
                instr_id,
                sentiment_score=s,
                published_at=now - timedelta(hours=i),
            )
        db_session.flush()

        agg = NewsAggregator(_factory_from_session(db_session))
        mean, count = await agg.aggregate_sentiment(instr_id, lookback_days=7)

        assert count == 4  # NULL atlanır
        assert mean == pytest.approx(0.3, abs=1e-6)

    @pytest.mark.asyncio
    async def test_lookback_excludes_old_news(self, db_session):
        """``lookback_days`` dışındaki haberler hesaba katılmaz."""
        instr_id = _seed_instrument(db_session)
        now = datetime.now(tz=timezone.utc)

        # 3 gün içinde 2 haber, 30 gün önce 1 haber (dışlanmalı)
        _seed_news(db_session, instr_id, sentiment_score=0.8, published_at=now - timedelta(days=1))
        _seed_news(db_session, instr_id, sentiment_score=0.4, published_at=now - timedelta(days=2))
        _seed_news(
            db_session, instr_id, sentiment_score=-0.9, published_at=now - timedelta(days=30)
        )
        db_session.flush()

        agg = NewsAggregator(_factory_from_session(db_session))
        mean, count = await agg.aggregate_sentiment(instr_id, lookback_days=7)

        assert count == 2
        assert mean == pytest.approx(0.6, abs=1e-6)

    @pytest.mark.asyncio
    async def test_empty_returns_none_zero(self, db_session):
        """Hiç haber yoksa (None, 0) döner."""
        instr_id = _seed_instrument(db_session, ticker="EMPTY")
        agg = NewsAggregator(_factory_from_session(db_session))
        mean, count = await agg.aggregate_sentiment(instr_id)

        assert mean is None
        assert count == 0


class TestAggregateByLanguage:
    @pytest.mark.asyncio
    async def test_tr_and_en_aggregated_separately(self, db_session):
        """TR ve EN haberler ayrı ortalamalara karışmaz."""
        instr_id = _seed_instrument(db_session, ticker="THYAO")
        now = datetime.now(tz=timezone.utc)

        # TR: 0.5, 0.3 -> mean 0.4
        _seed_news(db_session, instr_id, sentiment_score=0.5, published_at=now, language="tr")
        _seed_news(db_session, instr_id, sentiment_score=0.3, published_at=now, language="tr")
        # EN: -0.2 -> mean -0.2
        _seed_news(
            db_session, instr_id, sentiment_score=-0.2, published_at=now, language="en"
        )
        db_session.flush()

        agg = NewsAggregator(_factory_from_session(db_session))
        result = await agg.aggregate_by_language(instr_id, lookback_days=7)

        tr_mean, tr_count = result["tr"]
        en_mean, en_count = result["en"]

        assert tr_count == 2
        assert tr_mean == pytest.approx(0.4, abs=1e-6)
        assert en_count == 1
        assert en_mean == pytest.approx(-0.2, abs=1e-6)
