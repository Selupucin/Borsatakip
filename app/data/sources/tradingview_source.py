"""TradingView veri kaynağı (OHLCV + teknik analiz özeti).

# ============================================================
# KAYNAK: tradingview
# YASAL:  TradingView Terms of Service KİŞİSEL KULLANIM için
#         scraping/private feed kullanımına SINIRLI izin verir;
#         ticari yeniden dağıtım, ücretli ürün haline getirme,
#         kullanıcıya gösterilen verinin TV markası olmadan
#         sunulması YASAK. Sembol başına makul sayıda istek
#         tutulmalıdır.
# RATE:   Konservatif — 1 req / 2 sn (en fazla 30 req/dk),
#         Semaphore(1) + min-interval.
# robots.txt: TradingView crawl'a izin verir ancak /chart, /symbols
#             gibi alanlarda erişim politikası sıkı; bu modül
#             yalnızca ``tvdatafeed`` üzerinden çalışırsa veri
#             gönderir (saf HTML scrape FALLBACK iskelet).
# ============================================================

Mimari:
- ``tvdatafeed`` lib varsa OHLCV ondan alınır; gerçek market data.
- Lib yoksa modül seviyesinde ``TV_AVAILABLE=False`` set edilir ve
  fiyat metodları ``SourceUnavailableError`` fırlatır.
- Teknik özet (``fetch_technical_summary``) için ``tvdatafeed`` yetmez —
  bu kısım için ``tradingview-ta`` veya saf Playwright/REST scrape
  gerekir. Bu Faz 2 implementasyonu ``tradingview_ta`` (mevcut public
  scanner endpoint) varsa kullanır; yoksa NEUTRAL iskelet döner.

Not: ``tvdatafeed`` interaktif login isteyebilir; production'da
saklı token (``TV_USERNAME`` / ``TV_PASSWORD`` env) gerekir. Bu modül
guest session ile başlar; başarısız olursa SourceUnavailableError.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
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


# ---------------------------------------------------------------------------
# Lazy/import guard'lar
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard
    from tvDatafeed import Interval, TvDatafeed  # type: ignore
    TV_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    Interval = None  # type: ignore
    TvDatafeed = None  # type: ignore
    TV_AVAILABLE = False
    _TV_IMPORT_ERROR: Exception | None = exc
else:
    _TV_IMPORT_ERROR = None


try:  # pragma: no cover - import guard
    # tradingview_ta == ufak public scanner client
    from tradingview_ta import TA_Handler  # type: ignore
    TA_AVAILABLE = True
except Exception:  # pragma: no cover
    TA_Handler = None  # type: ignore
    TA_AVAILABLE = False


_INTERVAL_MAP = {
    "1m": "in_1_minute",
    "5m": "in_5_minute",
    "15m": "in_15_minute",
    "30m": "in_30_minute",
    "1h": "in_1_hour",
    "1d": "in_daily",
    "1wk": "in_weekly",
    "1mo": "in_monthly",
}


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
class TVSummary:
    """TradingView teknik analiz özeti (Recommend.All paneli)."""

    recommendation: str  # STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL
    oscillators_summary: str  # aynı vocab
    ma_summary: str  # aynı vocab
    support_levels: tuple[Decimal, ...] = field(default_factory=tuple)
    resistance_levels: tuple[Decimal, ...] = field(default_factory=tuple)


_NEUTRAL_SUMMARY = TVSummary(
    recommendation="NEUTRAL",
    oscillators_summary="NEUTRAL",
    ma_summary="NEUTRAL",
)


# Recommendation normalize
def _normalize_rec(raw: str | None) -> str:
    if not raw:
        return "NEUTRAL"
    s = str(raw).strip().upper().replace(" ", "_")
    if s in ("STRONG_BUY", "BUY", "NEUTRAL", "SELL", "STRONG_SELL"):
        return s
    if s.startswith("STRONG") and "BUY" in s:
        return "STRONG_BUY"
    if s.startswith("STRONG") and "SELL" in s:
        return "STRONG_SELL"
    if "BUY" in s:
        return "BUY"
    if "SELL" in s:
        return "SELL"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class TradingViewSource(BaseSource):
    name = "tradingview"

    # 1 istek / 2sn = 30/dk
    _semaphore = asyncio.Semaphore(1)
    _MIN_INTERVAL_S = 2.0
    _MAX_RETRY = 2
    _BACKOFF_BASE = 2.0

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        self._tv_client = None  # lazy init

        if not TV_AVAILABLE:
            logger.warning(
                "tvdatafeed yok ({}). TradingView OHLCV/quote çağrıları "
                "SourceUnavailableError fırlatacak.",
                _TV_IMPORT_ERROR,
            )
        if not TA_AVAILABLE:
            logger.info(
                "tradingview_ta yok — teknik özet NEUTRAL fallback dönecek."
            )

    # ---- BaseSource: OHLCV + Quote -----------------------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        if not TV_AVAILABLE:
            raise SourceUnavailableError("tvdatafeed kurulu değil")

        tv_iv = self._map_interval(interval)
        symbol, exchange = self._split_symbol(ticker)

        # tvdatafeed n_bars bazlı çalışır — start/end aralığını yaklaşık bar
        # sayısına çevirmek için günlük varsayım yeterli (Faz 2 minimum).
        days = max(1, (end - start).days)
        # Intraday için günlük ~10 bar (1h), günlük için 1 bar/gün
        per_day = 1 if interval in ("1d", "1wk", "1mo") else 10
        n_bars = min(days * per_day, 5000)

        df = await self._call(self._download_ohlcv, symbol, exchange, tv_iv, n_bars)
        if df is None or len(df) == 0:
            raise SourceError(f"tradingview: {ticker} için veri yok")

        bars: list[OHLCVBar] = []
        for ts, row in df.iterrows():
            try:
                ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            except Exception:
                continue
            if ts_py < start or ts_py > end:
                continue
            try:
                bars.append(
                    OHLCVBar(
                        timestamp=ts_py,
                        open=Decimal(str(row["open"])),
                        high=Decimal(str(row["high"])),
                        low=Decimal(str(row["low"])),
                        close=Decimal(str(row["close"])),
                        volume=int(row.get("volume", 0) or 0),
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.debug("tradingview: satır atlandı ({}): {}", ticker, exc)
                continue
        bars.sort(key=lambda b: b.timestamp)
        return bars

    async def fetch_quote(self, ticker: str) -> Quote:
        if not TV_AVAILABLE:
            raise SourceUnavailableError("tvdatafeed kurulu değil")
        symbol, exchange = self._split_symbol(ticker)
        df = await self._call(self._download_ohlcv, symbol, exchange, self._map_interval("1d"), 1)
        if df is None or len(df) == 0:
            raise SourceError(f"tradingview: {ticker} için quote yok")
        last = df.iloc[-1]
        ts = df.index[-1]
        ts_py = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else datetime.utcnow()
        return Quote(
            ticker=ticker,
            price=Decimal(str(last["close"])),
            timestamp=ts_py,
            source=self.name,
            volume=int(last.get("volume", 0) or 0) or None,
        )

    # ---- Public: teknik özet ------------------------------------------------

    async def fetch_technical_summary(
        self, ticker: str, exchange: str = "BIST"
    ) -> TVSummary:
        """TradingView "Technicals" panelinin özet kararını döndür.

        - ``tradingview_ta`` kurulu değilse NEUTRAL özet döner (fail-soft).
        - Destek/direnç seviyeleri ``tradingview_ta`` çıkışında yoksa boş tuple.
        """
        if not TA_AVAILABLE:
            return _NEUTRAL_SUMMARY

        sym, exch = self._split_symbol(ticker, default_exchange=exchange)

        try:
            analysis = await self._call(self._fetch_ta, sym, exch)
        except SourceError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("tradingview: teknik özet hatası ({}): {}", ticker, exc)
            return _NEUTRAL_SUMMARY

        summary = getattr(analysis, "summary", None) or {}
        osc = getattr(analysis, "oscillators", None) or {}
        ma = getattr(analysis, "moving_averages", None) or {}

        # Pivot noktaları: tradingview_ta indicators dict'inde 'Pivot.M.Classic.S1' vs.
        indicators = getattr(analysis, "indicators", None) or {}
        supports = []
        resistances = []
        for key, val in indicators.items():
            if not isinstance(val, (int, float)):
                continue
            if "Pivot" in key and ".S" in key:
                supports.append(Decimal(str(val)))
            elif "Pivot" in key and ".R" in key:
                resistances.append(Decimal(str(val)))

        return TVSummary(
            recommendation=_normalize_rec(summary.get("RECOMMENDATION")),
            oscillators_summary=_normalize_rec(osc.get("RECOMMENDATION")),
            ma_summary=_normalize_rec(ma.get("RECOMMENDATION")),
            support_levels=tuple(sorted(supports)),
            resistance_levels=tuple(sorted(resistances)),
        )

    # ---- Internal ----------------------------------------------------------

    @staticmethod
    def _split_symbol(ticker: str, default_exchange: str = "BIST") -> tuple[str, str]:
        """``BIST:THYAO`` -> (``THYAO``, ``BIST``); ``THYAO.IS`` -> (``THYAO``, ``BIST``);
        sade ``AAPL`` -> (``AAPL``, default)."""
        t = ticker.strip().upper()
        if ":" in t:
            ex, sym = t.split(":", 1)
            return sym, ex
        if t.endswith(".IS"):
            return t[:-3], "BIST"
        return t, default_exchange

    @staticmethod
    def _map_interval(interval: str):
        if not TV_AVAILABLE:
            raise SourceUnavailableError("tvdatafeed kurulu değil")
        key = _INTERVAL_MAP.get(interval)
        if key is None:
            raise SourceError(f"tradingview: {interval} desteklenmiyor")
        return getattr(Interval, key)

    def _ensure_tv(self):  # pragma: no cover - external IO
        if self._tv_client is None:
            self._tv_client = TvDatafeed()  # guest session
        return self._tv_client

    def _download_ohlcv(self, symbol, exchange, tv_iv, n_bars):  # pragma: no cover - external IO
        tv = self._ensure_tv()
        return tv.get_hist(symbol=symbol, exchange=exchange, interval=tv_iv, n_bars=n_bars)

    @staticmethod
    def _fetch_ta(symbol, exchange):  # pragma: no cover - external IO
        handler = TA_Handler(
            symbol=symbol,
            exchange=exchange,
            screener="turkey" if exchange.upper() == "BIST" else "america",
            interval="1d",
        )
        # tradingview_ta saf-Python requests bazlı; UA argümanı yok ama
        # underlying requests headers customize edilmez — KISITLAMA: TV ToS uyumlu
        # kullanım için aynı sürede aşırı istek atılmaz.
        return handler.get_analysis()

    async def _call(self, sync_fn, *args):
        """Senkron lib çağrısını semafor + min-interval + retry ile sar."""
        last_exc: Exception | None = None
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)

            for attempt in range(self._MAX_RETRY):
                try:
                    result = await asyncio.to_thread(sync_fn, *args)
                    self._last_call_ts = asyncio.get_event_loop().time()
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "tradingview deneme {}/{} başarısız: {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    self._last_call_ts = asyncio.get_event_loop().time()
        raise SourceUnavailableError(f"tradingview retry tükendi: {last_exc}")


__all__ = ["TradingViewSource", "TVSummary", "TV_AVAILABLE"]
