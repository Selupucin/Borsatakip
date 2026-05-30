"""SafetyEngine — kill switch, günlük limit, circuit breaker, audit log.

Doküman §7.5 (Güvenlik ve Koruma Önlemleri) referans alınmıştır.

Sorumluluk:
- **Kill switch:** Tek metod çağrısıyla TÜM otomatik işlemleri durdur
  (UI'ya da sinyal gider). ``AutoGate`` her karardan önce sorgular.
- **Günlük işlem limiti:** Bir takvim gününde ``max_daily_trades`` üstüne
  çıkılamaz. Hesap günü TZ-aware UTC.
- **Circuit breaker:** Portföy değeri zirvesinden ``max_drawdown_pct``
  düşerse otomatik durur.
- **Audit log:** Her emir, mod değişikliği, kill switch / breaker
  olayı loguru'ya yapılandırılmış kayıt düşer.

Tasarım notları:
- Kill switch durumu sınıf instance'ında tutulur (process-yerel).
  Çok-süreçli koşulda merkezi paylaşım için DB tablosu eklenebilir
  (Faz 4'te ``trade_safety_state`` tablosu önerilir).
- ``can_trade`` çağrısı async; tüm kontrolleri ardışık koşar.
- Tüm para alanları ``Decimal``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import and_, func, select


# ---------------------------------------------------------------------------
# Veri yapısı
# ---------------------------------------------------------------------------


@dataclass
class SafetyState:
    """Bir hesabın anlık safety durumunu özetler."""

    kill_switch_active: bool
    circuit_breaker_triggered: bool
    daily_trade_count: int
    daily_trade_limit: int
    portfolio_drawdown_pct: Decimal
    max_drawdown_pct: Decimal
    last_reset: datetime
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class SafetyEngine:
    """Kill switch + günlük limit + circuit breaker + audit log.

    Parameters
    ----------
    session_factory:
        SQLAlchemy ``Session`` fabrikası (portfolio ve account okumaları
        için).
    max_daily_trades:
        Bir günde bu hesap için yapılabilecek azami işlem sayısı
        (varsayılan 10).
    max_drawdown_pct:
        Portföy değeri zirveden bu yüzde altına düşerse circuit breaker
        tetiklenir (varsayılan 15.0).
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        max_daily_trades: int = 10,
        max_drawdown_pct: Decimal = Decimal("15"),
    ) -> None:
        self.session_factory = session_factory
        self.max_daily_trades = int(max_daily_trades)
        self.max_drawdown_pct = (
            max_drawdown_pct
            if isinstance(max_drawdown_pct, Decimal)
            else Decimal(str(max_drawdown_pct))
        )
        self._kill_switch_active: bool = False
        self._kill_switch_reason: Optional[str] = None
        self._kill_switch_at: Optional[datetime] = None
        # Zirve takibi (in-memory; Faz 4'te DB).
        self._peak_value: dict[int, Decimal] = {}
        self._last_reset: datetime = datetime.now(timezone.utc)

    # ----------------------------------------------------------- helpers

    def _session(self):
        return self.session_factory()

    @staticmethod
    def _close(session) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    # ----------------------------------------------------------- kill switch

    def activate_kill_switch(self, reason: str) -> None:
        """ANINDA tüm otomatik işlemleri durdur (process-yerel).

        ``AutoGate.evaluate`` bir sonraki çağrısında ``BLOCKED``
        döndürür. UI'ya sinyal göndermek çağıranın sorumluluğudur.
        """

        self._kill_switch_active = True
        self._kill_switch_reason = reason or "manual"
        self._kill_switch_at = datetime.now(timezone.utc)
        logger.warning(
            "KILL SWITCH AKTİF: reason={!r}, at={}",
            self._kill_switch_reason,
            self._kill_switch_at.isoformat(),
        )

    def deactivate_kill_switch(self, user_confirmation: bool) -> None:
        """Kill switch'i kapat — kullanıcı onayı zorunludur.

        Parameters
        ----------
        user_confirmation:
            ``True`` değilse hiçbir şey yapmaz (kazara devre dışı
            bırakmayı engellemek için).
        """

        if not user_confirmation:
            logger.info("Kill switch deaktivasyonu reddedildi: onay yok.")
            return
        prev_reason = self._kill_switch_reason
        self._kill_switch_active = False
        self._kill_switch_reason = None
        logger.warning(
            "KILL SWITCH DEAKTİF: previous_reason={!r}", prev_reason
        )

    @property
    def kill_switch_active(self) -> bool:
        return self._kill_switch_active

    # ----------------------------------------------------------- günlük limit

    async def check_daily_trade_limit(self, account_id: int) -> bool:
        """Bugün açılan ``portfolio`` satır sayısı limit altında mı?

        Returns
        -------
        bool
            ``True`` ise limit aşılmadı (işleme devam edilebilir).
        """

        count = await self._today_trade_count(account_id)
        ok = count < self.max_daily_trades
        if not ok:
            logger.warning(
                "Günlük işlem limiti aşıldı: account={}, count={}, limit={}",
                account_id,
                count,
                self.max_daily_trades,
            )
        return ok

    async def _today_trade_count(self, account_id: int) -> int:
        """Hesabın cüzdanlarındaki bugün açılmış portfolio satır sayısı."""

        from app.db.models import Portfolio, Wallet  # noqa: WPS433

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        tomorrow_start = today_start + timedelta(days=1)

        session = self._session()
        try:
            wallet_ids = (
                select(Wallet.id).where(Wallet.account_id == account_id).subquery()
            )
            count = session.execute(
                select(func.count(Portfolio.id)).where(
                    and_(
                        Portfolio.wallet_id.in_(select(wallet_ids.c.id)),
                        Portfolio.transaction_at >= today_start,
                        Portfolio.transaction_at < tomorrow_start,
                    )
                )
            ).scalar_one() or 0
        finally:
            self._close(session)
        return int(count)

    # ----------------------------------------------------------- circuit breaker

    async def check_circuit_breaker(
        self,
        account_id: int,
        current_portfolio_value: Optional[Decimal] = None,
    ) -> bool:
        """Portföy değeri zirveden çok mu düştü?

        Parameters
        ----------
        current_portfolio_value:
            ``None`` ise hesabın ``cash_balance + wallet.cash_balance``
            toplamı kullanılır (açık pozisyon değeri bilinmiyorsa
            konservatif yaklaşım). Çağıran taraf PositionService'ten
            güncel değer hesaplayıp verebilir.

        Returns
        -------
        bool
            ``True`` ise henüz tetiklenmedi (işleme devam edilebilir).
            ``False`` ise drawdown eşik üstüne çıktı.
        """

        value = current_portfolio_value
        if value is None:
            value = await self._account_cash_total(account_id)

        peak = self._peak_value.get(account_id)
        if peak is None or value > peak:
            self._peak_value[account_id] = value
            return True

        if peak <= 0:
            return True

        drawdown_pct = ((peak - value) / peak) * Decimal("100")
        if drawdown_pct >= self.max_drawdown_pct:
            logger.warning(
                "CIRCUIT BREAKER tetiklendi: account={}, peak={}, "
                "value={}, drawdown={:.2f}%, limit={}%",
                account_id,
                peak,
                value,
                float(drawdown_pct),
                self.max_drawdown_pct,
            )
            return False
        return True

    async def _account_cash_total(self, account_id: int) -> Decimal:
        """Hesabın toplam nakdi (account.cash_balance + tüm wallet.cash_balance)."""

        from app.db.models import UserAccount, Wallet  # noqa: WPS433

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            account_cash = (
                self._to_decimal(account.cash_balance, Decimal("0"))
                if account
                else Decimal("0")
            )
            wallet_cash = session.execute(
                select(func.coalesce(func.sum(Wallet.cash_balance), 0)).where(
                    Wallet.account_id == account_id
                )
            ).scalar_one() or 0
        finally:
            self._close(session)

        return account_cash + self._to_decimal(wallet_cash, Decimal("0"))

    # ----------------------------------------------------------- state

    async def get_state(self, account_id: int) -> SafetyState:
        """Hesabın anlık safety özet durumunu döndür."""

        today_count = await self._today_trade_count(account_id)
        current_value = await self._account_cash_total(account_id)
        peak = self._peak_value.get(account_id, current_value)
        if peak <= 0:
            drawdown_pct = Decimal("0")
        else:
            drawdown_pct = ((peak - current_value) / peak) * Decimal("100")
            if drawdown_pct < 0:
                drawdown_pct = Decimal("0")

        breaker_triggered = drawdown_pct >= self.max_drawdown_pct

        return SafetyState(
            kill_switch_active=self._kill_switch_active,
            circuit_breaker_triggered=breaker_triggered,
            daily_trade_count=today_count,
            daily_trade_limit=self.max_daily_trades,
            portfolio_drawdown_pct=drawdown_pct,
            max_drawdown_pct=self.max_drawdown_pct,
            last_reset=self._last_reset,
            extras={
                "kill_switch_reason": self._kill_switch_reason,
                "kill_switch_at": (
                    self._kill_switch_at.isoformat()
                    if self._kill_switch_at
                    else None
                ),
            },
        )

    # ----------------------------------------------------------- toplu karar

    async def can_trade(self, account_id: int) -> tuple[bool, list[str]]:
        """Tüm safety kontrollerini sırayla koş.

        Returns
        -------
        (bool, list[str])
            ``(True, [])`` → işleme devam edilebilir.
            ``(False, reasons)`` → engellendi, gerekçeler listede.
        """

        reasons: list[str] = []

        if self._kill_switch_active:
            reasons.append(
                f"Kill switch aktif: {self._kill_switch_reason or 'manual'}"
            )

        if not await self.check_daily_trade_limit(account_id):
            reasons.append(
                f"Günlük işlem limiti aşıldı (limit: {self.max_daily_trades})."
            )

        if not await self.check_circuit_breaker(account_id):
            reasons.append(
                f"Circuit breaker tetiklendi: drawdown ≥ %{self.max_drawdown_pct}"
            )

        return (len(reasons) == 0, reasons)

    # ----------------------------------------------------------- audit log

    async def audit_log(
        self,
        account_id: int,
        action: str,
        details: dict,
    ) -> None:
        """Yapılandırılmış audit log kaydı düş.

        Notes
        -----
        Şu an yalnızca loguru'ya yazar. Faz 4'te ``audit_log`` tablosu
        eklenip aynı kayıt DB'ye de yazılacak.

        Parameters
        ----------
        action:
            Örn. ``'order_placed'``, ``'mode_changed'``,
            ``'kill_switch_activated'``, ``'circuit_breaker_triggered'``.
        details:
            Aksiyona özgü serbest alan sözlüğü.
        """

        logger.bind(audit=True).info(
            "AUDIT account={} action={} details={}",
            account_id,
            action,
            details,
        )


__all__ = [
    "SafetyState",
    "SafetyEngine",
]
