"""Vade-bazlı öneri motorları — kısa / orta / uzun vade + dispatcher.

Doküman §5 (Öneri Motoru) ve agent kuralları (açıklanabilirlik, ağırlık
matrisi) referans alınarak yazılmıştır.

Vade × ağırlık matrisi (Doküman §5.2):

| Vade | Teknik | Sentiment | Fundamental | Mutabakat |
|---|---|---|---|---|
| Kısa (1-7g) | %60 | %30 | — | %10 |
| Orta (1-3a) | %40 | %25 | %35 | — |
| Uzun (3-12a+) | %30 | %20 | %50 | — |

Tasarım kuralları:
- Tüm alt skorlar **0–100 skala**; 0 = güçlü SAT, 50 = nötr, 100 = güçlü AL.
- ``action`` eşikleri: ``>= 60 → BUY``, ``<= 40 → SELL``, ortası ``HOLD``.
- ``confidence`` = ağırlıklı bileşik skorun 50'den uzaklığı × 2 (yani 0
  nötr → %0 güven, 100 veya 0 → %100 güven).
- ``risk_level``: ATR/close oranı + RSI uç bölge ile hesaplanır.
  Orta/uzun vadede ``beta`` da risk skoruna katkı yapar.
- ``target_price`` (yalnızca BUY): vadeye göre farklı strateji.
- Açıklanabilirlik: ``summary`` alanı vade'ye özel Türkçe metin
  ("Orta vade için F/K=8 sektör altında, RSI nötr..." vb.).

Para hesaplaması ``Decimal`` ile yapılır (UI'a TL/USD ondalık gösterimi).
Skorlar ``float``; DB ``Numeric(5,2)`` kolonuna 0–100 olarak yazılır.

Faz 3'te eklenenler:
- ``MidTermRecommender`` — fundamental ağırlıklı (orta vade).
- ``LongTermRecommender`` — fundamental dominant (uzun vade).
- ``RecommendationDispatcher`` — timeframe parametresine göre doğru
  recommender'a yönlendirme.
- ``MidTermInput`` / ``LongTermInput`` — fundamental snapshot dahil
  girdi paketleri.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from loguru import logger

from app.analysis.fundamental import FundamentalAnalyzer, FundamentalSnapshot


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: Kısa vade ağırlık dağılımı (Doküman §5.2).
SHORT_TERM_WEIGHTS: dict[str, float] = {
    "tech": 0.60,
    "sentiment": 0.30,
    "consensus": 0.10,
}

#: Orta vade (1-3 ay) ağırlık dağılımı (Doküman §5.2).
MID_TERM_WEIGHTS: dict[str, float] = {
    "tech": 0.40,
    "fundamental": 0.35,
    "sentiment": 0.25,
}

#: Uzun vade (3-12 ay+) ağırlık dağılımı (Doküman §5.2).
LONG_TERM_WEIGHTS: dict[str, float] = {
    "fundamental": 0.50,
    "tech": 0.30,
    "sentiment": 0.20,
}

#: action eşikleri (bileşik skor 0..100 üzerinden).
BUY_THRESHOLD = 60.0
SELL_THRESHOLD = 40.0

#: TradingView / Investing.com özet etiketlerinin 0..100 skala eşlemesi.
_CONSENSUS_MAP: dict[str, float] = {
    "STRONG_BUY": 100.0,
    "BUY": 75.0,
    "NEUTRAL": 50.0,
    "SELL": 25.0,
    "STRONG_SELL": 0.0,
}

#: Sentiment "düşük güven" eşiği — bu kadar haberden azsa skor nötre yakın
#: tutulur ve summary'ye uyarı eklenir.
MIN_SENTIMENT_COUNT = 3

#: Risk seviyesi sınırları: ATR / close oranı yüzdesi.
RISK_LOW_MAX_ATR_PCT = 2.0
RISK_HIGH_MIN_ATR_PCT = 5.0


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class RecommendationInput:
    """Öneri motoruna verilen girdi paketi.

    ``technical_snapshot`` ``TechnicalAnalyzer.latest_snapshot()`` çıktısıdır;
    en az ``rsi``, ``macd``, ``macd_signal``, ``ema_20``, ``ema_50``,
    ``ema_200``, ``bb_upper``, ``bb_lower``, ``atr``, ``close`` alanlarını
    içerebilir (alanların eksikliği toleranslı ele alınır).
    """

    instrument_id: int
    ticker: str
    technical_snapshot: dict
    sentiment_score: Optional[float]  # [-1, +1] aralığında ortalama
    sentiment_count: int  # kaç haber baz alındı
    tv_summary: Optional[str]  # STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL
    inv_summary: Optional[str]
    current_price: Decimal


@dataclass
class RecommendationOutput:
    """Öneri motorunun ürettiği çıktı (DB ``recommendations`` satırı + UI metni).

    Tüm puanlar 0..100 skalada. ``action`` ve ``timeframe`` DB CHECK
    constraint'lerine uygun string'ler.
    """

    action: str  # 'BUY' | 'HOLD' | 'SELL'
    timeframe: str  # 'short' | 'mid' | 'long'
    confidence: float  # 0..100
    tech_score: float
    sentiment_score: float
    summary: str
    risk_level: str  # 'low' | 'medium' | 'high'
    risk_score: float  # 0..100 (otomasyon eşiği için sayısal)
    target_price: Optional[Decimal]
    # Aşağıdaki üç alan vadeye göre kullanılır:
    # - short: consensus_score dolu, fundamental_score None
    # - mid/long: fundamental_score dolu, consensus_score None
    consensus_score: Optional[float] = None
    fundamental_score: Optional[float] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    contributions: dict[str, float] = field(default_factory=dict)
    """Açıklanabilirlik kırılımı: her sinyalin bileşik skora ağırlıklı katkısı."""


@dataclass
class MidTermInput:
    """Orta vade (1-3 ay) öneri motoru girdisi.

    Fundamental snapshot bu vade için **birinci derece önemli**
    (ağırlık %35). ``technical_snapshot`` yine günlük indikatörler
    üzerinden hesaplanır ama heuristik daha trend-takip odaklıdır.
    """

    instrument_id: int
    ticker: str
    technical_snapshot: dict
    sentiment_score: Optional[float]  # [-1, +1]
    sentiment_count: int
    fundamental_snapshot: Optional[FundamentalSnapshot]
    current_price: Decimal


@dataclass
class LongTermInput:
    """Uzun vade (3-12 ay+) öneri motoru girdisi.

    Fundamental snapshot bu vade için **dominant** (ağırlık %50).
    Teknik daha çok uzun trend (EMA200) ve volatilite penceresi için
    kullanılır.
    """

    instrument_id: int
    ticker: str
    technical_snapshot: dict
    sentiment_score: Optional[float]
    sentiment_count: int
    fundamental_snapshot: Optional[FundamentalSnapshot]
    current_price: Decimal


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _coerce_float(value) -> Optional[float]:
    """``None`` / ``NaN`` toleranslı float dönüşümü."""

    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN check
        return None
    return f


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class ShortTermRecommender:
    """Kısa vade (1–7 gün) öneri motoru.

    Ağırlıklar sabit değil — ``__init__(weights=...)`` ile override edilebilir;
    backtester Faz 3'te bunu otomatik optimize edecek. Varsayılan değerler
    Doküman §5.2'deki dağılım: ``%60 / %30 / %10``.
    """

    TIMEFRAME = "short"

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self.weights = dict(weights) if weights else dict(SHORT_TERM_WEIGHTS)
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"Ağırlık toplamı 1.0 olmalı, alındı: {total:.4f} "
                f"(weights={self.weights})"
            )

    # ------------------------------------------------------------- skorlama

    def score_technical(self, snapshot: dict) -> float:
        """Teknik snapshot'tan 0..100 skala teknik skoru üretir.

        Heuristik (her bileşen ±15..20 puan):
        - RSI < 30 → +20 (aşırı satım, alım fırsatı);
          RSI > 70 → −20 (aşırı alım, satım sinyali).
        - MACD > signal → +15;  MACD < signal → −15.
        - close > EMA50 → +10;  close < EMA50 → −10.
        - EMA20 > EMA50 > EMA200 (uptrend) → +10;  tam tersi → −10.
        - Bollinger pozisyonu: close üst bandın üstünde → −5 (aşırı genişledi),
          alt bandın altında → +5 (mean reversion fırsatı).

        Başlangıç noktası 50 (nötr). Sonuç [0, 100] aralığında clamp.
        """

        score = 50.0

        rsi = _coerce_float(snapshot.get("rsi"))
        if rsi is not None:
            if rsi < 30:
                score += 20
            elif rsi > 70:
                score -= 20

        macd = _coerce_float(snapshot.get("macd"))
        macd_signal = _coerce_float(snapshot.get("macd_signal"))
        if macd is not None and macd_signal is not None:
            score += 15 if macd > macd_signal else -15

        close = _coerce_float(snapshot.get("close"))
        ema_50 = _coerce_float(snapshot.get("ema_50"))
        if close is not None and ema_50 is not None:
            score += 10 if close > ema_50 else -10

        ema_20 = _coerce_float(snapshot.get("ema_20"))
        ema_200 = _coerce_float(snapshot.get("ema_200"))
        if ema_20 is not None and ema_50 is not None and ema_200 is not None:
            if ema_20 > ema_50 > ema_200:
                score += 10
            elif ema_20 < ema_50 < ema_200:
                score -= 10

        bb_upper = _coerce_float(snapshot.get("bb_upper"))
        bb_lower = _coerce_float(snapshot.get("bb_lower"))
        if close is not None and bb_upper is not None and close > bb_upper:
            score -= 5
        elif close is not None and bb_lower is not None and close < bb_lower:
            score += 5

        return _clamp(score)

    def score_sentiment(
        self, sentiment_score: Optional[float], count: int
    ) -> float:
        """[-1, +1] aralığındaki sentiment ortalamasını 0..100 skala'ya taşır.

        - ``sentiment_score is None`` veya ``count == 0`` → 50 (nötr, bilgi yok).
        - ``count < MIN_SENTIMENT_COUNT`` → güveni azalt (skoru 50'ye doğru çek).
        """

        if sentiment_score is None or count <= 0:
            return 50.0
        s = max(-1.0, min(1.0, float(sentiment_score)))
        base = 50.0 + s * 50.0  # -1 → 0, 0 → 50, +1 → 100
        if count < MIN_SENTIMENT_COUNT:
            # Düşük örneklem: skoru 50'ye doğru oranla (0.5 ağırlık) çek.
            base = 50.0 + (base - 50.0) * 0.5
        return _clamp(base)

    def score_consensus(
        self, tv: Optional[str], inv: Optional[str]
    ) -> float:
        """TradingView + Investing.com özetlerinin sayısal ortalaması.

        - Her iki kaynak da ``None`` → 50 (bilgi yok).
        - Tek kaynak varsa onun değeri kullanılır.
        - İki kaynak varsa basit ortalama (eşit ağırlık).
        Bilinmeyen özet etiketleri 50 (nötr) olarak ele alınır.
        """

        def _map(label: Optional[str]) -> Optional[float]:
            if label is None:
                return None
            return _CONSENSUS_MAP.get(label.upper(), 50.0)

        a = _map(tv)
        b = _map(inv)
        if a is None and b is None:
            return 50.0
        if a is None:
            return _clamp(b)  # type: ignore[arg-type]
        if b is None:
            return _clamp(a)
        return _clamp((a + b) / 2.0)

    def calculate_risk(self, snapshot: dict) -> tuple[str, float]:
        """Risk seviyesi etiketi + sayısal risk skoru (0..100) döndürür.

        Yöntem: ``ATR / close * 100`` yüzdesi temel alınır.
        - ``< RISK_LOW_MAX_ATR_PCT`` → ``low``;
        - ``>= RISK_HIGH_MIN_ATR_PCT`` → ``high``;
        - aradaki bölge → ``medium``.

        RSI uç değerleri (>=80 veya <=20) risk skorunu +10 yukarı çeker
        (anlık dönüş riski yüksek).
        """

        close = _coerce_float(snapshot.get("close"))
        atr = _coerce_float(snapshot.get("atr"))

        atr_pct = None
        if close is not None and atr is not None and close > 0:
            atr_pct = (atr / close) * 100.0

        if atr_pct is None:
            # ATR bilinmiyor → orta risk varsayılan, ortalama skor.
            level = "medium"
            risk_score = 50.0
        elif atr_pct < RISK_LOW_MAX_ATR_PCT:
            level = "low"
            # 0..RISK_LOW_MAX_ATR_PCT → 0..33 skor.
            risk_score = (atr_pct / RISK_LOW_MAX_ATR_PCT) * 33.0
        elif atr_pct >= RISK_HIGH_MIN_ATR_PCT:
            level = "high"
            # RISK_HIGH_MIN_ATR_PCT.. → 66..100 skor (clamp).
            risk_score = _clamp(66.0 + (atr_pct - RISK_HIGH_MIN_ATR_PCT) * 6.0)
        else:
            level = "medium"
            # RISK_LOW_MAX..RISK_HIGH_MIN → 33..66 lineer.
            span = RISK_HIGH_MIN_ATR_PCT - RISK_LOW_MAX_ATR_PCT
            risk_score = 33.0 + ((atr_pct - RISK_LOW_MAX_ATR_PCT) / span) * 33.0

        rsi = _coerce_float(snapshot.get("rsi"))
        if rsi is not None and (rsi >= 80 or rsi <= 20):
            risk_score = _clamp(risk_score + 10.0)
            if level == "low":
                level = "medium"

        return level, _clamp(risk_score)

    # --------------------------------------------------------------- öneri

    def recommend(self, inputs: RecommendationInput) -> RecommendationOutput:
        """Tüm alt skorları birleştirip nihai öneriyi üretir."""

        tech = self.score_technical(inputs.technical_snapshot)
        sent = self.score_sentiment(inputs.sentiment_score, inputs.sentiment_count)
        cons = self.score_consensus(inputs.tv_summary, inputs.inv_summary)

        # Ağırlıklı bileşik skor (0..100).
        composite = (
            tech * self.weights["tech"]
            + sent * self.weights["sentiment"]
            + cons * self.weights["consensus"]
        )

        # action eşikleri.
        if composite >= BUY_THRESHOLD:
            action = "BUY"
        elif composite <= SELL_THRESHOLD:
            action = "SELL"
        else:
            action = "HOLD"

        # confidence: nötrden uzaklık × 2 (0=tam nötr → 0; 100/0 → 100).
        confidence = _clamp(abs(composite - 50.0) * 2.0)

        risk_level, risk_score = self.calculate_risk(inputs.technical_snapshot)

        target_price = self._suggest_target(
            action, inputs.technical_snapshot, inputs.current_price
        )

        contributions = {
            "tech": tech * self.weights["tech"],
            "sentiment": sent * self.weights["sentiment"],
            "consensus": cons * self.weights["consensus"],
        }

        summary = self._build_summary(
            action=action,
            confidence=confidence,
            tech=tech,
            sent=sent,
            cons=cons,
            snapshot=inputs.technical_snapshot,
            sentiment_count=inputs.sentiment_count,
            sentiment_score=inputs.sentiment_score,
            tv_summary=inputs.tv_summary,
            inv_summary=inputs.inv_summary,
            risk_level=risk_level,
        )

        return RecommendationOutput(
            action=action,
            timeframe=self.TIMEFRAME,
            confidence=confidence,
            tech_score=tech,
            sentiment_score=sent,
            summary=summary,
            risk_level=risk_level,
            risk_score=risk_score,
            target_price=target_price,
            consensus_score=cons,
            fundamental_score=None,
            contributions=contributions,
        )

    # ----------------------------------------------------- yardımcı: hedef fiyat

    @staticmethod
    def _suggest_target(
        action: str, snapshot: dict, current_price: Decimal
    ) -> Optional[Decimal]:
        """BUY önerisinde teknik tabanlı basit hedef fiyat tahmini."""

        if action != "BUY":
            return None

        bb_upper = _coerce_float(snapshot.get("bb_upper"))
        atr = _coerce_float(snapshot.get("atr"))
        candidates: list[Decimal] = []

        try:
            current = Decimal(current_price)
        except (TypeError, ValueError):
            return None

        if bb_upper is not None and bb_upper > float(current):
            candidates.append(Decimal(str(bb_upper)))
        if atr is not None:
            candidates.append(current + Decimal(str(atr)))

        if not candidates:
            # Fallback: +%3 hedef (kısa vade için makul).
            return current * Decimal("1.03")
        return max(candidates)

    # ----------------------------------------------------- yardımcı: özet metin

    @staticmethod
    def _build_summary(
        *,
        action: str,
        confidence: float,
        tech: float,
        sent: float,
        cons: float,
        snapshot: dict,
        sentiment_count: int,
        sentiment_score: Optional[float],
        tv_summary: Optional[str],
        inv_summary: Optional[str],
        risk_level: str,
    ) -> str:
        """Türkçe açıklanabilirlik metni (Doküman §5.6).

        Örnek çıktı:
        ``Kısa vade AL önerisi (güven %72). Teknik analiz pozitif (RSI=28
        aşırı satım, MACD pozitif cross). Sentiment hafif pozitif (5
        haberden ortalama +0.40). Mutabakat: TV=BUY, INV=NEUTRAL. Risk:
        düşük.``
        """

        action_tr = {"BUY": "AL", "HOLD": "BEKLE", "SELL": "SAT"}.get(action, action)
        parts: list[str] = [
            f"Kısa vade {action_tr} önerisi (güven %{confidence:.0f})."
        ]

        # Teknik kırılım.
        tech_bits: list[str] = []
        rsi = _coerce_float(snapshot.get("rsi"))
        if rsi is not None:
            if rsi < 30:
                tech_bits.append(f"RSI={rsi:.1f} aşırı satım")
            elif rsi > 70:
                tech_bits.append(f"RSI={rsi:.1f} aşırı alım")
            else:
                tech_bits.append(f"RSI={rsi:.1f}")
        macd = _coerce_float(snapshot.get("macd"))
        macd_sig = _coerce_float(snapshot.get("macd_signal"))
        if macd is not None and macd_sig is not None:
            cross = "pozitif" if macd > macd_sig else "negatif"
            tech_bits.append(f"MACD {cross} cross")
        ema_20 = _coerce_float(snapshot.get("ema_20"))
        ema_50 = _coerce_float(snapshot.get("ema_50"))
        ema_200 = _coerce_float(snapshot.get("ema_200"))
        if ema_20 is not None and ema_50 is not None and ema_200 is not None:
            if ema_20 > ema_50 > ema_200:
                tech_bits.append("trend yukarı (EMA20>50>200)")
            elif ema_20 < ema_50 < ema_200:
                tech_bits.append("trend aşağı (EMA20<50<200)")

        tech_tone = (
            "pozitif" if tech >= 60 else "negatif" if tech <= 40 else "nötr"
        )
        parts.append(
            f"Teknik analiz {tech_tone} (skor {tech:.0f}/100"
            + (f"; {', '.join(tech_bits)})." if tech_bits else ").")
        )

        # Sentiment kırılım.
        if sentiment_count <= 0 or sentiment_score is None:
            parts.append("Sentiment: haber bulunamadı.")
        else:
            sent_tone = (
                "pozitif" if sent >= 60 else "negatif" if sent <= 40 else "nötr"
            )
            low_n = (
                " (örneklem düşük)" if sentiment_count < MIN_SENTIMENT_COUNT else ""
            )
            parts.append(
                f"Sentiment {sent_tone} "
                f"({sentiment_count} haberden ortalama {sentiment_score:+.2f}"
                f"{low_n})."
            )

        # Mutabakat kırılım.
        if tv_summary or inv_summary:
            parts.append(
                "Mutabakat: "
                f"TV={tv_summary or '—'}, INV={inv_summary or '—'} "
                f"(skor {cons:.0f}/100)."
            )

        # Risk.
        risk_tr = {"low": "düşük", "medium": "orta", "high": "yüksek"}.get(
            risk_level, risk_level
        )
        parts.append(f"Risk: {risk_tr}.")

        return " ".join(parts)

    # --------------------------------------------------------------- DB persist

    def save_recommendation(
        self, session, output: RecommendationOutput, instrument_id: int
    ):
        """``recommendations`` tablosuna bir satır ekler.

        ``stop_loss`` / ``take_profit`` Faz 3'te ``RiskManager`` tarafından
        doldurulup ``output`` üzerinden taşınabilir. ``fundamental_score``
        orta/uzun vade çıktılarında doludur; kısa vadede ``None`` kalır.
        """

        from app.db.models import Recommendation  # noqa: WPS433

        row = Recommendation(
            instrument_id=instrument_id,
            generated_at=datetime.now(tz=timezone.utc),
            action=output.action,
            timeframe=output.timeframe,
            confidence=float(output.confidence),
            risk_level=output.risk_level,
            risk_score=float(output.risk_score),
            target_price=(
                float(output.target_price) if output.target_price is not None else None
            ),
            stop_loss=(
                float(output.stop_loss) if output.stop_loss is not None else None
            ),
            take_profit=(
                float(output.take_profit) if output.take_profit is not None else None
            ),
            summary=output.summary,
            tech_score=float(output.tech_score),
            sentiment_score=float(output.sentiment_score),
            fundamental_score=(
                float(output.fundamental_score)
                if output.fundamental_score is not None
                else None
            ),
        )
        session.add(row)
        logger.debug(
            "Recommendation kaydedildi: instrument_id={}, action={}, "
            "timeframe={}, confidence={:.1f}",
            instrument_id,
            output.action,
            output.timeframe,
            output.confidence,
        )
        return row


# ---------------------------------------------------------------------------
# Orta vade öneri motoru (1-3 ay)
# ---------------------------------------------------------------------------


class MidTermRecommender:
    """Orta vade (1-3 ay) öneri motoru.

    Ağırlıklar Doküman §5.2: ``%40 teknik + %35 fundamental + %25
    sentiment``. Kaynak mutabakatı bu vadede kullanılmaz — TV/Inv özetleri
    kısa vade odaklıdır. Fundamental snapshot yoksa fundamental skoru
    50 (nötr) sayılır ve summary'de "fundamental veri yok" uyarısı düşer.
    """

    TIMEFRAME = "mid"

    def __init__(
        self,
        fundamental_analyzer: Optional[FundamentalAnalyzer] = None,
        weights: Optional[dict[str, float]] = None,
    ) -> None:
        self.fundamental_analyzer = fundamental_analyzer or FundamentalAnalyzer()
        self.weights = dict(weights) if weights else dict(MID_TERM_WEIGHTS)
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"MidTermRecommender ağırlık toplamı 1.0 olmalı, alındı: "
                f"{total:.4f} (weights={self.weights})"
            )

    # ------------------------------------------------------------- skorlar

    def score_technical(self, snapshot: dict) -> float:
        """Orta vade için trend odaklı teknik skor.

        Kısa vade ile aynı RSI/MACD/EMA bileşenleri ama trend (EMA200)
        ve hacim tarafı daha baskın. Aşırı satım/aşırı alım kısa vade
        kadar belirleyici değil — orta vadede dalga içinde kalma kabul
        edilir.
        """

        score = 50.0

        rsi = _coerce_float(snapshot.get("rsi"))
        if rsi is not None:
            # Orta vade için RSI sapması ±10 (kısa vadede ±20).
            if rsi < 35:
                score += 10
            elif rsi > 65:
                score -= 10

        macd = _coerce_float(snapshot.get("macd"))
        macd_signal = _coerce_float(snapshot.get("macd_signal"))
        if macd is not None and macd_signal is not None:
            score += 10 if macd > macd_signal else -10

        close = _coerce_float(snapshot.get("close"))
        ema_50 = _coerce_float(snapshot.get("ema_50"))
        ema_200 = _coerce_float(snapshot.get("ema_200"))

        # EMA200 üstü/altı orta vade trend onayı.
        if close is not None and ema_200 is not None:
            score += 15 if close > ema_200 else -15

        # EMA50 üstü/altı ek trend onayı.
        if close is not None and ema_50 is not None:
            score += 10 if close > ema_50 else -10

        ema_20 = _coerce_float(snapshot.get("ema_20"))
        if ema_20 is not None and ema_50 is not None and ema_200 is not None:
            if ema_20 > ema_50 > ema_200:
                score += 5
            elif ema_20 < ema_50 < ema_200:
                score -= 5

        return _clamp(score)

    def score_fundamental(
        self, snapshot: Optional[FundamentalSnapshot]
    ) -> float:
        """Fundamental skor — ``FundamentalAnalyzer.score`` delegasyonu.

        Snapshot ``None`` ise 50.0 (nötr, bilgi yok) döndürülür.
        """

        if snapshot is None:
            return 50.0
        return self.fundamental_analyzer.score(snapshot)

    def score_sentiment(
        self, sentiment_score: Optional[float], count: int
    ) -> float:
        """Sentiment skoru — kısa vade ile aynı mantık."""

        if sentiment_score is None or count <= 0:
            return 50.0
        s = max(-1.0, min(1.0, float(sentiment_score)))
        base = 50.0 + s * 50.0
        if count < MIN_SENTIMENT_COUNT:
            base = 50.0 + (base - 50.0) * 0.5
        return _clamp(base)

    def calculate_risk(
        self,
        snapshot: dict,
        fundamental: Optional[FundamentalSnapshot] = None,
    ) -> tuple[str, float]:
        """ATR/close + beta (varsa) ile risk skoru.

        - Teknik kısım kısa vade ile aynı (ATR %).
        - ``fundamental.beta > 1.5`` → risk skoru +10.
        - ``fundamental.debt_to_equity > 2`` → risk skoru +5.
        """

        close = _coerce_float(snapshot.get("close"))
        atr = _coerce_float(snapshot.get("atr"))

        atr_pct = None
        if close is not None and atr is not None and close > 0:
            atr_pct = (atr / close) * 100.0

        if atr_pct is None:
            level = "medium"
            risk_score = 50.0
        elif atr_pct < RISK_LOW_MAX_ATR_PCT:
            level = "low"
            risk_score = (atr_pct / RISK_LOW_MAX_ATR_PCT) * 33.0
        elif atr_pct >= RISK_HIGH_MIN_ATR_PCT:
            level = "high"
            risk_score = _clamp(66.0 + (atr_pct - RISK_HIGH_MIN_ATR_PCT) * 6.0)
        else:
            level = "medium"
            span = RISK_HIGH_MIN_ATR_PCT - RISK_LOW_MAX_ATR_PCT
            risk_score = 33.0 + ((atr_pct - RISK_LOW_MAX_ATR_PCT) / span) * 33.0

        if fundamental is not None:
            beta = _coerce_float(fundamental.beta)
            if beta is not None and beta > 1.5:
                risk_score = _clamp(risk_score + 10.0)
                if level == "low":
                    level = "medium"
            de = _coerce_float(fundamental.debt_to_equity)
            if de is not None and de > 2.0:
                risk_score = _clamp(risk_score + 5.0)

        return level, _clamp(risk_score)

    # ------------------------------------------------------------- öneri

    def recommend(self, inputs: MidTermInput) -> RecommendationOutput:
        tech = self.score_technical(inputs.technical_snapshot)
        fund = self.score_fundamental(inputs.fundamental_snapshot)
        sent = self.score_sentiment(inputs.sentiment_score, inputs.sentiment_count)

        composite = (
            tech * self.weights["tech"]
            + fund * self.weights["fundamental"]
            + sent * self.weights["sentiment"]
        )

        if composite >= BUY_THRESHOLD:
            action = "BUY"
        elif composite <= SELL_THRESHOLD:
            action = "SELL"
        else:
            action = "HOLD"

        confidence = _clamp(abs(composite - 50.0) * 2.0)
        risk_level, risk_score = self.calculate_risk(
            inputs.technical_snapshot, inputs.fundamental_snapshot
        )

        target_price = self._suggest_target_mid(
            action,
            inputs.technical_snapshot,
            inputs.fundamental_snapshot,
            inputs.current_price,
        )

        contributions = {
            "tech": tech * self.weights["tech"],
            "fundamental": fund * self.weights["fundamental"],
            "sentiment": sent * self.weights["sentiment"],
        }

        summary = self._build_summary(
            action=action,
            confidence=confidence,
            tech=tech,
            fund=fund,
            sent=sent,
            snapshot=inputs.technical_snapshot,
            fundamental=inputs.fundamental_snapshot,
            sentiment_count=inputs.sentiment_count,
            sentiment_score=inputs.sentiment_score,
            risk_level=risk_level,
        )

        return RecommendationOutput(
            action=action,
            timeframe=self.TIMEFRAME,
            confidence=confidence,
            tech_score=tech,
            sentiment_score=sent,
            summary=summary,
            risk_level=risk_level,
            risk_score=risk_score,
            target_price=target_price,
            consensus_score=None,
            fundamental_score=fund,
            contributions=contributions,
        )

    # --------------------------------------------------- yardımcı: hedef

    @staticmethod
    def _suggest_target_mid(
        action: str,
        snapshot: dict,
        fundamental: Optional[FundamentalSnapshot],
        current_price: Decimal,
    ) -> Optional[Decimal]:
        """Orta vade hedef fiyat: analist hedefi varsa onu, yoksa +%8 fallback.

        Bollinger üst bandı + 2×ATR ile sınırlı tutulur (aşırı iyimserliği
        keser).
        """

        if action != "BUY":
            return None

        try:
            current = Decimal(current_price)
        except (TypeError, ValueError):
            return None

        candidates: list[Decimal] = []
        if fundamental is not None:
            tp = _coerce_float(fundamental.target_price)
            if tp is not None and tp > float(current):
                candidates.append(Decimal(str(tp)))

        bb_upper = _coerce_float(snapshot.get("bb_upper"))
        atr = _coerce_float(snapshot.get("atr"))
        if atr is not None:
            candidates.append(current + Decimal(str(atr)) * Decimal("2"))
        if bb_upper is not None and bb_upper > float(current):
            candidates.append(Decimal(str(bb_upper)))

        if not candidates:
            return current * Decimal("1.08")  # +%8 fallback orta vade
        return max(candidates)

    # --------------------------------------------------- yardımcı: özet

    @staticmethod
    def _build_summary(
        *,
        action: str,
        confidence: float,
        tech: float,
        fund: float,
        sent: float,
        snapshot: dict,
        fundamental: Optional[FundamentalSnapshot],
        sentiment_count: int,
        sentiment_score: Optional[float],
        risk_level: str,
    ) -> str:
        """Orta vade Türkçe açıklama metni."""

        action_tr = {"BUY": "AL", "HOLD": "BEKLE", "SELL": "SAT"}.get(action, action)
        parts: list[str] = [
            f"Orta vade {action_tr} önerisi (güven %{confidence:.0f})."
        ]

        # Fundamental kırılım — orta vadede ön planda.
        if fundamental is None:
            parts.append("Fundamental: veri yok (skor nötr 50/100).")
        else:
            fund_bits: list[str] = []
            pe = _coerce_float(fundamental.pe_ratio)
            if pe is not None:
                fund_bits.append(f"F/K={pe:.1f}")
            pb = _coerce_float(fundamental.pb_ratio)
            if pb is not None:
                fund_bits.append(f"PD/DD={pb:.2f}")
            g = _coerce_float(fundamental.growth_yoy)
            if g is not None:
                fund_bits.append(f"büyüme {g * 100:+.1f}%")
            roe = _coerce_float(fundamental.roe)
            if roe is not None:
                fund_bits.append(f"ROE {roe * 100:.1f}%")
            fund_tone = (
                "güçlü" if fund >= 60 else "zayıf" if fund <= 40 else "nötr"
            )
            parts.append(
                f"Fundamental {fund_tone} (skor {fund:.0f}/100"
                + (f"; {', '.join(fund_bits)})." if fund_bits else ").")
            )

        # Teknik kırılım — trend odaklı.
        tech_bits: list[str] = []
        rsi = _coerce_float(snapshot.get("rsi"))
        if rsi is not None:
            tech_bits.append(f"RSI={rsi:.1f}")
        close = _coerce_float(snapshot.get("close"))
        ema_200 = _coerce_float(snapshot.get("ema_200"))
        if close is not None and ema_200 is not None:
            tech_bits.append(
                "EMA200 üstünde" if close > ema_200 else "EMA200 altında"
            )
        tech_tone = "pozitif" if tech >= 60 else "negatif" if tech <= 40 else "nötr"
        parts.append(
            f"Teknik {tech_tone} (skor {tech:.0f}/100"
            + (f"; {', '.join(tech_bits)})." if tech_bits else ").")
        )

        # Sentiment kırılım.
        if sentiment_count <= 0 or sentiment_score is None:
            parts.append("Sentiment: haber bulunamadı.")
        else:
            sent_tone = (
                "pozitif" if sent >= 60 else "negatif" if sent <= 40 else "nötr"
            )
            low_n = (
                " (örneklem düşük)" if sentiment_count < MIN_SENTIMENT_COUNT else ""
            )
            parts.append(
                f"Sentiment {sent_tone} "
                f"({sentiment_count} haberden ortalama {sentiment_score:+.2f}"
                f"{low_n})."
            )

        risk_tr = {"low": "düşük", "medium": "orta", "high": "yüksek"}.get(
            risk_level, risk_level
        )
        parts.append(f"Risk: {risk_tr}.")
        return " ".join(parts)

    # ---------------------------------------------------------- DB persist

    def save_recommendation(
        self, session, output: RecommendationOutput, instrument_id: int
    ):
        """Orta vade önerisini ``recommendations`` tablosuna yazar.

        Sadece sembolik delegasyon — `ShortTermRecommender.save_recommendation`
        ile aynı imza ve davranış (timeframe alanı çıktıdan gelir).
        """

        from app.db.models import Recommendation  # noqa: WPS433

        row = Recommendation(
            instrument_id=instrument_id,
            generated_at=datetime.now(tz=timezone.utc),
            action=output.action,
            timeframe=output.timeframe,
            confidence=float(output.confidence),
            risk_level=output.risk_level,
            risk_score=float(output.risk_score),
            target_price=(
                float(output.target_price) if output.target_price is not None else None
            ),
            stop_loss=(
                float(output.stop_loss) if output.stop_loss is not None else None
            ),
            take_profit=(
                float(output.take_profit) if output.take_profit is not None else None
            ),
            summary=output.summary,
            tech_score=float(output.tech_score),
            sentiment_score=float(output.sentiment_score),
            fundamental_score=(
                float(output.fundamental_score)
                if output.fundamental_score is not None
                else None
            ),
        )
        session.add(row)
        return row


# ---------------------------------------------------------------------------
# Uzun vade öneri motoru (3-12 ay+)
# ---------------------------------------------------------------------------


class LongTermRecommender:
    """Uzun vade (3-12 ay+) öneri motoru.

    Ağırlıklar Doküman §5.2: ``%50 fundamental + %30 teknik + %20
    sentiment``. Fundamental ağırlığı bu vadede dominant olduğu için
    snapshot yoksa öneri otomatik olarak HOLD'a yakın bir bileşik üretir.
    """

    TIMEFRAME = "long"

    def __init__(
        self,
        fundamental_analyzer: Optional[FundamentalAnalyzer] = None,
        weights: Optional[dict[str, float]] = None,
    ) -> None:
        self.fundamental_analyzer = fundamental_analyzer or FundamentalAnalyzer()
        self.weights = dict(weights) if weights else dict(LONG_TERM_WEIGHTS)
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            raise ValueError(
                f"LongTermRecommender ağırlık toplamı 1.0 olmalı, alındı: "
                f"{total:.4f} (weights={self.weights})"
            )

    # ------------------------------------------------------------- skorlar

    def score_technical(self, snapshot: dict) -> float:
        """Uzun vade için EMA200 trendi ağırlıklı teknik skor.

        - Close > EMA200 → +20 (uzun trend yukarı)
        - Close < EMA200 → −20
        - MACD pozitif → +10 / negatif → −10
        - RSI < 25 → +5 (aşırı satım fırsatı), RSI > 75 → −5
        """

        score = 50.0

        close = _coerce_float(snapshot.get("close"))
        ema_200 = _coerce_float(snapshot.get("ema_200"))
        if close is not None and ema_200 is not None:
            score += 20 if close > ema_200 else -20

        macd = _coerce_float(snapshot.get("macd"))
        macd_signal = _coerce_float(snapshot.get("macd_signal"))
        if macd is not None and macd_signal is not None:
            score += 10 if macd > macd_signal else -10

        rsi = _coerce_float(snapshot.get("rsi"))
        if rsi is not None:
            if rsi < 25:
                score += 5
            elif rsi > 75:
                score -= 5

        return _clamp(score)

    def score_fundamental(
        self, snapshot: Optional[FundamentalSnapshot]
    ) -> float:
        if snapshot is None:
            return 50.0
        return self.fundamental_analyzer.score(snapshot)

    def score_sentiment(
        self, sentiment_score: Optional[float], count: int
    ) -> float:
        if sentiment_score is None or count <= 0:
            return 50.0
        s = max(-1.0, min(1.0, float(sentiment_score)))
        base = 50.0 + s * 50.0
        if count < MIN_SENTIMENT_COUNT:
            base = 50.0 + (base - 50.0) * 0.5
        return _clamp(base)

    def calculate_risk(
        self,
        snapshot: dict,
        fundamental: Optional[FundamentalSnapshot] = None,
    ) -> tuple[str, float]:
        """Uzun vade risk: temel oranlar (beta, D/E) baskın."""

        # Teknik bileşen olarak ATR — fakat ağırlık daha düşük.
        close = _coerce_float(snapshot.get("close"))
        atr = _coerce_float(snapshot.get("atr"))
        atr_pct = None
        if close is not None and atr is not None and close > 0:
            atr_pct = (atr / close) * 100.0

        if atr_pct is None:
            risk_score = 50.0
        else:
            # ATR'yi 0..40 ölçeğine taşı (uzun vadede tek başına belirleyici
            # değil).
            risk_score = _clamp(atr_pct * 8.0, 0.0, 40.0)

        if fundamental is not None:
            beta = _coerce_float(fundamental.beta)
            if beta is not None:
                if beta > 1.5:
                    risk_score = _clamp(risk_score + 25.0)
                elif beta > 1.0:
                    risk_score = _clamp(risk_score + 10.0)
            de = _coerce_float(fundamental.debt_to_equity)
            if de is not None and de > 2.0:
                risk_score = _clamp(risk_score + 15.0)
            growth = _coerce_float(fundamental.growth_yoy)
            if growth is not None and growth < 0:
                risk_score = _clamp(risk_score + 10.0)
        else:
            # Fundamental yoksa belirsizlik primi ekle.
            risk_score = _clamp(risk_score + 15.0)

        if risk_score < 33:
            level = "low"
        elif risk_score < 66:
            level = "medium"
        else:
            level = "high"

        return level, _clamp(risk_score)

    # ------------------------------------------------------------- öneri

    def recommend(self, inputs: LongTermInput) -> RecommendationOutput:
        tech = self.score_technical(inputs.technical_snapshot)
        fund = self.score_fundamental(inputs.fundamental_snapshot)
        sent = self.score_sentiment(inputs.sentiment_score, inputs.sentiment_count)

        composite = (
            tech * self.weights["tech"]
            + fund * self.weights["fundamental"]
            + sent * self.weights["sentiment"]
        )

        if composite >= BUY_THRESHOLD:
            action = "BUY"
        elif composite <= SELL_THRESHOLD:
            action = "SELL"
        else:
            action = "HOLD"

        confidence = _clamp(abs(composite - 50.0) * 2.0)
        risk_level, risk_score = self.calculate_risk(
            inputs.technical_snapshot, inputs.fundamental_snapshot
        )

        target_price = self._suggest_target_long(
            action, inputs.fundamental_snapshot, inputs.current_price
        )

        contributions = {
            "fundamental": fund * self.weights["fundamental"],
            "tech": tech * self.weights["tech"],
            "sentiment": sent * self.weights["sentiment"],
        }

        summary = self._build_summary(
            action=action,
            confidence=confidence,
            tech=tech,
            fund=fund,
            sent=sent,
            snapshot=inputs.technical_snapshot,
            fundamental=inputs.fundamental_snapshot,
            sentiment_count=inputs.sentiment_count,
            sentiment_score=inputs.sentiment_score,
            risk_level=risk_level,
        )

        return RecommendationOutput(
            action=action,
            timeframe=self.TIMEFRAME,
            confidence=confidence,
            tech_score=tech,
            sentiment_score=sent,
            summary=summary,
            risk_level=risk_level,
            risk_score=risk_score,
            target_price=target_price,
            consensus_score=None,
            fundamental_score=fund,
            contributions=contributions,
        )

    @staticmethod
    def _suggest_target_long(
        action: str,
        fundamental: Optional[FundamentalSnapshot],
        current_price: Decimal,
    ) -> Optional[Decimal]:
        """Uzun vade hedef: analist hedefi > current ise onu, yoksa +%20 fallback."""

        if action != "BUY":
            return None
        try:
            current = Decimal(current_price)
        except (TypeError, ValueError):
            return None

        if fundamental is not None:
            tp = _coerce_float(fundamental.target_price)
            if tp is not None and tp > float(current):
                return Decimal(str(tp))
        return current * Decimal("1.20")

    @staticmethod
    def _build_summary(
        *,
        action: str,
        confidence: float,
        tech: float,
        fund: float,
        sent: float,
        snapshot: dict,
        fundamental: Optional[FundamentalSnapshot],
        sentiment_count: int,
        sentiment_score: Optional[float],
        risk_level: str,
    ) -> str:
        action_tr = {"BUY": "AL", "HOLD": "BEKLE", "SELL": "SAT"}.get(action, action)
        parts: list[str] = [
            f"Uzun vade {action_tr} önerisi (güven %{confidence:.0f})."
        ]

        # Fundamental ağırlıkta — önce göster.
        if fundamental is None:
            parts.append(
                "Fundamental: veri yok — uzun vade analiz güveni düşük "
                "(skor nötr 50/100)."
            )
        else:
            fund_bits: list[str] = []
            pe = _coerce_float(fundamental.pe_ratio)
            if pe is not None:
                fund_bits.append(f"F/K={pe:.1f}")
            roe = _coerce_float(fundamental.roe)
            if roe is not None:
                fund_bits.append(f"ROE %{roe * 100:.1f}")
            de = _coerce_float(fundamental.debt_to_equity)
            if de is not None:
                fund_bits.append(f"D/E={de:.2f}")
            g = _coerce_float(fundamental.growth_yoy)
            if g is not None:
                fund_bits.append(f"büyüme {g * 100:+.1f}%")
            fund_tone = (
                "güçlü" if fund >= 60 else "zayıf" if fund <= 40 else "ortalama"
            )
            parts.append(
                f"Fundamental {fund_tone} (skor {fund:.0f}/100"
                + (f"; {', '.join(fund_bits)})." if fund_bits else ").")
            )

        # Uzun trend teknik.
        close = _coerce_float(snapshot.get("close"))
        ema_200 = _coerce_float(snapshot.get("ema_200"))
        trend_label = ""
        if close is not None and ema_200 is not None:
            trend_label = (
                "EMA200 üstünde (uzun trend yukarı)"
                if close > ema_200
                else "EMA200 altında (uzun trend aşağı)"
            )
        tech_tone = "pozitif" if tech >= 60 else "negatif" if tech <= 40 else "nötr"
        if trend_label:
            parts.append(f"Teknik {tech_tone} (skor {tech:.0f}/100; {trend_label}).")
        else:
            parts.append(f"Teknik {tech_tone} (skor {tech:.0f}/100).")

        # Sentiment.
        if sentiment_count <= 0 or sentiment_score is None:
            parts.append("Sentiment: haber bulunamadı.")
        else:
            sent_tone = (
                "pozitif" if sent >= 60 else "negatif" if sent <= 40 else "nötr"
            )
            low_n = (
                " (örneklem düşük)" if sentiment_count < MIN_SENTIMENT_COUNT else ""
            )
            parts.append(
                f"Sentiment {sent_tone} "
                f"({sentiment_count} haberden ortalama {sentiment_score:+.2f}"
                f"{low_n})."
            )

        risk_tr = {"low": "düşük", "medium": "orta", "high": "yüksek"}.get(
            risk_level, risk_level
        )
        parts.append(f"Risk: {risk_tr}.")
        return " ".join(parts)

    def save_recommendation(
        self, session, output: RecommendationOutput, instrument_id: int
    ):
        """Uzun vade önerisini ``recommendations`` tablosuna yazar."""

        from app.db.models import Recommendation  # noqa: WPS433

        row = Recommendation(
            instrument_id=instrument_id,
            generated_at=datetime.now(tz=timezone.utc),
            action=output.action,
            timeframe=output.timeframe,
            confidence=float(output.confidence),
            risk_level=output.risk_level,
            risk_score=float(output.risk_score),
            target_price=(
                float(output.target_price) if output.target_price is not None else None
            ),
            stop_loss=(
                float(output.stop_loss) if output.stop_loss is not None else None
            ),
            take_profit=(
                float(output.take_profit) if output.take_profit is not None else None
            ),
            summary=output.summary,
            tech_score=float(output.tech_score),
            sentiment_score=float(output.sentiment_score),
            fundamental_score=(
                float(output.fundamental_score)
                if output.fundamental_score is not None
                else None
            ),
        )
        session.add(row)
        return row


# ---------------------------------------------------------------------------
# Dispatcher — timeframe → doğru recommender
# ---------------------------------------------------------------------------


class RecommendationDispatcher:
    """Vade parametresine göre doğru recommender'a yönlendiren fasad.

    Kullanım:

    >>> dispatcher = RecommendationDispatcher()
    >>> dispatcher.recommend("short", short_input)
    RecommendationOutput(action='BUY', timeframe='short', ...)
    >>> dispatcher.recommend("mid", mid_input)
    >>> dispatcher.recommend("long", long_input)

    Recommender'lar varsayılan ağırlıklarla kurulur; testte override
    edilebilir (``self.short = ...``).
    """

    VALID_TIMEFRAMES = ("short", "mid", "long")

    def __init__(
        self,
        fundamental_analyzer: Optional[FundamentalAnalyzer] = None,
    ) -> None:
        self.short = ShortTermRecommender()
        self.mid = MidTermRecommender(fundamental_analyzer=fundamental_analyzer)
        self.long = LongTermRecommender(fundamental_analyzer=fundamental_analyzer)

    def recommend(self, timeframe: str, inputs) -> RecommendationOutput:
        """``timeframe`` ∈ {short, mid, long}.

        Yanlış vade ise ``ValueError``. Tip uyuşmazlığında (örn. mid vade
        için ``RecommendationInput`` verilmiş) ``TypeError`` fırlatılır.
        """

        tf = (timeframe or "").lower()
        if tf == "short":
            if not isinstance(inputs, RecommendationInput):
                raise TypeError(
                    "Kısa vade için RecommendationInput bekleniyor, "
                    f"alındı: {type(inputs).__name__}"
                )
            return self.short.recommend(inputs)
        if tf == "mid":
            if not isinstance(inputs, MidTermInput):
                raise TypeError(
                    "Orta vade için MidTermInput bekleniyor, "
                    f"alındı: {type(inputs).__name__}"
                )
            return self.mid.recommend(inputs)
        if tf == "long":
            if not isinstance(inputs, LongTermInput):
                raise TypeError(
                    "Uzun vade için LongTermInput bekleniyor, "
                    f"alındı: {type(inputs).__name__}"
                )
            return self.long.recommend(inputs)
        raise ValueError(
            f"Geçersiz timeframe={timeframe!r}; biri olmalı: {self.VALID_TIMEFRAMES}"
        )


__all__ = [
    "BUY_THRESHOLD",
    "SELL_THRESHOLD",
    "SHORT_TERM_WEIGHTS",
    "MID_TERM_WEIGHTS",
    "LONG_TERM_WEIGHTS",
    "MIN_SENTIMENT_COUNT",
    "RISK_LOW_MAX_ATR_PCT",
    "RISK_HIGH_MIN_ATR_PCT",
    "RecommendationInput",
    "RecommendationOutput",
    "MidTermInput",
    "LongTermInput",
    "ShortTermRecommender",
    "MidTermRecommender",
    "LongTermRecommender",
    "RecommendationDispatcher",
]
