"""SchedulerService — uygulamanın arka plan döngülerini yönetir.

Görev döngüleri:
- **Fiyat tick** (60 sn): Aktif tarama evrenindeki tüm enstrümanlar için
  yfinance + stooq + (varsa) isyatirim'dan paralel quote çek, comparator'la
  doğrulanmış fiyatı price_history'e yaz.
- **İndikatör tick** (5 dk): Her enstrüman için son N günlük OHLCV'yi yfinance'ten
  çek, TechnicalAnalyzer ile RSI/MACD/EMA hesapla, technical_signals'a yaz.
- **Öneri tick** (10 dk): Her enstrüman ve her vade için ShortTermRecommender
  (orta/uzun ileride) çalıştır, recommendations'a yaz, yeterli güvendeyse
  bot_picks'e ekle.
- **Alarm tick** (30 sn): Aktif alarmları AlertEngine ile değerlendir.
- **Bot picks bakım** (5 dk): Açık pick'lerden target'a ulaşan veya süresi
  dolanları kapat.

Tarama evreni:
- Tüm watchlist'lerdeki hisseler.
- Watchlist yoksa varsayılan: BIST top-10 + ABD top-10.
- Endeks instrument'ları her zaman dahil (XU100, ^GSPC) — benchmark için.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Iterable

import pandas as pd
from loguru import logger
from PySide6.QtCore import QObject, QThreadPool, QTimer, Signal
from sqlalchemy import select

from app.analysis.recommender import (
    LongTermInput,
    LongTermRecommender,
    MidTermInput,
    MidTermRecommender,
    RecommendationInput,
    ShortTermRecommender,
)
from app.analysis.technical import TechnicalAnalyzer
from app.data.base_source import Quote
from app.data.collector import DataCollector
from app.data.comparator import PriceComparator
from app.data.sources.rss_source import RSSSource
from app.data.sources.yfinance_source import YFinanceSource

# Stooq şu an pandas-datareader / pandas 3.0 uyumsuzluğu nedeniyle import edilmiyor;
# yfinance tek başına yeterli. İleride pandas-datareader patch'lenirse aktif edilecek.
from app.db.models import Instrument, PriceHistory, Recommendation, WatchlistItem
from app.db.session import _get_async_session_local

# ---------------------------------------------------------------------------
# Sabit aralıklar (ms)
# ---------------------------------------------------------------------------

INTERVAL_PRICE_MS = 60_000          # 1 dk
INTERVAL_INDICATOR_MS = 5 * 60_000  # 5 dk
INTERVAL_RECOMMEND_MS = 30 * 60_000  # 30 dk (spam azaltıldı — kullanıcı şikayeti)
INTERVAL_ALERT_MS = 30_000          # 30 sn
INTERVAL_BOT_PICKS_MS = 5 * 60_000  # 5 dk
INTERVAL_NEWS_MS = 15 * 60_000      # 15 dk — RSS feed taraması
INTERVAL_FX_MS = 5 * 60_000         # 5 dk — döviz/altın kurları
INTERVAL_UPDATE_CHECK_MS = 6 * 60 * 60_000  # 6 saat — GitHub Releases sürüm kontrolü

# Aynı (instrument, timeframe, action) için yeni promote'tan önce geçmesi
# gereken minimum süre — kullanıcı "aynı bildirimler" şikayet etti.
PROMOTE_DEDUP_HOURS = 6

# Default tarama evreni — watchlist boşsa
DEFAULT_BIST_UNIVERSE = ["THYAO", "AKBNK", "GARAN", "ASELS", "EREGL", "KCHOL", "BIMAS", "TUPRS", "SISE", "TCELL"]
DEFAULT_US_UNIVERSE = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "META", "JPM", "V", "WMT"]

# Sentinel ticker'lar: yfinance sembolüne çevirme tablosu
YFINANCE_TICKER_MAP = {
    "XU100": "XU100.IS",   # BIST 100
    "^GSPC": "^GSPC",       # S&P 500
}

# Döviz + emtia çiftleri — fx_rates'e yazılır
FX_PAIRS: tuple[tuple[str, str], ...] = (
    ("USDTRY", "TRY=X"),
    ("EURTRY", "EURTRY=X"),
    ("XAUUSD", "GC=F"),     # Altın futures USD/oz
)


def _to_yf(ticker: str, exchange: str | None = None) -> str:
    """Instrument ticker'ını yfinance sembolüne çevir."""
    if ticker in YFINANCE_TICKER_MAP:
        return YFINANCE_TICKER_MAP[ticker]
    if exchange == "BIST":
        return f"{ticker}.IS"
    return ticker


class SchedulerService(QObject):
    """Periyodik arka plan task'larının orkestratörü."""

    #: MainWindow update sinyali — yeni sürüm bulununca emit edilir
    update_available = Signal(object)  # UpdateInfo | None

    def __init__(self, bridge, main_window=None) -> None:
        super().__init__()
        self._bridge = bridge
        self._main_window = main_window
        self._session_factory = _get_async_session_local()

        # Kaynaklar — yfinance birincil
        self._sources = [
            YFinanceSource(),
        ]
        self._collector = DataCollector(self._sources, self._session_factory)
        self._comparator = PriceComparator(self._session_factory)

        try:
            self._technical = TechnicalAnalyzer()
        except Exception as exc:
            logger.warning("TechnicalAnalyzer init başarısız: {} — indikatör tick devre dışı", exc)
            self._technical = None

        self._recommender = ShortTermRecommender()
        self._recommender_mid = MidTermRecommender()
        self._recommender_long = LongTermRecommender()

        # Haber kaynağı (RSS) — opsiyonel; feedparser yoksa fail-soft
        try:
            self._rss = RSSSource()
        except Exception as exc:
            logger.warning("RSSSource init başarısız: {} — haber tick devre dışı", exc)
            self._rss = None

        # Timer'lar — start() ile başlar
        self._timers: list[QTimer] = []
        self._is_running = False
        # Re-entrancy guard: tick çakışmasın
        self._busy: dict[str, bool] = {}

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True

        self._timers = [
            self._make_timer(INTERVAL_PRICE_MS, self._tick_prices, "price"),
            self._make_timer(INTERVAL_INDICATOR_MS, self._tick_indicators, "indicators"),
            self._make_timer(INTERVAL_RECOMMEND_MS, self._tick_recommend, "recommend"),
            self._make_timer(INTERVAL_ALERT_MS, self._tick_alerts, "alerts"),
            self._make_timer(INTERVAL_BOT_PICKS_MS, self._tick_bot_picks_maintenance, "bot_picks_maint"),
            self._make_timer(INTERVAL_NEWS_MS, self._tick_news, "news"),
            self._make_timer(INTERVAL_UPDATE_CHECK_MS, self._tick_update_check, "update"),
        ]
        for t in self._timers:
            t.start()

        logger.info(
            "SchedulerService başladı — price={}s, indicator={}s, recommend={}s, news={}s",
            INTERVAL_PRICE_MS // 1000,
            INTERVAL_INDICATOR_MS // 1000,
            INTERVAL_RECOMMEND_MS // 1000,
            INTERVAL_NEWS_MS // 1000,
        )

        # İlk tick'ler hemen — kullanıcı uygulamayı açar açmaz veri görsün
        QTimer.singleShot(500, self._tick_prices)
        QTimer.singleShot(2000, self._tick_indicators)
        QTimer.singleShot(4000, self._tick_recommend)
        QTimer.singleShot(6000, self._tick_news)
        # Update check 30 sn sonra — açılış logunu boğmasın
        QTimer.singleShot(30_000, self._tick_update_check)

    def stop(self) -> None:
        for t in self._timers:
            t.stop()
        self._timers.clear()
        self._is_running = False
        logger.info("SchedulerService durdu")

    def _make_timer(self, interval_ms: int, slot, name: str) -> QTimer:
        t = QTimer(self)
        t.setInterval(interval_ms)
        t.timeout.connect(slot)
        t.setObjectName(name)
        return t

    # -----------------------------------------------------------------------
    # Tick slot'ları — QTimer'dan çağrılır, async coro'yu QThreadPool'a iletir
    # -----------------------------------------------------------------------

    def _tick_prices(self) -> None:
        if self._busy.get("price"):
            return
        self._busy["price"] = True
        self._bridge.run_async(
            self._fetch_active_prices,
            on_success=lambda r: self._on_tick_done("price", r),
            on_error=lambda e: self._on_tick_error("price", e),
        )

    def _tick_indicators(self) -> None:
        if self._technical is None or self._busy.get("indicators"):
            return
        self._busy["indicators"] = True
        self._bridge.run_async(
            self._compute_indicators_for_universe,
            on_success=lambda r: self._on_tick_done("indicators", r),
            on_error=lambda e: self._on_tick_error("indicators", e),
        )

    def _tick_recommend(self) -> None:
        if self._busy.get("recommend"):
            return
        self._busy["recommend"] = True
        self._bridge.run_async(
            self._generate_recommendations,
            on_success=lambda r: self._on_tick_done("recommend", r),
            on_error=lambda e: self._on_tick_error("recommend", e),
        )

    def _tick_alerts(self) -> None:
        # Şimdilik basit log; AlertEngine entegrasyonu için ticker bazlı current_price gerekir
        pass

    def _tick_bot_picks_maintenance(self) -> None:
        if self._busy.get("bot_picks_maint"):
            return
        self._busy["bot_picks_maint"] = True
        self._bridge.run_async(
            self._maintain_open_bot_picks,
            on_success=lambda r: self._on_tick_done("bot_picks_maint", r),
            on_error=lambda e: self._on_tick_error("bot_picks_maint", e),
        )

    def _tick_news(self) -> None:
        if self._rss is None or self._busy.get("news"):
            return
        self._busy["news"] = True
        self._bridge.run_async(
            self._fetch_news_from_rss,
            on_success=lambda r: self._on_tick_done("news", r),
            on_error=lambda e: self._on_tick_error("news", e),
        )

    def _tick_update_check(self) -> None:
        """GitHub Releases üzerinden yeni sürüm kontrolü (her 6 saatte bir)."""
        if self._busy.get("update"):
            return
        self._busy["update"] = True
        from app.services.updater import UpdateChecker

        self._bridge.run_async(
            lambda: UpdateChecker().check_for_updates(),
            on_success=self._on_update_check_done,
            on_error=lambda e: self._on_tick_error("update", e),
        )

    def _on_update_check_done(self, info) -> None:
        self._busy["update"] = False
        if info is None:
            return
        try:
            if info.is_newer:
                logger.info(
                    "Yeni sürüm bulundu: {} (mevcut {})",
                    info.latest_version, info.current_version,
                )
                self.update_available.emit(info)
                if self._main_window is not None:
                    try:
                        self._main_window.set_update_available(info.latest_version)
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("set_update_available hata: {}", exc)
            else:
                logger.debug("Güncel — en son: {}", info.latest_version)
        except Exception as exc:  # noqa: BLE001
            logger.debug("update check sonuç işleme hata: {}", exc)

    def _on_tick_done(self, name: str, result) -> None:
        self._busy[name] = False
        if result:
            logger.debug("Scheduler tick [{}] tamam: {}", name, result)

    def _on_tick_error(self, name: str, exc) -> None:
        self._busy[name] = False
        logger.error("Scheduler tick [{}] hata: {}", name, exc)

    # -----------------------------------------------------------------------
    # Tarama evrenini belirle
    # -----------------------------------------------------------------------

    async def _get_universe(self) -> list[Instrument]:
        """Tüm watchlist hisseleri + endeksler. Boşsa default top-10 BIST + ABD."""
        async with self._session_factory() as session:
            # Watchlist'teki instrument_id'leri çek
            wl_ids = (
                await session.execute(
                    select(WatchlistItem.instrument_id).distinct()
                )
            ).scalars().all()

            if wl_ids:
                instruments = (
                    await session.execute(
                        select(Instrument).where(Instrument.id.in_(wl_ids))
                    )
                ).scalars().all()
            else:
                # Default evren
                tickers = DEFAULT_BIST_UNIVERSE + DEFAULT_US_UNIVERSE
                instruments = (
                    await session.execute(
                        select(Instrument).where(Instrument.ticker.in_(tickers))
                    )
                ).scalars().all()

            # Endeksler her zaman dahil
            indices = (
                await session.execute(
                    select(Instrument).where(Instrument.sector == "Endeks")
                )
            ).scalars().all()

            seen = {i.id for i in instruments}
            for idx in indices:
                if idx.id not in seen:
                    instruments.append(idx)
                    seen.add(idx.id)

            return list(instruments)

    # -----------------------------------------------------------------------
    # Coro'lar — QThreadPool'da asyncio.run() ile çalışır
    # -----------------------------------------------------------------------

    async def _fetch_active_prices(self) -> str:
        """Tüm aktif evrendeki hisseler için paralel quote çekimi + DB yazımı."""
        instruments = await self._get_universe()
        if not instruments:
            return "evren boş"

        total_quotes = 0
        for inst in instruments:
            yf_ticker = _to_yf(inst.ticker, inst.exchange)
            try:
                quotes = await self._collector.fetch_all_quotes(yf_ticker)
            except Exception as exc:
                logger.warning("fetch_all_quotes hata [{}]: {}", inst.ticker, exc)
                continue

            # Quote.source'u kaynak adıyla doldur (yfinance/stooq source zaten yapıyor)
            valid_quotes = [q for q in quotes if q is not None and getattr(q, "price", None) is not None]
            if not valid_quotes:
                continue

            try:
                await self._collector.save_quotes(valid_quotes, instrument_id=inst.id)
                total_quotes += len(valid_quotes)
            except Exception as exc:
                logger.warning("save_quotes hata [{}]: {}", inst.ticker, exc)

        # Döviz + altın kurlarını çek
        for pair, yf_sym in FX_PAIRS:
            await self._fetch_fx_rate(pair, yf_sym)

        return f"{len(instruments)} hisse, {total_quotes} quote, {len(FX_PAIRS)} fx kaydedildi"

    async def _fetch_fx_rate(self, pair: str, yf_symbol: str) -> None:
        """yfinance'tan bir döviz çifti çek, fx_rates tablosuna idempotent yaz."""
        try:
            quote = await self._sources[0].fetch_quote(yf_symbol)
        except Exception as exc:
            logger.debug("fx fetch_quote hata [{}]: {}", pair, exc)
            return
        if quote is None or quote.price is None:
            return

        from app.db.models import FxRate

        async with self._session_factory() as session:
            try:
                # Idempotent: aynı (pair, timestamp) varsa atla
                exists = (
                    await session.execute(
                        select(FxRate.id)
                        .where(FxRate.pair == pair, FxRate.timestamp == quote.timestamp)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if exists is not None:
                    return
                row = FxRate(
                    pair=pair,
                    rate=float(quote.price),
                    timestamp=quote.timestamp,
                    source="yfinance",
                )
                session.add(row)
                await session.commit()
                logger.debug("FX kaydedildi: {}={}", pair, quote.price)
            except Exception as exc:
                logger.warning("fx_rate save hata [{}]: {}", pair, exc)
                await session.rollback()

    async def _compute_indicators_for_universe(self) -> str:
        """Her enstrüman için son 200 günlük OHLCV → indikatör → technical_signals."""
        if self._technical is None:
            return "TA backend yok"

        instruments = await self._get_universe()
        if not instruments:
            return "evren boş"

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=200)
        computed = 0

        for inst in instruments:
            # Endeks hesabını atla (target ATR/RSI uygulanmaz)
            if inst.sector == "Endeks":
                continue
            yf_ticker = _to_yf(inst.ticker, inst.exchange)
            try:
                # yfinance source senkron'a sarmal → asyncio.to_thread içeride
                bars = await self._sources[0].fetch_ohlcv(yf_ticker, start, end, interval="1d")
            except Exception as exc:
                logger.warning("fetch_ohlcv hata [{}]: {}", inst.ticker, exc)
                continue

            if not bars or len(bars) < 30:
                continue

            df = pd.DataFrame(
                [
                    {
                        "timestamp": b.timestamp,
                        "open": float(b.open),
                        "high": float(b.high),
                        "low": float(b.low),
                        "close": float(b.close),
                        "verified_close": float(b.close),
                        "volume": int(b.volume),
                    }
                    for b in bars
                ]
            )

            try:
                indicators = self._technical.compute_all(df)
                snapshot = self._technical.latest_snapshot(df)
            except Exception as exc:
                logger.warning("compute_all hata [{}]: {}", inst.ticker, exc)
                continue

            async with self._session_factory() as session:
                try:
                    self._technical.save_signals(session, instrument_id=inst.id, snapshot=snapshot)
                    await session.commit()
                    computed += 1
                except Exception as exc:
                    logger.warning("save_signals hata [{}]: {}", inst.ticker, exc)
                    await session.rollback()

        return f"{computed} hisse için indikatör hesaplandı"

    async def _generate_recommendations(self) -> str:
        """Her enstrüman için 3 vade öneri üret.

        Promote kuralları (kullanıcı geri bildirimi):
        - **BUY:** her zaman bot_picks'e ekle (kullanıcı manuel alıcı, fırsat görsün)
        - **SELL:** SADECE açık pozisyon varsa bot_picks'e ekle (elinde hisse
          yoksa "sat" anlamsız)
        - **HOLD:** bot_picks'e KOYULMAZ (kullanıcı şikayet etti — gürültü)
        - **Aynı (instrument, timeframe, action) son 6 saatte zaten varsa:** atla
        """
        instruments = await self._get_universe()
        if not instruments:
            return "evren boş"

        from app.db.models import OpenPosition, TechnicalSignal

        # Açık pozisyon olan instrument_id'leri çek — SELL filtresi için
        async with self._session_factory() as _s0:
            open_inst_ids: set[int] = set(
                (await _s0.execute(select(OpenPosition.instrument_id).distinct()))
                .scalars()
                .all()
            )

        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=PROMOTE_DEDUP_HOURS)
        produced = 0
        promoted = 0
        async with self._session_factory() as session:
            for inst in instruments:
                if inst.sector == "Endeks":
                    continue
                # En son technical_signal'i çek
                ts = (
                    await session.execute(
                        select(TechnicalSignal)
                        .where(TechnicalSignal.instrument_id == inst.id)
                        .order_by(TechnicalSignal.timestamp.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if ts is None:
                    continue

                snapshot = {
                    "rsi": float(ts.rsi) if ts.rsi is not None else None,
                    "macd": float(ts.macd) if ts.macd is not None else None,
                    "macd_signal": float(ts.macd_signal) if ts.macd_signal is not None else None,
                    "ema_20": float(ts.ema_20) if ts.ema_20 is not None else None,
                    "ema_50": float(ts.ema_50) if ts.ema_50 is not None else None,
                    "ema_200": float(ts.ema_200) if ts.ema_200 is not None else None,
                    "bb_upper": float(ts.bb_upper) if ts.bb_upper is not None else None,
                    "bb_lower": float(ts.bb_lower) if ts.bb_lower is not None else None,
                    "close": float(ts.ema_20) if ts.ema_20 is not None else 0.0,
                }

                # En son fiyat
                last_price_row = (
                    await session.execute(
                        select(PriceHistory.close)
                        .where(PriceHistory.instrument_id == inst.id)
                        .order_by(PriceHistory.timestamp.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if last_price_row is None:
                    continue
                current_price = Decimal(str(last_price_row))
                snapshot["close"] = float(current_price)

                # 3 vade için ayrı öneri üret (kısa + orta + uzun)
                short_inputs = RecommendationInput(
                    instrument_id=inst.id,
                    ticker=inst.ticker,
                    technical_snapshot=snapshot,
                    sentiment_score=None,
                    sentiment_count=0,
                    tv_summary=None,
                    inv_summary=None,
                    current_price=current_price,
                )
                mid_inputs = MidTermInput(
                    instrument_id=inst.id,
                    ticker=inst.ticker,
                    technical_snapshot=snapshot,
                    sentiment_score=None,
                    sentiment_count=0,
                    fundamental_snapshot=None,
                    current_price=current_price,
                )
                long_inputs = LongTermInput(
                    instrument_id=inst.id,
                    ticker=inst.ticker,
                    technical_snapshot=snapshot,
                    sentiment_score=None,
                    sentiment_count=0,
                    fundamental_snapshot=None,
                    current_price=current_price,
                )

                from app.db.models import BotPick as _BotPick

                for rec_obj, rec_inputs in (
                    (self._recommender, short_inputs),
                    (self._recommender_mid, mid_inputs),
                    (self._recommender_long, long_inputs),
                ):
                    try:
                        output = rec_obj.recommend(rec_inputs)
                        rec_obj.save_recommendation(session, output, inst.id)
                        produced += 1
                    except Exception as exc:
                        logger.warning(
                            "recommend hata [{}/{}]: {}",
                            inst.ticker,
                            getattr(rec_obj, "__class__", type(rec_obj)).__name__,
                            exc,
                        )
                        continue

                    # HOLD bot_picks'e gitmez (gürültü)
                    if output.action == "HOLD":
                        # Var olan açık BUY pick'i HOLD aldıysa açık kalır
                        # (kullanıcı pozisyon almaya karar verecek)
                        continue
                    # SELL sadece açık pozisyon olan hisseler için
                    if output.action == "SELL" and inst.id not in open_inst_ids:
                        continue

                    # Mevcut açık pick (aynı timeframe)
                    existing_open_pick = (
                        await session.execute(
                            select(_BotPick)
                            .where(
                                _BotPick.instrument_id == inst.id,
                                _BotPick.timeframe == output.timeframe,
                                _BotPick.is_open.is_(True),
                            )
                            .limit(1)
                        )
                    ).scalar_one_or_none()

                    if existing_open_pick is not None:
                        if existing_open_pick.action == output.action:
                            # Aynı yön → güncel tut (confidence ve target_price)
                            existing_open_pick.confidence = float(output.confidence)
                            if output.target_price is not None:
                                existing_open_pick.target_price = float(output.target_price)
                            logger.debug(
                                "Bot pick güncellendi: {} {} {} (güven %{:.0f})",
                                inst.ticker, output.action, output.timeframe, output.confidence,
                            )
                        else:
                            # Ters yön → eski pick'i 'stopped' ile kapat, yeni aç
                            existing_open_pick.is_open = False
                            existing_open_pick.closed_at = datetime.now(tz=timezone.utc)
                            existing_open_pick.price_at_close = float(current_price)
                            existing_open_pick.outcome = "stopped"
                            if existing_open_pick.price_at_pick:
                                pp = float(existing_open_pick.price_at_pick)
                                cp = float(current_price)
                                ret_pct = ((cp - pp) / pp * 100.0) if existing_open_pick.action == "BUY" else ((pp - cp) / pp * 100.0)
                                existing_open_pick.return_pct = ret_pct
                            logger.info(
                                "Bot pick kapandı (analiz ters döndü): {} eski={} yeni={}",
                                inst.ticker, existing_open_pick.action, output.action,
                            )
                            try:
                                new_pick = _BotPick(
                                    instrument_id=inst.id,
                                    timeframe=output.timeframe,
                                    action=output.action,
                                    confidence=float(output.confidence),
                                    price_at_pick=float(current_price),
                                    target_price=float(output.target_price) if output.target_price is not None else None,
                                    is_open=True,
                                )
                                session.add(new_pick)
                                promoted += 1
                            except Exception as exc:
                                logger.warning("bot_pick reverse promote hata [{}]: {}", inst.ticker, exc)
                        continue

                    # Hiç açık pick yok — yeni ekle
                    try:
                        pick = _BotPick(
                            instrument_id=inst.id,
                            timeframe=output.timeframe,
                            action=output.action,
                            confidence=float(output.confidence),
                            price_at_pick=float(current_price),
                            target_price=float(output.target_price) if output.target_price is not None else None,
                            is_open=True,
                        )
                        session.add(pick)
                        promoted += 1
                        logger.debug(
                            "Bot pick promote: {} {} {} (güven %{:.0f})",
                            inst.ticker, output.action, output.timeframe, output.confidence,
                        )
                    except Exception as exc:
                        logger.warning("bot_pick promote hata [{}]: {}", inst.ticker, exc)

            await session.commit()

        return f"{produced} öneri üretildi, {promoted} bot pick eklendi"

    async def _maintain_open_bot_picks(self) -> str:
        """Açık bot pick'lerden target'a ulaşan veya süresi dolanları kapat."""
        from app.portfolio.bot_picks import BotPicksService
        from app.db.session import SessionLocal

        # BotPicksService sync session bekler — async factory yerine SessionLocal kullan.
        service = BotPicksService(SessionLocal)

        # Her ticker için son fiyatı topla
        async with self._session_factory() as session:
            from app.db.models import BotPick

            open_picks = (
                await session.execute(
                    select(BotPick).where(BotPick.is_open.is_(True))
                )
            ).scalars().all()

            if not open_picks:
                return "açık pick yok"

            prices: dict[int, Decimal] = {}
            for pick in open_picks:
                row = (
                    await session.execute(
                        select(PriceHistory.close)
                        .where(PriceHistory.instrument_id == pick.instrument_id)
                        .order_by(PriceHistory.timestamp.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if row is not None:
                    prices[pick.instrument_id] = Decimal(str(row))

        closed_ids = await service.update_open_picks(prices)
        return f"{len(closed_ids)} pick kapatıldı"

    async def _fetch_news_from_rss(self) -> str:
        """RSS feed'lerinden son haberleri çek, news_feed tablosuna idempotent yaz."""
        if self._rss is None:
            return "RSS devre dışı"

        try:
            items = await self._rss.fetch_news(since=None)
        except Exception as exc:
            logger.warning("RSS fetch_news hata: {}", exc)
            return f"hata: {exc}"

        if not items:
            return "yeni haber yok"

        from app.db.models import NewsFeed

        inserted = 0
        async with self._session_factory() as session:
            for item in items:
                # Idempotent: aynı URL varsa atla
                exists = (
                    await session.execute(
                        select(NewsFeed.id).where(NewsFeed.url == item.url).limit(1)
                    )
                ).scalar_one_or_none()
                if exists is not None:
                    continue
                try:
                    row = NewsFeed(
                        instrument_id=None,  # ticker-ilişkilendirme Faz 4
                        source=item.source,
                        title=item.title,
                        url=item.url,
                        published_at=item.published_at,
                        language=item.language,
                        sentiment=None,
                        sentiment_score=None,
                    )
                    session.add(row)
                    inserted += 1
                except Exception as exc:
                    logger.warning("news insert hata [{}]: {}", item.url, exc)
            if inserted:
                await session.commit()

        return f"{inserted} yeni haber kaydedildi"
