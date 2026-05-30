"""Faz 2 veri kaynakları — mock'lu birim testleri.

Hiçbir gerçek dış servis çağrılmaz. Her kaynak için:
- happy path: mock'lanmış lib çıktısından dataclass üretimi
- fiyat reddi: ToS gereği fiyat metodları SourceError
- credential / import yokluğunda SourceUnavailableError
- rate limit / parsing yardımcı testleri
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.data.base_source import SourceError, SourceUnavailableError


# ===========================================================================
# RSS source
# ===========================================================================


class TestRSSSource:
    def test_rss_news_item_dataclass_fields(self):
        """NewsItem dataclass alanları ile inşa edilir."""
        from app.data.sources.rss_source import NewsItem

        n = NewsItem(
            title="Test başlık",
            url="https://example.com/a",
            published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            source="reuters_finance",
            language="en",
            summary="özet",
        )
        assert n.title == "Test başlık"
        assert n.language == "en"
        assert n.summary == "özet"

    @pytest.mark.asyncio
    async def test_fetch_quote_raises_source_error(self):
        """RSS quote desteklemez — SourceError."""
        from app.data.sources import rss_source as mod
        # feedparser sahte: import guard'ı geçsin diye True
        with patch.object(mod, "feedparser", MagicMock()):
            src = mod.RSSSource(feeds=[("test", "http://x", "en")])
            with pytest.raises(SourceError):
                await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_fetch_news_without_feedparser_raises_unavailable(self):
        """feedparser yoksa fetch_news -> SourceUnavailableError."""
        from app.data.sources import rss_source as mod

        with patch.object(mod, "feedparser", None):
            src = mod.RSSSource(feeds=[("test", "http://x", "en")])
            with pytest.raises(SourceUnavailableError):
                await src.fetch_news()

    @pytest.mark.asyncio
    async def test_fetch_news_with_mocked_parser_returns_items(self):
        """feedparser.parse mock'lanır → NewsItem listesi döner."""
        from app.data.sources import rss_source as mod

        # feedparser.parse'in döndüreceği sahte parsed object
        fake_entry = {
            "title": "Mock haber",
            "link": "https://example.com/m",
            "published_parsed": (2026, 5, 27, 12, 0, 0, 0, 0, 0),
            "summary": "kısa özet",
        }
        fake_parsed = SimpleNamespace(entries=[fake_entry])

        fake_fp = MagicMock()
        fake_fp.parse = MagicMock(return_value=fake_parsed)

        with patch.object(mod, "feedparser", fake_fp):
            src = mod.RSSSource(feeds=[("reuters_finance", "http://x", "en")])
            items = await src.fetch_news()

        assert len(items) == 1
        assert items[0].title == "Mock haber"
        assert items[0].source == "reuters_finance"
        assert items[0].language == "en"


# ===========================================================================
# KAP source
# ===========================================================================


class TestKapSource:
    def test_disclosure_dataclass_construct(self):
        from app.data.sources.kap_source import KapDisclosure

        d = KapDisclosure(
            ticker="THYAO",
            title="Pay alım satım bildirimi",
            url="https://kap.org.tr/x",
            published_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            subject="özet",
            category="ÖZE",
        )
        assert d.ticker == "THYAO"
        assert d.category == "ÖZE"

    @pytest.mark.asyncio
    async def test_fetch_quote_raises_source_error(self):
        """KAP fiyat değil — quote SourceError."""
        from app.data.sources import kap_source as mod
        with patch.object(mod, "httpx", MagicMock()):
            src = mod.KapSource()
            with pytest.raises(SourceError):
                await src.fetch_quote("THYAO")

    @pytest.mark.asyncio
    async def test_fetch_disclosures_without_httpx_raises_unavailable(self):
        from app.data.sources import kap_source as mod

        with patch.object(mod, "httpx", None):
            src = mod.KapSource()
            with pytest.raises(SourceUnavailableError):
                await src.fetch_disclosures()

    @pytest.mark.asyncio
    async def test_fetch_disclosures_calls_endpoint_once(self):
        """``_call`` bir kere çağrılır (rate limit semaphore + bekleme)."""
        from app.data.sources import kap_source as mod

        with patch.object(mod, "httpx", MagicMock()):
            src = mod.KapSource()
            # _call'ı mock'la — gerçek HTTP'ye çıkma
            src._call = AsyncMock(
                return_value={
                    "items": [
                        {
                            "title": "Bildirim 1",
                            "url": "/tr/Bildirim/123",
                            "publishDate": "2026-05-27 10:00:00",
                            "ticker": "THYAO",
                        }
                    ]
                }
            )
            out = await src.fetch_disclosures()

        assert src._call.await_count == 1
        assert len(out) == 1
        assert out[0].ticker == "THYAO"
        # link tamamlanmış olmalı (BASE_URL ile)
        assert out[0].url.startswith("https://www.kap.org.tr")


# ===========================================================================
# TradingView source
# ===========================================================================


class TestTradingViewSource:
    @pytest.mark.asyncio
    async def test_fetch_technical_summary_without_ta_returns_neutral(self):
        """TA_AVAILABLE=False → NEUTRAL özet (fail-soft, exception YOK)."""
        from app.data.sources import tradingview_source as mod

        with patch.object(mod, "TA_AVAILABLE", False):
            src = mod.TradingViewSource()
            summary = await src.fetch_technical_summary("AAPL")

        assert summary.recommendation == "NEUTRAL"
        assert summary.ma_summary == "NEUTRAL"

    @pytest.mark.asyncio
    async def test_fetch_quote_without_tv_raises_unavailable(self):
        from app.data.sources import tradingview_source as mod

        with patch.object(mod, "TV_AVAILABLE", False):
            src = mod.TradingViewSource()
            with pytest.raises(SourceUnavailableError):
                await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_without_tv_raises_unavailable(self):
        from app.data.sources import tradingview_source as mod

        with patch.object(mod, "TV_AVAILABLE", False):
            src = mod.TradingViewSource()
            with pytest.raises(SourceUnavailableError):
                await src.fetch_ohlcv(
                    "AAPL",
                    datetime(2026, 1, 1, tzinfo=timezone.utc),
                    datetime(2026, 5, 1, tzinfo=timezone.utc),
                )

    def test_tv_summary_dataclass_construct(self):
        from app.data.sources.tradingview_source import TVSummary

        s = TVSummary(
            recommendation="STRONG_BUY",
            oscillators_summary="BUY",
            ma_summary="BUY",
            support_levels=(Decimal("100"),),
            resistance_levels=(Decimal("110"),),
        )
        assert s.recommendation == "STRONG_BUY"


# ===========================================================================
# Investing source
# ===========================================================================


class TestInvestingSource:
    @pytest.mark.asyncio
    async def test_fetch_quote_raises_source_error_tos(self):
        """ToS gereği fiyat reddedilir."""
        from app.data.sources import investing_source as mod

        src = mod.InvestingSource()
        with pytest.raises(SourceError):
            await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_raises_source_error_tos(self):
        from app.data.sources import investing_source as mod

        src = mod.InvestingSource()
        with pytest.raises(SourceError):
            await src.fetch_ohlcv(
                "AAPL",
                datetime(2026, 1, 1, tzinfo=timezone.utc),
                datetime(2026, 5, 1, tzinfo=timezone.utc),
            )

    def test_inv_summary_dataclass_construct(self):
        from app.data.sources.investing_source import InvSummary

        s = InvSummary(
            recommendation="BUY",
            oscillators_summary="NEUTRAL",
            ma_summary="BUY",
        )
        assert s.recommendation == "BUY"


# ===========================================================================
# Finviz source
# ===========================================================================


class TestFinvizSource:
    def test_parse_number_b_suffix(self):
        from app.data.sources.finviz_source import _parse_number

        assert _parse_number("2.9B") == pytest.approx(2.9e9, rel=1e-6)
        assert _parse_number("3.4M") == pytest.approx(3.4e6, rel=1e-6)
        assert _parse_number("12.5%") == pytest.approx(0.125, rel=1e-6)
        assert _parse_number("-") is None
        assert _parse_number(None) is None
        assert _parse_number("N/A") is None
        assert _parse_number("1,234.56") == pytest.approx(1234.56, rel=1e-6)

    @pytest.mark.asyncio
    async def test_fetch_fundamentals_without_finviz_raises_unavailable(self):
        from app.data.sources import finviz_source as mod

        with patch.object(mod, "FV_AVAILABLE", False):
            src = mod.FinvizSource()
            with pytest.raises(SourceUnavailableError):
                await src.fetch_fundamentals("AAPL")

    @pytest.mark.asyncio
    async def test_fetch_fundamentals_with_mock_returns_dataclass(self):
        """finvizfinance mock'lanır → FundamentalData üretilir."""
        from app.data.sources import finviz_source as mod

        with patch.object(mod, "FV_AVAILABLE", True):
            src = mod.FinvizSource()
            src._call = AsyncMock(
                return_value={
                    "P/E": "28.5",
                    "P/B": "12.3",
                    "Market Cap": "2.9B",
                    "Dividend %": "0.55%",
                    "EPS (ttm)": "6.10",
                    "Beta": "1.20",
                    "Target Price": "210.00",
                    "Sector": "Technology",
                    "Industry": "Consumer Electronics",
                }
            )
            data = await src.fetch_fundamentals("AAPL")

        assert data.ticker == "AAPL"
        assert data.pe_ratio == pytest.approx(28.5)
        assert data.market_cap == pytest.approx(2.9e9, rel=1e-6)
        assert data.sector == "Technology"


# ===========================================================================
# Reddit source
# ===========================================================================


class TestRedditSource:
    @pytest.mark.asyncio
    async def test_no_credentials_raises_unavailable(self, monkeypatch):
        from app.data.sources import reddit_source as mod
        from app import config as cfg

        # praw varmış gibi davran
        monkeypatch.setattr(mod, "PRAW_AVAILABLE", True)
        # credentials boş
        monkeypatch.setattr(cfg.settings, "reddit_client_id", "", raising=False)
        monkeypatch.setattr(cfg.settings, "reddit_client_secret", "", raising=False)
        monkeypatch.setattr(cfg.settings, "reddit_user_agent", "", raising=False)

        src = mod.RedditSource()
        with pytest.raises(SourceUnavailableError, match="Reddit credentials yok"):
            await src.fetch_recent_posts(subreddit="wallstreetbets", limit=1)

    @pytest.mark.asyncio
    async def test_fetch_quote_raises_source_error(self, monkeypatch):
        from app.data.sources import reddit_source as mod

        monkeypatch.setattr(mod, "PRAW_AVAILABLE", True)
        src = mod.RedditSource()
        with pytest.raises(SourceError):
            await src.fetch_quote("AAPL")
