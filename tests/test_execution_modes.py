"""TradingMode + MODE_REGISTRY (app/trading/execution_modes.py) testleri."""

from __future__ import annotations

import pytest

from app.trading.execution_modes import (
    MODE_REGISTRY,
    ModeDescription,
    TradingMode,
    is_active_in_current_phase,
    requires_broker,
)


class TestTradingModeEnum:
    def test_enum_has_four_values(self):
        modes = set(TradingMode)
        assert len(modes) == 4
        assert TradingMode.MANUAL_PARALLEL in modes
        assert TradingMode.SEMI_AUTO in modes
        assert TradingMode.FULL_AUTO in modes
        assert TradingMode.PAPER in modes

    def test_values_match_db_check_constraint(self):
        assert TradingMode.MANUAL_PARALLEL.value == "manual_parallel"
        assert TradingMode.SEMI_AUTO.value == "semi_auto"
        assert TradingMode.FULL_AUTO.value == "full_auto"
        assert TradingMode.PAPER.value == "paper"


class TestModeRegistry:
    def test_registry_has_four_entries(self):
        assert len(MODE_REGISTRY) == 4
        for mode in TradingMode:
            assert mode in MODE_REGISTRY
            assert isinstance(MODE_REGISTRY[mode], ModeDescription)

    def test_manual_parallel_no_broker_required(self):
        meta = MODE_REGISTRY[TradingMode.MANUAL_PARALLEL]
        assert meta.requires_broker is False
        assert meta.automated_execution is False

    def test_paper_no_broker_required(self):
        meta = MODE_REGISTRY[TradingMode.PAPER]
        assert meta.requires_broker is False

    def test_semi_auto_requires_broker(self):
        meta = MODE_REGISTRY[TradingMode.SEMI_AUTO]
        assert meta.requires_broker is True

    def test_full_auto_requires_broker_and_automated(self):
        meta = MODE_REGISTRY[TradingMode.FULL_AUTO]
        assert meta.requires_broker is True
        assert meta.automated_execution is True


class TestPhaseGuard:
    def test_manual_parallel_active(self):
        assert is_active_in_current_phase(TradingMode.MANUAL_PARALLEL) is True

    def test_paper_active(self):
        assert is_active_in_current_phase(TradingMode.PAPER) is True

    def test_semi_auto_inactive_in_current_phase(self):
        assert is_active_in_current_phase(TradingMode.SEMI_AUTO) is False

    def test_full_auto_inactive_in_current_phase(self):
        assert is_active_in_current_phase(TradingMode.FULL_AUTO) is False


class TestRequiresBroker:
    def test_paper_does_not_require_broker(self):
        assert requires_broker(TradingMode.PAPER) is False

    def test_full_auto_requires_broker(self):
        assert requires_broker(TradingMode.FULL_AUTO) is True
