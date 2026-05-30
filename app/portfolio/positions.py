"""Açık pozisyon (open_positions) ve işlem (portfolio) servisi — Faz 3 Batch 2.

Doküman §6.4 (Portföy ve Bakiye Takibi) ve §6.5 (İşlem Geçmişi) referans
alınmıştır.

Sorumluluk:
- ``apply_buy`` / ``apply_sell`` — kullanıcı veya bot işlemi DB'ye yansır.
  - ``portfolio`` tablosuna INSERT (kalıcı işlem geçmişi).
  - ``open_positions`` tablosunda upsert (weighted-average cost ile).
  - ``wallet.cash_balance`` mutasyonu (price*qty ± commission).
- ``list_open_positions`` — cüzdan bazlı UI satırları (``PositionView``).
- ``list_all_open_positions`` — hesabın tüm cüzdanlarındaki pozisyonlar.

**Cost basis stratejisi: weighted-average (avg cost).**
Her BUY sonrası ortalama maliyet yeniden hesaplanır:
    new_avg = (old_qty * old_avg + new_qty * new_price) / (old_qty + new_qty)
SELL satırlarında avg_cost değişmez (FIFO yerine bu seçildi —
basit, BIST/ABD perakende için yaygın yöntem). FIFO'ya geçmek
istenirse ayrı bir ``cost_basis_method`` parametresiyle bu modül
genişletilebilir; şimdilik tek mod.

Realized P&L formülü (SELL):
    realized_pnl = (sell_price - avg_cost) * sell_qty - commission

Hold-days hesabı:
    hold_days = (sell_time - opened_at).days

``opened_at`` ilk BUY'da yazılır; üzerine BUY gelse bile sıfırlanmaz
(pozisyonun yaşı korunur). SELL ile pozisyon kapanırsa kayıt SİLİNİR
(yeniden BUY yapılırsa ``opened_at`` o günden başlar).

Tasarım kuralları:
- ``session_factory`` sözleşmesi Faz 2 ile aynı.
- API **async**, DB erişimi sync SQLAlchemy 2.0.
- Para ``Decimal``; DB'ye ``float`` cast.
- Cash flow audit kaydı **isteğe bağlı**: ``apply_buy/sell`` cash_flows
  tablosuna ``flow_type='trade_buy'`` veya ``'trade_sell'`` kaydı yazar
  (audit + UI history zenginliği). ``commission`` ayrı satır olarak değil,
  trade amount içine dahil tutulur — kullanıcı tek satırda görmek ister.

trading-executor için not:
- Gerçek emir doldurulduktan sonra trading-executor bu servisi çağırır:
  ``apply_buy(wallet_id, instrument_id, qty, fill_price, commission,
  fill_time, followed_bot=is_bot_pick)``.
- Paper trading aynı imzayı çağırır — fark yok.

ui-developer için not:
- ``portfolio_widget.py`` (Faz 3 Batch 4) ``list_all_open_positions``
  çıktısını grid + sektör pasta grafiği olarak çizecek.
- ``history_widget.py`` ``portfolio`` tablosundan okuyacak (ayrı servis
  metodu eklenmedi — basit select; istenirse ``list_transactions``
  helper'ı eklenebilir).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, select


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class PositionView:
    """Tek bir açık pozisyon — UI grid satırı.

    ``current_price`` yoksa ``market_value``, ``pnl_unrealized``,
    ``pnl_pct``, ``weight_pct`` de ``None`` döner. Caller son fiyatları
    ``data-collector`` veya ``price_history``'den çekip vermelidir.
    """

    instrument_id: int
    ticker: str
    quantity: Decimal
    avg_cost: Decimal
    current_price: Optional[Decimal]
    market_value: Optional[Decimal]
    pnl_unrealized: Optional[Decimal]
    pnl_pct: Optional[Decimal]
    weight_pct: Optional[Decimal]
    sector: Optional[str]


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class PositionService:
    """Açık pozisyon ve işlem yönetimi (weighted-average cost).

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

    @staticmethod
    def _ensure_aware(ts: datetime) -> datetime:
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts

    # ------------------------------------------------------------- buy

    async def apply_buy(
        self,
        wallet_id: int,
        instrument_id: int,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        transaction_at: datetime | None = None,
        followed_bot: bool = False,
        notes: str = "",
        skip_cash_check: bool = False,
    ) -> int:
        """BUY işlemini uygula.

        ``transaction_at`` None ise UTC now varsayılır.

        ``skip_cash_check=True`` ise cüzdan nakdi yetersizliği görmezden
        gelinir (manuel-eşli mod için: kullanıcı gerçek aracı kurumunda
        işlemi yaptı, bot sadece pozisyon TAKİP ediyor).
        """

        qty = self._to_decimal(quantity, Decimal("0"))
        prc = self._to_decimal(price, Decimal("0"))
        comm = self._to_decimal(commission, Decimal("0"))
        if qty <= 0:
            raise ValueError("BUY quantity pozitif olmalı.")
        if prc <= 0:
            raise ValueError("BUY price pozitif olmalı.")
        if comm < 0:
            raise ValueError("commission negatif olamaz.")

        if transaction_at is None:
            transaction_at = datetime.now(tz=timezone.utc)
        tx_at = self._ensure_aware(transaction_at)
        gross = qty * prc
        total_cost = gross + comm

        from app.db.models import (  # noqa: WPS433
            CashFlow,
            OpenPosition,
            Portfolio,
            Wallet,
        )

        session = self._session()
        try:
            wallet = session.get(Wallet, wallet_id)
            if wallet is None:
                raise LookupError(f"Wallet bulunamadı: id={wallet_id}")

            cash = self._to_decimal(wallet.cash_balance, Decimal("0"))
            if cash < total_cost and not skip_cash_check:
                raise ValueError(
                    f"Cüzdan nakdi yetersiz: cash={cash}, "
                    f"gerekli={total_cost} (gross={gross}+comm={comm})"
                )

            # 1) portfolio
            tx = Portfolio(
                wallet_id=int(wallet_id),
                instrument_id=int(instrument_id),
                action="BUY",
                quantity=float(qty),
                price=float(prc),
                total=float(gross),
                commission=float(comm),
                realized_pnl=None,
                hold_days=None,
                followed_bot=bool(followed_bot),
                transaction_at=tx_at,
                notes=notes or None,
            )
            session.add(tx)
            session.flush()
            tx_id = int(tx.id)

            # 2) open_positions upsert (weighted-average)
            pos = session.execute(
                select(OpenPosition).where(
                    and_(
                        OpenPosition.wallet_id == wallet_id,
                        OpenPosition.instrument_id == instrument_id,
                    )
                )
            ).scalar_one_or_none()

            if pos is None:
                pos = OpenPosition(
                    wallet_id=int(wallet_id),
                    instrument_id=int(instrument_id),
                    quantity=float(qty),
                    avg_cost=float(prc),
                    opened_at=tx_at,
                )
                session.add(pos)
            else:
                old_qty = self._to_decimal(pos.quantity, Decimal("0"))
                old_avg = self._to_decimal(pos.avg_cost, Decimal("0"))
                new_qty = old_qty + qty
                if new_qty == 0:
                    # Teorik olarak imkansız (qty>0) ama defansif.
                    pos.quantity = 0.0
                    pos.avg_cost = float(old_avg)
                else:
                    new_avg = (old_qty * old_avg + qty * prc) / new_qty
                    pos.quantity = float(new_qty)
                    pos.avg_cost = float(new_avg)
                # opened_at korunur (pozisyonun yaşı)

            # 3) wallet cash
            # Manuel-eşli modda (skip_cash_check=True) kullanıcı gerçek alımı
            # KENDİ aracı kurum hesabından yaptı; BorsaBot'un sanal cüzdan
            # nakdine dokunmuyoruz (yoksa tahsis edilmemiş cüzdanlar negatif
            # bakiyeye düşüyordu). Sadece pozisyonu takip ederiz.
            if not skip_cash_check:
                wallet.cash_balance = float(cash - total_cost)

            # 4) cash flow audit — manuel modda da log düşelim ki kullanıcı
            # nakit akışını izleyebilsin (sadece bookkeeping, real impact yok).
            note_prefix = "BUY (manuel-eşli, dış cüzdandan)" if skip_cash_check else "BUY"
            session.add(
                CashFlow(
                    account_id=int(wallet.account_id),
                    wallet_id=int(wallet_id),
                    flow_type="trade_buy",
                    amount=float(0 if skip_cash_check else -total_cost),
                    notes=(
                        f"{note_prefix} {qty} @ {prc} (comm={comm}) instr={instrument_id} "
                        f"tx={tx_id}"
                    ),
                )
            )

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "BUY uygulandı: tx={}, wallet={}, instr={}, qty={}, price={}, "
            "comm={}, total_cost={}, followed_bot={}",
            tx_id,
            wallet_id,
            instrument_id,
            qty,
            prc,
            comm,
            total_cost,
            bool(followed_bot),
        )
        return tx_id

    # ------------------------------------------------------------- sell

    async def apply_sell(
        self,
        wallet_id: int,
        instrument_id: int,
        quantity: Decimal,
        price: Decimal,
        commission: Decimal,
        transaction_at: datetime | None = None,
        followed_bot: bool = False,
        notes: str = "",
        skip_position_check: bool = False,
    ) -> tuple[int, Decimal]:
        """SELL işlemini uygula.

        ``skip_position_check=True``: open_positions'ta yeterli adet olmasa
        da işlemi kaydet (manuel-eşli mod için: bot pozisyonu bilmiyor
        olabilir; kullanıcı satışı kendi aracında zaten yaptı).

        Sıralama:
        1. Validasyon (qty>0, price>0, commission>=0).
        2. ``open_positions`` mevcut + qty yeterli mi?
        3. ``realized_pnl = (price - avg_cost) * qty - commission``.
        4. ``hold_days = (tx_at - opened_at).days``.
        5. ``portfolio`` INSERT (action='SELL', realized_pnl, hold_days dolu).
        6. ``open_positions``: qty -= sell_qty; 0'a düşerse SİL.
           avg_cost değişmez (weighted-average mantığı, kalan adetler için).
        7. ``wallet.cash_balance`` += (gross - commission).
        8. ``cash_flows`` INSERT (flow_type='trade_sell', amount>0).

        Returns
        -------
        tuple[int, Decimal]
            ``(portfolio_id, realized_pnl)``.

        Raises
        ------
        ValueError
            qty<=0 / price<=0 / commission<0 / qty fazla.
        LookupError
            Wallet veya pozisyon yoksa.
        """

        qty = self._to_decimal(quantity, Decimal("0"))
        prc = self._to_decimal(price, Decimal("0"))
        comm = self._to_decimal(commission, Decimal("0"))
        if qty <= 0:
            raise ValueError("SELL quantity pozitif olmalı.")
        if prc <= 0:
            raise ValueError("SELL price pozitif olmalı.")
        if comm < 0:
            raise ValueError("commission negatif olamaz.")

        if transaction_at is None:
            transaction_at = datetime.now(tz=timezone.utc)
        tx_at = self._ensure_aware(transaction_at)
        gross = qty * prc
        net_cash_in = gross - comm

        from app.db.models import (  # noqa: WPS433
            CashFlow,
            OpenPosition,
            Portfolio,
            Wallet,
        )

        session = self._session()
        try:
            wallet = session.get(Wallet, wallet_id)
            if wallet is None:
                raise LookupError(f"Wallet bulunamadı: id={wallet_id}")

            pos = session.execute(
                select(OpenPosition).where(
                    and_(
                        OpenPosition.wallet_id == wallet_id,
                        OpenPosition.instrument_id == instrument_id,
                    )
                )
            ).scalar_one_or_none()
            if pos is None and not skip_position_check:
                raise LookupError(
                    f"Açık pozisyon yok: wallet={wallet_id}, "
                    f"instr={instrument_id}"
                )

            if pos is None:
                # skip_position_check: avg_cost bilinmiyor → realized_pnl=0
                old_qty = qty
                avg_cost = prc
            else:
                old_qty = self._to_decimal(pos.quantity, Decimal("0"))
                avg_cost = self._to_decimal(pos.avg_cost, Decimal("0"))
            if qty > old_qty and not skip_position_check:
                raise ValueError(
                    f"Yetersiz pozisyon: mevcut qty={old_qty}, satış={qty}"
                )

            realized = (prc - avg_cost) * qty - comm

            opened_at = pos.opened_at
            if opened_at is not None and opened_at.tzinfo is None:
                opened_at = opened_at.replace(tzinfo=timezone.utc)
            hold_days = None
            if opened_at is not None:
                hold_days = (tx_at - opened_at).days

            tx = Portfolio(
                wallet_id=int(wallet_id),
                instrument_id=int(instrument_id),
                action="SELL",
                quantity=float(qty),
                price=float(prc),
                total=float(gross),
                commission=float(comm),
                realized_pnl=float(realized),
                hold_days=int(hold_days) if hold_days is not None else None,
                followed_bot=bool(followed_bot),
                transaction_at=tx_at,
                notes=notes or None,
            )
            session.add(tx)
            session.flush()
            tx_id = int(tx.id)

            if pos is not None:
                new_qty = old_qty - qty
                if new_qty <= 0:
                    session.delete(pos)
                else:
                    pos.quantity = float(new_qty)
                    # avg_cost aynı kalır

            # Manuel-eşli modda (skip_position_check=True) gerçek satış
            # kullanıcının aracı kurum hesabında yapılır; sanal cüzdan
            # nakdine dokunma (BUY tarafıyla simetrik tutmak için).
            if not skip_position_check:
                wallet.cash_balance = float(
                    self._to_decimal(wallet.cash_balance, Decimal("0")) + net_cash_in
                )

            note_prefix = "SELL (manuel-eşli, dış cüzdana)" if skip_position_check else "SELL"
            session.add(
                CashFlow(
                    account_id=int(wallet.account_id),
                    wallet_id=int(wallet_id),
                    flow_type="trade_sell",
                    amount=float(0 if skip_position_check else net_cash_in),
                    notes=(
                        f"{note_prefix} {qty} @ {prc} (comm={comm}, "
                        f"realized={realized}) instr={instrument_id} tx={tx_id}"
                    ),
                )
            )

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "SELL uygulandı: tx={}, wallet={}, instr={}, qty={}, price={}, "
            "comm={}, realized={}, hold_days={}, followed_bot={}",
            tx_id,
            wallet_id,
            instrument_id,
            qty,
            prc,
            comm,
            realized,
            hold_days,
            bool(followed_bot),
        )
        return tx_id, realized

    # ------------------------------------------------------------- read

    async def list_open_positions(
        self,
        wallet_id: int,
        current_prices: Optional[dict[int, Decimal]] = None,
    ) -> list[PositionView]:
        """Cüzdandaki açık pozisyonları döndür.

        ``current_prices`` verilirse market_value / pnl_unrealized /
        pnl_pct / weight_pct doldurulur. weight_pct, cüzdanın toplam
        positions_value'una göre yüzdedir (nakit hariç).
        """

        from app.db.models import Instrument, OpenPosition  # noqa: WPS433

        cp = current_prices or {}

        session = self._session()
        try:
            stmt = (
                select(
                    OpenPosition.instrument_id,
                    OpenPosition.quantity,
                    OpenPosition.avg_cost,
                    Instrument.ticker,
                    Instrument.sector,
                )
                .join(Instrument, Instrument.id == OpenPosition.instrument_id)
                .where(OpenPosition.wallet_id == wallet_id)
                .order_by(Instrument.ticker.asc())
            )
            rows = session.execute(stmt).all()
        finally:
            self._close(session)

        return self._build_views(rows, cp)

    async def list_all_open_positions(
        self,
        account_id: int,
        current_prices: Optional[dict[int, Decimal]] = None,
    ) -> list[PositionView]:
        """Hesabın **tüm cüzdanlarındaki** açık pozisyonlar."""

        from app.db.models import Instrument, OpenPosition, Wallet  # noqa: WPS433

        cp = current_prices or {}

        session = self._session()
        try:
            stmt = (
                select(
                    OpenPosition.instrument_id,
                    OpenPosition.quantity,
                    OpenPosition.avg_cost,
                    Instrument.ticker,
                    Instrument.sector,
                )
                .join(Wallet, Wallet.id == OpenPosition.wallet_id)
                .join(Instrument, Instrument.id == OpenPosition.instrument_id)
                .where(Wallet.account_id == account_id)
                .order_by(Instrument.ticker.asc())
            )
            rows = session.execute(stmt).all()
        finally:
            self._close(session)

        return self._build_views(rows, cp)

    # ------------------------------------------------------------- private

    def _build_views(
        self,
        rows,
        current_prices: dict[int, Decimal],
    ) -> list[PositionView]:
        """``list_*open_positions`` için ortak view builder."""

        # Önce her satırı normalize et + total market value topla
        normalized: list[dict] = []
        total_market_value = Decimal("0")

        for r in rows:
            instr_id = int(r[0])
            qty = self._to_decimal(r[1], Decimal("0"))
            avg = self._to_decimal(r[2], Decimal("0"))
            ticker = str(r[3] or "")
            sector = str(r[4]) if r[4] is not None else None
            current = current_prices.get(instr_id)
            if current is not None:
                current = self._to_decimal(current, Decimal("0"))
                mv = qty * current
                pnl_u = qty * (current - avg)
                pnl_pct = (
                    ((current - avg) / avg * Decimal("100"))
                    if avg > 0
                    else None
                )
            else:
                mv = None
                pnl_u = None
                pnl_pct = None

            if mv is not None:
                total_market_value += mv

            normalized.append(
                {
                    "instrument_id": instr_id,
                    "ticker": ticker,
                    "quantity": qty,
                    "avg_cost": avg,
                    "current_price": current,
                    "market_value": mv,
                    "pnl_unrealized": pnl_u,
                    "pnl_pct": pnl_pct,
                    "sector": sector,
                }
            )

        views: list[PositionView] = []
        for n in normalized:
            mv = n["market_value"]
            weight = None
            if mv is not None and total_market_value > 0:
                weight = (mv / total_market_value) * Decimal("100")
            views.append(
                PositionView(
                    instrument_id=n["instrument_id"],
                    ticker=n["ticker"],
                    quantity=n["quantity"],
                    avg_cost=n["avg_cost"],
                    current_price=n["current_price"],
                    market_value=mv,
                    pnl_unrealized=n["pnl_unrealized"],
                    pnl_pct=n["pnl_pct"],
                    weight_pct=weight,
                    sector=n["sector"],
                )
            )
        return views


__all__ = [
    "PositionView",
    "PositionService",
]
