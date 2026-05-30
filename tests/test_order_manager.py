"""OrderManager (app/trading/order_manager.py) birim testleri."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.trading.execution_modes import TradingMode
from app.trading.order_manager import (
    OrderManager,
    OrderRequest,
    OrderValidation,
)


def _make_order(
    action="BUY", quantity=Decimal("10"), price=Decimal("100"),
    order_type="limit", wallet_id=1, instrument_id=1,
) -> OrderRequest:
    return OrderRequest(
        instrument_id=instrument_id,
        action=action,
        quantity=quantity,
        price=price,
        order_type=order_type,
        wallet_id=wallet_id,
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_order_passes(self):
        mgr = OrderManager(session_factory=None)
        out = mgr.validate(
            _make_order(),
            last_price=Decimal("100"),
            portfolio_value=Decimal("100000"),
        )
        assert isinstance(out, OrderValidation)
        assert out.is_valid is True
        assert out.errors == []

    def test_zero_quantity_invalid(self):
        mgr = OrderManager(session_factory=None)
        out = mgr.validate(
            _make_order(quantity=Decimal("0")),
            last_price=Decimal("100"),
            portfolio_value=Decimal("10000"),
        )
        assert out.is_valid is False
        assert any("quantity" in e for e in out.errors)

    def test_negative_price_invalid(self):
        mgr = OrderManager(session_factory=None)
        out = mgr.validate(
            _make_order(price=Decimal("-1")),
            last_price=Decimal("100"),
            portfolio_value=Decimal("10000"),
        )
        assert out.is_valid is False
        assert any("price" in e for e in out.errors)

    def test_price_deviation_above_threshold_invalid(self):
        """price ±%20 son fiyattan saparsa errors (PRICE_DEVIATION_PCT=0.20)."""
        mgr = OrderManager(session_factory=None)
        out = mgr.validate(
            _make_order(price=Decimal("130")),  # +%30
            last_price=Decimal("100"),
            portfolio_value=Decimal("100000"),
        )
        assert out.is_valid is False
        assert any("sapıyor" in e for e in out.errors)

    def test_large_trade_value_invalid(self):
        """trade > %30 portföy → MAX_TRADE_VALUE_PCT eşik aşıldı, errors."""
        mgr = OrderManager(session_factory=None)
        out = mgr.validate(
            _make_order(quantity=Decimal("100"), price=Decimal("50")),
            last_price=Decimal("50"),
            portfolio_value=Decimal("10000"),
        )
        # 100*50 = 5000; 5000/10000 = %50 (>%30)
        assert out.is_valid is False

    def test_unknown_portfolio_value_yields_warning(self):
        mgr = OrderManager(session_factory=None)
        out = mgr.validate(
            _make_order(),
            last_price=Decimal("100"),
            portfolio_value=Decimal("0"),
        )
        # Portföy 0 ise sadece uyarı, hata yok (action ve diğerleri OK)
        assert any("Portföy" in w or "portföy" in w.lower() for w in out.warnings)


# ---------------------------------------------------------------------------
# execute — mod bazlı delege
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.asyncio
    async def test_manual_parallel_returns_awaiting(self):
        mgr = OrderManager(session_factory=None)
        out = await mgr.execute(_make_order(), TradingMode.MANUAL_PARALLEL)
        assert out["status"] == "awaiting_manual"

    @pytest.mark.asyncio
    async def test_paper_dry_run_when_no_paper_service(self):
        mgr = OrderManager(session_factory=None)
        out = await mgr.execute(_make_order(), TradingMode.PAPER)
        assert out["status"] == "paper_executed"
        assert out["result"]["dry_run"] is True

    @pytest.mark.asyncio
    async def test_semi_auto_no_broker_raises(self):
        mgr = OrderManager(session_factory=None, broker=None)
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await mgr.execute(_make_order(), TradingMode.SEMI_AUTO)

    @pytest.mark.asyncio
    async def test_full_auto_no_broker_raises(self):
        mgr = OrderManager(session_factory=None, broker=None)
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await mgr.execute(_make_order(), TradingMode.FULL_AUTO)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


class TestPrepare:
    @pytest.mark.asyncio
    async def test_prepare_returns_audit_metadata(self):
        mgr = OrderManager(session_factory=None)
        out = await mgr.prepare(_make_order())
        assert "audit_id" in out
        assert "prepared_at" in out
        assert out["audit_id"].startswith("ord-")
