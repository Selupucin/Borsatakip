"""Stooq veri kaynağı.

# ============================================================
# KAYNAK: stooq
# AMACI:  Stooq.com OHLCV geçmişi (CSV) — yfinance için sağlam yedek
# KAPSAM: NYSE / NASDAQ + BIST. BIST ticker formatı küçük harf + ``.tr``
#         son eki (örn. ``thyao.tr``). ABD için ``aapl.us``.
# LİMİT:  Resmi rate-limit dokümante değil; pratikte 1 istek/sn üzerinde
#         403 dönebilir. Konservatif: 1 istek/sn semaforla sınırlandık.
#         Anlık quote API'si yok — son OHLCV barının kapanışı kullanılır
#         (15+ dk gecikmeli).
# YEDEK:  Stooq kırıldığında ABD için yfinance, BIST için isyatirim devralır.
# ============================================================

``pandas_datareader.data.DataReader(..., 'stooq')`` senkron çalışır;
``asyncio.to_thread`` ile sarmalanır.

Önemli not: Stooq quote'ları **end-of-day** veridir — gerçek zamanlı
değildir. Karşılaştırma motorunda quote olarak kullanılırken bu durum
``Quote.timestamp`` ile teşhis edilebilir.
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
    from pandas_datareader import data as pdr_data  # type: ignore
except Exception as exc:  # pragma: no cover
    pdr_data = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


def _to_stooq_ticker(ticker: str) -> str:
    """Standart ticker'ı Stooq formatına çevir.

    Örnekler:
        ``AAPL`` -> ``aapl.us``
        ``THYAO.IS`` -> ``thyao.tr``
        ``thyao.tr`` -> ``thyao.tr`` (zaten Stooq formatında)
    """
    t = ticker.strip().lower()
    if "." in t:
        # ``thyao.is`` -> ``thyao.tr`` (Stooq BIST için ``.tr`` ister)
        base, suffix = t.rsplit(".", 1)
        if suffix == "is":
            return f"{base}.tr"
        return t
    # Suffix yoksa ABD varsay
    return f"{t}.us"


class StooqSource(BaseSource):
    name = "stooq"

    _semaphore = asyncio.Semaphore(1)
    _MAX_RETRY = 3
    _BACKOFF_BASE = 1.0

    def __init__(self) -> None:
        super().__init__()
        if pdr_data is None:
            logger.warning(
                "pandas_datareader import edilemedi: {}. StooqSource çağrıları "
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
        if pdr_data is None:
            raise SourceUnavailableError("pandas_datareader kurulu değil")

        if interval not in ("1d", "1wk", "1mo"):
            # Stooq sadece daily/weekly/monthly destekler
            raise SourceError(f"stooq: {interval} interval'i desteklenmiyor")

        sym = _to_stooq_ticker(ticker)

        df = await self._with_retry(
            asyncio.to_thread, self._download_ohlcv, sym, start, end
        )

        if df is None or df.empty:
            raise SourceError(f"stooq: {ticker} ({sym}) için veri yok")

        # Stooq DataFrame'i ters kronolojik geliyor — kronolojik sırala
        df = df.sort_index()

        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            try:
                bars.append(
                    OHLCVBar(
                        timestamp=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                        open=Decimal(str(row["Open"])),
                        high=Decimal(str(row["High"])),
                        low=Decimal(str(row["Low"])),
                        close=Decimal(str(row["Close"])),
                        volume=int(row.get("Volume", 0) or 0),
                    )
                )
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug("stooq: {} satır atlandı: {}", ticker, exc)
                continue

        return bars

    async def fetch_quote(self, ticker: str) -> Quote:
        """Stooq'ta gerçek-zamanlı quote yok — son OHLCV kapanışı döndürülür.

        ``Quote.timestamp`` son bar tarihidir; karşılaştırma motoru bu
        gecikmenin farkındadır (eski kapanış ile canlı fiyatı eşitlemez).
        """
        end = datetime.utcnow()
        start = end - timedelta(days=7)  # son hafta yeterli
        bars = await self.fetch_ohlcv(ticker, start, end, "1d")
        if not bars:
            raise SourceError(f"stooq: {ticker} için son fiyat alınamadı")
        last = bars[-1]
        return Quote(
            ticker=ticker,
            price=last.close,
            timestamp=last.timestamp,
            source=self.name,
            volume=last.volume,
        )

    # ---- Internal -----------------------------------------------------------

    @staticmethod
    def _download_ohlcv(symbol: str, start: datetime, end: datetime):  # pragma: no cover - external IO
        return pdr_data.DataReader(symbol, "stooq", start=start, end=end)

    async def _with_retry(self, runner, *args):
        last_exc: Exception | None = None
        async with self._semaphore:
            for attempt in range(self._MAX_RETRY):
                try:
                    return await runner(*args)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "stooq deneme {}/{} başarısız: {} — {}s sonra tekrar",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
        raise SourceUnavailableError(f"stooq retry tükendi: {last_exc}")


__all__ = ["StooqSource"]
