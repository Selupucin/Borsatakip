"""İşlem risk skoru — Faz 3 Batch 3.

Doküman §7.4 (Risk Tabanlı Otomasyon) referans alınmıştır.

Formül:

::

    risk_score = volatility_weight * vol_score
               + size_weight       * size_score
               + liquidity_weight  * liq_score
               + concentration_weight * conc_score

Her bileşen 0..100 ölçeğine normalize edilir; ağırlıklar
``WEIGHTS`` sözlüğünden alınır. Toplam skor 0..100 aralığındadır:

- ``score < 25``   → ``risk_level = 'low'``
- ``score < 60``   → ``risk_level = 'medium'``
- ``score >= 60``  → ``risk_level = 'high'``

``requires_confirmation`` bayrağı, skorun ``threshold`` parametresini
aşıp aşmadığına bakar — ``AutoGate`` ``full_auto`` modunda bu bayrakla
karar verir.

Tasarım notları:
- Servis stateless (sadece statik ağırlıklar) — sınıf instance'ı
  hafıza paylaşmaz.
- Tüm para alanları ``Decimal``; volatilite, hacim, oran gibi alanlar
  ``float`` (analytical hesaplamalar için yeterli).
- ``score_*`` metodları bağımsız test edilebilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: Risk seviyesi etiket eşikleri (skor → 'low'|'medium'|'high').
RISK_LEVEL_THRESHOLDS: dict[str, float] = {
    "low": 25.0,
    "medium": 60.0,
}


def risk_level_from_score(score: float) -> str:
    """Sayısal skoru etikete çevir."""

    if score < RISK_LEVEL_THRESHOLDS["low"]:
        return "low"
    if score < RISK_LEVEL_THRESHOLDS["medium"]:
        return "medium"
    return "high"


# ---------------------------------------------------------------------------
# Veri yapısı
# ---------------------------------------------------------------------------


@dataclass
class TradeRiskScore:
    """Bir işlem için hesaplanmış risk skoru çıktısı.

    Attributes
    ----------
    score:
        Toplam risk skoru (0..100).
    components:
        Bileşen bazlı ham skorlar (ağırlıklandırılmamış, 0..100).
    risk_level:
        ``'low' | 'medium' | 'high'`` etiketi.
    reasons:
        İnsan-okunabilir gerekçeler (UI'da tooltip / log için).
    requires_confirmation:
        Skor verilen eşiği aştı mı? ``AutoGate`` bu bayrağa bakar.
    """

    score: float
    components: dict[str, float]
    risk_level: str
    reasons: list[str] = field(default_factory=list)
    requires_confirmation: bool = False


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class TradeRiskScorer:
    """İşlem risk skorlayıcı.

    Ağırlıklar ``WEIGHTS`` sözlüğüyle tanımlıdır ve toplamı 1.0'dır.
    İstenirse ``__init__`` ile override edilebilir.
    """

    #: Bileşen ağırlıkları (toplam = 1.0).
    WEIGHTS: dict[str, float] = {
        "volatility": 0.30,
        "trade_size": 0.30,
        "liquidity": 0.20,
        "concentration": 0.20,
    }

    # Bileşen sabitleri — eşik değerleri.
    VOLATILITY_HIGH_RATIO: float = 0.05  # ATR / price >= %5 → 100
    VOLATILITY_LOW_RATIO: float = 0.005  # <= %0.5 → 0
    TRADE_SIZE_HIGH_PCT: float = 0.20    # işlem >= %20 portföy → 100
    TRADE_SIZE_LOW_PCT: float = 0.01     # <= %1 → 0
    LIQUIDITY_LOW_VOLUME: float = 10_000.0    # avg_volume <= 10k → 100
    LIQUIDITY_HIGH_VOLUME: float = 5_000_000.0  # >= 5M → 0
    CONCENTRATION_HIGH_PCT: float = 0.40  # sektör >= %40 → 100
    CONCENTRATION_LOW_PCT: float = 0.10   # <= %10 → 0

    def __init__(self, weights: Optional[dict[str, float]] = None) -> None:
        if weights is not None:
            total = sum(weights.values())
            if total <= 0:
                raise ValueError("weights toplamı pozitif olmalı")
            # Normalize edip ata (toplam 1.0 garantili).
            self.weights = {k: v / total for k, v in weights.items()}
        else:
            self.weights = dict(self.WEIGHTS)

    # ----------------------------------------------------------- bileşen skorları

    @staticmethod
    def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
        return max(lo, min(hi, value))

    @staticmethod
    def _linear_map(value: float, lo: float, hi: float) -> float:
        """``value`` ``[lo, hi]`` aralığını ``[0, 100]`` ölçeğine eşler.

        ``lo == hi`` veya ``value <= lo`` → 0; ``value >= hi`` → 100.
        """

        if hi <= lo:
            return 0.0
        if value <= lo:
            return 0.0
        if value >= hi:
            return 100.0
        return ((value - lo) / (hi - lo)) * 100.0

    def score_volatility(self, atr: float, current_price: Decimal) -> float:
        """Volatilite riski: ATR / fiyat oranı.

        0..100 ölçek; ATR/price ``VOLATILITY_LOW_RATIO`` altında → 0,
        ``VOLATILITY_HIGH_RATIO`` üstünde → 100.
        """

        if current_price is None or float(current_price) <= 0:
            return 0.0
        if atr is None or atr <= 0:
            return 0.0
        ratio = float(atr) / float(current_price)
        return self._linear_map(
            ratio, self.VOLATILITY_LOW_RATIO, self.VOLATILITY_HIGH_RATIO
        )

    def score_trade_size(
        self,
        trade_value: Decimal,
        portfolio_value: Decimal,
    ) -> float:
        """İşlem tutarı / portföy değeri — büyük işlem daha riskli."""

        pv = float(portfolio_value) if portfolio_value is not None else 0.0
        tv = float(trade_value) if trade_value is not None else 0.0
        if pv <= 0:
            # Portföy değeri yoksa konservatif: yüksek risk varsay.
            return 100.0
        pct = tv / pv
        return self._linear_map(pct, self.TRADE_SIZE_LOW_PCT, self.TRADE_SIZE_HIGH_PCT)

    def score_liquidity(self, avg_volume: float) -> float:
        """Likidite riski: düşük hacim → yüksek risk skoru.

        ``avg_volume <= LIQUIDITY_LOW_VOLUME`` → 100 (illikit).
        ``avg_volume >= LIQUIDITY_HIGH_VOLUME`` → 0 (likit).
        """

        if avg_volume is None or avg_volume < 0:
            return 100.0
        # Ters yön: düşük hacim → yüksek skor. Linear_map'i ters kullanıyoruz.
        if avg_volume <= self.LIQUIDITY_LOW_VOLUME:
            return 100.0
        if avg_volume >= self.LIQUIDITY_HIGH_VOLUME:
            return 0.0
        # avg_volume LO..HI arasında lineer ters.
        span = self.LIQUIDITY_HIGH_VOLUME - self.LIQUIDITY_LOW_VOLUME
        pos = avg_volume - self.LIQUIDITY_LOW_VOLUME
        return 100.0 * (1.0 - pos / span)

    def score_concentration(
        self,
        sector: Optional[str],
        sector_weights: dict[str, float],
    ) -> float:
        """Sektör konsantrasyonu riski.

        ``sector_weights`` toplam portföydeki sektör ağırlıkları
        (0..1 aralığı, örn. ``{'banka': 0.35, 'enerji': 0.20}``).
        İşlem yapılan sektörün ağırlığı yüksekse risk yüksek.
        """

        if not sector:
            return 0.0
        weight = float(sector_weights.get(sector, 0.0) or 0.0)
        # 0..1'lik girdiyi yüzdeye çeviriyoruz, sonra map ediyoruz.
        return self._linear_map(
            weight, self.CONCENTRATION_LOW_PCT, self.CONCENTRATION_HIGH_PCT
        )

    # ----------------------------------------------------------- toplam

    def calculate(
        self,
        atr: float,
        current_price: Decimal,
        trade_value: Decimal,
        portfolio_value: Decimal,
        avg_volume: float,
        sector: Optional[str],
        sector_weights: dict[str, float],
        threshold: float = 50.0,
    ) -> TradeRiskScore:
        """Tüm bileşenleri hesapla, ağırlıklı toplamı döndür.

        Parameters
        ----------
        threshold:
            ``AutoGate`` eşiği (varsayılan 50). Skor bunu aşarsa
            ``requires_confirmation = True``.
        """

        vol = self.score_volatility(atr, current_price)
        size = self.score_trade_size(trade_value, portfolio_value)
        liq = self.score_liquidity(avg_volume)
        conc = self.score_concentration(sector, sector_weights or {})

        components = {
            "volatility": round(vol, 2),
            "trade_size": round(size, 2),
            "liquidity": round(liq, 2),
            "concentration": round(conc, 2),
        }

        total = (
            self.weights.get("volatility", 0.0) * vol
            + self.weights.get("trade_size", 0.0) * size
            + self.weights.get("liquidity", 0.0) * liq
            + self.weights.get("concentration", 0.0) * conc
        )
        total = self._clamp(total)

        reasons: list[str] = []
        if vol >= 60:
            reasons.append(f"Yüksek volatilite (ATR/fiyat oranı: {atr / max(float(current_price), 1):.2%})")
        if size >= 60:
            pv = float(portfolio_value) if portfolio_value else 1.0
            reasons.append(
                f"İşlem tutarı portföyün %{(float(trade_value)/max(pv,1))*100:.1f}'i"
            )
        if liq >= 60:
            reasons.append(f"Düşük likidite (ortalama hacim: {avg_volume:,.0f})")
        if conc >= 60:
            w = sector_weights.get(sector, 0.0) if sector else 0.0
            reasons.append(f"Sektör konsantrasyonu yüksek: '{sector}' %{w*100:.1f}")

        return TradeRiskScore(
            score=round(total, 2),
            components=components,
            risk_level=risk_level_from_score(total),
            reasons=reasons,
            requires_confirmation=(total >= float(threshold)),
        )


__all__ = [
    "RISK_LEVEL_THRESHOLDS",
    "risk_level_from_score",
    "TradeRiskScore",
    "TradeRiskScorer",
]
