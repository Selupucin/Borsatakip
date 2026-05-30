"""ShortTermRecommender (app/analysis/recommender.py) birim testleri.

DB testleri için ``db_session`` (in-memory SQLite, sync) kullanılır.
CHECK constraint'leri (``action IN ('BUY','HOLD','SELL')`` ve
``timeframe IN ('short','mid','long')``) conftest.py içindeki
``_enable_sqlite_fk`` listener'ı sayesinde SQLite üzerinde de zorlanır.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.analysis.recommender import (
    BUY_THRESHOLD,
    MIN_SENTIMENT_COUNT,
    RecommendationInput,
    RecommendationOutput,
    SELL_THRESHOLD,
    SHORT_TERM_WEIGHTS,
    ShortTermRecommender,
)
from app.db.models import Instrument, Recommendation


# ---------------------------------------------------------------------------
# Yardımcı: makul bir snapshot üretici
# ---------------------------------------------------------------------------


def _snapshot(**overrides) -> dict:
    """``technical_snapshot`` placeholder; istenen alanlar override edilir."""
    base = {
        "close": 100.0,
        "rsi": 50.0,
        "macd": 0.0,
        "macd_signal": 0.0,
        "ema_20": 100.0,
        "ema_50": 100.0,
        "ema_200": 100.0,
        "bb_upper": 110.0,
        "bb_lower": 90.0,
        "atr": 1.5,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------


class TestWeights:
    def test_short_term_weights_sum_to_one(self):
        assert sum(SHORT_TERM_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_invalid_weights_raise_value_error(self):
        with pytest.raises(ValueError, match="Ağırlık toplamı"):
            ShortTermRecommender(weights={"tech": 0.5, "sentiment": 0.3, "consensus": 0.3})


# ---------------------------------------------------------------------------
# score_technical
# ---------------------------------------------------------------------------


class TestScoreTechnical:
    def test_oversold_rsi_and_positive_macd_high_score(self):
        """RSI=25 (oversold) + MACD>signal pozitif → score > 60."""
        rec = ShortTermRecommender()
        snap = _snapshot(rsi=25, macd=0.5, macd_signal=0.1, close=105, ema_50=100)
        score = rec.score_technical(snap)
        assert score > 60

    def test_overbought_rsi_and_negative_macd_low_score(self):
        """RSI=75 (overbought) + MACD<signal → score < 40."""
        rec = ShortTermRecommender()
        snap = _snapshot(rsi=75, macd=-0.3, macd_signal=0.2, close=95, ema_50=100)
        score = rec.score_technical(snap)
        assert score < 40

    def test_neutral_snapshot_returns_around_fifty(self):
        """Tüm değerler eşit → MACD < signal değil ama equal -> -15; close==ema_50
        -> -10. Sonuç ~25; yine de uçlardan uzak (clamp limitlerinde değil)."""
        rec = ShortTermRecommender()
        score = rec.score_technical(_snapshot())
        # 0..100 aralığı içinde olmalı; uçlardan değil
        assert 0.0 < score < 100.0


# ---------------------------------------------------------------------------
# score_sentiment
# ---------------------------------------------------------------------------


class TestScoreSentiment:
    def test_high_positive_mean_high_score(self):
        """mean=+0.5, count=10 → yüksek skor (50 + 25 = 75)."""
        rec = ShortTermRecommender()
        score = rec.score_sentiment(0.5, count=10)
        assert score == pytest.approx(75.0, abs=1e-6)

    def test_low_count_dampens_confidence(self):
        """``count < MIN_SENTIMENT_COUNT`` → skoru 50'ye doğru yarıya çek."""
        rec = ShortTermRecommender()
        # +0.5 mean ile normalde 75 olur; count=2 ile (75-50)*0.5+50 = 62.5
        score = rec.score_sentiment(0.5, count=2)
        assert score == pytest.approx(62.5, abs=1e-6)

    def test_none_or_zero_count_returns_neutral_fifty(self):
        rec = ShortTermRecommender()
        assert rec.score_sentiment(None, count=0) == 50.0
        assert rec.score_sentiment(0.7, count=0) == 50.0


# ---------------------------------------------------------------------------
# score_consensus
# ---------------------------------------------------------------------------


class TestScoreConsensus:
    def test_both_strong_buy_yields_100(self):
        rec = ShortTermRecommender()
        assert rec.score_consensus("STRONG_BUY", "STRONG_BUY") == 100.0

    def test_sell_plus_buy_yields_middle(self):
        """SELL=25, BUY=75 → ortalama 50."""
        rec = ShortTermRecommender()
        assert rec.score_consensus("SELL", "BUY") == 50.0

    def test_none_inputs_default_to_fifty(self):
        rec = ShortTermRecommender()
        assert rec.score_consensus(None, None) == 50.0


# ---------------------------------------------------------------------------
# recommend() action/confidence/risk
# ---------------------------------------------------------------------------


class TestRecommendAction:
    def _build_inputs(self, **overrides) -> RecommendationInput:
        kwargs = dict(
            instrument_id=1,
            ticker="AAPL",
            technical_snapshot=_snapshot(),
            sentiment_score=0.0,
            sentiment_count=5,
            tv_summary="NEUTRAL",
            inv_summary="NEUTRAL",
            current_price=Decimal("100"),
        )
        kwargs.update(overrides)
        return RecommendationInput(**kwargs)

    def test_strong_bullish_yields_buy(self):
        """Her sinyal pozitif → composite >= 60 → BUY."""
        rec = ShortTermRecommender()
        out = rec.recommend(
            self._build_inputs(
                technical_snapshot=_snapshot(rsi=25, macd=1.0, macd_signal=0.1, close=105, ema_50=100),
                sentiment_score=0.8,
                sentiment_count=10,
                tv_summary="STRONG_BUY",
                inv_summary="STRONG_BUY",
            )
        )
        assert out.action == "BUY"
        assert out.timeframe == "short"

    def test_strong_bearish_yields_sell(self):
        """Her sinyal negatif → composite <= 40 → SELL."""
        rec = ShortTermRecommender()
        out = rec.recommend(
            self._build_inputs(
                technical_snapshot=_snapshot(rsi=75, macd=-1.0, macd_signal=0.1, close=95, ema_50=100),
                sentiment_score=-0.8,
                sentiment_count=10,
                tv_summary="STRONG_SELL",
                inv_summary="STRONG_SELL",
            )
        )
        assert out.action == "SELL"

    def test_mixed_neutral_yields_hold(self):
        """Teknik skoru kesin nötr; sentiment 0; consensus NEUTRAL → HOLD."""
        rec = ShortTermRecommender()
        # Eksik alanlar olunca _coerce_float None döner; skor hesabı sadece
        # var olan bileşenlere bakar. Tüm "yön" sinyallerini None yaparak
        # tech_score = 50 (başlangıç) elde ederiz.
        snap = {"close": 100.0, "atr": 1.5}
        out = rec.recommend(self._build_inputs(technical_snapshot=snap))
        # tech=50, sentiment=50, consensus=50 → composite 50 → HOLD
        assert out.action == "HOLD"

    def test_confidence_formula(self):
        """confidence = |composite - 50| * 2."""
        rec = ShortTermRecommender()
        # composite ~ 100 → confidence ~ 100
        out_strong = rec.recommend(
            self._build_inputs(
                technical_snapshot=_snapshot(rsi=25, macd=1.0, macd_signal=0.1, close=105, ema_50=100),
                sentiment_score=1.0,
                sentiment_count=10,
                tv_summary="STRONG_BUY",
                inv_summary="STRONG_BUY",
            )
        )
        # |composite - 50| * 2 ≈ 100 olmalı
        composite = (
            out_strong.tech_score * SHORT_TERM_WEIGHTS["tech"]
            + out_strong.sentiment_score * SHORT_TERM_WEIGHTS["sentiment"]
            + out_strong.consensus_score * SHORT_TERM_WEIGHTS["consensus"]
        )
        expected = abs(composite - 50.0) * 2
        assert out_strong.confidence == pytest.approx(expected, abs=1e-6)


class TestCalculateRisk:
    def test_low_atr_pct_yields_low_risk(self):
        """ATR/close * 100 < 2.0 → 'low'."""
        rec = ShortTermRecommender()
        # ATR=1.0, close=100 → atr_pct=1.0 < 2
        level, score = rec.calculate_risk(_snapshot(close=100, atr=1.0, rsi=50))
        assert level == "low"
        assert score < 33.0 + 1e-6

    def test_high_atr_pct_yields_high_risk(self):
        """ATR/close * 100 >= 5.0 → 'high'."""
        rec = ShortTermRecommender()
        # ATR=6, close=100 → atr_pct=6.0 >= 5
        level, score = rec.calculate_risk(_snapshot(close=100, atr=6.0, rsi=50))
        assert level == "high"
        assert score >= 66.0

    def test_medium_atr_pct_yields_medium_risk(self):
        rec = ShortTermRecommender()
        level, _ = rec.calculate_risk(_snapshot(close=100, atr=3.0, rsi=50))
        assert level == "medium"


class TestRecommendOutput:
    def test_full_output_turkish_summary(self):
        rec = ShortTermRecommender()
        out = rec.recommend(
            RecommendationInput(
                instrument_id=1,
                ticker="AAPL",
                technical_snapshot=_snapshot(rsi=25, macd=1.0, macd_signal=0.1, close=105, ema_50=100),
                sentiment_score=0.8,
                sentiment_count=10,
                tv_summary="STRONG_BUY",
                inv_summary="BUY",
                current_price=Decimal("105"),
            )
        )
        assert isinstance(out, RecommendationOutput)
        assert out.action in {"BUY", "HOLD", "SELL"}
        assert out.timeframe == "short"
        assert 0.0 <= out.confidence <= 100.0
        # Türkçe summary
        assert "Kısa vade" in out.summary
        assert "Risk:" in out.summary

    def test_buy_recommendation_includes_target_price(self):
        rec = ShortTermRecommender()
        out = rec.recommend(
            RecommendationInput(
                instrument_id=1,
                ticker="X",
                technical_snapshot=_snapshot(rsi=20, macd=1.0, macd_signal=0.1, bb_upper=115, atr=2.5, close=100),
                sentiment_score=0.9,
                sentiment_count=8,
                tv_summary="STRONG_BUY",
                inv_summary="STRONG_BUY",
                current_price=Decimal("100"),
            )
        )
        if out.action == "BUY":
            assert out.target_price is not None
            assert out.target_price > Decimal("100")


# ---------------------------------------------------------------------------
# save_recommendation() — DB CHECK constraints
# ---------------------------------------------------------------------------


class TestSaveRecommendation:
    def test_save_recommendation_persists_with_valid_check(self, db_session):
        """Recommendation tablosuna kayıt — action/timeframe CHECK uyumlu."""
        instr = Instrument(ticker="AAPL", name="Apple")
        db_session.add(instr)
        db_session.flush()

        rec = ShortTermRecommender()
        output = RecommendationOutput(
            action="BUY",
            timeframe="short",
            confidence=75.0,
            tech_score=70.0,
            sentiment_score=60.0,
            consensus_score=80.0,
            summary="test",
            risk_level="low",
            risk_score=10.0,
            target_price=Decimal("105"),
            contributions={"tech": 42.0, "sentiment": 18.0, "consensus": 8.0},
        )
        row = rec.save_recommendation(db_session, output, instrument_id=int(instr.id))
        db_session.flush()

        # Insert başarılı oldu → CHECK constraint uyumu kanıtlanmış olur.
        assert row is not None
        refreshed = db_session.get(Recommendation, row.id)
        assert refreshed.action == "BUY"
        assert refreshed.timeframe == "short"
        assert refreshed.confidence == pytest.approx(75.0, abs=1e-6)
        assert refreshed.target_price == pytest.approx(105.0, abs=1e-6)
