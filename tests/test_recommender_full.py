"""Mid + Long + Dispatcher recommender testleri (Faz 3 Batch 1).

ShortTermRecommender testleri `tests/test_recommender.py`'da; bu dosya
orta + uzun vade ve dispatcher mantığını kapsar.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.analysis.fundamental import FundamentalSnapshot
from app.analysis.recommender import (
    LONG_TERM_WEIGHTS,
    MID_TERM_WEIGHTS,
    LongTermInput,
    LongTermRecommender,
    MidTermInput,
    MidTermRecommender,
    RecommendationDispatcher,
    RecommendationInput,
    RecommendationOutput,
    ShortTermRecommender,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tech_snapshot(**overrides) -> dict:
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
        "atr": 2.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Sabitler (ağırlık toplamı)
# ---------------------------------------------------------------------------


class TestWeightSums:
    def test_mid_term_weights_sum_to_one(self):
        assert sum(MID_TERM_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_long_term_weights_sum_to_one(self):
        assert sum(LONG_TERM_WEIGHTS.values()) == pytest.approx(1.0, abs=1e-9)

    def test_mid_term_invalid_weights_raise(self):
        with pytest.raises(ValueError):
            MidTermRecommender(
                weights={"tech": 0.5, "fundamental": 0.3, "sentiment": 0.3}
            )

    def test_long_term_invalid_weights_raise(self):
        with pytest.raises(ValueError):
            LongTermRecommender(
                weights={"fundamental": 0.6, "tech": 0.3, "sentiment": 0.3}
            )


# ---------------------------------------------------------------------------
# MidTermRecommender
# ---------------------------------------------------------------------------


class TestMidTermRecommender:
    def test_score_fundamental_none_returns_50(self):
        rec = MidTermRecommender()
        assert rec.score_fundamental(None) == 50.0

    def test_score_fundamental_with_snapshot(self):
        rec = MidTermRecommender()
        snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", pe_ratio=8.0, growth_yoy=0.15
        )
        score = rec.score_fundamental(snap)
        # 50 + 10 (PE) + 15 (growth) = 75
        assert score == pytest.approx(75.0)

    def test_recommend_buy_when_all_signals_positive(self):
        """tech~80, fund~70, sent~60 → composite > 60 → BUY."""
        rec = MidTermRecommender()
        # Tech: close>EMA200(+15), close>EMA50(+10), MACD>signal(+10),
        # ema_20>ema_50>ema_200(+5) → 50+15+10+10+5 = 90
        tech_snap = _tech_snapshot(
            close=120.0, ema_50=110.0, ema_200=100.0, ema_20=115.0,
            macd=0.5, macd_signal=0.1,
        )
        fund_snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", pe_ratio=8.0, growth_yoy=0.15
        )
        inputs = MidTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot=tech_snap,
            sentiment_score=0.4,
            sentiment_count=10,
            fundamental_snapshot=fund_snap,
            current_price=Decimal("120"),
        )
        out = rec.recommend(inputs)
        assert out.action == "BUY"
        assert out.timeframe == "mid"
        assert out.fundamental_score is not None
        assert out.consensus_score is None

    def test_recommend_hold_when_all_neutral(self):
        rec = MidTermRecommender()
        inputs = MidTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=None,
            sentiment_count=0,
            fundamental_snapshot=None,
            current_price=Decimal("100"),
        )
        out = rec.recommend(inputs)
        # tech=50, fund=50, sent=50 → composite=50 → HOLD
        assert out.action == "HOLD"

    def test_recommend_sell_when_all_negative(self):
        rec = MidTermRecommender()
        tech_snap = _tech_snapshot(
            close=80.0, ema_50=90.0, ema_200=110.0, ema_20=85.0,
            macd=-0.5, macd_signal=0.1, rsi=70,
        )
        fund_snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", pe_ratio=40.0,
            growth_yoy=-0.10, debt_to_equity=3.0,
        )
        inputs = MidTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot=tech_snap,
            sentiment_score=-0.8,
            sentiment_count=10,
            fundamental_snapshot=fund_snap,
            current_price=Decimal("80"),
        )
        out = rec.recommend(inputs)
        assert out.action == "SELL"

    def test_output_timeframe_is_mid(self):
        rec = MidTermRecommender()
        inputs = MidTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=None,
            sentiment_count=0,
            fundamental_snapshot=None,
            current_price=Decimal("100"),
        )
        out = rec.recommend(inputs)
        assert out.timeframe == "mid"


# ---------------------------------------------------------------------------
# LongTermRecommender
# ---------------------------------------------------------------------------


class TestLongTermRecommender:
    def test_score_fundamental_dominant(self):
        """Long vade ağırlıkta fundamental → yüksek F/K skoru düşürür."""
        rec = LongTermRecommender()
        # High F/K → -10 fundamental skor
        high_pe_snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", pe_ratio=40.0
        )
        low_pe_snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", pe_ratio=5.0
        )
        assert rec.score_fundamental(high_pe_snap) < 50.0
        assert rec.score_fundamental(low_pe_snap) > 50.0

    def test_score_technical_uses_ema200(self):
        """Long vade teknik skoru EMA200 trendine duyarlı (+/-20)."""
        rec = LongTermRecommender()
        # close > ema_200 → +20
        snap_up = _tech_snapshot(close=120.0, ema_200=100.0)
        snap_down = _tech_snapshot(close=80.0, ema_200=100.0)
        assert rec.score_technical(snap_up) > rec.score_technical(snap_down)

    def test_recommend_buy_with_strong_fundamentals(self):
        rec = LongTermRecommender()
        tech_snap = _tech_snapshot(close=120.0, ema_200=100.0,
                                   macd=0.5, macd_signal=0.1)
        fund_snap = FundamentalSnapshot(
            instrument_id=1, ticker="X",
            pe_ratio=8.0, pb_ratio=1.2, growth_yoy=0.20, roe=0.20,
        )
        inputs = LongTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot=tech_snap,
            sentiment_score=0.5,
            sentiment_count=8,
            fundamental_snapshot=fund_snap,
            current_price=Decimal("120"),
        )
        out = rec.recommend(inputs)
        assert out.action == "BUY"
        assert out.timeframe == "long"
        assert out.fundamental_score is not None
        assert out.fundamental_score > 70  # very high

    def test_long_term_output_timeframe(self):
        rec = LongTermRecommender()
        inputs = LongTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=None,
            sentiment_count=0,
            fundamental_snapshot=None,
            current_price=Decimal("100"),
        )
        out = rec.recommend(inputs)
        assert out.timeframe == "long"


# ---------------------------------------------------------------------------
# RecommendationDispatcher
# ---------------------------------------------------------------------------


class TestRecommendationDispatcher:
    def test_short_dispatches_to_short_recommender(self):
        dispatcher = RecommendationDispatcher()
        short_input = RecommendationInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=0.0,
            sentiment_count=5,
            tv_summary="NEUTRAL",
            inv_summary="NEUTRAL",
            current_price=Decimal("100"),
        )
        out = dispatcher.recommend("short", short_input)
        assert isinstance(out, RecommendationOutput)
        assert out.timeframe == "short"

    def test_mid_dispatches_to_mid_recommender(self):
        dispatcher = RecommendationDispatcher()
        mid_input = MidTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=None,
            sentiment_count=0,
            fundamental_snapshot=None,
            current_price=Decimal("100"),
        )
        out = dispatcher.recommend("mid", mid_input)
        assert out.timeframe == "mid"

    def test_long_dispatches_to_long_recommender(self):
        dispatcher = RecommendationDispatcher()
        long_input = LongTermInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=None,
            sentiment_count=0,
            fundamental_snapshot=None,
            current_price=Decimal("100"),
        )
        out = dispatcher.recommend("long", long_input)
        assert out.timeframe == "long"

    def test_invalid_timeframe_raises_value_error(self):
        dispatcher = RecommendationDispatcher()
        with pytest.raises(ValueError, match="Geçersiz timeframe"):
            dispatcher.recommend("invalid", None)

    def test_wrong_input_type_raises_type_error(self):
        dispatcher = RecommendationDispatcher()
        # mid timeframe için RecommendationInput verirsek TypeError
        short_input = RecommendationInput(
            instrument_id=1,
            ticker="X",
            technical_snapshot={"close": 100.0, "atr": 2.0},
            sentiment_score=None,
            sentiment_count=0,
            tv_summary=None,
            inv_summary=None,
            current_price=Decimal("100"),
        )
        with pytest.raises(TypeError):
            dispatcher.recommend("mid", short_input)
