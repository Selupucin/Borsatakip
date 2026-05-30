"""TCMB veri kaynağı.

# ============================================================
# KAYNAK: tcmb
# AMACI:  TCMB EVDS üzerinden USD/TRY, EUR/TRY vb. resmi döviz kurları
# KAPSAM: SADECE DÖVİZ KURU. HİSSE FİYATI İÇİN KULLANILMAZ.
# LİMİT:  TCMB EVDS rate-limit dokümante etmez; ancak iyi niyetli kullanım
#         için saniyede 1 isteği aşmıyoruz. Veri günlük (T+1) gecikmeli.
# YEDEK:  TCMB erişilemezse fx için yfinance (``USDTRY=X``) devralabilir,
#         ama TCMB resmi kaynak olduğu için Faz 1'de tek yetki TCMB'dedir.
# ============================================================

KURAL — DİKKAT:
    TCMB **hisse fiyat kaynağı değildir**. ``fetch_ohlcv`` ve ``fetch_quote``
    yalnızca FX çifti (``USDTRY``, ``EURTRY``, ``GBPTRY``...) için anlam
    taşır. Hisse ticker'ı verilirse ``SourceError`` fırlatılır.

EVDS Endpoint:
    https://evds2.tcmb.gov.tr/service/evds/series=<SERIES>&...
    Resmi rehber: https://evds2.tcmb.gov.tr/help/videos/EVDS_Web_Service_Usage_Guide.pdf
    Bu modül anahtarsız (public) seri ID'lerini kullanır
    (örn. TP.DK.USD.A.YTL — USD/TRY günlük resmi).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from loguru import logger

from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
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


_BASE_URL = "https://evds2.tcmb.gov.tr/service/evds/"

# Pair -> EVDS seri kodu (alış kuru). Genişletmek için ekle.
_PAIR_SERIES = {
    "USDTRY": "TP.DK.USD.A.YTL",
    "EURTRY": "TP.DK.EUR.A.YTL",
    "GBPTRY": "TP.DK.GBP.A.YTL",
    "CHFTRY": "TP.DK.CHF.A.YTL",
    "JPYTRY": "TP.DK.JPY.A.YTL",
}


class TCMBSource(BaseSource):
    name = "tcmb"

    _semaphore = asyncio.Semaphore(1)
    _MIN_INTERVAL_S = 1.0
    _MAX_RETRY = 3
    _BACKOFF_BASE = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        if httpx is None:
            logger.warning(
                "httpx import edilemedi: {}. TCMBSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )

    # ---- Public API ---------------------------------------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        """FX çifti için günlük kuru OHLC olarak döndür.

        TCMB intraday vermez — OHLC tek değer (close = open = high = low).
        Hisse ticker'ı verilirse SourceError fırlatır.
        """
        series = self._resolve_series(ticker)
        if interval != "1d":
            raise SourceError(f"tcmb: {interval} desteklenmiyor (sadece 1d)")

        rows = await self._fetch_series(series, start, end)

        bars: list[OHLCVBar] = []
        for row in rows:
            try:
                ts = datetime.strptime(row["Tarih"], "%d-%m-%Y")
            except (KeyError, ValueError):
                continue
            val_raw = row.get(series.replace(".", "_")) or row.get(series)
            if val_raw in (None, "", "null"):
                continue
            try:
                price = Decimal(str(val_raw))
            except Exception:
                continue
            bars.append(
                OHLCVBar(
                    timestamp=ts,
                    open=price,
                    high=price,
                    low=price,
                    close=price,
                    volume=0,
                )
            )

        bars.sort(key=lambda b: b.timestamp)
        return bars

    async def fetch_quote(self, ticker: str) -> Quote:
        """FX çifti için en güncel resmi kuru döndür."""
        end = datetime.utcnow()
        start = end - timedelta(days=10)
        bars = await self.fetch_ohlcv(ticker, start, end, "1d")
        if not bars:
            raise SourceError(f"tcmb: {ticker} için son kur alınamadı")
        last = bars[-1]
        return Quote(
            ticker=ticker,
            price=last.close,
            timestamp=last.timestamp,
            source=self.name,
        )

    def _health_check_ticker(self) -> str:
        return "USDTRY"

    # ---- Internal -----------------------------------------------------------

    @staticmethod
    def _resolve_series(ticker: str) -> str:
        key = ticker.strip().upper().replace("/", "")
        if key not in _PAIR_SERIES:
            raise SourceError(
                f"tcmb: {ticker} desteklenen FX çifti değil. "
                f"TCMB sadece döviz kuru sağlar — hisse fiyatı için DEĞİL. "
                f"Desteklenen: {list(_PAIR_SERIES)}"
            )
        return _PAIR_SERIES[key]

    async def _fetch_series(
        self, series: str, start: datetime, end: datetime
    ) -> list[dict]:
        if httpx is None:
            raise SourceUnavailableError("httpx kurulu değil")

        url = (
            f"{_BASE_URL}series={series}"
            f"&startDate={start.strftime('%d-%m-%Y')}"
            f"&endDate={end.strftime('%d-%m-%Y')}"
            f"&type=json"
        )

        last_exc: Exception | None = None
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)

            for attempt in range(self._MAX_RETRY):
                try:
                    async with httpx.AsyncClient(timeout=30.0) as client:
                        resp = await client.get(url)
                    self._last_call_ts = asyncio.get_event_loop().time()
                    resp.raise_for_status()
                    data = resp.json()
                    return list(data.get("items") or [])
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "tcmb deneme {}/{} başarısız: {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    self._last_call_ts = asyncio.get_event_loop().time()
        raise SourceUnavailableError(f"tcmb retry tükendi: {last_exc}")


__all__ = ["TCMBSource"]
