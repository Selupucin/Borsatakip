"""AutoGate (app/trading/auto_gate.py) karar matrisi testleri."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.trading.auto_gate import AutoGate, GateResult, TradeDecision
from app.trading.execution_modes import TradingMode
from app.trading.risk_scorer import TradeRiskScore, TradeRiskScorer


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _make_safety(ok: bool = True, reasons=None):
    """Mock SafetyEngine: can_trade çağrısı (ok, reasons) döner."""
    mock = AsyncMock()
    mock.can_trade = AsyncMock(return_value=(ok, reasons or []))
    return mock


def _make_risk_score(score: float = 30.0, requires_confirm: bool = False):
    return TradeRiskScore(
        score=score,
        components={"volatility": score, "trade_size": score,
                    "liquidity": score, "concentration": score},
        risk_level="low" if score < 25 else "medium" if score < 60 else "high",
        reasons=[],
        requires_confirmation=requires_confirm,
    )


class _DummyBroker:
    """Sadece broker var/yok kontrolü için yer tutucu."""
    name = "dummy"


# ---------------------------------------------------------------------------
# manual_parallel — her zaman MANUAL_ONLY
# ---------------------------------------------------------------------------


class TestManualParallel:
    @pytest.mark.asyncio
    async def test_low_score_yields_manual_only(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(), broker=None)
        out = await gate.evaluate(
            mode=TradingMode.MANUAL_PARALLEL,
            risk_score=_make_risk_score(20.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.MANUAL_ONLY

    @pytest.mark.asyncio
    async def test_high_score_still_manual_only(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(), broker=None)
        out = await gate.evaluate(
            mode=TradingMode.MANUAL_PARALLEL,
            risk_score=_make_risk_score(90.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.MANUAL_ONLY


# ---------------------------------------------------------------------------
# paper — her zaman EXECUTE_AUTO (safety OK ise)
# ---------------------------------------------------------------------------


class TestPaperMode:
    @pytest.mark.asyncio
    async def test_paper_executes_auto(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(), broker=None)
        out = await gate.evaluate(
            mode=TradingMode.PAPER,
            risk_score=_make_risk_score(40.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.EXECUTE_AUTO


# ---------------------------------------------------------------------------
# semi_auto — broker yok → BLOCKED; broker var → REQUEST_CONFIRMATION
# ---------------------------------------------------------------------------


class TestSemiAuto:
    @pytest.mark.asyncio
    async def test_no_broker_blocked(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(), broker=None)
        out = await gate.evaluate(
            mode=TradingMode.SEMI_AUTO,
            risk_score=_make_risk_score(30.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.BLOCKED

    @pytest.mark.asyncio
    async def test_with_broker_request_confirmation(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(),
                        broker=_DummyBroker())
        out = await gate.evaluate(
            mode=TradingMode.SEMI_AUTO,
            risk_score=_make_risk_score(20.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.REQUEST_CONFIRMATION


# ---------------------------------------------------------------------------
# full_auto — broker var, skor < eşik → EXECUTE_AUTO; aksi onay
# ---------------------------------------------------------------------------


class TestFullAuto:
    @pytest.mark.asyncio
    async def test_no_broker_blocked(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(), broker=None)
        out = await gate.evaluate(
            mode=TradingMode.FULL_AUTO,
            risk_score=_make_risk_score(30.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.BLOCKED

    @pytest.mark.asyncio
    async def test_low_score_with_broker_executes(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(),
                        broker=_DummyBroker())
        out = await gate.evaluate(
            mode=TradingMode.FULL_AUTO,
            risk_score=_make_risk_score(30.0, requires_confirm=False),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.EXECUTE_AUTO

    @pytest.mark.asyncio
    async def test_high_score_with_broker_requests_confirmation(self):
        gate = AutoGate(TradeRiskScorer(), _make_safety(),
                        broker=_DummyBroker())
        out = await gate.evaluate(
            mode=TradingMode.FULL_AUTO,
            risk_score=_make_risk_score(70.0, requires_confirm=True),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.REQUEST_CONFIRMATION


# ---------------------------------------------------------------------------
# Safety override: her modda BLOCKED
# ---------------------------------------------------------------------------


class TestSafetyOverride:
    @pytest.mark.asyncio
    async def test_kill_switch_blocks_paper(self):
        safety = _make_safety(ok=False, reasons=["Kill switch aktif"])
        gate = AutoGate(TradeRiskScorer(), safety, broker=None)
        out = await gate.evaluate(
            mode=TradingMode.PAPER,
            risk_score=_make_risk_score(10.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.BLOCKED

    @pytest.mark.asyncio
    async def test_safety_blocks_manual_parallel(self):
        safety = _make_safety(ok=False, reasons=["Günlük limit aşıldı"])
        gate = AutoGate(TradeRiskScorer(), safety, broker=None)
        out = await gate.evaluate(
            mode=TradingMode.MANUAL_PARALLEL,
            risk_score=_make_risk_score(10.0),
            threshold=50.0,
            account_id=1, instrument_id=1, trade_value=Decimal("100"),
        )
        assert out.decision == TradeDecision.BLOCKED
