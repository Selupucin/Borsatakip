"""Finviz veri kaynağı (ABD hisseleri için temel veriler + analist hedefleri).

# ============================================================
# KAYNAK: finviz
# YASAL:  Finviz.com ToS gereği veri KİŞİSEL/AKADEMİK kullanım
#         içindir; tabloların scrape ile toplu/tekrarlı çekilmesi
#         ve ticari ürün haline getirilmesi YASAK. ``finvizfinance``
#         lib resmi olmayıp scrape ile çalışır; bu nedenle aynı
#         ToS kısıtları geçerlidir.
# RATE:   1 req / 3 sn — Finviz aşırı istekleri 429/captcha ile
#         engeller. Semaphore(1) + 3s min interval.
# robots.txt: Finviz çoğu yolu allow eder ama Crawl-delay sıkıdır.
# ============================================================

Mimari:
- ``finvizfinance`` lib senkrondur ve scrape ile DataFrame döndürür;
  tüm çağrılar ``asyncio.to_thread`` ile sarılır.
- ``fetch_quote`` desteklenir (Finviz quote sayfası — son fiyat,
  hacim, anlık metrikler).
- ``fetch_ohlcv`` Finviz tarafında ücretli/sınırlı; bu modül
  ``SourceError`` ile fail eder (yfinance/stooq önerilir).
- Faz 2 için ana çıktı: ``fetch_fundamentals`` — F/K, PD/DD, market cap,
  beta, sektör/endüstri, analist hedef fiyat, EPS, temettü yield.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from loguru import logger

from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    SourceError,
    SourceUnavailableError,
)


try:  # pragma: no cover - import guard
    from finvizfinance.quote import finvizfinance  # type: ignore
    FV_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    finvizfinance = None  # type: ignore
    FV_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]


def _pick_ua() -> str:
    return random.choice(_USER_AGENTS)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FundamentalData:
    """Finviz temel veriler özet (Faz 2 minimum)."""

    ticker: str
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    market_cap: Optional[float] = None  # USD, mutlak (örn. 2_900_000_000_000)
    dividend_yield: Optional[float] = None  # 0.05 = %5
    eps: Optional[float] = None
    beta: Optional[float] = None
    target_price: Optional[float] = None  # analist konsensüs hedefi (USD)
    sector: Optional[str] = None
    industry: Optional[str] = None


# Finviz raw dict'inden çekilecek anahtarlar (görüldüğü adlarla)
_FV_KEYS = {
    "pe_ratio": "P/E",
    "pb_ratio": "P/B",
    "market_cap": "Market Cap",
    "dividend_yield": "Dividend %",  # bazı sürümlerde "Dividend Yield"
    "eps": "EPS (ttm)",
    "beta": "Beta",
    "target_price": "Target Price",
    "sector": "Sector",
    "industry": "Industry",
}


def _parse_number(raw: Any) -> Optional[float]:
    """Finviz'in '%5.2', '2.9B', '-' gibi değerlerini float'a çevir."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s in ("", "-", "N/A", "n/a"):
        return None
    s = s.replace(",", "")
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    mult = 1.0
    if s.endswith("B"):
        mult = 1e9
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1e6
        s = s[:-1]
    elif s.endswith("K"):
        mult = 1e3
        s = s[:-1]
    try:
        val = float(s) * mult
        if pct:
            val = val / 100.0
        return val
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class FinvizSource(BaseSource):
    name = "finviz"

    # 1 req / 3 sn
    _MIN_INTERVAL_S = 3.0
    _MAX_RETRY = 2
    _BACKOFF_BASE = 2.0
    _semaphore = asyncio.Semaphore(1)

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        if not FV_AVAILABLE:
            logger.warning(
                "finvizfinance import edilemedi ({}). FinvizSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )

    # ---- BaseSource: quote destekli, OHLCV reddeder ------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        # Finviz tarihsel veri OHLCV scrape için uygun değil; yfinance kullan.
        raise SourceError("finviz: OHLCV desteklenmiyor — yfinance/stooq kullan")

    async def fetch_quote(self, ticker: str) -> Quote:
        if not FV_AVAILABLE:
            raise SourceUnavailableError("finvizfinance kurulu değil")

        raw = await self._call(self._fetch_raw, ticker)
        price_raw = raw.get("Price") or raw.get("Prev Close")
        price = _parse_number(price_raw)
        if price is None:
            raise SourceError(f"finviz: {ticker} için fiyat parse edilemedi")

        volume = _parse_number(raw.get("Volume"))
        return Quote(
            ticker=ticker.upper(),
            price=Decimal(str(price)),
            timestamp=datetime.utcnow(),
            source=self.name,
            volume=int(volume) if volume is not None else None,
        )

    # ---- Public: fundamentals ----------------------------------------------

    async def fetch_fundamentals(self, ticker: str) -> FundamentalData:
        if not FV_AVAILABLE:
            raise SourceUnavailableError("finvizfinance kurulu değil")

        raw = await self._call(self._fetch_raw, ticker)

        kwargs: dict[str, Any] = {"ticker": ticker.upper()}
        for attr, key in _FV_KEYS.items():
            val = raw.get(key)
            if attr in ("sector", "industry"):
                kwargs[attr] = str(val).strip() if val and str(val).strip() else None
            else:
                kwargs[attr] = _parse_number(val)

        return FundamentalData(**kwargs)

    # ---- Internal ----------------------------------------------------------

    @staticmethod
    def _fetch_raw(ticker: str) -> dict[str, Any]:  # pragma: no cover - external IO
        # finvizfinance içeride requests kullanır; UA override için patch
        # kütüphaneye göre değişir — burada nazik bekleme bağımız.
        stock = finvizfinance(ticker.upper())
        return stock.ticker_fundament() or {}

    async def _call(self, sync_fn, *args):
        last_exc: Exception | None = None
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)

            for attempt in range(self._MAX_RETRY):
                try:
                    # User-Agent rotasyonu: finvizfinance global yapılandırma
                    # sunmadığı için en azından sürpriz ortam değişkeni set ediyoruz.
                    # (Lib'in kendi session'ı yine de kontrolde — kibarlık göstergesi.)
                    import os
                    os.environ.setdefault("REQUESTS_USER_AGENT", _pick_ua())

                    result = await asyncio.to_thread(sync_fn, *args)
                    self._last_call_ts = asyncio.get_event_loop().time()
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "finviz deneme {}/{} başarısız: {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    self._last_call_ts = asyncio.get_event_loop().time()
        raise SourceUnavailableError(f"finviz retry tükendi: {last_exc}")


__all__ = ["FinvizSource", "FundamentalData"]
