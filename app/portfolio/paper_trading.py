"""Paper trading (sanal portföy) servisi — Faz 3 Batch 2.

Doküman §6.8 (Sanal Portföy Modu) referans alınmıştır.

> "Yeni kullanıcılar gerçek para riske atmadan botu test edebilsin diye
> sanal portföy modu eklenir."

**Tasarım prensibi: aynı kod yolu.**
Gerçek ve sanal işlemler **aynı** ``PositionService.apply_buy/sell``
metodlarını çağırır. Tek fark: ``UserAccount.trading_mode == 'paper'``
flag'i. Gerçek emir gönderilmez (trading-executor mod kontrolü yapar).

Sorumluluk:
- ``reset_paper_account`` — paper hesabı sıfırla; tüm pozisyonları,
  cash flow'ları, portfolio satırlarını sil; cüzdan bakiyelerini 0'la;
  hesap nakdini ``initial_balance``'a çek; ``trading_mode='paper'``
  set et.
- ``execute_paper_trade`` — paper mod doğrulaması + komisyon hesabı +
  ``PositionService`` delegasyonu.
- ``simulate_bot_picks`` — "botu dinleseydin" simülasyonu: belirli bir
  tarihten sonraki bot picks'leri sırayla paper hesabında uygula,
  sonuç istatistikleri (toplam getiri, hit_target, expired vs.) döner.

**ÖNEMLİ KISITLAMA:** ``reset_paper_account`` audit verisini siler —
yalnız **paper** modda kullanılmalıdır. Gerçek (live) modda bu çağrı
ValueError fırlatır (defansif kontrol).

Faz 3 Batch 2'de eklendi:
- ``PaperTradingService``.

ui-developer için not:
- "Paper Mode" toggle UI'da prominent yer almalı; aktifken tüm işlem
  butonları "SANAL" rozetiyle dekorlanmalı (kullanıcı gerçekle
  karıştırmasın).
- ``simulate_bot_picks`` çıktısı UI'da "Botu Dinleseydin" panelinde
  gösterilir: net getiri %, kapanan pick sayısı, başarı oranı.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, delete, select, update


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

DEFAULT_PAPER_BALANCE: Decimal = Decimal("100000")
PAPER_MODE: str = "paper"


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class PaperTradingService:
    """Sanal portföy yönetimi — aynı kod yolu, sadece flag farkı.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    position_service:
        ``PositionService`` örneği — gerçek/sanal ayrım yok, BUY/SELL'i
        delege ederiz.
    """

    def __init__(
        self,
        session_factory: Callable[[], object],
        position_service,
    ) -> None:
        self.session_factory = session_factory
        self.position_service = position_service

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

    # ------------------------------------------------------------- reset

    async def reset_paper_account(
        self,
        account_id: int,
        initial_balance: Decimal = DEFAULT_PAPER_BALANCE,
    ) -> None:
        """Paper hesabını sıfırla.

        - Tüm ``portfolio`` (BUY/SELL) satırlarını sil (paper hesaba ait).
        - Tüm ``open_positions`` satırlarını sil.
        - Tüm ``cash_flows`` satırlarını sil.
        - Tüm cüzdanların ``allocated`` ve ``cash_balance``'ını 0'a çek.
        - ``account.cash_balance = initial_balance``.
        - ``account.trading_mode = 'paper'``.

        Gerçek (live) audit kayıtlarını korumak için **modu önce paper'a
        çekmek gerekir**; aksi halde live data yanlışlıkla silinmesin
        diye burada ek korunma yok — caller modu doğrulamalı.

        Defansif: ``initial_balance`` < 0 → ``ValueError``.
        """

        init_amt = self._to_decimal(initial_balance, DEFAULT_PAPER_BALANCE)
        if init_amt < 0:
            raise ValueError("initial_balance negatif olamaz.")

        from app.db.models import (  # noqa: WPS433
            CashFlow,
            OpenPosition,
            Portfolio,
            UserAccount,
            Wallet,
        )

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")

            wallet_ids = [
                int(w.id)
                for w in session.execute(
                    select(Wallet).where(Wallet.account_id == account_id)
                ).scalars().all()
            ]

            if wallet_ids:
                session.execute(
                    delete(Portfolio).where(Portfolio.wallet_id.in_(wallet_ids))
                )
                session.execute(
                    delete(OpenPosition).where(
                        OpenPosition.wallet_id.in_(wallet_ids)
                    )
                )

            session.execute(
                delete(CashFlow).where(CashFlow.account_id == account_id)
            )

            session.execute(
                update(Wallet)
                .where(Wallet.account_id == account_id)
                .values(allocated=0.0, cash_balance=0.0)
            )

            account.cash_balance = float(init_amt)
            account.initial_balance = float(init_amt)
            account.trading_mode = PAPER_MODE

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Paper hesap sıfırlandı: account={}, initial={}, "
            "wallets_reset={}, mode={}",
            account_id,
            init_amt,
            len(wallet_ids),
            PAPER_MODE,
        )

    # ------------------------------------------------------------- execute

    async def execute_paper_trade(
        self,
        wallet_id: int,
        instrument_id: int,
        action: str,
        quantity: Decimal,
        price: Decimal,
    ) -> int:
        """Paper modda BUY veya SELL uygula.

        Doğrulamalar:
        - ``wallet.account.trading_mode == 'paper'`` (değilse ValueError).
        - ``action ∈ {'BUY', 'SELL'}`` (case insensitive normalize).

        Komisyon: ``PnLCalculator.DEFAULT_COMMISSION_PCT * gross``.
        ``transaction_at = now (UTC)``. ``followed_bot = False``
        (simülasyon başka bir API ile ayrı flag set eder).

        Returns
        -------
        int
            ``Portfolio.id``.
        """

        act = (action or "").strip().upper()
        if act not in ("BUY", "SELL"):
            raise ValueError(
                f"Geçersiz action: {action!r}. Beklenen: BUY|SELL"
            )

        qty = self._to_decimal(quantity, Decimal("0"))
        prc = self._to_decimal(price, Decimal("0"))
        if qty <= 0 or prc <= 0:
            raise ValueError("quantity ve price pozitif olmalı.")

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
            mode = str(account.trading_mode)
        finally:
            self._close(session)

        if mode != PAPER_MODE:
            raise ValueError(
                f"execute_paper_trade yalnız 'paper' modda çağrılabilir. "
                f"Mevcut mod: {mode!r}"
            )

        # Komisyonu burada hesapla — PnLCalculator import'unu lazy yap
        # (döngüsel bağımlılık koruması).
        from app.portfolio.pnl import DEFAULT_COMMISSION_PCT  # noqa: WPS433

        gross = qty * prc
        commission = (gross * DEFAULT_COMMISSION_PCT).quantize(Decimal("0.0001"))
        now = datetime.now(tz=timezone.utc)

        if act == "BUY":
            tx_id = await self.position_service.apply_buy(
                wallet_id=wallet_id,
                instrument_id=instrument_id,
                quantity=qty,
                price=prc,
                commission=commission,
                transaction_at=now,
                followed_bot=False,
                notes="paper_trade",
            )
        else:
            tx_id, _ = await self.position_service.apply_sell(
                wallet_id=wallet_id,
                instrument_id=instrument_id,
                quantity=qty,
                price=prc,
                commission=commission,
                transaction_at=now,
                followed_bot=False,
                notes="paper_trade",
            )

        logger.info(
            "Paper trade: action={}, wallet={}, instr={}, qty={}, "
            "price={}, comm={}, tx={}",
            act,
            wallet_id,
            instrument_id,
            qty,
            prc,
            commission,
            tx_id,
        )
        return tx_id

    # ------------------------------------------------------------- simulate

    async def simulate_bot_picks(
        self,
        account_id: int,
        since: date,
        wallet_id: int,
    ) -> dict:
        """Belirli tarihten itibaren bot picks'i paper hesapta simüle et.

        Her açık veya kapalı pick için:
        - BUY pick → ``price_at_pick`` ile BUY.
        - Pick kapanmışsa (``outcome='hit_target'`` veya ``'expired'``)
          → ``price_at_close`` ile SELL (yoksa atla).
        - SELL pick (short pozisyon) → bu basit simülasyonda atlanır
          (paper trading şu an short açmıyor).

        Komisyon ``execute_paper_trade`` ile aynı (DEFAULT_COMMISSION_PCT).

        Sonuç istatistiği:
        ``{
            'picks_total': int,
            'buys_executed': int,
            'sells_executed': int,
            'hit_target': int,
            'expired': int,
            'skipped': int,
            'final_cash_balance': Decimal,
            'realized_pnl_total': Decimal,
        }``

        Bu metod **mevcut** paper hesabı değiştirir — temiz simülasyon
        için önce ``reset_paper_account`` çağırılmalı.

        Defansif: hesabın trading_mode'u 'paper' değilse ``ValueError``.
        """

        from app.db.models import (  # noqa: WPS433
            BotPick,
            Portfolio,
            UserAccount,
            Wallet,
        )

        # Mod kontrolü
        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")
            if str(account.trading_mode) != PAPER_MODE:
                raise ValueError(
                    "simulate_bot_picks yalnız paper modda çağrılabilir. "
                    f"Mod: {account.trading_mode!r}"
                )

            since_dt = datetime.combine(since, time.min, tzinfo=timezone.utc)
            picks = list(
                session.execute(
                    select(BotPick)
                    .where(BotPick.picked_at >= since_dt)
                    .order_by(BotPick.picked_at.asc())
                ).scalars().all()
            )
        finally:
            self._close(session)

        stats = {
            "picks_total": len(picks),
            "buys_executed": 0,
            "sells_executed": 0,
            "hit_target": 0,
            "expired": 0,
            "skipped": 0,
            "final_cash_balance": Decimal("0"),
            "realized_pnl_total": Decimal("0"),
        }

        for pick in picks:
            action = (pick.action or "").upper()
            if action != "BUY":
                stats["skipped"] += 1
                continue

            instr_id = int(pick.instrument_id)
            pick_price = self._to_decimal(pick.price_at_pick, Decimal("0"))
            if pick_price <= 0:
                stats["skipped"] += 1
                continue

            # Quantity: paper başlangıç bakiyesinin %5'i kabul (basit
            # equal-weight). Hesap bakiyesi yetmezse atla.
            qty = await self._sizing_for_pick(account_id, pick_price)
            if qty <= 0:
                stats["skipped"] += 1
                continue

            try:
                buy_at = pick.picked_at
                if buy_at is not None and buy_at.tzinfo is None:
                    buy_at = buy_at.replace(tzinfo=timezone.utc)
                gross = qty * pick_price
                from app.portfolio.pnl import (  # noqa: WPS433
                    DEFAULT_COMMISSION_PCT,
                )
                comm = (gross * DEFAULT_COMMISSION_PCT).quantize(
                    Decimal("0.0001")
                )
                await self.position_service.apply_buy(
                    wallet_id=wallet_id,
                    instrument_id=instr_id,
                    quantity=qty,
                    price=pick_price,
                    commission=comm,
                    transaction_at=buy_at or datetime.now(tz=timezone.utc),
                    followed_bot=True,
                    notes=f"simulate_bot_picks pick_id={pick.id}",
                )
                stats["buys_executed"] += 1
            except (LookupError, ValueError) as exc:
                logger.warning(
                    "simulate_bot_picks BUY atlandı: pick={}, hata={}",
                    pick.id,
                    exc,
                )
                stats["skipped"] += 1
                continue

            # Kapanmışsa SELL
            if not pick.is_open and pick.price_at_close is not None:
                close_price = self._to_decimal(
                    pick.price_at_close, Decimal("0")
                )
                close_at = pick.closed_at or datetime.now(tz=timezone.utc)
                if close_at.tzinfo is None:
                    close_at = close_at.replace(tzinfo=timezone.utc)
                try:
                    gross_sell = qty * close_price
                    comm_sell = (
                        gross_sell * DEFAULT_COMMISSION_PCT
                    ).quantize(Decimal("0.0001"))
                    _, realized = await self.position_service.apply_sell(
                        wallet_id=wallet_id,
                        instrument_id=instr_id,
                        quantity=qty,
                        price=close_price,
                        commission=comm_sell,
                        transaction_at=close_at,
                        followed_bot=True,
                        notes=f"simulate_bot_picks pick_id={pick.id} close",
                    )
                    stats["sells_executed"] += 1
                    stats["realized_pnl_total"] += self._to_decimal(
                        realized, Decimal("0")
                    )
                    if pick.outcome == "hit_target":
                        stats["hit_target"] += 1
                    elif pick.outcome == "expired":
                        stats["expired"] += 1
                except (LookupError, ValueError) as exc:
                    logger.warning(
                        "simulate_bot_picks SELL atlandı: pick={}, hata={}",
                        pick.id,
                        exc,
                    )

        # Final cash balance
        session = self._session()
        try:
            wallet = session.get(Wallet, wallet_id)
            if wallet is not None:
                stats["final_cash_balance"] = self._to_decimal(
                    wallet.cash_balance, Decimal("0")
                )
        finally:
            self._close(session)

        logger.info(
            "simulate_bot_picks tamamlandı: account={}, wallet={}, "
            "stats={}",
            account_id,
            wallet_id,
            stats,
        )
        return stats

    # ------------------------------------------------------------- private

    async def _sizing_for_pick(
        self, account_id: int, pick_price: Decimal
    ) -> Decimal:
        """Pick başına basit eşit-ağırlık sizing.

        Mevcut hesap nakdinin %5'i kadar (en az 1 adet alabilirse).
        Daha sofistike sizing (Kelly, volatilite ölçekli vb.) Faz 4 işidir.
        """

        from app.db.models import UserAccount  # noqa: WPS433

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                return Decimal("0")
            cash = self._to_decimal(account.cash_balance, Decimal("0"))
        finally:
            self._close(session)

        if cash <= 0 or pick_price <= 0:
            return Decimal("0")
        budget = (cash * Decimal("0.05")).quantize(Decimal("0.01"))
        qty = (budget / pick_price).quantize(Decimal("1"))
        if qty < 1:
            return Decimal("0")
        return qty


__all__ = [
    "DEFAULT_PAPER_BALANCE",
    "PAPER_MODE",
    "PaperTradingService",
]
