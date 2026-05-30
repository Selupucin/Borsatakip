"""Alpha Vantage veri kaynağı.

# ============================================================
# KAYNAK: alphavantage
# AMACI:  Bağımsız üçüncü kaynak — ABD hisseleri için çapraz doğrulama
# KAPSAM: NYSE / NASDAQ + bazı global piyasalar. BIST yok.
# LİMİT:  Ücretsiz plan: GÜNDE 25 istek + DAKİKADA 5 istek.
#         Bu sınırlar nedeniyle Alpha Vantage **birincil kaynak DEĞİLDİR**;
#         yalnızca yfinance/stooq fiyatları arasındaki tutarsızlığı kırmak
#         (tie-breaker) için kullanılır. Saatte 1-2 ticker doğrulaması yeter.
# YEDEK:  Alpha Vantage kotaya takıldığında doğrulama atlanır; verified_close
#         kalan iki kaynağın ağırlıklı ortalaması olur.
# ============================================================

REST API doğrudan ``httpx.AsyncClient`` ile çağrılır (alpha_vantage lib
senkron + opinionated; biz tam asenkron ve düşük bağımlılıklı tutuyoruz).

Rate-limit stratejisi:
- ``asyncio.Semaphore(1)``: aynı anda sadece 1 istek.
- Günlük 25 sayacı (RAM içinde sliding-window, gün dönüşünde sıfırlanır).
- Dakikada 5 sayacı (deque + 60s pencere).
- Kota dolduğunda ``RateLimitError`` fırlatılır; collector kaynağı
  *devre dışı bırakmaz* (failure değil — kotadan kaynaklı geçici durum).
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from loguru import logger

from app.config import settings
from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
)


try:  # pragma: no cover - import guard
    import httpx  # type: ignore
except Exception as exc:  # pragma: no cover
    httpx = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


_BASE_URL = "https://www.alphavantage.co/query"
_DAILY_LIMIT = 25
_PER_MINUTE_LIMIT = 5


class _RateLimiter:
    """Çift pencereli rate limiter: dakikada 5 + günde 25."""

    def __init__(self) -> None:
        self._minute_window: deque[float] = deque()
        self._day_count = 0
        self._day_start = datetime.utcnow().date()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now_dt = datetime.utcnow()
            now_ts = now_dt.timestamp()

            # Gün rollover
            if now_dt.date() != self._day_start:
                self._day_start = now_dt.date()
                self._day_count = 0

            if self._day_count >= _DAILY_LIMIT:
                raise RateLimitError(
                    f"alphavantage: günlük {_DAILY_LIMIT} istek kotası doldu"
                )

            # Dakikalık pencere temizliği
            while self._minute_window and now_ts - self._minute_window[0] > 60.0:
                self._minute_window.popleft()

            if len(self._minute_window) >= _PER_MINUTE_LIMIT:
                wait = 60.0 - (now_ts - self._minute_window[0]) + 0.1
                logger.info(
                    "alphavantage: dakika kotası dolu, {:.1f}s bekleniyor", wait
                )
                await asyncio.sleep(wait)

            self._minute_window.append(datetime.utcnow().timestamp())
            self._day_count += 1


class AlphaVantageSource(BaseSource):
    name = "alphavantage"

    _semaphore = asyncio.Semaphore(1)
    _MAX_RETRY = 2
    _BACKOFF_BASE = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._api_key = settings.alpha_vantage_api_key
        self._limiter = _RateLimiter()
        if httpx is None:
            logger.warning(
                "httpx import edilemedi: {}. AlphaVantageSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )
        if not self._api_key:
            logger.warning(
                "ALPHA_VANTAGE_API_KEY tanımlı değil — AlphaVantageSource pasif."
            )

    # ---- Public API ---------------------------------------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        if httpx is None:
            raise SourceUnavailableError("httpx kurulu değil")
        if not self._api_key:
            raise SourceUnavailableError("alpha vantage api key yok")

        function = "TIME_SERIES_DAILY" if interval == "1d" else "TIME_SERIES_INTRADAY"
        params: dict[str, Any] = {
            "function": function,
            "symbol": ticker,
            "apikey": self._api_key,
            "outputsize": "full",
        }
        if function == "TIME_SERIES_INTRADAY":
            params["interval"] = interval if interval.endswith("min") else "60min"

        data = await self._call(params)
        series_key = next((k for k in data if "Time Series" in k), None)
        if series_key is None:
            raise SourceError(f"alphavantage: beklenen Time Series anahtarı yok ({list(data.keys())})")

        bars: list[OHLCVBar] = []
        for ts_str, row in data[series_key].items():
            try:
                ts = datetime.fromisoformat(ts_str)
            except ValueError:
                continue
            if ts < start or ts > end:
                continue
            try:
                bars.append(
                    OHLCVBar(
                        timestamp=ts,
                        open=Decimal(row["1. open"]),
                        high=Decimal(row["2. high"]),
                        low=Decimal(row["3. low"]),
                        close=Decimal(row["4. close"]),
                        volume=int(row["5. volume"]),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.debug("alphavantage: {} satır atlandı: {}", ticker, exc)

        bars.sort(key=lambda b: b.timestamp)
        return bars

    async def fetch_quote(self, ticker: str) -> Quote:
        if httpx is None:
            raise SourceUnavailableError("httpx kurulu değil")
        if not self._api_key:
            raise SourceUnavailableError("alpha vantage api key yok")

        params = {
            "function": "GLOBAL_QUOTE",
            "symbol": ticker,
            "apikey": self._api_key,
        }
        data = await self._call(params)
        gq = data.get("Global Quote") or {}
        price_str = gq.get("05. price")
        if not price_str:
            raise SourceError(f"alphavantage: {ticker} için Global Quote boş")

        ts_str = gq.get("07. latest trading day")
        try:
            ts = datetime.fromisoformat(ts_str) if ts_str else datetime.utcnow()
        except ValueError:
            ts = datetime.utcnow()

        return Quote(
            ticker=ticker,
            price=Decimal(price_str),
            timestamp=ts,
            source=self.name,
            volume=int(gq.get("06. volume", 0) or 0) or None,
        )

    # ---- Internal -----------------------------------------------------------

    async def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        await self._limiter.acquire()

        last_exc: Exception | None = None
        async with self._semaphore:
            for attempt in range(self._MAX_RETRY):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.get(_BASE_URL, params=params)
                    if resp.status_code == 429:
                        raise RateLimitError("alphavantage HTTP 429")
                    resp.raise_for_status()
                    data = resp.json()

                    # Alpha Vantage bazen 200 dönüp body'de 'Note' / 'Information'
                    # ile rate-limit haber verir.
                    if "Note" in data or "Information" in data:
                        msg = data.get("Note") or data.get("Information")
                        if "call frequency" in str(msg).lower() or "rate" in str(msg).lower():
                            raise RateLimitError(f"alphavantage soft-limit: {msg}")
                        raise SourceError(f"alphavantage: {msg}")
                    if "Error Message" in data:
                        raise SourceError(f"alphavantage: {data['Error Message']}")
                    return data
                except RateLimitError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "alphavantage deneme {}/{} başarısız: {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        raise SourceUnavailableError(f"alphavantage retry tükendi: {last_exc}")


__all__ = ["AlphaVantageSource"]
