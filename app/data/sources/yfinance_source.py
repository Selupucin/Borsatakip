"""yfinance veri kaynağı.

# ============================================================
# KAYNAK: yfinance
# AMACI:  Yahoo Finance üzerinden OHLCV ve quote verisi çekmek
# KAPSAM: NYSE / NASDAQ + BIST (``.IS`` suffix ile, örn. ``THYAO.IS``)
# LİMİT:  Resmi rate-limit yok; pratikte ~2000 istek/saat üstünde IP
#         yavaşlatması başlar. Konservatif: 1 istek/sn semaforla sınırlandık.
# YEDEK:  yfinance kırıldığında ABD için ``stooq``, BIST için ``isyatirim``
#         devralır.
# ============================================================

yfinance kütüphanesi senkron olduğu için tüm çağrılar ``asyncio.to_thread``
ile arka plan thread'ine taşınır; böylece collector'ın asyncio event loop'u
bloklanmaz.

Hata politikası:
- ``Exception`` -> ``SourceUnavailableError`` (collector failure sayar)
- Boş DataFrame -> ``SourceError`` (ticker geçersiz veya veri yok)
- Retry: 3 deneme, exponential back-off (1s -> 2s -> 4s).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from loguru import logger

from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    SourceError,
    SourceUnavailableError,
)


# Senkron import'u modül seviyesinde tutuyoruz; collector starup'ta hatayı
# erkenden görmemizi sağlar. Bağımlılık kurulu değilse `SourceUnavailableError`
# fırlatarak collector tarafından devre dışı bırakılır.
try:  # pragma: no cover - import guard
    import yfinance as yf  # type: ignore
except Exception as exc:  # pragma: no cover
    yf = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


_INTERVAL_MAP = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "60m",
    "1d": "1d",
    "1wk": "1wk",
    "1mo": "1mo",
}


class YFinanceSource(BaseSource):
    name = "yfinance"

    _MAX_RETRY = 3
    _BACKOFF_BASE = 1.0  # saniye

    def __init__(self) -> None:
        super().__init__()
        # Per-loop Semaphore cache: asyncio.run() her tick yeni loop yarattığı için
        # class-level Semaphore "bound to different event loop" hatası verir.
        self._sem_per_loop: dict[int, asyncio.Semaphore] = {}
        if yf is None:
            logger.warning(
                "yfinance import edilemedi: {}. YFinanceSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )

    @property
    def _semaphore(self) -> asyncio.Semaphore:
        """Aktif event loop'a bağlı Semaphore — yoksa lazy yaratır."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Çalışan loop yoksa yeni Semaphore döndür (sync call edge case)
            return asyncio.Semaphore(1)
        loop_id = id(loop)
        sem = self._sem_per_loop.get(loop_id)
        if sem is None:
            sem = asyncio.Semaphore(1)
            self._sem_per_loop[loop_id] = sem
        return sem

    # ---- Public API ---------------------------------------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        if yf is None:
            raise SourceUnavailableError("yfinance kurulu değil")

        yf_interval = _INTERVAL_MAP.get(interval, interval)

        df = await self._with_retry(
            asyncio.to_thread,
            self._download_ohlcv,
            ticker,
            start,
            end,
            yf_interval,
        )

        if df is None or df.empty:
            raise SourceError(f"yfinance: {ticker} için veri yok")

        # Yeni yfinance MultiIndex column döndürür: ('Open', 'AAPL'). Flatten.
        if hasattr(df.columns, "levels") and df.columns.nlevels > 1:
            df = df.copy()
            df.columns = df.columns.get_level_values(0)

        import math

        def _dec(v) -> Decimal | None:
            """NaN/None değerleri sessizce skip eder."""
            if v is None:
                return None
            try:
                f = float(v)
            except (TypeError, ValueError):
                return None
            if math.isnan(f) or math.isinf(f):
                return None
            return Decimal(str(f))

        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            o = _dec(row.get("Open"))
            h = _dec(row.get("High"))
            l = _dec(row.get("Low"))
            c = _dec(row.get("Close"))
            if c is None:
                continue  # close zorunlu, yoksa satır atla
            try:
                vol_raw = row.get("Volume")
                vol_f = float(vol_raw) if vol_raw is not None else 0.0
                if math.isnan(vol_f):
                    vol_f = 0.0
                bars.append(
                    OHLCVBar(
                        timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                        open=o or c,
                        high=h or c,
                        low=l or c,
                        close=c,
                        volume=int(vol_f),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("yfinance: {} satır atlandı: {}", ticker, exc)
                continue

        return bars

    async def fetch_quote(self, ticker: str) -> Quote:
        if yf is None:
            raise SourceUnavailableError("yfinance kurulu değil")

        info = await self._with_retry(
            asyncio.to_thread, self._download_quote, ticker
        )

        price = info.get("price")
        ts = info.get("timestamp") or datetime.utcnow()

        if price is None:
            raise SourceError(f"yfinance: {ticker} için fiyat alınamadı")

        return Quote(
            ticker=ticker,
            price=Decimal(str(price)),
            timestamp=ts,
            source=self.name,
            volume=info.get("volume"),
        )

    # ---- Internal (senkron, thread'de koşar) --------------------------------

    @staticmethod
    def _download_ohlcv(
        ticker: str, start: datetime, end: datetime, interval: str
    ):  # pragma: no cover - external IO
        """Yahoo Finance chart API'sini doğrudan çağırır → pandas DataFrame.

        yfinance.download() içeride pandas string_arrow ile native crash veriyor
        (Windows + pandas 3.0 + pyarrow). Bunun yerine JSON endpoint çekip
        kendimiz DataFrame kuruyoruz — pandas string array yok, sadece numeric.
        """
        import json
        import urllib.error
        import urllib.request

        import pandas as pd

        p1 = int(start.timestamp())
        p2 = int(end.timestamp())
        # interval mapping zaten yapılmış (1d/1h/1wk gibi)
        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?period1={p1}&period2={p2}&interval={interval}&includePrePost=false"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise SourceError(f"yfinance JSON OHLCV başarısız: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SourceError(f"yfinance JSON parse hatası: {exc}") from exc

        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            return pd.DataFrame()

        r0 = result[0]
        timestamps = r0.get("timestamp") or []
        if not timestamps:
            return pd.DataFrame()
        quote = ((r0.get("indicators") or {}).get("quote") or [{}])[0]
        if not quote:
            return pd.DataFrame()

        # Tüm seriler aynı uzunlukta olmalı; yfinance bazen None'lar koyar
        df = pd.DataFrame(
            {
                "Open": quote.get("open") or [None] * len(timestamps),
                "High": quote.get("high") or [None] * len(timestamps),
                "Low": quote.get("low") or [None] * len(timestamps),
                "Close": quote.get("close") or [None] * len(timestamps),
                "Volume": quote.get("volume") or [0] * len(timestamps),
            },
            index=pd.to_datetime(timestamps, unit="s", utc=True),
        )
        return df

    @staticmethod
    def _download_quote(ticker: str) -> dict[str, Any]:  # pragma: no cover - external IO
        """Yahoo Finance chart API'sini doğrudan çağırır (yfinance bypass).

        Sebep: yfinance.Ticker.fast_info.last_price dahili olarak history()
        çağırıyor → pandas DataFrame → pandas 3.0 + pyarrow + Windows
        kombinasyonunda native access violation (Windows fatal exception).
        Doğrudan JSON endpoint kullanmak hem güvenli hem hızlı.
        """
        import json
        import urllib.error
        import urllib.request

        url = (
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
            f"?range=1d&interval=1m&includePrePost=false"
        )
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            raise SourceError(f"yfinance JSON quote başarısız: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise SourceError(f"yfinance JSON parse hatası: {exc}") from exc

        result = (payload.get("chart") or {}).get("result") or []
        if not result:
            raise SourceError(f"yfinance: {ticker} için sonuç yok")

        meta = result[0].get("meta") or {}
        price = (
            meta.get("regularMarketPrice")
            or meta.get("previousClose")
            or meta.get("chartPreviousClose")
        )
        if price is None:
            raise SourceError(f"yfinance: {ticker} için fiyat alınamadı")

        volume = meta.get("regularMarketVolume") or 0
        ts_unix = meta.get("regularMarketTime")
        if ts_unix:
            ts = datetime.fromtimestamp(int(ts_unix), tz=timezone.utc)
        else:
            ts = datetime.now(tz=timezone.utc)

        return {"price": float(price), "volume": int(volume or 0), "timestamp": ts}

    # ---- Retry helper -------------------------------------------------------

    async def _with_retry(self, runner, *args):
        """``runner(*args)`` çağrısını semafor + exponential back-off ile dener."""
        last_exc: Exception | None = None
        async with self._semaphore:
            for attempt in range(self._MAX_RETRY):
                try:
                    return await runner(*args)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "yfinance deneme {}/{} başarısız: {} — {}s sonra tekrar",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        raise SourceUnavailableError(f"yfinance retry tükendi: {last_exc}")


__all__ = ["YFinanceSource"]
