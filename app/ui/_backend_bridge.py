"""UI ile backend servisleri arasındaki köprü — Faz 3 backend wiring.

UI widget'ları backend servis instance'larına bu modül üzerinden ulaşır.
Async servis metodlarını Qt UI thread'ini bloklamadan çağırmak için
``QThreadPool`` üzerinde ``asyncio.run()`` ile koşan basit bir worker
sağlar.

Tasarım kuralları
-----------------
- **Tek session factory:** Tüm portföy/cüzdan servisleri aynı sync
  ``SessionLocal`` callable'ını paylaşır. Servisler ``session_factory``
  sözleşmesi gereği her çağrıda yeni bir session açar ve kapatır.
- **Lazy import:** ``app.db.session`` ve servis modülleri henüz ImportError
  veriyorsa (örn. test ortamında PostgreSQL sürücüsü kurulu değil) bridge
  ``available=False`` ile sessizce devam eder; widget'lar boş state'e düşer
  ve toast hatasıyla bilgilendirir.
- **Singleton:** ``get_bridge()`` ilk çağrıda oluşturur, sonrakilerde aynı
  instance'ı döner. UI widget'ları test edilebilir kalsın diye
  bridge `None` da olabilir; widget'ların hepsi defansif kontrol yapar.
- **UI thread güvenliği:** ``run_async()`` callback'leri Qt sinyal/slot
  ile bağladığı için worker thread'inde alınan sonuçlar otomatik olarak
  ana thread'de işlenir.

Kullanım (widget içinde)::

    from app.ui._backend_bridge import get_bridge

    self._bridge = get_bridge()
    if self._bridge.available:
        self._bridge.run_async(
            lambda: self._bridge.account.get_snapshot(self._bridge.account_id),
            on_success=self._render_snapshot,
            on_error=self._render_error,
        )
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from loguru import logger
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal


# ---------------------------------------------------------------------------
# Async worker — QRunnable + asyncio.run
# ---------------------------------------------------------------------------


class _AsyncWorkerSignals(QObject):
    """QRunnable'a sinyal eklemek için yardımcı QObject taşıyıcısı.

    ``QRunnable`` ``QObject`` olmadığı için doğrudan sinyal taşıyamaz.
    """

    finished = Signal(object)  # callback (result)
    error = Signal(object)     # callback (exception)


class _AsyncWorker(QRunnable):
    """Bir ``async`` callable'ı ayrı thread'de ``asyncio.run`` ile çalıştırır.

    Sonuç ``finished`` sinyali ile UI thread'ine taşınır; istisna olursa
    ``error`` sinyali tetiklenir.
    """

    def __init__(self, coro_factory: Callable[[], Any]) -> None:
        super().__init__()
        self._coro_factory = coro_factory
        self.signals = _AsyncWorkerSignals()

    def run(self) -> None:  # noqa: D401
        try:
            coro_or_value = self._coro_factory()
            if asyncio.iscoroutine(coro_or_value):
                result = asyncio.run(coro_or_value)
            else:
                # Senkron callable verilmiş — sonucu olduğu gibi geçir.
                result = coro_or_value
            self.signals.finished.emit(result)
        except Exception as exc:  # noqa: BLE001
            logger.exception("BackendBridge async worker hatası: {}", exc)
            self.signals.error.emit(exc)


# ---------------------------------------------------------------------------
# Backend bridge
# ---------------------------------------------------------------------------


class BackendBridge(QObject):
    """UI ↔ backend servis köprüsü.

    Tüm servisleri lazy olarak yaratır; sync ``SessionLocal`` callable'ını
    ``session_factory`` olarak iletir. ``available=False`` ise hiçbir
    servis instance üretilmez (test/headless ortamı).
    """

    def __init__(self) -> None:
        super().__init__()
        self.available: bool = False
        self.session_factory: Optional[Callable[[], Any]] = None
        self.account_id: Optional[int] = None

        # Instruments cache — UI tarafında dialog açılışlarını hızlandırır.
        # Sync DB sorgusu yerine 60sn'lik snapshot kullanır.
        self._instruments_cache: list[tuple[str, str, str]] = []
        self._instruments_cache_ts: float = 0.0
        self._instruments_cache_ttl: float = 60.0  # saniye

        # Lazy servis cache'leri.
        self._account = None
        self._wallets = None
        self._cash_flows = None
        self._positions = None
        self._pnl = None
        self._benchmark = None
        self._paper = None
        self._watchlist = None
        self._bot_picks = None
        self._manual = None
        self._safety = None
        self._risk_scorer = None
        self._risk_mgr = None
        self._auto_gate = None

        # SessionLocal'ı hazır mı? — opsiyonel, fail-safe.
        try:
            from app.db.session import SessionLocal  # noqa: WPS433

            # SessionLocal bir LazyProxy; doğrudan callable kullanılır.
            self.session_factory = SessionLocal
            self.available = True
            logger.info("BackendBridge: SessionLocal yüklendi.")
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "BackendBridge: SessionLocal yüklenemedi ({}). "
                "UI placeholder modda çalışacak.",
                exc,
            )

    # ------------------------------------------------------------------
    # Servis lazy property'leri
    # ------------------------------------------------------------------
    @property
    def account(self):
        if self._account is None and self.available:
            from app.portfolio.account import AccountService  # noqa: WPS433

            self._account = AccountService(self.session_factory)
        return self._account

    @property
    def wallets(self):
        if self._wallets is None and self.available:
            from app.portfolio.wallets import WalletService  # noqa: WPS433

            self._wallets = WalletService(self.session_factory)
        return self._wallets

    @property
    def cash_flows(self):
        if self._cash_flows is None and self.available:
            from app.portfolio.cash_flows import CashFlowService  # noqa: WPS433

            self._cash_flows = CashFlowService(self.session_factory)
        return self._cash_flows

    @property
    def positions(self):
        if self._positions is None and self.available:
            from app.portfolio.positions import PositionService  # noqa: WPS433

            self._positions = PositionService(self.session_factory)
        return self._positions

    @property
    def pnl(self):
        if self._pnl is None and self.available:
            from app.portfolio.pnl import PnLCalculator  # noqa: WPS433

            self._pnl = PnLCalculator(self.session_factory)
        return self._pnl

    @property
    def benchmark(self):
        if self._benchmark is None and self.available:
            from app.portfolio.benchmark import BenchmarkService  # noqa: WPS433

            self._benchmark = BenchmarkService(self.session_factory)
        return self._benchmark

    @property
    def paper(self):
        if self._paper is None and self.available:
            from app.portfolio.paper_trading import PaperTradingService  # noqa: WPS433

            self._paper = PaperTradingService(self.session_factory, self.positions)
        return self._paper

    @property
    def watchlist(self):
        if self._watchlist is None and self.available:
            from app.portfolio.watchlist import WatchlistService  # noqa: WPS433

            self._watchlist = WatchlistService(self.session_factory)
        return self._watchlist

    @property
    def bot_picks(self):
        if self._bot_picks is None and self.available:
            from app.portfolio.bot_picks import BotPicksService  # noqa: WPS433
            from app.db.session import SessionLocal  # sync — BotPicksService sync session bekler

            self._bot_picks = BotPicksService(SessionLocal)
        return self._bot_picks

    @property
    def manual(self):
        if self._manual is None and self.available:
            from app.trading.manual_parallel import ManualParallelService  # noqa: WPS433

            self._manual = ManualParallelService(
                self.session_factory, position_service=self.positions
            )
        return self._manual

    @property
    def safety(self):
        if self._safety is None and self.available:
            from app.broker.safety import SafetyEngine  # noqa: WPS433

            self._safety = SafetyEngine(self.session_factory)
        return self._safety

    @property
    def risk_scorer(self):
        if self._risk_scorer is None:
            from app.trading.risk_scorer import TradeRiskScorer  # noqa: WPS433

            self._risk_scorer = TradeRiskScorer()
        return self._risk_scorer

    @property
    def risk_mgr(self):
        if self._risk_mgr is None and self.available:
            from app.analysis.risk import RiskManager  # noqa: WPS433

            self._risk_mgr = RiskManager(self.session_factory)
        return self._risk_mgr

    @property
    def auto_gate(self):
        if self._auto_gate is None and self.available:
            from app.trading.auto_gate import AutoGate  # noqa: WPS433

            self._auto_gate = AutoGate(
                risk_scorer=self.risk_scorer,
                safety_engine=self.safety,
                broker=None,  # Faz 4'te broker adapter buraya bağlanacak
            )
        return self._auto_gate

    # ------------------------------------------------------------------
    # Async runner
    # ------------------------------------------------------------------
    def run_async(
        self,
        coro_factory: Callable[[], Any],
        on_success: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[BaseException], None]] = None,
    ) -> None:
        """Coroutine üreten callable'ı thread pool'da çalıştır.

        Parameters
        ----------
        coro_factory:
            Çağrıldığında bir ``coroutine`` döndürmesi beklenir (sync
            değer de dönebilir; o zaman doğrudan ``on_success``'e gider).
            UI thread'inde **çağrılır ama beklenmez** — async metod
            worker thread'inde ``asyncio.run`` ile çalıştırılır.
        on_success:
            Sonuç UI thread'inde bu callable'a iletilir.
        on_error:
            İstisna UI thread'inde bu callable'a iletilir.
        """

        worker = _AsyncWorker(coro_factory)
        if on_success is not None:
            worker.signals.finished.connect(on_success)
        if on_error is not None:
            worker.signals.error.connect(on_error)
        QThreadPool.globalInstance().start(worker)

    # ------------------------------------------------------------------
    # Instruments cache
    # ------------------------------------------------------------------
    def cached_instruments(
        self, force_refresh: bool = False
    ) -> list[tuple[str, str, str]]:
        """Tüm instruments tablosunu ``(ticker, name, exchange)`` listesi olarak döndür.

        60 saniyelik in-memory cache; UI thread'inde okunduğu için thread-safe
        olması beklenmez. Dialog açılışlarını hızlandırmak için kullanılır
        (ör. ChartWidget "Hisse Seç", WatchlistWidget "Hisse Ekle").

        Returns
        -------
        list[tuple[str, str, str]]
            ``[(ticker, name, exchange), ...]`` exchange + ticker sırasında.
            Backend hazır değilse veya hata olursa boş liste.
        """

        if not self.available or self.session_factory is None:
            return []

        now = time.monotonic()
        if (
            not force_refresh
            and self._instruments_cache
            and (now - self._instruments_cache_ts) < self._instruments_cache_ttl
        ):
            return self._instruments_cache

        try:
            from sqlalchemy import select  # noqa: WPS433

            from app.db.models import Instrument  # noqa: WPS433

            session = self.session_factory()
            try:
                rows = session.execute(
                    select(Instrument.ticker, Instrument.name, Instrument.exchange)
                    .order_by(Instrument.exchange, Instrument.ticker)
                ).all()
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            data = [(str(r[0]), str(r[1] or ""), str(r[2] or "")) for r in rows]
        except Exception as exc:  # noqa: BLE001
            logger.debug("BackendBridge.cached_instruments hata: {}", exc)
            return self._instruments_cache  # eski cache geri dön (varsa)

        self._instruments_cache = data
        self._instruments_cache_ts = now
        return data

    def invalidate_instruments_cache(self) -> None:
        """Cache'i sıfırla — yeni instrument eklendiğinde çağrılabilir."""

        self._instruments_cache = []
        self._instruments_cache_ts = 0.0

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------
    async def ensure_account(self) -> int:
        """Default hesabı garanti et, ``account_id`` döndür.

        Tek-kullanıcılı uygulamada ``AccountService.get_or_create`` sonucu
        cache'lenir; ardışık çağrılar DB'ye gitmez.
        """

        if self.account_id is not None:
            return self.account_id
        if not self.available or self.account is None:
            raise RuntimeError("BackendBridge kullanılabilir değil.")
        acc_id = await self.account.get_or_create()
        # WalletService.init_matrix idempotent — varsa dokunmaz, yoksa kurar.
        try:
            await self.wallets.init_matrix(acc_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "BackendBridge.ensure_account: wallets.init_matrix başarısız: {}",
                exc,
            )
        self.account_id = int(acc_id)
        return self.account_id


# ---------------------------------------------------------------------------
# Singleton erişim
# ---------------------------------------------------------------------------

_bridge: Optional[BackendBridge] = None


def get_bridge() -> BackendBridge:
    """Process-yerel ``BackendBridge`` singleton'ını döndür."""

    global _bridge
    if _bridge is None:
        _bridge = BackendBridge()
    return _bridge


def reset_bridge() -> None:
    """Test izolasyonu için singleton'ı sıfırla."""

    global _bridge
    _bridge = None


__all__ = ["BackendBridge", "get_bridge", "reset_bridge"]
