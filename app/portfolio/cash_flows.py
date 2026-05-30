"""Cash flow (para giriş/çıkış) servisi — Faz 3 Batch 2.

Doküman §6.1 ("Bütçe sabit değildir... her hareket cash_flows tablosuna
kaydedilir") referans alınmıştır.

Sorumluluk:
- ``deposit`` / ``withdrawal`` — hesap genelinde para yatırma/çekme.
  ``wallet_id=NULL`` ile kaydedilir (cüzdana özel değil, genel hesaba).
- ``list_flows`` — audit ve UI history için hareketleri listeler.
- ``net_deposited`` — TWR hesabında "yatırılan net sermaye" olarak
  kullanılır (deposit - withdrawal); reallocate kayıtları toplamda
  sıfırlandığı için doğal olarak elenir.

Önemli kurallar:
- ``cash_flows`` satırları **AUDIT** içindir — silinmez (model
  ``cascade`` yok, soft-delete tercih edilir).
- ``flow_type`` enum'u model tarafında string; bu serviste tanımlı
  küme: ``{deposit, withdrawal, allocate, reallocate, trade_buy,
  trade_sell, commission, fee, dividend}``. CHECK constraint
  modelinde yok (esneklik için). Tutarlılığı bu serviste sağlıyoruz.
- ``allocate`` ve ``reallocate`` kayıtlarını **bu modül yazmaz** —
  ``WalletService`` yazar (cüzdan transferi atomik olmalı). Bu servis
  yalnız ``deposit`` ve ``withdrawal`` mutasyonlarını yapar.

Tasarım kuralları:
- ``session_factory`` sözleşmesi Faz 2 ile aynı.
- API **async**.
- Para ``Decimal``; DB'ye ``float`` cast.

Faz 3 Batch 2'de eklendi:
- ``CashFlowService`` (deposit/withdrawal/list/net_deposited).

ui-developer için not:
- ``history_widget.py`` (Faz 3 Batch 4) ``list_flows`` çıktısını kronolojik
  tabloda göstermeli. ``flow_type`` rozetiyle (deposit/withdrawal/
  allocate/reallocate/trade_*) renkli göster.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, func, select


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: Bilinen flow_type değerleri (audit için tutarlılık).
KNOWN_FLOW_TYPES: frozenset[str] = frozenset(
    {
        "deposit",
        "withdrawal",
        "allocate",
        "reallocate",
        "trade_buy",
        "trade_sell",
        "commission",
        "fee",
        "dividend",
    }
)


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class CashFlowService:
    """Hesap düzeyinde para giriş/çıkış audit servisi.

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

    # ------------------------------------------------------------- mutations

    async def deposit(
        self,
        account_id: int,
        amount: Decimal,
        notes: str = "",
    ) -> int:
        """Hesap geneline para yatır.

        - ``account.cash_balance`` += amount
        - ``cash_flows`` satırı INSERT: ``flow_type='deposit'``,
          ``wallet_id=NULL``, ``amount > 0``.

        Returns
        -------
        int
            Yeni ``CashFlow.id``.
        """

        amt = self._to_decimal(amount, Decimal("0"))
        if amt <= 0:
            raise ValueError("deposit amount pozitif olmalı.")

        from app.db.models import CashFlow, UserAccount  # noqa: WPS433

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")

            old_cash = self._to_decimal(account.cash_balance, Decimal("0"))
            account.cash_balance = float(old_cash + amt)

            row = CashFlow(
                account_id=int(account_id),
                wallet_id=None,
                flow_type="deposit",
                amount=float(amt),
                notes=notes or "deposit",
            )
            session.add(row)
            session.flush()
            flow_id = int(row.id)

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "CashFlow deposit: account={}, amount={}, new_cash={}",
            account_id,
            amt,
            old_cash + amt,
        )
        return flow_id

    async def withdrawal(
        self,
        account_id: int,
        amount: Decimal,
        notes: str = "",
    ) -> int:
        """Hesap genelinden para çek.

        - cash_balance >= amount koşulu (yetersizse ``ValueError``).
        - ``account.cash_balance`` -= amount
        - ``cash_flows`` INSERT: ``flow_type='withdrawal'``,
          ``wallet_id=NULL``, ``amount < 0`` (negatif kaydedilir;
          net_deposited toplamı doğrudan hesaplanabilsin diye).
        """

        amt = self._to_decimal(amount, Decimal("0"))
        if amt <= 0:
            raise ValueError("withdrawal amount pozitif olmalı.")

        from app.db.models import CashFlow, UserAccount  # noqa: WPS433

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")

            old_cash = self._to_decimal(account.cash_balance, Decimal("0"))
            if old_cash < amt:
                raise ValueError(
                    f"Hesap nakdi yetersiz: cash={old_cash}, withdrawal={amt}"
                )

            account.cash_balance = float(old_cash - amt)

            row = CashFlow(
                account_id=int(account_id),
                wallet_id=None,
                flow_type="withdrawal",
                amount=float(-amt),  # negatif: çıkış
                notes=notes or "withdrawal",
            )
            session.add(row)
            session.flush()
            flow_id = int(row.id)

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "CashFlow withdrawal: account={}, amount={}, new_cash={}",
            account_id,
            amt,
            old_cash - amt,
        )
        return flow_id

    # ------------------------------------------------------------- read

    async def list_flows(
        self,
        account_id: int,
        wallet_id: Optional[int] = None,
        limit: int = 100,
    ) -> list[dict]:
        """Cash flow'ları ``occurred_at DESC`` sırasıyla listele.

        Parameters
        ----------
        account_id:
            Hedef hesap.
        wallet_id:
            ``None`` ise hesap geneli (hem wallet'a özel hem genel
            kayıtlar dahil). Belirli bir id verilirse yalnız o
            cüzdanın kayıtları.
        limit:
            En son N kayıt.

        Returns
        -------
        list[dict]
            Her satır: ``{id, account_id, wallet_id, flow_type, amount,
            occurred_at, notes}``.
        """

        from app.db.models import CashFlow  # noqa: WPS433

        session = self._session()
        try:
            stmt = (
                select(CashFlow)
                .where(CashFlow.account_id == account_id)
                .order_by(CashFlow.occurred_at.desc(), CashFlow.id.desc())
                .limit(int(limit))
            )
            if wallet_id is not None:
                stmt = stmt.where(CashFlow.wallet_id == int(wallet_id))

            rows = list(session.execute(stmt).scalars().all())
        finally:
            self._close(session)

        result: list[dict] = []
        for r in rows:
            result.append(
                {
                    "id": int(r.id),
                    "account_id": int(r.account_id),
                    "wallet_id": int(r.wallet_id) if r.wallet_id is not None else None,
                    "flow_type": str(r.flow_type) if r.flow_type else None,
                    "amount": self._to_decimal(r.amount, Decimal("0")),
                    "occurred_at": r.occurred_at,
                    "notes": str(r.notes or ""),
                }
            )
        return result

    async def net_deposited(
        self,
        account_id: int,
        wallet_id: Optional[int] = None,
    ) -> Decimal:
        """Net yatırılan sermaye.

        Hesap genelinde: ``Σ deposit - Σ withdrawal``.
        Cüzdan bazında: ``Σ allocate (giriş) - Σ allocate (çıkış)`` +
        reallocate net etkisi. Reallocate kayıtlarında bir cüzdan için
        toplam zaten net sıfır olabilir; bu yüzden ``wallet_id`` verilirse
        ``Σ amount`` (allocate + reallocate dahil) hesaplanır — yani
        cüzdanın "kullanıcı tarafından koyulan net sermayesi".

        Hesap geneli için yalnız ``deposit`` ve ``withdrawal`` flow_type
        kayıtları toplanır (allocate/reallocate hesap geneli için
        nötrdür — sadece cüzdanlar arası akış).

        TWR hesabında "external cash flow" tanımı için kritik —
        yatırılan/çekilen parayı getirinin dışında bırakmak için
        ``BenchmarkService.time_weighted_return`` bu metodu kullanır.
        """

        from app.db.models import CashFlow  # noqa: WPS433

        session = self._session()
        try:
            if wallet_id is None:
                stmt = select(func.coalesce(func.sum(CashFlow.amount), 0)).where(
                    and_(
                        CashFlow.account_id == account_id,
                        CashFlow.flow_type.in_(("deposit", "withdrawal")),
                    )
                )
            else:
                stmt = select(func.coalesce(func.sum(CashFlow.amount), 0)).where(
                    and_(
                        CashFlow.account_id == account_id,
                        CashFlow.wallet_id == int(wallet_id),
                        CashFlow.flow_type.in_(("allocate", "reallocate")),
                    )
                )
            total = session.execute(stmt).scalar_one() or 0
        finally:
            self._close(session)

        return self._to_decimal(total, Decimal("0"))


__all__ = [
    "KNOWN_FLOW_TYPES",
    "CashFlowService",
]
