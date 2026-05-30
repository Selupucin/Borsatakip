"""Risk yönetimi: pozisyon büyüklüğü, stop-loss/take-profit, korelasyon.

Doküman §5.5 (Risk Yönetimi) ve agent kuralları (ATR pozisyon büyüklüğü,
stop-loss/take-profit, korelasyon analizi, sektör yoğunlaşma) referans
alınarak yazılmıştır.

Tasarım kuralları:
- Para Decimal; oranlar/yüzdeler float.
- Pozisyon büyüklüğü ATR-tabanlı: volatilite arttıkça pozisyon küçülür.
  Üst sınır portföyün %10'u (tek hisse maks. ağırlığı).
- Stop-loss/take-profit ATR çarpanlarıyla (1.5×ATR / 3×ATR — 2:1 R/R)
  belirlenir; destek-direnç verilirse en yakın seviyeye snap'lenir.
- Portföy korelasyon matrisi Pearson; verified_close günlük log
  returns üzerinden hesaplanır.
- Aşırı korelasyon (|ρ| > 0.8) → diversifikasyon uyarısı.
- Sektör yoğunlaşma %30'u aşarsa uyarı.
- Tüm async DB metodları sync session'ı ``asyncio.to_thread`` ile değil
  basit sync çağrıyla saran async wrapper'lardır (mevcut tasarımla uyum).
- ``RiskManager`` ATR/teknik snapshot için doğrudan ``TechnicalAnalyzer``
  üzerinden çalışır — DB şemasına ATR kolonu eklenmesini gerektirmez
  (analiz runtime'da hesaplanır).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from typing import Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import and_, select


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class PositionSizeRecommendation:
    """ATR-bazlı pozisyon büyüklüğü önerisi.

    Attributes
    ----------
    max_position_pct:
        Portföy değerinin maksimum kaçı (örn. 0.05 = %5) bu pozisyona
        ayrılmalı.
    suggested_quantity:
        Tam sayı önerilen adet (alt yuvarlanmış).
    based_on_atr:
        Hesaplamada kullanılan ATR değeri (debug için).
    reason:
        Kısa Türkçe açıklama (UI tooltip).
    """

    max_position_pct: float
    suggested_quantity: int
    based_on_atr: float
    reason: str


@dataclass
class StopLossTakeProfit:
    """Stop-loss / take-profit fiyat önerisi.

    Attributes
    ----------
    stop_loss:
        Pozisyon kapatma fiyatı (zarar limiti).
    take_profit:
        Pozisyon kapatma fiyatı (kâr alma seviyesi).
    risk_reward_ratio:
        ``(take_profit - entry) / (entry - stop_loss)`` veya tersi (SELL).
    based_on:
        Hangi yöntem kullanıldı: ``'atr'``, ``'support_resistance'``,
        ``'percentage'``.
    """

    stop_loss: Decimal
    take_profit: Decimal
    risk_reward_ratio: float
    based_on: str


@dataclass
class CorrelationWarning:
    """Yüksek korelasyonlu hisse çifti uyarısı.

    Attributes
    ----------
    instrument_pair:
        ``(ticker_a, ticker_b)`` veya ``(id_a, id_b)`` döndürülebilir
        — caller'ın anlamasına bağlı; default olarak ticker string.
    correlation:
        Pearson korelasyon (-1..+1).
    warning:
        Kullanıcıya gösterilecek Türkçe uyarı metni.
    """

    instrument_pair: tuple[str, str]
    correlation: float
    warning: str


@dataclass
class PortfolioRisk:
    """Genel portföy risk özeti.

    Attributes
    ----------
    risk_score:
        0..100 portföy risk skoru.
    warnings:
        Türkçe uyarı listesi (UI'da banner / liste).
    correlations:
        Yüksek korelasyon çiftleri.
    sector_concentration:
        Sektör → ağırlık (0..1).
    """

    risk_score: float
    warnings: list[str] = field(default_factory=list)
    correlations: list[CorrelationWarning] = field(default_factory=list)
    sector_concentration: dict[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: İşlem başına maksimum risk yüzdesi (sermayenin).
DEFAULT_RISK_PER_TRADE = 0.02

#: Tek hisse maksimum portföy ağırlığı (concentration cap).
MAX_POSITION_PCT = 0.10

#: Stop-loss ATR çarpanı (BUY: entry − 1.5×ATR).
STOP_LOSS_ATR_MULT = 1.5

#: Take-profit ATR çarpanı (BUY: entry + 3×ATR → 2:1 R/R).
TAKE_PROFIT_ATR_MULT = 3.0

#: Diversifikasyon uyarı eşiği — Pearson korelasyon mutlak değeri.
HIGH_CORRELATION_THRESHOLD = 0.8

#: Sektör yoğunlaşma uyarı eşiği (portföyün yüzdesi).
SECTOR_CONCENTRATION_THRESHOLD = 0.30


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _to_decimal(value) -> Optional[Decimal]:
    """Tip-toleranslı Decimal dönüşümü."""

    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class RiskManager:
    """Portföy ve işlem seviyesi risk yönetimi.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni ``Session`` döndüren callable. DB-bağımsız
        metodlar (``position_size``, ``stop_loss_take_profit``) için
        gerekmez; ``None`` da kabul edilir.
    """

    DEFAULT_RISK_PER_TRADE = DEFAULT_RISK_PER_TRADE
    MAX_POSITION_PCT = MAX_POSITION_PCT
    STOP_LOSS_ATR_MULT = STOP_LOSS_ATR_MULT
    TAKE_PROFIT_ATR_MULT = TAKE_PROFIT_ATR_MULT
    HIGH_CORRELATION_THRESHOLD = HIGH_CORRELATION_THRESHOLD
    SECTOR_CONCENTRATION_THRESHOLD = SECTOR_CONCENTRATION_THRESHOLD

    def __init__(
        self, session_factory: Optional[Callable[[], object]] = None
    ) -> None:
        self.session_factory = session_factory

    # ------------------------------------------------------------- pozisyon büyüklüğü

    def position_size(
        self,
        portfolio_value: Decimal,
        atr: float,
        current_price: Decimal,
        risk_per_trade: Optional[float] = None,
    ) -> PositionSizeRecommendation:
        """ATR-bazlı pozisyon büyüklüğü önerisi.

        Formül:
        ``max_position_pct = min(MAX_POSITION_PCT, risk_per_trade / (atr / current_price))``

        Mantık: Volatilite (ATR/price) ne kadar büyükse, aynı risk
        toleransıyla o kadar küçük pozisyon. Maksimum tek hisse ağırlığı
        ``MAX_POSITION_PCT`` (varsayılan %10) ile sınırlı.

        ``suggested_quantity`` = floor(portfolio_value × max_position_pct
        / current_price).

        Parameters
        ----------
        portfolio_value:
            Cüzdanın toplam değeri (nakit + pozisyon).
        atr:
            Average True Range — günlük volatilite (price units).
        current_price:
            Hissenin güncel fiyatı.
        risk_per_trade:
            İşlem başına risk yüzdesi (0.02 = %2). ``None`` ise
            ``DEFAULT_RISK_PER_TRADE`` kullanılır.
        """

        rpt = risk_per_trade if risk_per_trade is not None else self.DEFAULT_RISK_PER_TRADE
        rpt = max(0.0001, min(1.0, float(rpt)))

        pv = _to_decimal(portfolio_value) or Decimal("0")
        cp = _to_decimal(current_price) or Decimal("0")

        if pv <= 0 or cp <= 0:
            return PositionSizeRecommendation(
                max_position_pct=0.0,
                suggested_quantity=0,
                based_on_atr=float(atr or 0.0),
                reason="Geçersiz portföy değeri veya fiyat — pozisyon önerilemez.",
            )

        atr_f = float(atr) if atr is not None else 0.0
        cp_f = float(cp)
        if atr_f <= 0 or cp_f <= 0:
            # ATR bilinmiyor → maks cap ile başla (en muhafazakâr değil ama
            # mantıklı varsayılan).
            max_pct = self.MAX_POSITION_PCT * 0.5  # ATR yoksa yarıya kıs.
            reason = (
                "ATR=0 veya bilinmiyor; varsayılan %{:.1f} cap'in yarısı "
                "kullanıldı.".format(self.MAX_POSITION_PCT * 100)
            )
        else:
            atr_pct = atr_f / cp_f
            raw_pct = rpt / atr_pct  # Volatiliteye ters orantılı.
            max_pct = min(self.MAX_POSITION_PCT, max(0.0, raw_pct))
            reason = (
                "ATR/fiyat=%{:.2f}, risk-per-trade=%{:.1f} → maks. pozisyon "
                "%{:.1f} (cap %{:.0f})."
            ).format(
                atr_pct * 100,
                rpt * 100,
                max_pct * 100,
                self.MAX_POSITION_PCT * 100,
            )

        allocation = pv * Decimal(str(max_pct))
        qty_decimal = (allocation / cp).quantize(Decimal("1"), rounding=ROUND_DOWN)
        qty = int(qty_decimal)
        if qty < 0:
            qty = 0

        return PositionSizeRecommendation(
            max_position_pct=float(max_pct),
            suggested_quantity=qty,
            based_on_atr=atr_f,
            reason=reason,
        )

    # ------------------------------------------------------------- stop / take

    def stop_loss_take_profit(
        self,
        current_price: Decimal,
        atr: float,
        action: str,
        support_levels: Optional[list[float]] = None,
        resistance_levels: Optional[list[float]] = None,
    ) -> StopLossTakeProfit:
        """ATR-tabanlı stop-loss / take-profit + opsiyonel S/R snap.

        Varsayılan oran 2:1 (TP = 3×ATR, SL = 1.5×ATR).

        - **BUY** : SL = entry − SL_mult×ATR; TP = entry + TP_mult×ATR
        - **SELL**: SL = entry + SL_mult×ATR; TP = entry − TP_mult×ATR

        Destek/direnç listesi verilirse:
        - BUY: SL en yakın support'a (entry'nin altında) snap'lenir;
          TP en yakın resistance'a (entry'nin üstünde) snap'lenir.
        - SELL: ters.

        ATR yoksa veya 0 ise yüzdesel fallback (±%3 / ±%6).
        """

        entry = _to_decimal(current_price)
        if entry is None or entry <= 0:
            raise ValueError("current_price > 0 olmalı")

        act = (action or "").upper()
        if act not in {"BUY", "SELL"}:
            raise ValueError(f"action 'BUY' veya 'SELL' olmalı, alındı: {action!r}")

        atr_f = float(atr) if atr is not None else 0.0

        if atr_f > 0:
            sl_delta = Decimal(str(self.STOP_LOSS_ATR_MULT * atr_f))
            tp_delta = Decimal(str(self.TAKE_PROFIT_ATR_MULT * atr_f))
            based_on = "atr"
        else:
            # Yüzde fallback.
            sl_delta = entry * Decimal("0.03")
            tp_delta = entry * Decimal("0.06")
            based_on = "percentage"

        if act == "BUY":
            stop_loss = entry - sl_delta
            take_profit = entry + tp_delta
        else:
            stop_loss = entry + sl_delta
            take_profit = entry - tp_delta

        # S/R snap (sadece geçerli yön).
        if support_levels or resistance_levels:
            new_sl, new_tp, snapped = self._snap_to_sr(
                act, entry, stop_loss, take_profit,
                support_levels or [], resistance_levels or [],
            )
            stop_loss = new_sl
            take_profit = new_tp
            if snapped:
                based_on = "support_resistance"

        # R/R hesabı (yöne göre işaret düzeltmesi).
        if act == "BUY":
            risk = entry - stop_loss
            reward = take_profit - entry
        else:
            risk = stop_loss - entry
            reward = entry - take_profit

        if risk <= 0:
            rr = 0.0
        else:
            rr = float(reward / risk)

        # SL negatif olamaz (BUY tarafı için savunmacı).
        if stop_loss < 0:
            stop_loss = Decimal("0")

        return StopLossTakeProfit(
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_reward_ratio=rr,
            based_on=based_on,
        )

    @staticmethod
    def _snap_to_sr(
        action: str,
        entry: Decimal,
        sl: Decimal,
        tp: Decimal,
        supports: list[float],
        resistances: list[float],
    ) -> tuple[Decimal, Decimal, bool]:
        """SL/TP'yi en yakın destek-direnç seviyesine snap'ler.

        Returns
        -------
        (new_sl, new_tp, snapped)
            ``snapped`` True ise en az bir tarafta S/R kullanıldı.
        """

        snapped = False
        entry_f = float(entry)
        if action == "BUY":
            # SL: entry altındaki en yüksek support.
            candidates = [s for s in supports if s < entry_f]
            if candidates:
                sl_new = Decimal(str(max(candidates)))
                # Yalnızca daha makul (entry'ye daha yakın) ise snap.
                if sl_new > sl:
                    sl = sl_new
                    snapped = True
            # TP: entry üstündeki en düşük resistance.
            cand2 = [r for r in resistances if r > entry_f]
            if cand2:
                tp_new = Decimal(str(min(cand2)))
                if tp_new < tp:
                    tp = tp_new
                    snapped = True
        else:  # SELL
            cand = [r for r in resistances if r > entry_f]
            if cand:
                sl_new = Decimal(str(min(cand)))
                if sl_new < sl:
                    sl = sl_new
                    snapped = True
            cand2 = [s for s in supports if s < entry_f]
            if cand2:
                tp_new = Decimal(str(max(cand2)))
                if tp_new > tp:
                    tp = tp_new
                    snapped = True
        return sl, tp, snapped

    # ------------------------------------------------------------- korelasyon

    async def portfolio_correlation(
        self,
        instrument_ids: list[int],
        lookback_days: int = 90,
    ) -> pd.DataFrame:
        """Verified_close günlük log-return üzerinden Pearson korelasyon matrisi.

        Returns
        -------
        pd.DataFrame
            ``index`` = ``columns`` = instrument_id; diagonal 1.0.
            Tek hisse veya hiç veri yoksa boş DataFrame.
        """

        if not instrument_ids or self.session_factory is None:
            return pd.DataFrame(index=instrument_ids, columns=instrument_ids)

        prices = self._fetch_price_panel(instrument_ids, lookback_days)
        if prices.empty or prices.shape[0] < 2:
            return pd.DataFrame(
                np.nan, index=instrument_ids, columns=instrument_ids
            )

        # Log returns (vectorized).
        returns = np.log(prices / prices.shift(1)).dropna(how="all")
        if returns.empty:
            return pd.DataFrame(
                np.nan, index=instrument_ids, columns=instrument_ids
            )

        corr = returns.corr(method="pearson")
        # Tüm istenen ID'lerin korelasyon matrisinde temsil edildiğinden emin ol.
        corr = corr.reindex(index=instrument_ids, columns=instrument_ids)
        return corr

    def _fetch_price_panel(
        self, instrument_ids: list[int], lookback_days: int
    ) -> pd.DataFrame:
        """``verified_close`` panelini ``(timestamp × instrument_id)`` döndürür."""

        from app.db.models import PriceHistory  # noqa: WPS433

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        session = self.session_factory()
        try:
            stmt = select(
                PriceHistory.instrument_id,
                PriceHistory.timestamp,
                PriceHistory.verified_close,
            ).where(
                and_(
                    PriceHistory.instrument_id.in_(instrument_ids),
                    PriceHistory.timestamp >= cutoff,
                    PriceHistory.verified_close.isnot(None),
                )
            )
            rows = session.execute(stmt).all()
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(
            rows, columns=["instrument_id", "timestamp", "verified_close"]
        )
        df["verified_close"] = df["verified_close"].astype("float64")
        panel = df.pivot_table(
            index="timestamp",
            columns="instrument_id",
            values="verified_close",
            aggfunc="last",
        ).sort_index()
        return panel

    # ------------------------------------------------------------- diversifikasyon

    async def diversification_warnings(
        self, wallet_id: int, lookback_days: int = 90
    ) -> list[CorrelationWarning]:
        """Cüzdandaki açık pozisyonlar arası yüksek korelasyon uyarıları.

        |ρ| > ``HIGH_CORRELATION_THRESHOLD`` (varsayılan 0.8) olan
        çiftler için Türkçe uyarı üretir.
        """

        if self.session_factory is None:
            return []

        positions = self._fetch_open_positions(wallet_id)
        if len(positions) < 2:
            return []

        instrument_ids = [p["instrument_id"] for p in positions]
        ticker_by_id = {p["instrument_id"]: p["ticker"] for p in positions}
        corr = await self.portfolio_correlation(instrument_ids, lookback_days)
        if corr.empty:
            return []

        warnings: list[CorrelationWarning] = []
        seen: set[tuple[int, int]] = set()
        for a in instrument_ids:
            for b in instrument_ids:
                if a == b:
                    continue
                pair = tuple(sorted((a, b)))
                if pair in seen:
                    continue
                seen.add(pair)
                try:
                    rho = float(corr.loc[a, b])
                except KeyError:
                    continue
                if math.isnan(rho):
                    continue
                if abs(rho) >= self.HIGH_CORRELATION_THRESHOLD:
                    ta = ticker_by_id.get(a, str(a))
                    tb = ticker_by_id.get(b, str(b))
                    warnings.append(
                        CorrelationWarning(
                            instrument_pair=(ta, tb),
                            correlation=rho,
                            warning=(
                                f"{ta} ve {tb} arasında yüksek korelasyon "
                                f"(ρ={rho:+.2f}); birlikte hareket riski — "
                                "diversifikasyon için birini azaltmayı "
                                "değerlendirin."
                            ),
                        )
                    )
        return warnings

    def _fetch_open_positions(self, wallet_id: int) -> list[dict]:
        """Cüzdandaki açık pozisyonları (id, ticker, quantity, avg_cost, sector) döndürür."""

        from app.db.models import Instrument, OpenPosition  # noqa: WPS433

        session = self.session_factory()
        try:
            stmt = (
                select(
                    OpenPosition.instrument_id,
                    Instrument.ticker,
                    OpenPosition.quantity,
                    OpenPosition.avg_cost,
                    Instrument.sector,
                )
                .join(Instrument, Instrument.id == OpenPosition.instrument_id)
                .where(
                    and_(
                        OpenPosition.wallet_id == wallet_id,
                        OpenPosition.quantity.isnot(None),
                        OpenPosition.quantity > 0,
                    )
                )
            )
            rows = session.execute(stmt).all()
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        return [
            {
                "instrument_id": int(r[0]),
                "ticker": str(r[1]),
                "quantity": float(r[2]) if r[2] is not None else 0.0,
                "avg_cost": float(r[3]) if r[3] is not None else 0.0,
                "sector": str(r[4]) if r[4] else "Unknown",
            }
            for r in rows
        ]

    # ------------------------------------------------------------- sektör

    async def sector_concentration(self, wallet_id: int) -> dict[str, float]:
        """Sektör bazlı portföy ağırlığı (0..1 normalize).

        Ağırlık ``quantity × avg_cost`` (maliyet bazlı). Güncel fiyatla
        revaluation çağıran tarafa bırakılır (portfolio-manager).
        """

        positions = self._fetch_open_positions(wallet_id)
        if not positions:
            return {}

        total = sum(p["quantity"] * p["avg_cost"] for p in positions)
        if total <= 0:
            return {}

        weights: dict[str, float] = {}
        for p in positions:
            value = p["quantity"] * p["avg_cost"]
            sector = p["sector"] or "Unknown"
            weights[sector] = weights.get(sector, 0.0) + value / total
        return weights

    # ------------------------------------------------------------- toplam risk

    async def overall_risk_score(
        self, wallet_id: int, lookback_days: int = 90
    ) -> tuple[float, list[str]]:
        """Cüzdan için 0..100 toplam risk skoru + Türkçe uyarı listesi.

        Bileşenler:
        - Sektör yoğunlaşma: en büyük sektör ağırlığı > %30 ise +20.
        - Aşırı korelasyon çifti sayısı × 5 (cap 30).
        - Pozisyon sayısı: 1 hisse → +25; 2 → +10 (yetersiz çeşitlilik).
        - Baseline 30 (orta düzey hareket riski).

        Returns
        -------
        (score, warnings)
        """

        warnings: list[str] = []
        score = 30.0

        sectors = await self.sector_concentration(wallet_id)
        if sectors:
            top_sector, top_weight = max(sectors.items(), key=lambda kv: kv[1])
            if top_weight > self.SECTOR_CONCENTRATION_THRESHOLD:
                score += 20.0
                warnings.append(
                    f"Sektör yoğunlaşma yüksek: {top_sector} "
                    f"%{top_weight * 100:.0f} (eşik %{int(self.SECTOR_CONCENTRATION_THRESHOLD * 100)})."
                )

        positions = self._fetch_open_positions(wallet_id)
        n = len(positions)
        if n == 1:
            score += 25.0
            warnings.append(
                "Cüzdanda tek hisse var; çeşitlendirme yapmanız önerilir."
            )
        elif n == 2:
            score += 10.0
            warnings.append(
                "Cüzdanda yalnızca 2 hisse var; çeşitlendirme sınırlı."
            )

        corr_warnings = await self.diversification_warnings(
            wallet_id, lookback_days=lookback_days
        )
        if corr_warnings:
            score += min(30.0, 5.0 * len(corr_warnings))
            for w in corr_warnings:
                warnings.append(w.warning)

        return _clamp(score), warnings


__all__ = [
    "DEFAULT_RISK_PER_TRADE",
    "MAX_POSITION_PCT",
    "STOP_LOSS_ATR_MULT",
    "TAKE_PROFIT_ATR_MULT",
    "HIGH_CORRELATION_THRESHOLD",
    "SECTOR_CONCENTRATION_THRESHOLD",
    "PositionSizeRecommendation",
    "StopLossTakeProfit",
    "CorrelationWarning",
    "PortfolioRisk",
    "RiskManager",
]
