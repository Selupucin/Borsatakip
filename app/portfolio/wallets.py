"""Cüzdan matrisi (Wallet) servisi — Faz 3 Batch 2.

Doküman §6.1 (Bütçe Dağılımı: Havuz × Vade Matrisi) referans alınmıştır.

Matris: **2 havuz × 3 vade = 6 bağımsız cüzdan.**

| Pool \\ Timeframe | short | mid | long |
|---|---|---|---|
| bot  | ('bot','short')  | ('bot','mid')  | ('bot','long')  |
| user | ('user','short') | ('user','mid') | ('user','long') |

Her cüzdan kendi nakit bakiyesini (``cash_balance``), açık pozisyonlarını
ve kâr-zararını tutar. Tahsisat (``allocated``) **kullanıcı tarafından
ayrılan toplam bütçedir** — pozisyon almak nakit bakiyeyi azaltır ama
``allocated`` aynı kalır; reallocate ile değişir.

Tasarım kuralları:
- ``session_factory`` sözleşmesi Faz 2 servisleriyle aynı.
- API **async**; sync SQLAlchemy 2.0 session içinde.
- Para alanları ``Decimal``; mevcut model ``Numeric(18, 4)`` (Python
  tarafında ``float`` olarak görünür) — yazımda ``float(decimal)``,
  okumada ``Decimal(str(value))`` ile sağlam dönüşüm.
- Cash flow kayıtları ``CashFlowService`` üzerinden yazılır (DRY); bu
  modül CashFlow tablosuna **doğrudan** yazmaz, ``CashFlowService``'i
  delege olarak çağırır. ``CashFlowService`` import'u runtime — döngüsel
  bağımlılığı önlemek için fonksiyon içinde lazy import.

Faz 3 Batch 2'de eklendi:
- ``init_matrix`` — 6 cüzdanı idempotent yarat.
- ``list_wallets`` / ``get_wallet`` — okuma.
- ``allocate`` / ``reallocate`` — para dağıtımı (cash_flows zincirli).
- ``compute_snapshot`` — anlık değerleme.

ui-developer için not:
- ``WalletSnapshot.return_pct_twr`` hesabı **bu modül değil**
  ``BenchmarkService.time_weighted_return`` üzerinden alınır; yazımda
  default 0 verilir, UI gerekirse benchmark servisinden okur.
- ``budget_widget.py`` (Faz 3 Batch 4) bu servisin ``list_wallets``
  çıktısını grid olarak çizecek.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, func, select


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: ``wallets.pool`` CHECK constraint.
VALID_POOLS: tuple[str, ...] = ("bot", "user")

#: ``wallets.timeframe`` CHECK constraint.
VALID_TIMEFRAMES: tuple[str, ...] = ("short", "mid", "long")


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class WalletSnapshot:
    """Cüzdan anlık değerleme — UI ve P&L hesapları için.

    ``allocated`` ile ``cash_balance + positions_value`` aynı olmaz —
    aradaki fark realized P&L + komisyon etkisinden gelir. ``total_value``
    her zaman ``cash_balance + positions_value``'dur (kullanıcının görece
    "şu an cüzdanım bu kadar para eder" değeri).
    """

    id: int
    pool: str
    timeframe: str
    allocated: Decimal
    cash_balance: Decimal
    positions_value: Decimal
    total_value: Decimal
    pnl_realized: Decimal
    pnl_unrealized: Decimal
    pnl_total: Decimal
    return_pct_twr: Decimal


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class WalletService:
    """Havuz × vade cüzdan matrisi yönetimi.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    """

    POOLS: tuple[str, ...] = VALID_POOLS
    TIMEFRAMES: tuple[str, ...] = VALID_TIMEFRAMES

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

    @classmethod
    def _validate_pool(cls, pool: str) -> str:
        p = (pool or "").strip().lower()
        if p not in cls.POOLS:
            raise ValueError(
                f"Geçersiz pool: {pool!r}. Beklenen: {list(cls.POOLS)}"
            )
        return p

    @classmethod
    def _validate_timeframe(cls, timeframe: str) -> str:
        tf = (timeframe or "").strip().lower()
        if tf not in cls.TIMEFRAMES:
            raise ValueError(
                f"Geçersiz timeframe: {timeframe!r}. "
                f"Beklenen: {list(cls.TIMEFRAMES)}"
            )
        return tf

    # ------------------------------------------------------------- init / read

    async def init_matrix(self, account_id: int) -> dict[tuple[str, str], int]:
        """6 cüzdanı (2 pool × 3 timeframe) idempotent yarat.

        Var olanlar dokunulmadan bırakılır; yalnız eksik hücreler
        eklenir. Bütçe (allocated, cash_balance) **0** olarak başlatılır
        — kullanıcı ``allocate`` ile doldurur.

        Returns
        -------
        dict
            ``{(pool, timeframe): wallet_id}``.
        """

        from app.db.models import Wallet  # noqa: WPS433

        result: dict[tuple[str, str], int] = {}

        session = self._session()
        try:
            existing = session.execute(
                select(Wallet).where(Wallet.account_id == account_id)
            ).scalars().all()
            for w in existing:
                key = (str(w.pool), str(w.timeframe))
                result[key] = int(w.id)

            created = 0
            for pool in self.POOLS:
                for tf in self.TIMEFRAMES:
                    key = (pool, tf)
                    if key in result:
                        continue
                    row = Wallet(
                        account_id=int(account_id),
                        pool=pool,
                        timeframe=tf,
                        allocated=0.0,
                        cash_balance=0.0,
                    )
                    session.add(row)
                    session.flush()
                    result[key] = int(row.id)
                    created += 1

            if created > 0:
                commit = getattr(session, "commit", None)
                if callable(commit):
                    commit()
        finally:
            self._close(session)

        logger.info(
            "Wallet matrisi init: account={}, total={}, yeni eklenen={}",
            account_id,
            len(result),
            created,
        )
        return result

    async def list_wallets(self, account_id: int) -> list[WalletSnapshot]:
        """Hesabın tüm cüzdanlarını döndür (positions_value=0 sabit).

        Açık pozisyonların güncel değerini hesaplamak için
        ``compute_snapshot(wallet_id, current_prices)`` ayrı çağrılmalı.
        Bu metod hızlı liste (UI dashboard meta) için optimize.
        """

        from app.db.models import Wallet  # noqa: WPS433

        session = self._session()
        try:
            stmt = (
                select(Wallet)
                .where(Wallet.account_id == account_id)
                .order_by(Wallet.pool.asc(), Wallet.timeframe.asc())
            )
            rows = list(session.execute(stmt).scalars().all())
        finally:
            self._close(session)

        snapshots: list[WalletSnapshot] = []
        for w in rows:
            cash = self._to_decimal(w.cash_balance, Decimal("0"))
            alloc = self._to_decimal(w.allocated, Decimal("0"))
            snapshots.append(
                WalletSnapshot(
                    id=int(w.id),
                    pool=str(w.pool),
                    timeframe=str(w.timeframe),
                    allocated=alloc,
                    cash_balance=cash,
                    positions_value=Decimal("0"),
                    total_value=cash,
                    pnl_realized=Decimal("0"),
                    pnl_unrealized=Decimal("0"),
                    pnl_total=Decimal("0"),
                    return_pct_twr=Decimal("0"),
                )
            )
        return snapshots

    async def get_wallet(
        self, account_id: int, pool: str, timeframe: str
    ) -> int:
        """Belirli (pool, timeframe) hücresinin ``wallet_id``'sini döndür.

        Raises
        ------
        LookupError
            Cüzdan yoksa (``init_matrix`` çağrılmamış).
        """

        p = self._validate_pool(pool)
        tf = self._validate_timeframe(timeframe)

        from app.db.models import Wallet  # noqa: WPS433

        session = self._session()
        try:
            row = session.execute(
                select(Wallet).where(
                    and_(
                        Wallet.account_id == account_id,
                        Wallet.pool == p,
                        Wallet.timeframe == tf,
                    )
                )
            ).scalar_one_or_none()
        finally:
            self._close(session)

        if row is None:
            raise LookupError(
                f"Wallet bulunamadı: account={account_id}, "
                f"pool={p}, timeframe={tf}. "
                "Önce init_matrix çağırın."
            )
        return int(row.id)

    # ------------------------------------------------------------- allocate

    async def allocate(self, wallet_id: int, amount: Decimal) -> None:
        """Cüzdana bütçe ayır.

        - ``account.cash_balance`` -= amount
        - ``wallet.allocated`` += amount
        - ``wallet.cash_balance`` += amount
        - ``cash_flows`` satırı INSERT (``flow_type='allocate'``,
          ``wallet_id=hedef``).

        Raises
        ------
        ValueError
            amount<=0 veya hesap nakdi yetersizse.
        LookupError
            Cüzdan veya hesap yoksa.
        """

        amt = self._to_decimal(amount, Decimal("0"))
        if amt <= 0:
            raise ValueError("allocate amount pozitif olmalı.")

        from app.db.models import UserAccount, Wallet  # noqa: WPS433

        session = self._session()
        try:
            wallet = session.get(Wallet, wallet_id)
            if wallet is None:
                raise LookupError(f"Wallet bulunamadı: id={wallet_id}")

            account = session.get(UserAccount, int(wallet.account_id))
            if account is None:
                raise LookupError(
                    f"UserAccount bulunamadı: id={wallet.account_id}"
                )

            acc_cash = self._to_decimal(account.cash_balance, Decimal("0"))
            if acc_cash < amt:
                raise ValueError(
                    f"Hesap nakdi yetersiz: cash={acc_cash}, allocate={amt}"
                )

            account.cash_balance = float(acc_cash - amt)
            wallet.allocated = float(
                self._to_decimal(wallet.allocated, Decimal("0")) + amt
            )
            wallet.cash_balance = float(
                self._to_decimal(wallet.cash_balance, Decimal("0")) + amt
            )

            # CashFlow kaydı — döngüsel import önlemek için lazy.
            from app.db.models import CashFlow  # noqa: WPS433

            session.add(
                CashFlow(
                    account_id=int(wallet.account_id),
                    wallet_id=int(wallet.id),
                    flow_type="allocate",
                    amount=float(amt),
                    notes=f"allocate to wallet {wallet_id}",
                )
            )

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Wallet allocate: wallet_id={}, amount={}",
            wallet_id,
            amt,
        )

    async def deposit_and_allocate(
        self, wallet_id: int, amount: Decimal
    ) -> None:
        """Hesaba para yat + aynı tutarı doğrudan cüzdana tahsis et (atomic).

        UI kolaylığı: kullanıcı 'cüzdana yatır' diyor; arka planda 2 işlem
        yapılır:
        1. Hesap cash_balance += amount + cash_flows('deposit') satırı
        2. Cüzdan allocated/cash_balance += amount + cash_flows('allocate')
        """
        amt = self._to_decimal(amount, Decimal("0"))
        if amt <= 0:
            raise ValueError("deposit_and_allocate amount pozitif olmalı.")

        from app.db.models import CashFlow, UserAccount, Wallet  # noqa: WPS433

        session = self._session()
        try:
            wallet = session.get(Wallet, wallet_id)
            if wallet is None:
                raise LookupError(f"Wallet bulunamadı: id={wallet_id}")
            account = session.get(UserAccount, int(wallet.account_id))
            if account is None:
                raise LookupError(
                    f"UserAccount bulunamadı: id={wallet.account_id}"
                )

            # Hesaba para yatır
            account.cash_balance = float(
                self._to_decimal(account.cash_balance, Decimal("0")) + amt
            )
            session.add(
                CashFlow(
                    account_id=int(account.id),
                    wallet_id=None,
                    flow_type="deposit",
                    amount=float(amt),
                    notes=f"deposit (auto via cüzdana yatır wallet={wallet_id})",
                )
            )

            # Hesaptan cüzdana tahsis et
            account.cash_balance = float(
                self._to_decimal(account.cash_balance, Decimal("0")) - amt
            )
            wallet.allocated = float(
                self._to_decimal(wallet.allocated, Decimal("0")) + amt
            )
            wallet.cash_balance = float(
                self._to_decimal(wallet.cash_balance, Decimal("0")) + amt
            )
            session.add(
                CashFlow(
                    account_id=int(account.id),
                    wallet_id=int(wallet.id),
                    flow_type="allocate",
                    amount=float(amt),
                    notes=f"allocate to wallet {wallet_id} (auto deposit)",
                )
            )

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Wallet deposit_and_allocate: wallet_id={}, amount={}",
            wallet_id,
            amt,
        )

    async def reallocate(
        self,
        from_wallet_id: int,
        to_wallet_id: int,
        amount: Decimal,
    ) -> None:
        """Cüzdanlar arası transfer (atomik tek transaction).

        - from_wallet.allocated -= amount
        - from_wallet.cash_balance -= amount  (cash yetersiz ise ValueError)
        - to_wallet.allocated += amount
        - to_wallet.cash_balance += amount
        - cash_flows: iki satır INSERT (çıkış + giriş), ``flow_type='reallocate'``.
          Net etki sıfırdır (audit için iki satır).

        Aynı hesap altındaki iki cüzdan olmalı (cross-account transfer
        bu API ile yapılamaz).
        """

        amt = self._to_decimal(amount, Decimal("0"))
        if amt <= 0:
            raise ValueError("reallocate amount pozitif olmalı.")
        if from_wallet_id == to_wallet_id:
            raise ValueError("from_wallet_id ve to_wallet_id aynı olamaz.")

        from app.db.models import CashFlow, Wallet  # noqa: WPS433

        session = self._session()
        try:
            src = session.get(Wallet, from_wallet_id)
            dst = session.get(Wallet, to_wallet_id)
            if src is None:
                raise LookupError(f"Kaynak wallet yok: id={from_wallet_id}")
            if dst is None:
                raise LookupError(f"Hedef wallet yok: id={to_wallet_id}")
            if int(src.account_id) != int(dst.account_id):
                raise ValueError(
                    "Cross-account reallocate desteklenmez: "
                    f"src.account={src.account_id}, dst.account={dst.account_id}"
                )

            src_cash = self._to_decimal(src.cash_balance, Decimal("0"))
            if src_cash < amt:
                raise ValueError(
                    f"Kaynak cüzdan nakdi yetersiz: cash={src_cash}, "
                    f"reallocate={amt}"
                )

            src.allocated = float(
                self._to_decimal(src.allocated, Decimal("0")) - amt
            )
            src.cash_balance = float(src_cash - amt)
            dst.allocated = float(
                self._to_decimal(dst.allocated, Decimal("0")) + amt
            )
            dst.cash_balance = float(
                self._to_decimal(dst.cash_balance, Decimal("0")) + amt
            )

            note = f"reallocate {from_wallet_id} → {to_wallet_id}"
            session.add(
                CashFlow(
                    account_id=int(src.account_id),
                    wallet_id=int(src.id),
                    flow_type="reallocate",
                    amount=float(-amt),  # çıkış (negatif)
                    notes=note,
                )
            )
            session.add(
                CashFlow(
                    account_id=int(dst.account_id),
                    wallet_id=int(dst.id),
                    flow_type="reallocate",
                    amount=float(amt),  # giriş (pozitif)
                    notes=note,
                )
            )

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Wallet reallocate: {} → {}, amount={}",
            from_wallet_id,
            to_wallet_id,
            amt,
        )

    # ------------------------------------------------------------- snapshot

    async def compute_snapshot(
        self,
        wallet_id: int,
        current_prices: dict[int, Decimal],
    ) -> WalletSnapshot:
        """Cüzdanın anlık değerlemesi.

        Parameters
        ----------
        wallet_id:
            Hedef cüzdan id'si.
        current_prices:
            ``{instrument_id: current_price}`` — pozisyonların güncel
            piyasa fiyatları. Eksik instrument için pozisyon değeri
            ``quantity * avg_cost`` (yani gerçekleşmemiş P&L = 0) kabul
            edilir — koruyucu yaklaşım (eksik veri performansı şişirmesin).

        Hesaplamalar:
            positions_value = Σ qty * (current_price or avg_cost)
            pnl_unrealized  = Σ qty * (current_price - avg_cost)
                               (current_price yoksa 0)
            pnl_realized    = Σ portfolio.realized_pnl (SELL satırları)
            pnl_total       = pnl_realized + pnl_unrealized
            total_value     = cash_balance + positions_value
            return_pct_twr  = 0  (BenchmarkService.time_weighted_return
                                  ile ayrı hesaplanır — bu modül time
                                  serisi tutmuyor.)
        """

        from app.db.models import OpenPosition, Portfolio, Wallet  # noqa: WPS433

        session = self._session()
        try:
            wallet = session.get(Wallet, wallet_id)
            if wallet is None:
                raise LookupError(f"Wallet bulunamadı: id={wallet_id}")

            positions = session.execute(
                select(OpenPosition).where(OpenPosition.wallet_id == wallet_id)
            ).scalars().all()

            positions_value = Decimal("0")
            pnl_unrealized = Decimal("0")
            for pos in positions:
                qty = self._to_decimal(pos.quantity, Decimal("0"))
                avg = self._to_decimal(pos.avg_cost, Decimal("0"))
                current = current_prices.get(int(pos.instrument_id))
                if current is None:
                    # Eksik fiyat: pozisyon ham maliyet ile değerlensin.
                    positions_value += qty * avg
                else:
                    current = self._to_decimal(current, Decimal("0"))
                    positions_value += qty * current
                    pnl_unrealized += qty * (current - avg)

            realized_sum = session.execute(
                select(func.coalesce(func.sum(Portfolio.realized_pnl), 0)).where(
                    and_(
                        Portfolio.wallet_id == wallet_id,
                        Portfolio.action == "SELL",
                        Portfolio.realized_pnl.isnot(None),
                    )
                )
            ).scalar_one() or 0

            pnl_realized = self._to_decimal(realized_sum, Decimal("0"))
            pnl_total = pnl_realized + pnl_unrealized
            cash = self._to_decimal(wallet.cash_balance, Decimal("0"))
            alloc = self._to_decimal(wallet.allocated, Decimal("0"))
            total_value = cash + positions_value

            snapshot = WalletSnapshot(
                id=int(wallet.id),
                pool=str(wallet.pool),
                timeframe=str(wallet.timeframe),
                allocated=alloc,
                cash_balance=cash,
                positions_value=positions_value,
                total_value=total_value,
                pnl_realized=pnl_realized,
                pnl_unrealized=pnl_unrealized,
                pnl_total=pnl_total,
                return_pct_twr=Decimal("0"),
            )
        finally:
            self._close(session)

        return snapshot


__all__ = [
    "VALID_POOLS",
    "VALID_TIMEFRAMES",
    "WalletSnapshot",
    "WalletService",
]
