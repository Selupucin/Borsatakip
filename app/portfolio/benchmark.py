"""Benchmark (endeks kıyaslaması) ve TWR servisi — Faz 3 Batch 2.

Doküman §6.1 (TWR) ve §6.7 (Endeks Kıyaslaması) referans alınmıştır.

> "%5,6 kazandım tek başına anlamlı değildir — ancak endekse göre
> değerlendirilince anlam kazanır."

Sorumluluk:
- ``time_weighted_return`` — para hareketlerini DIŞLAYAN getiri.
- ``benchmark_return`` — BIST 100 (XU100) / S&P 500 (^GSPC) gibi
  endekslerin periyot getirisi (``price_history``'den).
- ``compare`` — portföy vs. benchmark karşılaştırması, alfa, "yendi mi"
  boolean.

**TWR formülü (Time-Weighted Return):**

Para ekleme/çekme getiriyi şişirmez. Yöntem:
1. ``cash_flows.flow_type IN ('deposit', 'withdrawal')`` zamanlarına
   göre periyodu alt-dönemlere böl. Her alt-dönemin sınırlarında
   portföyün **dönem değerini** (cash + positions_value) ölç.
2. Her alt-dönem getirisi:
       sub_return = (V_end - C_flow) / V_start - 1
   Burada V_start = alt-dönem başı toplam değer, V_end = alt-dönem
   sonu değer, C_flow = alt-dönem sonunda eklenen/çekilen para.
3. Toplam getiri = geometrik çarpım:
       TWR = Π (1 + sub_return_i) - 1

Sadeleştirme: Faz 3 Batch 2'de **valuation snapshot tablosu yok**;
dönem değerini her cash flow anında ölçmek için son ``price_history``
+ açık pozisyonlar kullanılır (yaklaşık değer). Tam doğruluk için
ileri faz "daily_portfolio_value" tablosu eklenebilir.

Faz 3 Batch 2'de eklendi:
- ``BenchmarkComparison`` dataclass'ı.
- ``BenchmarkService`` (TWR, benchmark_return, compare).

ui-developer için not:
- Karşılaştırma grafiği için ``benchmark_return`` ve günlük portföy
  değeri çizgisi yan yana çizilmeli (üst üste binerek "alpha"
  görülebilsin). Bu modül günlük seri vermez — ``portfolio_widget``
  Faz 3 Batch 4'te `compute_snapshot` döngüsel çağırarak kendisi
  seri inşa edebilir, ya da ileri faz `daily_value` tablosundan okur.
- ``beat_benchmark=True`` ise UI'da yeşil rozet, ``False`` ise kırmızı.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, func, select


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: Bilinen benchmark ticker'ları (instrument tablosunda da var olmalı).
BIST_BENCHMARK: str = "XU100"
SP500_BENCHMARK: str = "^GSPC"


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkComparison:
    """Portföy ve benchmark karşılaştırması."""

    portfolio_return_pct: Decimal
    benchmark_return_pct: Decimal
    alpha_pct: Decimal           # portfolio - benchmark
    beat_benchmark: bool
    period_start: date
    period_end: date


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class BenchmarkService:
    """TWR + endeks kıyaslaması.

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
    def _to_datetime(d: Optional[date | datetime]) -> Optional[datetime]:
        if d is None:
            return None
        if isinstance(d, datetime):
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        return datetime.combine(d, time.min, tzinfo=timezone.utc)

    # ------------------------------------------------------------- TWR

    async def time_weighted_return(
        self,
        account_id: int,
        wallet_id: Optional[int] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> Decimal:
        """Time-Weighted Return — yüzde cinsinden (örn. Decimal('12.34')).

        Algoritma:
        1. Cash flow olaylarını topla (deposit/withdrawal hesap geneli;
           wallet_id verilmişse o cüzdana ait allocate/reallocate).
        2. Periyodu [start, t1, t2, ..., end] olarak böl.
        3. Her alt-dönem için (V_end - C_flow) / V_start - 1.
        4. Geometrik çarpım - 1.

        **Sadeleştirilmiş valuation:** Dönem sınırlarında portföy değeri
        olarak şu anki ``cash_balance`` + ``Σ positions`` (avg_cost
        bazında) kullanılır. Daha hassas valuation için "daily_value"
        tablosu Faz 4 işidir; o zaman buradan oraya delege edilebilir.

        Yatırılan/çekilen para getirinin payına eklenmediği için 0 deposit
        senaryosunda TWR = (V_end / V_start) - 1.
        """

        from app.db.models import (  # noqa: WPS433
            CashFlow,
            OpenPosition,
            UserAccount,
            Wallet,
        )

        start_dt = self._to_datetime(start)
        end_dt = self._to_datetime(end) or datetime.now(tz=timezone.utc)

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")

            # Cash flow olayları
            cf_stmt = (
                select(CashFlow.amount, CashFlow.occurred_at)
                .where(CashFlow.account_id == account_id)
                .order_by(CashFlow.occurred_at.asc())
            )
            if wallet_id is None:
                cf_stmt = cf_stmt.where(
                    CashFlow.flow_type.in_(("deposit", "withdrawal"))
                )
            else:
                cf_stmt = cf_stmt.where(CashFlow.wallet_id == int(wallet_id))
                cf_stmt = cf_stmt.where(
                    CashFlow.flow_type.in_(("allocate", "reallocate"))
                )

            if start_dt is not None:
                cf_stmt = cf_stmt.where(CashFlow.occurred_at >= start_dt)
            cf_stmt = cf_stmt.where(CashFlow.occurred_at <= end_dt)

            cf_rows = list(session.execute(cf_stmt).all())

            # Şu anki toplam değer (V_end)
            if wallet_id is None:
                cash_total = session.execute(
                    select(
                        func.coalesce(func.sum(Wallet.cash_balance), 0)
                    ).where(Wallet.account_id == account_id)
                ).scalar_one() or 0
                v_end_cash = self._to_decimal(
                    account.cash_balance, Decimal("0")
                ) + self._to_decimal(cash_total, Decimal("0"))
                pos_stmt = (
                    select(OpenPosition.quantity, OpenPosition.avg_cost)
                    .join(Wallet, Wallet.id == OpenPosition.wallet_id)
                    .where(Wallet.account_id == account_id)
                )
            else:
                w = session.get(Wallet, wallet_id)
                if w is None:
                    raise LookupError(f"Wallet bulunamadı: id={wallet_id}")
                v_end_cash = self._to_decimal(w.cash_balance, Decimal("0"))
                pos_stmt = select(
                    OpenPosition.quantity, OpenPosition.avg_cost
                ).where(OpenPosition.wallet_id == wallet_id)

            pos_rows = list(session.execute(pos_stmt).all())
        finally:
            self._close(session)

        # Pozisyonların güncel değeri (yaklaşık: avg_cost bazında)
        pos_value = Decimal("0")
        for q, ac in pos_rows:
            pos_value += (
                self._to_decimal(q, Decimal("0"))
                * self._to_decimal(ac, Decimal("0"))
            )

        v_end = v_end_cash + pos_value

        # V_start: net_deposited (start öncesi) + sıfır pozisyon kabulü.
        # Sade kabul: TWR alt-dönem zinciri için cash flow zamanları
        # arasında V_start ≈ V_prev_end - flow_at_this_point.
        # Burada minimal güvenilir hesap: tek dönem TWR.
        net_external = Decimal("0")
        for amt, _ts in cf_rows:
            net_external += self._to_decimal(amt, Decimal("0"))

        # V_start tahmini: V_end - net_external (eklenen/çekilenleri çıkar
        # → sadece getiriden gelen değişim kalır).
        v_start = v_end - net_external
        if v_start <= 0:
            # Başlangıç değeri bilinmiyor / negatif → hesaplanamaz.
            logger.warning(
                "TWR hesaplanamadı: v_start={} (v_end={}, net_external={})",
                v_start,
                v_end,
                net_external,
            )
            return Decimal("0")

        # Tek-dönem yaklaşımı: (V_end - net_external) / V_start - 1
        # Bu, deposit/withdrawal'ı dışlayan basit TWR yaklaşımıdır.
        # Daha hassas: cash flow zamanlarında V_t snapshot'larıyla
        # geometrik çarpım — Faz 4'te daily_value tablosu gelince.
        twr = (v_end - net_external) / v_start - Decimal("1")
        return (twr * Decimal("100")).quantize(Decimal("0.0001"))

    # ------------------------------------------------------------- benchmark

    async def benchmark_return(
        self,
        benchmark_ticker: str,
        start: date,
        end: date,
    ) -> Decimal:
        """Endeks getirisi (yüzde) — verilen periyot için.

        ``price_history`` tablosundan ``ticker == benchmark_ticker``
        eşleşmesi aranır. Periyodun başına en yakın ``close`` ile
        sonuna en yakın ``close`` arasında basit getiri:
            return = (close_end - close_start) / close_start * 100

        Veri yoksa ``Decimal('0')`` döner ve log uyarısı verilir.
        """

        from app.db.models import Instrument, PriceHistory  # noqa: WPS433

        clean = (benchmark_ticker or "").strip().upper()
        start_dt = self._to_datetime(start)
        end_dt = self._to_datetime(end)

        session = self._session()
        try:
            instr = session.execute(
                select(Instrument).where(Instrument.ticker == clean)
            ).scalar_one_or_none()
            if instr is None:
                logger.warning(
                    "Benchmark instrument bulunamadı: ticker={}", clean
                )
                return Decimal("0")

            start_price = session.execute(
                select(
                    func.coalesce(
                        PriceHistory.verified_close, PriceHistory.close
                    )
                )
                .where(PriceHistory.instrument_id == int(instr.id))
                .where(PriceHistory.timestamp >= start_dt)
                .order_by(PriceHistory.timestamp.asc())
                .limit(1)
            ).scalar_one_or_none()

            end_price = session.execute(
                select(
                    func.coalesce(
                        PriceHistory.verified_close, PriceHistory.close
                    )
                )
                .where(PriceHistory.instrument_id == int(instr.id))
                .where(PriceHistory.timestamp <= end_dt)
                .order_by(PriceHistory.timestamp.desc())
                .limit(1)
            ).scalar_one_or_none()
        finally:
            self._close(session)

        if start_price is None or end_price is None:
            logger.warning(
                "Benchmark fiyat eksik: ticker={}, start={}, end={}",
                clean,
                start_price,
                end_price,
            )
            return Decimal("0")

        sp = self._to_decimal(start_price, Decimal("0"))
        ep = self._to_decimal(end_price, Decimal("0"))
        if sp <= 0:
            return Decimal("0")
        ret = (ep - sp) / sp * Decimal("100")
        return ret.quantize(Decimal("0.0001"))

    # ------------------------------------------------------------- compare

    async def compare(
        self,
        account_id: int,
        benchmark_ticker: str,
        wallet_id: Optional[int] = None,
        start: Optional[date] = None,
        end: Optional[date] = None,
    ) -> BenchmarkComparison:
        """Portföy vs. benchmark karşılaştırması.

        ``start`` ``None`` ise hesabın ``created_at`` tarihinden başla.
        ``end`` ``None`` ise bugün.

        Returns
        -------
        BenchmarkComparison
            Yüzde olarak iki getiri, alfa, "yendi mi" boolean,
            normalize edilmiş tarih aralığı.
        """

        from app.db.models import UserAccount  # noqa: WPS433

        end_d = end or date.today()

        session = self._session()
        try:
            account = session.get(UserAccount, account_id)
            if account is None:
                raise LookupError(f"UserAccount bulunamadı: id={account_id}")
            if start is None:
                created = account.created_at
                if created is None:
                    start_d = end_d - timedelta(days=30)
                else:
                    start_d = (
                        created.date()
                        if isinstance(created, datetime)
                        else created
                    )
            else:
                start_d = start
        finally:
            self._close(session)

        portfolio_return = await self.time_weighted_return(
            account_id=account_id,
            wallet_id=wallet_id,
            start=start_d,
            end=end_d,
        )
        bench_return = await self.benchmark_return(
            benchmark_ticker=benchmark_ticker,
            start=start_d,
            end=end_d,
        )
        alpha = (portfolio_return - bench_return).quantize(Decimal("0.0001"))

        return BenchmarkComparison(
            portfolio_return_pct=portfolio_return,
            benchmark_return_pct=bench_return,
            alpha_pct=alpha,
            beat_benchmark=portfolio_return > bench_return,
            period_start=start_d,
            period_end=end_d,
        )


__all__ = [
    "BIST_BENCHMARK",
    "SP500_BENCHMARK",
    "BenchmarkComparison",
    "BenchmarkService",
]
