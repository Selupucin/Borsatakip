"""Veri kaynakları birim testleri.

Test edilen kaynaklar:
    - YFinanceSource
    - StooqSource
    - AlphaVantageSource
    - IsYatirimSource
    - TCMBSource

Mock stratejisi:
- Hiçbir gerçek HTTP çağrısı yapılmaz.
- ``yfinance``, ``pandas_datareader``, ``isyatirimhisse`` modülleri
  ``monkeypatch`` ile stub'lanır.
- ``httpx.AsyncClient`` (alpha vantage, tcmb) async context manager mock'lanır.

Doğrulanan davranışlar:
    * ``name`` attribute
    * happy-path quote/ohlcv
    * empty / hata case
    * SourceUnavailableError / RateLimitError
    * Rate-limit semafor davranışı (sayı bazında)
    * TCMB: hisse ticker -> SourceError
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
)


# ---------------------------------------------------------------------------
# YFinance
# ---------------------------------------------------------------------------


class TestYFinanceSource:
    def test_name_attribute(self):
        from app.data.sources.yfinance_source import YFinanceSource

        assert YFinanceSource.name == "yfinance"

    @pytest.mark.asyncio
    async def test_fetch_quote_happy_path(self, monkeypatch):
        """_download_quote JSON pipeline -> geçerli Quote dönmeli."""
        from app.data.sources import yfinance_source as ym

        # yfinance import'unu görünür kıl (None değil), ama _download_quote'u doğrudan mock'la.
        monkeypatch.setattr(ym, "yf", MagicMock())

        def _fake_download_quote(ticker):
            return {
                "price": 152.34,
                "volume": 1_234_500,
                "timestamp": datetime(2024, 1, 15, 12, 0),
            }

        monkeypatch.setattr(ym.YFinanceSource, "_download_quote", staticmethod(_fake_download_quote))

        src = ym.YFinanceSource()
        q = await src.fetch_quote("AAPL")

        assert isinstance(q, Quote)
        assert q.ticker == "AAPL"
        assert q.source == "yfinance"
        assert q.price == Decimal("152.34")
        assert q.volume == 1_234_500

    @pytest.mark.asyncio
    async def test_fetch_quote_raises_when_yfinance_not_installed(self, monkeypatch):
        from app.data.sources import yfinance_source as ym

        monkeypatch.setattr(ym, "yf", None)
        src = ym.YFinanceSource()
        with pytest.raises(SourceUnavailableError):
            await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_happy_path(self, monkeypatch):
        from app.data.sources import yfinance_source as ym

        idx = pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC")
        df = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [101.0, 102.0, 103.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [100.5, 101.5, 102.5],
                "Volume": [1000, 1100, 1200],
            },
            index=idx,
        )

        monkeypatch.setattr(ym, "yf", MagicMock())
        monkeypatch.setattr(
            ym.YFinanceSource,
            "_download_ohlcv",
            staticmethod(lambda ticker, start, end, interval: df),
        )

        src = ym.YFinanceSource()
        bars = await src.fetch_ohlcv(
            "AAPL", datetime(2024, 1, 1), datetime(2024, 1, 4), "1d"
        )

        assert len(bars) == 3
        assert all(isinstance(b, OHLCVBar) for b in bars)
        assert bars[0].close == Decimal("100.5")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_empty_raises_source_error(self, monkeypatch):
        from app.data.sources import yfinance_source as ym

        monkeypatch.setattr(ym, "yf", MagicMock())
        monkeypatch.setattr(
            ym.YFinanceSource,
            "_download_ohlcv",
            staticmethod(lambda ticker, start, end, interval: pd.DataFrame()),
        )

        src = ym.YFinanceSource()
        monkeypatch.setattr(src, "_MAX_RETRY", 1)
        monkeypatch.setattr(src, "_BACKOFF_BASE", 0.0)

        with pytest.raises(SourceError):
            await src.fetch_ohlcv(
                "AAPL", datetime(2024, 1, 1), datetime(2024, 1, 4), "1d"
            )

    @pytest.mark.asyncio
    async def test_with_retry_eventually_raises_source_unavailable(self, monkeypatch):
        from app.data.sources import yfinance_source as ym

        def _raise(*_a, **_kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(ym, "yf", MagicMock())
        monkeypatch.setattr(ym.YFinanceSource, "_download_ohlcv", staticmethod(_raise))

        src = ym.YFinanceSource()
        monkeypatch.setattr(src, "_MAX_RETRY", 2)
        monkeypatch.setattr(src, "_BACKOFF_BASE", 0.0)

        with pytest.raises(SourceUnavailableError):
            await src.fetch_ohlcv(
                "AAPL", datetime(2024, 1, 1), datetime(2024, 1, 4), "1d"
            )


# ---------------------------------------------------------------------------
# Stooq
# ---------------------------------------------------------------------------


class TestStooqSource:
    def test_name_attribute(self):
        from app.data.sources.stooq_source import StooqSource

        assert StooqSource.name == "stooq"

    def test_ticker_normalization(self):
        from app.data.sources.stooq_source import _to_stooq_ticker

        assert _to_stooq_ticker("AAPL") == "aapl.us"
        assert _to_stooq_ticker("THYAO.IS") == "thyao.tr"
        assert _to_stooq_ticker("thyao.tr") == "thyao.tr"

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_happy_path(self, monkeypatch):
        from app.data.sources import stooq_source as ss

        idx = pd.date_range("2024-01-01", periods=2, freq="D")
        df = pd.DataFrame(
            {
                "Open": [100.0, 101.0],
                "High": [101.0, 102.0],
                "Low": [99.0, 100.0],
                "Close": [100.5, 101.5],
                "Volume": [1000, 1100],
            },
            index=idx,
        )

        fake_pdr = MagicMock()
        fake_pdr.DataReader.return_value = df
        monkeypatch.setattr(ss, "pdr_data", fake_pdr)

        src = ss.StooqSource()
        bars = await src.fetch_ohlcv(
            "AAPL", datetime(2024, 1, 1), datetime(2024, 1, 3), "1d"
        )
        assert len(bars) == 2

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_unsupported_interval_raises(self, monkeypatch):
        from app.data.sources import stooq_source as ss

        monkeypatch.setattr(ss, "pdr_data", MagicMock())
        src = ss.StooqSource()
        with pytest.raises(SourceError):
            await src.fetch_ohlcv(
                "AAPL", datetime(2024, 1, 1), datetime(2024, 1, 2), "5m"
            )

    @pytest.mark.asyncio
    async def test_fetch_quote_when_module_missing(self, monkeypatch):
        from app.data.sources import stooq_source as ss

        monkeypatch.setattr(ss, "pdr_data", None)
        src = ss.StooqSource()
        with pytest.raises(SourceUnavailableError):
            await src.fetch_quote("AAPL")


# ---------------------------------------------------------------------------
# Alpha Vantage
# ---------------------------------------------------------------------------


def _async_httpx_client(get_return_value):
    """``httpx.AsyncClient`` benzeri async context manager mock üretir."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=get_return_value)

    cm = AsyncMock()
    cm.__aenter__.return_value = client
    cm.__aexit__.return_value = None

    fake_httpx = MagicMock()
    fake_httpx.AsyncClient = MagicMock(return_value=cm)
    return fake_httpx, client


class TestAlphaVantageSource:
    def test_name_attribute(self):
        from app.data.sources.alphavantage_source import AlphaVantageSource

        assert AlphaVantageSource.name == "alphavantage"

    @pytest.mark.asyncio
    async def test_fetch_quote_happy_path(self, monkeypatch):
        from app.data.sources import alphavantage_source as av

        monkeypatch.setattr(av.settings, "alpha_vantage_api_key", "X", raising=False)

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "Global Quote": {
                "05. price": "172.50",
                "06. volume": "1500",
                "07. latest trading day": "2024-01-15",
            }
        }

        fake_httpx, _client = _async_httpx_client(resp)
        monkeypatch.setattr(av, "httpx", fake_httpx)

        src = av.AlphaVantageSource()
        q = await src.fetch_quote("AAPL")
        assert q.price == Decimal("172.50")
        assert q.source == "alphavantage"
        assert q.volume == 1500

    @pytest.mark.asyncio
    async def test_fetch_quote_no_api_key_raises_unavailable(self, monkeypatch):
        from app.data.sources import alphavantage_source as av

        monkeypatch.setattr(av.settings, "alpha_vantage_api_key", "", raising=False)
        monkeypatch.setattr(av, "httpx", MagicMock())
        src = av.AlphaVantageSource()
        with pytest.raises(SourceUnavailableError):
            await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_http_429_raises_rate_limit(self, monkeypatch):
        from app.data.sources import alphavantage_source as av

        monkeypatch.setattr(av.settings, "alpha_vantage_api_key", "X", raising=False)

        resp = MagicMock()
        resp.status_code = 429
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {}

        fake_httpx, _ = _async_httpx_client(resp)
        monkeypatch.setattr(av, "httpx", fake_httpx)

        src = av.AlphaVantageSource()
        with pytest.raises(RateLimitError):
            await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_soft_rate_limit_in_body_raises(self, monkeypatch):
        from app.data.sources import alphavantage_source as av

        monkeypatch.setattr(av.settings, "alpha_vantage_api_key", "X", raising=False)

        resp = MagicMock()
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "Note": "Thank you for using Alpha Vantage. Our standard API call frequency is 5 per minute."
        }

        fake_httpx, _ = _async_httpx_client(resp)
        monkeypatch.setattr(av, "httpx", fake_httpx)

        src = av.AlphaVantageSource()
        with pytest.raises(RateLimitError):
            await src.fetch_quote("AAPL")

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_after_quota(self, monkeypatch):
        """RateLimiter günde 25 sınırı dolunca RateLimitError fırlatır."""
        from app.data.sources.alphavantage_source import _RateLimiter, _DAILY_LIMIT

        rl = _RateLimiter()
        # Sayacı doldur
        rl._day_count = _DAILY_LIMIT
        with pytest.raises(RateLimitError):
            await rl.acquire()


# ---------------------------------------------------------------------------
# İş Yatırım
# ---------------------------------------------------------------------------


class TestIsYatirimSource:
    def test_name_attribute(self):
        from app.data.sources.isyatirim_source import IsYatirimSource

        assert IsYatirimSource.name == "isyatirim"

    def test_ticker_normalization(self):
        from app.data.sources.isyatirim_source import _normalize_bist_ticker

        assert _normalize_bist_ticker("THYAO.IS") == "THYAO"
        assert _normalize_bist_ticker("thyao") == "THYAO"
        assert _normalize_bist_ticker("garan.tr") == "GARAN"

    def test_health_check_ticker_is_thyao(self):
        from app.data.sources.isyatirim_source import IsYatirimSource

        # Module-level monkeypatch yapma; instance _health_check_ticker'ı kontrol et
        with patch("app.data.sources.isyatirim_source.StockData", MagicMock()):
            src = IsYatirimSource()
            assert src._health_check_ticker() == "THYAO"

    @pytest.mark.asyncio
    async def test_fetch_quote_when_module_missing(self, monkeypatch):
        from app.data.sources import isyatirim_source as iss

        monkeypatch.setattr(iss, "StockData", None)
        src = iss.IsYatirimSource()
        with pytest.raises(SourceUnavailableError):
            await src.fetch_quote("THYAO")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_unsupported_interval_raises(self, monkeypatch):
        from app.data.sources import isyatirim_source as iss

        monkeypatch.setattr(iss, "StockData", MagicMock())
        src = iss.IsYatirimSource()
        with pytest.raises(SourceError):
            await src.fetch_ohlcv(
                "THYAO", datetime(2024, 1, 1), datetime(2024, 1, 2), "1h"
            )

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_happy_path(self, monkeypatch):
        from app.data.sources import isyatirim_source as iss

        # isyatirimhisse DataFrame'i TR sütun adlarıyla döner
        df = pd.DataFrame(
            {
                "tarih": pd.to_datetime(["2024-01-01", "2024-01-02"]),
                "acilis": [10.0, 10.5],
                "yuksek": [10.5, 11.0],
                "dusuk": [9.8, 10.2],
                "kapanis": [10.3, 10.8],
                "hacim": [1000, 1200],
            }
        )

        fake_stock_data_class = MagicMock()
        fake_instance = MagicMock()
        fake_instance.get_data.return_value = df
        fake_stock_data_class.return_value = fake_instance
        monkeypatch.setattr(iss, "StockData", fake_stock_data_class)

        src = iss.IsYatirimSource()
        bars = await src.fetch_ohlcv(
            "THYAO", datetime(2024, 1, 1), datetime(2024, 1, 3), "1d"
        )
        assert len(bars) == 2
        assert bars[0].close == Decimal("10.3")


# ---------------------------------------------------------------------------
# TCMB
# ---------------------------------------------------------------------------


class TestTCMBSource:
    def test_name_attribute(self):
        from app.data.sources.tcmb_source import TCMBSource

        assert TCMBSource.name == "tcmb"

    def test_health_check_ticker_is_usdtry(self):
        from app.data.sources.tcmb_source import TCMBSource

        assert TCMBSource()._health_check_ticker() == "USDTRY"

    def test_resolve_series_for_stock_ticker_raises_source_error(self):
        """KURAL: Hisse ticker'ı verildiğinde TCMB ``SourceError`` fırlatır."""
        from app.data.sources.tcmb_source import TCMBSource

        src = TCMBSource()
        with pytest.raises(SourceError):
            src._resolve_series("THYAO")

    def test_resolve_series_for_usdtry_returns_known_code(self):
        from app.data.sources.tcmb_source import TCMBSource, _PAIR_SERIES

        src = TCMBSource()
        assert src._resolve_series("USDTRY") == _PAIR_SERIES["USDTRY"]
        assert src._resolve_series("usd/try") == _PAIR_SERIES["USDTRY"]

    @pytest.mark.asyncio
    async def test_fetch_quote_for_stock_ticker_raises(self, monkeypatch):
        """Hisse ticker -> fetch_quote SourceError."""
        from app.data.sources import tcmb_source as ts

        monkeypatch.setattr(ts, "httpx", MagicMock())
        src = ts.TCMBSource()
        with pytest.raises(SourceError):
            await src.fetch_quote("THYAO")

    @pytest.mark.asyncio
    async def test_fetch_quote_for_usdtry_happy_path(self, monkeypatch):
        from app.data.sources import tcmb_source as ts

        # EVDS yanıtı: items listesi, her item Tarih + seri kodu (nokta->_)
        series = "TP.DK.USD.A.YTL"
        items = [
            {"Tarih": "10-01-2024", series.replace(".", "_"): "29.45"},
            {"Tarih": "11-01-2024", series.replace(".", "_"): "29.50"},
        ]

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {"items": items}

        fake_httpx, _ = _async_httpx_client(resp)
        monkeypatch.setattr(ts, "httpx", fake_httpx)

        src = ts.TCMBSource()
        q = await src.fetch_quote("USDTRY")
        assert q.source == "tcmb"
        assert q.price == Decimal("29.50")

    @pytest.mark.asyncio
    async def test_fetch_ohlcv_httpx_not_installed(self, monkeypatch):
        from app.data.sources import tcmb_source as ts

        monkeypatch.setattr(ts, "httpx", None)
        src = ts.TCMBSource()
        with pytest.raises(SourceUnavailableError):
            await src.fetch_ohlcv(
                "USDTRY", datetime(2024, 1, 1), datetime(2024, 1, 10), "1d"
            )


# ---------------------------------------------------------------------------
# Rate-limit / semafor sayım testi (generic)
# ---------------------------------------------------------------------------


class TestSemaphoreBehavior:
    @pytest.mark.asyncio
    async def test_yfinance_semaphore_serializes_concurrent_calls(self, monkeypatch):
        """3 paralel `_with_retry` çağrısı semafor=1 ile serileşir.

        Her çağrı kısa async no-op; semafor olmadan paralel biterdi. Sayım:
        ``call_count == 3`` (hepsi başarılı), eşzamanlı görülen MAX ``in_flight``
        1 olmalı.
        """
        from app.data.sources import yfinance_source as ym

        monkeypatch.setattr(ym, "yf", MagicMock())
        src = ym.YFinanceSource()

        in_flight = 0
        max_in_flight = 0
        call_count = 0
        lock = asyncio.Lock()

        async def fake_runner():
            nonlocal in_flight, max_in_flight, call_count
            async with lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
                call_count += 1
            await asyncio.sleep(0.02)
            async with lock:
                in_flight -= 1
            return "ok"

        async def _runner_wrapper(*_args, **_kwargs):
            return await fake_runner()

        await asyncio.gather(
            src._with_retry(_runner_wrapper),
            src._with_retry(_runner_wrapper),
            src._with_retry(_runner_wrapper),
        )
        assert call_count == 3
        assert max_in_flight == 1  # semafor=1 sayesinde
