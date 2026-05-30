"""DataCollector.save_news() ve save_disclosures() birim testleri.

In-memory SQLite (async) üzerinde gerçek NewsFeed satırları yazılır.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.data.collector import DataCollector
from app.data.sources.kap_source import KapDisclosure
from app.data.sources.rss_source import NewsItem
from app.db.models import Instrument, NewsFeed


# ---------------------------------------------------------------------------
# save_news
# ---------------------------------------------------------------------------


class TestSaveNews:
    @pytest.mark.asyncio
    async def test_save_news_inserts_rows(self, async_session_factory):
        """NewsItem listesi → news_feed satırları."""
        async with async_session_factory() as session:
            instr = Instrument(ticker="AAPL", name="Apple")
            session.add(instr)
            await session.commit()
            instrument_id = int(instr.id)

        collector = DataCollector([], async_session_factory)
        items = [
            NewsItem(
                title="N1",
                url="https://e.com/n1",
                published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
                source="reuters",
                language="en",
            ),
            NewsItem(
                title="N2",
                url="https://e.com/n2",
                published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
                source="reuters",
                language="en",
            ),
        ]
        n = await collector.save_news(items, instrument_id=instrument_id)
        assert n == 2

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(NewsFeed).where(NewsFeed.instrument_id == instrument_id)
                )
            ).scalars().all()
            assert len(rows) == 2
            urls = {r.url for r in rows}
            assert urls == {"https://e.com/n1", "https://e.com/n2"}

    @pytest.mark.asyncio
    async def test_save_news_is_idempotent_per_url(self, async_session_factory):
        """Aynı URL için ikinci save_news no-op."""
        async with async_session_factory() as session:
            instr = Instrument(ticker="MSFT", name="Microsoft")
            session.add(instr)
            await session.commit()
            instrument_id = int(instr.id)

        collector = DataCollector([], async_session_factory)
        item = NewsItem(
            title="N1",
            url="https://e.com/dup",
            published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            source="reuters",
            language="en",
        )
        first = await collector.save_news([item], instrument_id=instrument_id)
        second = await collector.save_news([item], instrument_id=instrument_id)

        assert first == 1
        assert second == 0  # duplicate yutuldu

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(NewsFeed).where(NewsFeed.instrument_id == instrument_id)
                )
            ).scalars().all()
            assert len(rows) == 1


# ---------------------------------------------------------------------------
# save_disclosures
# ---------------------------------------------------------------------------


class TestSaveDisclosures:
    @pytest.mark.asyncio
    async def test_save_disclosures_delegates_to_save_news(self, async_session_factory):
        """KapDisclosure listesi → news_feed (source='kap', language='tr')."""
        async with async_session_factory() as session:
            instr = Instrument(ticker="THYAO", name="Türk Hava Yolları")
            session.add(instr)
            await session.commit()
            instrument_id = int(instr.id)

        collector = DataCollector([], async_session_factory)
        d1 = KapDisclosure(
            ticker="THYAO",
            title="Bildirim 1",
            url="https://kap.org.tr/x/1",
            published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            subject="özet 1",
        )
        d2 = KapDisclosure(
            ticker="THYAO",
            title="Bildirim 2",
            url="https://kap.org.tr/x/2",
            published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            subject="özet 2",
        )

        n = await collector.save_disclosures(
            [d1, d2], ticker_to_instrument_id={"THYAO": instrument_id}
        )
        assert n == 2

        async with async_session_factory() as session:
            rows = (
                await session.execute(
                    select(NewsFeed).where(NewsFeed.instrument_id == instrument_id)
                )
            ).scalars().all()
            assert len(rows) == 2
            for r in rows:
                assert r.source == "kap"
                assert r.language == "tr"
