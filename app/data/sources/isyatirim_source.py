"""İş Yatırım veri kaynağı.

# ============================================================
# KAYNAK: isyatirim
# AMACI:  BIST hisseleri için BİRİNCİL fiyat ve finansal tablo kaynağı
# KAPSAM: Sadece BIST. Ticker formatı sade hisse kodu (örn. ``THYAO``,
#         ``GARAN``). Yatırımcının ``.IS`` suffix'i otomatik silinir.
# LİMİT:  Resmi rate-limit dokümante değil; kişisel kullanım için
#         tasarlandığı için **agresif istek atmamak hayati**. Konservatif:
#         saniyede en fazla 2 istek (Semaphore(1) + 0.5s minimum bekleme).
# YEDEK:  isyatirim kırıldığında BIST için stooq devralır (gecikme artar).
#         Quote için canlı veriye en yakın alternatif: KAP scraping
#         (Faz 2'de devreye alınır).
# ============================================================

``isyatirimhisse`` kütüphanesi senkron + pandas DataFrame döndürür;
``asyncio.to_thread`` ile sarmalanır.

Önemli not: İş Yatırım resmi bir public API sunmaz; ``isyatirimhisse`` lib
JSON endpoint'lerini scrape eder. Endpoint değişimine karşı 2 deneme +
graceful failure stratejisi uygulanır.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
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
    from isyatirimhisse import StockData  # type: ignore
except Exception as exc:  # pragma: no cover
    StockData = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


def _normalize_bist_ticker(ticker: str) -> str:
    """``THYAO.IS`` -> ``THYAO`` (İş Yatırım sade kod ister)."""
    t = ticker.strip().upper()
    if t.endswith(".IS"):
        return t[:-3]
    if t.endswith(".TR"):
        return t[:-3]
    return t


class IsYatirimSource(BaseSource):
    name = "isyatirim"

    # Aşırı istek atmamak için: en fazla 1 paralel, her istek arası min 0.5s
    _semaphore = asyncio.Semaphore(1)
    _MIN_INTERVAL_S = 0.5  # -> en fazla 2 istek/sn

    _MAX_RETRY = 2
    _BACKOFF_BASE = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        if StockData is None:
            logger.warning(
                "isyatirimhisse import edilemedi: {}. IsYatirimSource çağrıları "
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
        if StockData is None:
            raise SourceUnavailableError("isyatirimhisse kurulu değil")
        if interval != "1d":
            # isyatirimhisse sadece günlük destekliyor (resmi)
            raise SourceError(f"isyatirim: {interval} desteklenmiyor (sadece 1d)")

        sym = _normalize_bist_ticker(ticker)

        df = await self._with_retry(
            asyncio.to_thread, self._download_ohlcv, sym, start, end
        )

        if df is None or len(df) == 0:
            raise SourceError(f"isyatirim: {ticker} ({sym}) için veri yok")

        # isyatirimhisse sütun adlarını TR ile döner — esnek eşleştirme
        col_map = self._resolve_columns(df.columns)

        bars: list[OHLCVBar] = []
        for _, row in df.iterrows():
            try:
                ts_raw = row[col_map["date"]]
                ts = self._parse_ts(ts_raw)
                bars.append(
                    OHLCVBar(
                        timestamp=ts,
                        open=Decimal(str(row[col_map["open"]])),
                        high=Decimal(str(row[col_map["high"]])),
                        low=Decimal(str(row[col_map["low"]])),
                        close=Decimal(str(row[col_map["close"]])),
                        volume=int(row.get(col_map["volume"], 0) or 0),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("isyatirim: {} satır atlandı: {}", ticker, exc)
                continue

        bars.sort(key=lambda b: b.timestamp)
        return bars

    async def fetch_quote(self, ticker: str) -> Quote:
        """Son kapanış fiyatını döndür (isyatirim canlı tick vermez)."""
        from datetime import timedelta as _td

        end = datetime.utcnow()
        start = end - _td(days=10)
        bars = await self.fetch_ohlcv(ticker, start, end, "1d")
        if not bars:
            raise SourceError(f"isyatirim: {ticker} için son fiyat alınamadı")
        last = bars[-1]
        return Quote(
            ticker=ticker,
            price=last.close,
            timestamp=last.timestamp,
            source=self.name,
            volume=last.volume,
        )

    # ---- Health check override (BIST ticker) --------------------------------

    def _health_check_ticker(self) -> str:
        return "THYAO"

    # ---- Internal -----------------------------------------------------------

    @staticmethod
    def _resolve_columns(cols) -> dict[str, str]:
        """isyatirimhisse sürümleri arasında sütun adı değişebilir; eşle."""
        names = {c.lower(): c for c in cols}
        def pick(*candidates: str) -> str:
            for cand in candidates:
                if cand in names:
                    return names[cand]
            raise SourceError(f"isyatirim: sütun bulunamadı, aday: {candidates}")
        return {
            "date": pick("tarih", "date", "datetime"),
            "open": pick("acilis", "açilis", "açılış", "open"),
            "high": pick("yuksek", "yüksek", "high"),
            "low": pick("dusuk", "düşük", "low"),
            "close": pick("kapanis", "kapanış", "close", "fiyat"),
            "volume": pick("hacim", "miktar", "volume") if any(
                k in names for k in ("hacim", "miktar", "volume")
            ) else next(iter(names.values())),
        }

    @staticmethod
    def _parse_ts(raw) -> datetime:
        if isinstance(raw, datetime):
            return raw
        if hasattr(raw, "to_pydatetime"):
            return raw.to_pydatetime()
        return datetime.fromisoformat(str(raw))

    @staticmethod
    def _download_ohlcv(symbol: str, start: datetime, end: datetime):  # pragma: no cover - external IO
        sd = StockData()
        # API: get_data(symbols=[...], start_date='dd-mm-YYYY', end_date='dd-mm-YYYY')
        return sd.get_data(
            symbols=[symbol],
            start_date=start.strftime("%d-%m-%Y"),
            end_date=end.strftime("%d-%m-%Y"),
        )

    async def _with_retry(self, runner, *args):
        last_exc: Exception | None = None
        async with self._semaphore:
            # Hız sınırı: önceki istekten bu yana en az _MIN_INTERVAL_S geçsin
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)

            for attempt in range(self._MAX_RETRY):
                try:
                    result = await runner(*args)
                    self._last_call_ts = asyncio.get_event_loop().time()
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "isyatirim deneme {}/{} başarısız: {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
            self._last_call_ts = asyncio.get_event_loop().time()
        raise SourceUnavailableError(f"isyatirim retry tükendi: {last_exc}")


__all__ = ["IsYatirimSource"]
