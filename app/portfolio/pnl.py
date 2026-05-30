"""Komisyon / vergi / kur etkili P&L hesaplayıcı — Faz 3 Batch 2.

Doküman §6.6 (Komisyon, Vergi ve Kur Etkisi) referans alınmıştır.

> "Kazandım sandığın şey aslında masraf veya kur etkisi olabilir."

Bu modül **bilgilendirme amaçlıdır.** Vergi hesabı UI'da kullanıcıya
gösterilirken **mali müşavir uyarısı** ile birlikte gösterilmelidir
(şablon: ``TAX_DISCLAIMER`` sabiti).

Sorumluluk:
- ``PnLBreakdown`` — bir işlemin / pozisyonun çok-eksenli getiri özeti:
  yerel para bazlı hisse getirisi (``instrument_pnl_local``), kur etkisi
  (``fx_pnl``), TL bazlı toplam, komisyon ve tahmini vergi.
- ``closed_trade_pnl(portfolio_id)`` — tek bir kapanan SELL satırı için
  detay kırılımı.
- ``position_pnl(wallet_id, instr_id, current_price, current_fx)`` —
  açık pozisyonun anlık unrealized kırılımı.
- ``wallet_total_pnl(...)`` — cüzdan bazında toplam (realized +
  unrealized) kırılım.

**Kur etkisi tanımı (önemli):**
ABD hissesi (currency='USD') için:
    instrument_pnl_local = (sell_price - buy_price) * qty           [USD]
    instrument_pnl_try   = instrument_pnl_local * fx_at_sell        [TRY]
    fx_pnl               = (fx_at_sell - fx_at_buy) * buy_price * qty
                                                                    [TRY]
    total_pnl_try        = instrument_pnl_try + fx_pnl

TRY hissesi (BIST) için:
    instrument_pnl_local = (sell_price - buy_price) * qty           [TRY]
    fx_pnl               = 0
    total_pnl_try        = instrument_pnl_local

Sadeleştirme: kur kazancı yalnız yabancı para enstrümanlar için
hesaplanır. ``fx_rates`` tablosunda 'USDTRY' pair'i bulunamazsa
varsayılan 1.0 (degraded mode) kullanılır.

Komisyon: ``portfolio.commission`` kolonundan okunur (apply_buy /
apply_sell tarafından doldurulur). Açık pozisyonun anlık unrealized
P&L'ında "fiili" komisyon yok (henüz satılmadı) — placeholder olarak
``DEFAULT_COMMISSION_PCT * (qty * current_price)`` ile tahmini bir
"çıkış komisyonu" gösterilir (UI'da "tahmini" rozetiyle).

Vergi: Türkiye bireysel BIST için stopaj genelde 0; ABD temettüsünde %15
stopaj örnektir. Sermaye kazancı vergisi BIST yerli hissede genellikle
yok (uzun vadeli istisna), ABD'de varsayılan %0. **Hepsi indikatif** —
kullanıcı kendi mali müşavirine danışmalı.

Faz 3 Batch 2'de eklendi:
- ``PnLBreakdown`` dataclass'ı.
- ``PnLCalculator`` (fx_rate, closed_trade_pnl, position_pnl,
  wallet_total_pnl).

ui-developer için not:
- ``portfolio_widget.py`` her pozisyon satırının yanında
  ``instrument_pnl_try`` ve ``fx_pnl``'i **AYRI** sütunda göstermeli
  (kullanıcı net hisse getirisini kur etkisinden ayırt edebilsin).
- Tahmini vergi göstergesi her zaman küçük "i" tooltip ile mali müşavir
  uyarısı vermeli (``TAX_DISCLAIMER`` sabitini UI çağırabilir).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import select


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: Komisyon oranı (.env'de yüzde olarak %0.2 → 0.002 oran).
#:
#: ``.env`` ``DEFAULT_COMMISSION_PCT`` yüzde cinsinden yazılmış (örn. 0.2);
#: hesaplamalarda **oran** (0.002) gerektiği için 100'e bölünür.
def _read_commission_pct_from_env() -> Decimal:
    raw = os.getenv("DEFAULT_COMMISSION_PCT", "0.2")
    try:
        pct = Decimal(str(raw).strip())
    except Exception:
        pct = Decimal("0.2")
    return (pct / Decimal("100")).quantize(Decimal("0.000001"))


#: Varsayılan komisyon oranı (oran biçiminde, ör. 0.002 = %0.2).
DEFAULT_COMMISSION_PCT: Decimal = _read_commission_pct_from_env()

#: BIST yerli hisse genel sermaye kazancı stopaj oranı — placeholder.
BIST_TAX_PCT: Decimal = Decimal("0.0")

#: ABD temettü stopaj örneği — sermaye kazancı için değil.
US_DIVIDEND_TAX_PCT: Decimal = Decimal("0.15")

#: USDTRY pair adı — fx_rates tablosunda kullanılır.
DEFAULT_FX_PAIR: str = "USDTRY"

#: Kullanıcıya gösterilmesi gereken yasal uyarı.
TAX_DISCLAIMER: str = (
    "Vergi tahmini bilgi amaçlıdır; kesin durum için mali müşavirinize "
    "danışınız."
)


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class PnLBreakdown:
    """Çok-eksenli P&L özeti.

    ``net_pnl_try = total_pnl_try - tax_estimate`` (komisyon zaten
    ``total_pnl_try``'a dahil edilmiş çünkü realized formülü komisyonu
    düşer; ``commission_total`` bilgi amaçlı ayrı tutulur).
    """

    instrument_pnl_local: Decimal
    fx_pnl: Decimal
    total_pnl_try: Decimal
    commission_total: Decimal
    tax_estimate: Decimal
    net_pnl_try: Decimal


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class PnLCalculator:
    """Komisyon / vergi / kur dahil net P&L hesaplayıcı.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    """

    DEFAULT_COMMISSION_PCT: Decimal = DEFAULT_COMMISSION_PCT
    BIST_TAX_PCT: Decimal = BIST_TAX_PCT
    US_DIVIDEND_TAX_PCT: Decimal = US_DIVIDEND_TAX_PCT

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
    def _is_foreign_currency(currency: Optional[str]) -> bool:
        if currency is None:
            return False
        return currency.strip().upper() not in ("TRY", "TL", "")

    # ------------------------------------------------------------- fx

    async def get_fx_rate(
        self,
        pair: str,
        at: Optional[datetime] = None,
    ) -> Decimal:
        """En yakın FX kuru.

        ``pair`` örn. 'USDTRY'. ``at=None`` ise en son kayıt; aksi halde
        verilen zamandan ``timestamp <= at`` filtresiyle en son kayıt.
        Yoksa ``Decimal('1')`` (degraded fallback) — caller log üzerinden
        farkındalık sağlar.
        """

        clean_pair = (pair or "").strip().upper() or DEFAULT_FX_PAIR
        # TRY-TRY → 1.0
        if clean_pair in ("TRYTRY", "TLTL"):
            return Decimal("1")

        from app.db.models import FxRate  # noqa: WPS433

        session = self._session()
        try:
            stmt = (
                select(FxRate.rate, FxRate.timestamp)
                .where(FxRate.pair == clean_pair)
                .order_by(FxRate.timestamp.desc())
                .limit(1)
            )
            if at is not None:
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
                stmt = (
                    select(FxRate.rate, FxRate.timestamp)
                    .where(FxRate.pair == clean_pair)
                    .where(FxRate.timestamp <= at)
                    .order_by(FxRate.timestamp.desc())
                    .limit(1)
                )
            row = session.execute(stmt).first()
        finally:
            self._close(session)

        if row is None or row[0] is None:
            logger.warning(
                "FX kuru bulunamadı: pair={}, at={} → fallback 1.0 kullanılıyor.",
                clean_pair,
                at,
            )
            return Decimal("1")
        return self._to_decimal(row[0], Decimal("1"))

    # ------------------------------------------------------------- closed trade

    async def closed_trade_pnl(self, portfolio_id: int) -> PnLBreakdown:
        """Tek bir kapanan SELL satırının detaylı P&L kırılımı.

        ``portfolio.realized_pnl`` zaten komisyonu düşmüş olarak gelir;
        kur etkisi ayrı hesaplanır.

        Kur etkisinin hesaplanabilmesi için **eşleşen BUY işleminin**
        ortalama maliyetine ve o günkü kurun bilinmesine ihtiyaç var.
        Sadeleştirme: weighted-average cost stratejisinde "alış kuru"
        olarak SELL anından **önceki en yakın BUY'un transaction_at**
        tarihindeki kuru kullanırız (yaklaşık değer). Daha hassas
        per-lot tracking ileri faz işidir.
        """

        from app.db.models import Instrument, Portfolio  # noqa: WPS433

        session = self._session()
        try:
            sell = session.get(Portfolio, portfolio_id)
            if sell is None:
                raise LookupError(f"Portfolio bulunamadı: id={portfolio_id}")
            if str(sell.action) != "SELL":
                raise ValueError(
                    f"closed_trade_pnl yalnız SELL için: id={portfolio_id} "
                    f"action={sell.action}"
                )

            instr = session.get(Instrument, int(sell.instrument_id))
            currency = str(instr.currency) if instr and instr.currency else "TRY"

            sell_qty = self._to_decimal(sell.quantity, Decimal("0"))
            sell_price = self._to_decimal(sell.price, Decimal("0"))
            commission = self._to_decimal(sell.commission, Decimal("0"))
            realized_local = self._to_decimal(sell.realized_pnl, Decimal("0"))
            sell_time = sell.transaction_at

            # En yakın önceki BUY (kur etkisi için referans)
            buy_stmt = (
                select(Portfolio.price, Portfolio.transaction_at)
                .where(Portfolio.wallet_id == int(sell.wallet_id))
                .where(Portfolio.instrument_id == int(sell.instrument_id))
                .where(Portfolio.action == "BUY")
                .where(Portfolio.transaction_at <= sell_time)
                .order_by(Portfolio.transaction_at.desc())
                .limit(1)
            )
            buy_row = session.execute(buy_stmt).first()
        finally:
            self._close(session)

        is_fx = self._is_foreign_currency(currency)
        if is_fx and buy_row is not None and sell_time is not None:
            buy_price = self._to_decimal(buy_row[0], Decimal("0"))
            buy_time = buy_row[1]
            fx_at_buy = await self.get_fx_rate(DEFAULT_FX_PAIR, buy_time)
            fx_at_sell = await self.get_fx_rate(DEFAULT_FX_PAIR, sell_time)
            instrument_pnl_try = realized_local * fx_at_sell
            fx_pnl = (fx_at_sell - fx_at_buy) * buy_price * sell_qty
            total_pnl_try = instrument_pnl_try + fx_pnl
        else:
            fx_pnl = Decimal("0")
            total_pnl_try = realized_local

        tax_estimate = self._estimate_tax(currency, total_pnl_try)
        net_pnl_try = total_pnl_try - tax_estimate

        return PnLBreakdown(
            instrument_pnl_local=realized_local,
            fx_pnl=fx_pnl,
            total_pnl_try=total_pnl_try,
            commission_total=commission,
            tax_estimate=tax_estimate,
            net_pnl_try=net_pnl_try,
        )

    # ------------------------------------------------------------- position

    async def position_pnl(
        self,
        wallet_id: int,
        instrument_id: int,
        current_price: Decimal,
        current_fx: Optional[Decimal] = None,
    ) -> PnLBreakdown:
        """Açık pozisyonun anlık unrealized P&L kırılımı.

        ``current_fx`` verilmezse ve enstrüman yabancı para ise
        ``get_fx_rate(DEFAULT_FX_PAIR)`` ile en güncel kur çekilir.

        Tahmini çıkış komisyonu = ``DEFAULT_COMMISSION_PCT *
        (qty * current_price)`` — sadece bilgi amaçlı total'a dahil
        edilmez; ``commission_total`` alanında gösterilir.
        """

        from app.db.models import Instrument, OpenPosition, Portfolio  # noqa: WPS433

        session = self._session()
        try:
            pos = session.execute(
                select(OpenPosition).where(
                    (OpenPosition.wallet_id == wallet_id)
                    & (OpenPosition.instrument_id == instrument_id)
                )
            ).scalar_one_or_none()
            if pos is None:
                raise LookupError(
                    f"Açık pozisyon yok: wallet={wallet_id}, "
                    f"instr={instrument_id}"
                )

            instr = session.get(Instrument, int(instrument_id))
            currency = str(instr.currency) if instr and instr.currency else "TRY"

            qty = self._to_decimal(pos.quantity, Decimal("0"))
            avg = self._to_decimal(pos.avg_cost, Decimal("0"))
            opened_at = pos.opened_at

            # Bu pozisyona ait BUY'lardan en eski olanın transaction_at'i ile
            # kur etkisinin başlangıç noktasını alalım.
            first_buy = session.execute(
                select(Portfolio.transaction_at)
                .where(Portfolio.wallet_id == wallet_id)
                .where(Portfolio.instrument_id == instrument_id)
                .where(Portfolio.action == "BUY")
                .order_by(Portfolio.transaction_at.asc())
                .limit(1)
            ).scalar_one_or_none()

            buy_anchor = first_buy or opened_at
        finally:
            self._close(session)

        curr = self._to_decimal(current_price, Decimal("0"))
        instrument_pnl_local = (curr - avg) * qty

        is_fx = self._is_foreign_currency(currency)
        if is_fx:
            if current_fx is None:
                current_fx = await self.get_fx_rate(DEFAULT_FX_PAIR)
            fx_now = self._to_decimal(current_fx, Decimal("1"))
            fx_at_buy = await self.get_fx_rate(DEFAULT_FX_PAIR, buy_anchor)
            instrument_pnl_try = instrument_pnl_local * fx_now
            fx_pnl = (fx_now - fx_at_buy) * avg * qty
            total_pnl_try = instrument_pnl_try + fx_pnl
        else:
            fx_pnl = Decimal("0")
            total_pnl_try = instrument_pnl_local

        est_commission = (
            self.DEFAULT_COMMISSION_PCT * (qty * curr)
        ).quantize(Decimal("0.0001"))
        tax_estimate = self._estimate_tax(currency, total_pnl_try)
        net_pnl_try = total_pnl_try - tax_estimate

        return PnLBreakdown(
            instrument_pnl_local=instrument_pnl_local,
            fx_pnl=fx_pnl,
            total_pnl_try=total_pnl_try,
            commission_total=est_commission,
            tax_estimate=tax_estimate,
            net_pnl_try=net_pnl_try,
        )

    # ------------------------------------------------------------- wallet total

    async def wallet_total_pnl(
        self,
        wallet_id: int,
        current_prices: dict[int, Decimal],
        current_fx: Optional[dict[str, Decimal]] = None,
    ) -> PnLBreakdown:
        """Cüzdan toplam P&L kırılımı.

        Hesaplama yöntemi:
        - Realized: ``Σ closed_trade_pnl(portfolio_id)`` (son N SELL
          satırı için). Performans gerekçesiyle burada yalnız toplam
          ``Σ realized_pnl`` ve ``Σ commission`` agrege edilir;
          per-trade kur etkisi yaklaşımı atlanır (uzun zincirde
          maliyetli). Detay isteyen UI per-trade çağırmalı.
        - Unrealized: ``Σ position_pnl(...)`` her açık pozisyon için.

        Bu yüzden ``fx_pnl`` yalnız **unrealized** kısımdan gelir;
        realized kısım yerel para bazında özetlenir (instrument_pnl_local
        kolonu altında, TL bazlı toplam birebir aynıdır eğer tüm pozisyonlar
        TL ise). Karma para portföyü için kullanıcıya UI'da uyarı vermek
        iyi olur.
        """

        from app.db.models import OpenPosition, Portfolio  # noqa: WPS433

        session = self._session()
        try:
            # Realized toplamları
            realized_rows = session.execute(
                select(Portfolio.realized_pnl, Portfolio.commission).where(
                    (Portfolio.wallet_id == wallet_id)
                    & (Portfolio.action == "SELL")
                )
            ).all()

            # Açık pozisyonlar (per-instrument)
            position_rows = session.execute(
                select(OpenPosition.instrument_id).where(
                    OpenPosition.wallet_id == wallet_id
                )
            ).scalars().all()
            open_instrument_ids = [int(i) for i in position_rows]
        finally:
            self._close(session)

        total_realized_local = Decimal("0")
        total_commission = Decimal("0")
        for rp, cm in realized_rows:
            total_realized_local += self._to_decimal(rp, Decimal("0"))
            total_commission += self._to_decimal(cm, Decimal("0"))

        fx_pnl_total = Decimal("0")
        instr_pnl_local_total = total_realized_local
        total_pnl_try = total_realized_local  # TL hisseler için identitiy

        for instr_id in open_instrument_ids:
            current_price = current_prices.get(instr_id)
            if current_price is None:
                continue
            curr_fx = None
            if current_fx is not None:
                curr_fx = current_fx.get(DEFAULT_FX_PAIR)
            try:
                br = await self.position_pnl(
                    wallet_id=wallet_id,
                    instrument_id=instr_id,
                    current_price=self._to_decimal(current_price, Decimal("0")),
                    current_fx=curr_fx,
                )
            except LookupError:
                continue
            instr_pnl_local_total += br.instrument_pnl_local
            fx_pnl_total += br.fx_pnl
            total_pnl_try += br.total_pnl_try
            total_commission += br.commission_total  # tahmini çıkış komisyonu

        tax_estimate = total_pnl_try * (
            self.BIST_TAX_PCT if total_pnl_try > 0 else Decimal("0")
        )
        net_pnl_try = total_pnl_try - tax_estimate

        return PnLBreakdown(
            instrument_pnl_local=instr_pnl_local_total,
            fx_pnl=fx_pnl_total,
            total_pnl_try=total_pnl_try,
            commission_total=total_commission,
            tax_estimate=tax_estimate,
            net_pnl_try=net_pnl_try,
        )

    # ------------------------------------------------------------- tax

    def _estimate_tax(self, currency: Optional[str], pnl_try: Decimal) -> Decimal:
        """İndikatif vergi tahmini.

        - TL hisse (BIST): ``BIST_TAX_PCT * max(pnl, 0)``. Stopaj
          bireysel yerli hissede genelde 0.
        - USD hisse: sermaye kazancı vergisi varsayılan 0 (bilgi amaçlı).
          ``US_DIVIDEND_TAX_PCT`` yalnız temettü için — burada
          kullanılmaz; UI temettü için ayrı çağırır.
        """

        if pnl_try <= 0:
            return Decimal("0")
        cur = (currency or "TRY").strip().upper()
        if cur in ("TRY", "TL"):
            return (pnl_try * self.BIST_TAX_PCT).quantize(Decimal("0.0001"))
        # USD ve diğer dövizler için sermaye kazancı placeholder.
        return Decimal("0")


__all__ = [
    "DEFAULT_COMMISSION_PCT",
    "BIST_TAX_PCT",
    "US_DIVIDEND_TAX_PCT",
    "DEFAULT_FX_PAIR",
    "TAX_DISCLAIMER",
    "PnLBreakdown",
    "PnLCalculator",
]
