"""TradeRiskScorer (app/trading/risk_scorer.py) birim testleri."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.trading.risk_scorer import (
    RISK_LEVEL_THRESHOLDS,
    TradeRiskScore,
    TradeRiskScorer,
    risk_level_from_score,
)


class TestWeights:
    def test_default_weights_sum_to_one(self):
        s = sum(TradeRiskScorer.WEIGHTS.values())
        assert s == pytest.approx(1.0, abs=1e-9)

    def test_custom_weights_normalize(self):
        scorer = TradeRiskScorer(
            weights={"volatility": 1, "trade_size": 1, "liquidity": 1,
                     "concentration": 1}
        )
        s = sum(scorer.weights.values())
        assert s == pytest.approx(1.0, abs=1e-9)

    def test_negative_weights_raise(self):
        with pytest.raises(ValueError):
            TradeRiskScorer(weights={"volatility": -1})


class TestScoreVolatility:
    def test_low_atr_yields_low_score(self):
        scorer = TradeRiskScorer()
        # ATR/price = 0.001 (very low)
        s = scorer.score_volatility(atr=0.1, current_price=Decimal("100"))
        assert s == 0.0

    def test_high_atr_yields_max_score(self):
        scorer = TradeRiskScorer()
        # ATR/price = 0.10 (>= %5)
        s = scorer.score_volatility(atr=10.0, current_price=Decimal("100"))
        assert s == 100.0

    def test_zero_atr_returns_zero(self):
        scorer = TradeRiskScorer()
        s = scorer.score_volatility(atr=0.0, current_price=Decimal("100"))
        assert s == 0.0


class TestScoreTradeSize:
    def test_large_trade_yields_max_score(self):
        scorer = TradeRiskScorer()
        # trade/portfolio >= %20 → 100
        s = scorer.score_trade_size(Decimal("3000"), Decimal("10000"))
        assert s == 100.0

    def test_small_trade_yields_zero(self):
        scorer = TradeRiskScorer()
        # trade/portfolio = 0.001 (< %1)
        s = scorer.score_trade_size(Decimal("10"), Decimal("10000"))
        assert s == 0.0

    def test_zero_portfolio_returns_max(self):
        scorer = TradeRiskScorer()
        s = scorer.score_trade_size(Decimal("100"), Decimal("0"))
        assert s == 100.0  # konservatif


class TestScoreLiquidity:
    def test_low_volume_yields_max_score(self):
        scorer = TradeRiskScorer()
        assert scorer.score_liquidity(avg_volume=5000) == 100.0

    def test_high_volume_yields_zero(self):
        scorer = TradeRiskScorer()
        assert scorer.score_liquidity(avg_volume=10_000_000) == 0.0


class TestScoreConcentration:
    def test_high_sector_weight_yields_max(self):
        scorer = TradeRiskScorer()
        s = scorer.score_concentration("Banking", {"Banking": 0.50})
        assert s == 100.0

    def test_low_sector_weight_yields_zero(self):
        scorer = TradeRiskScorer()
        s = scorer.score_concentration("Banking", {"Banking": 0.05})
        assert s == 0.0

    def test_no_sector_returns_zero(self):
        scorer = TradeRiskScorer()
        s = scorer.score_concentration(None, {})
        assert s == 0.0


class TestCalculate:
    def test_calculate_returns_trade_risk_score(self):
        scorer = TradeRiskScorer()
        out = scorer.calculate(
            atr=2.0,
            current_price=Decimal("100"),
            trade_value=Decimal("500"),
            portfolio_value=Decimal("10000"),
            avg_volume=1_000_000,
            sector="Tech",
            sector_weights={"Tech": 0.20},
            threshold=50.0,
        )
        assert isinstance(out, TradeRiskScore)
        assert 0.0 <= out.score <= 100.0
        assert out.risk_level in {"low", "medium", "high"}
        assert "volatility" in out.components
        assert "trade_size" in out.components

    def test_calculate_requires_confirmation_when_above_threshold(self):
        scorer = TradeRiskScorer()
        out = scorer.calculate(
            atr=20.0,             # max vol
            current_price=Decimal("100"),
            trade_value=Decimal("5000"),  # max size
            portfolio_value=Decimal("10000"),
            avg_volume=1000,      # max illiquidity
            sector="X",
            sector_weights={"X": 0.99},
            threshold=50.0,
        )
        assert out.requires_confirmation is True
        assert out.score >= 50.0


class TestRiskLevelFromScore:
    def test_thresholds(self):
        assert risk_level_from_score(10.0) == "low"
        assert risk_level_from_score(40.0) == "medium"
        assert risk_level_from_score(80.0) == "high"
