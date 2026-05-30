"""Hesap (UserAccount) servisi — Faz 3 Batch 2.

Doküman §6 (özellikle §6.4 Portföy ve Bakiye Takibi) ve §7 (İşlem Modları)
referans alınmıştır.

Sorumluluk:
- Tek satırlık ``user_account`` kaydının yaratılması ve okunması
  (uygulama tek-kullanıcılı; ``get_or_create`` idempotent).
- ``trading_mode`` ve ``risk_threshold`` ayarı — DB CHECK constraint'leri
  ile uyumlu (``manual_parallel|semi_auto|full_auto|paper`` ve 0..100).
- ``AccountSnapshot`` — UI dashboard için anlık özet (nakit + cüzdan
  toplam değeri). ``total_value`` cüzdan servislerinin ürettiği güncel
  pozisyon değerlerini toplar.

Tasarım kuralları:
- ``session_factory`` sözleşmesi ``WatchlistService`` / ``BotPicksService``
  ile aynı (Faz 2'deki sync session fabrikası).
- API **async**.
- Para alanları ``Decimal``; DB ``Numeric(18, 4)`` kolonlarına
  ``float(decimal)`` cast ile yazılır (mevcut model float-tipli olduğu
  için aynı sözleşme kullanılır).
- Hiçbir cüzdan/pozisyon işi burada yapılmaz — ``wallets.py``,
  ``positions.py``, ``pnl.py`` sorumluluğundadır.

Faz 3 Batch 2'de eklendi:
- ``AccountService`` (CRUD).
- ``AccountSnapshot`` dataclass'ı.

trading-executor için not:
- ``set_trading_mode(account_id, mode)`` tek geçerli giriş noktasıdır;
  emir gönderme öncesi modun ``full_auto`` veya ``semi_auto`` olduğunu
  burası üzerinden doğrulamalı. ``paper`` modunda ``trading-executor``
  hiçbir gerçek emir göndermez (``paper_trading.py``'a delege eder).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import func, select


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: ``user_account.trading_mode`` CHECK constraint kümesi.
VALID_TRADING_MODES: frozenset[str] = frozenset(
    {"manual_parallel", "semi_auto", "full_auto", "paper"}
)

#: ``user_account.risk_threshold`` — UI kaydırıcı 0..100 arası.
MIN_RISK_THRESHOLD: float = 0.0
MAX_RISK_THRESHOLD: float = 100.0


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class AccountSnapshot:
    """Hesap düzeyinde anlık özet — UI dashboard satırı.

    ``total_value`` = ``cash_balance`` + tüm cüzdanların güncel pozisyon
    değerleri toplamı. Pozisyon değerleri için çağıran tarafın
    ``current_prices`` haritasını vermesi gerekir; ``get_snapshot``
    parametre almıyorsa pozisyonlar 0 sayılır (sade nakit görünümü).
    """

    account_id: int
    trading_mode: str
    risk_threshold: float
    initial_balance: Decimal
    cash_balance: Decimal
    total_value: Decimal
    currency: str


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class AccountService:
    """UserAccount tablosu üzerinde CRUD.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    """

    def __init__(self, session_factory: Callable[[], object]) -> None:
        self.session_factory = session_factory

    # ------------------------------------------------------------- helpers

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

    # ------------------------------------------------------------- create / read

    async def get_or_create(
        self,
        initial_balance: Decimal = Decimal("15000"),
        currency: str = "TRY",
    ) -> int:
        """Tek satırlık ``user_account`` kaydını idempotent yarat veya bul.

        Uygulama tek-kullanıcılı olduğu için ``account_id`` her zaman
        var olan ilk (en küçük id) satır olarak seçilir. Yoksa yeni kayıt
        açılır; ``cash_balance = initial_balance`` ile başlatılır.

        Returns
        -------
        int
            ``UserAccount.id``.
        """

        from app.db.models import UserAccount  # noqa: WPS433

        clean_currency = (currency or "TRY").strip().upper() or "TRY"
        init_amt = self._to_decimal(initial_balance, Decimal("0"))
        if init_amt < 0:
            raise ValueError("initial_balance negatif olamaz.")

        session = self._session()
        try:
            row = session.execute(
                select(UserAccount).order_by(UserAccount.id.asc()).limit(1)
            ).scalar_one_or_none()

            if row is not None:
                acc_id = int(row.id)
                logger.debug("UserAccount mevcut: id={}", acc_id)
                return acc_id

            row = UserAccount(
                trading_mode="manual_parallel",
                risk_threshold=50.0,
                initial_balance=float(init_amt),
                cash_balance=float(init_amt),
                currency=clean_currency,
            )
            session.add(row)
            session.flush()
            acc_id = int(row.id)
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "UserAccount oluşturuldu: id={}, initial_balance={} {}, mode=manual_parallel",
            acc_id,
            init_amt,
            clean_currency,
        )
        return acc_id

    async def get_snapshot(self, account_id: int) -> AccountSnapshot:
        """Hesabın anlık özetini döndür.

        ``total_value`` burada yalnızca cüzdan ``cash_balance`` toplamı
        + ``account.cash_balance`` (dağıtılmamış nakit) olarak hesaplanır.
        **Açık pozisyonların güncel piyasa değeri için**
        ``WalletService.compute_snapshot`` veya ``PositionService.list_*``
        çağrılıp toplamı caller tarafından AccountSnapshot.total_value
        üstüne eklenebilir (servis bağımsız tutulsun diye burada
        çağırmıyoruz — döngüsel import önlemek için).
        """

        from app.db.models import UserAccount, Wallet  # noqa: WPS433

        session = self._session()
        try:
            row = session.get(UserAccount, account_id)
            if row is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")

            wallet_cash_total = session.execute(
                select(func.coalesce(func.sum(Wallet.cash_balance), 0)).where(
                    Wallet.account_id == account_id
                )
            ).scalar_one() or 0

            account_cash = self._to_decimal(row.cash_balance, Decimal("0"))
            wallet_cash = self._to_decimal(wallet_cash_total, Decimal("0"))
            total_value = account_cash + wallet_cash

            snapshot = AccountSnapshot(
                account_id=int(row.id),
                trading_mode=str(row.trading_mode),
                risk_threshold=float(row.risk_threshold or 0.0),
                initial_balance=self._to_decimal(row.initial_balance, Decimal("0")),
                cash_balance=account_cash,
                total_value=total_value,
                currency=str(row.currency or "TRY"),
            )
        finally:
            self._close(session)

        return snapshot

    # ------------------------------------------------------------- update

    async def set_trading_mode(self, account_id: int, mode: str) -> None:
        """``trading_mode`` ayarla — CHECK constraint uyumlu.

        Raises
        ------
        ValueError
            ``mode`` geçersiz küme dışındaysa.
        LookupError
            Hesap yoksa.
        """

        clean = (mode or "").strip().lower()
        if clean not in VALID_TRADING_MODES:
            raise ValueError(
                f"Geçersiz trading_mode: {mode!r}. "
                f"Beklenen: {sorted(VALID_TRADING_MODES)}"
            )

        from app.db.models import UserAccount  # noqa: WPS433

        session = self._session()
        try:
            row = session.get(UserAccount, account_id)
            if row is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")
            old = str(row.trading_mode)
            row.trading_mode = clean
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "UserAccount.trading_mode güncellendi: id={}, {} → {}",
            account_id,
            old,
            clean,
        )

    async def set_risk_threshold(self, account_id: int, threshold: float) -> None:
        """``risk_threshold`` ayarla (0..100 arası).

        Doküman §5.3 risk skoru ile aynı ölçek; kullanıcı bu eşiğin
        altındaki risk skorlu önerileri otomatik dinler.
        """

        try:
            t = float(threshold)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Geçersiz threshold: {threshold!r}") from exc
        if not (MIN_RISK_THRESHOLD <= t <= MAX_RISK_THRESHOLD):
            raise ValueError(
                f"risk_threshold {MIN_RISK_THRESHOLD}..{MAX_RISK_THRESHOLD} "
                f"aralığında olmalı, geldi: {t}"
            )

        from app.db.models import UserAccount  # noqa: WPS433

        session = self._session()
        try:
            row = session.get(UserAccount, account_id)
            if row is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")
            old = float(row.risk_threshold or 0.0)
            row.risk_threshold = t
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "UserAccount.risk_threshold güncellendi: id={}, {} → {}",
            account_id,
            old,
            t,
        )


__all__ = [
    "VALID_TRADING_MODES",
    "MIN_RISK_THRESHOLD",
    "MAX_RISK_THRESHOLD",
    "AccountSnapshot",
    "AccountService",
]
