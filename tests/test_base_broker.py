"""BaseBroker arayüzü + ExampleBroker placeholder (Faz 4 İSKELETİ) testleri."""

from __future__ import annotations

import pytest

from app.broker.adapters.example_broker import ExampleBroker
from app.broker.base_broker import (
    BaseBroker,
    BrokerBalance,
    BrokerOrder,
    BrokerOrderResult,
    BrokerPosition,
)


class TestBaseBrokerIsAbstract:
    def test_cannot_instantiate_base_broker(self):
        with pytest.raises(TypeError):
            BaseBroker()  # noqa: type: ignore[abstract]


class TestExampleBrokerInstantiation:
    def test_can_instantiate(self):
        broker = ExampleBroker()
        assert broker is not None
        assert broker.name == "example"

    def test_accepts_api_key_params_but_doesnt_store(self):
        broker = ExampleBroker(api_key="x", api_secret="y")
        assert broker._api_key_provided is True
        assert broker._api_secret_provided is True


class TestExampleBrokerMethodsRaise:
    @pytest.mark.asyncio
    async def test_authenticate_raises(self):
        broker = ExampleBroker()
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.authenticate()

    @pytest.mark.asyncio
    async def test_place_order_raises(self):
        broker = ExampleBroker()
        from decimal import Decimal
        order = BrokerOrder(
            broker_order_id="test-1",
            ticker="AAPL",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("100"),
            order_type="limit",
        )
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.place_order(order)

    @pytest.mark.asyncio
    async def test_cancel_order_raises(self):
        broker = ExampleBroker()
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.cancel_order("x")

    @pytest.mark.asyncio
    async def test_get_order_raises(self):
        broker = ExampleBroker()
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.get_order("x")

    @pytest.mark.asyncio
    async def test_get_positions_raises(self):
        broker = ExampleBroker()
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.get_positions()

    @pytest.mark.asyncio
    async def test_get_balance_raises(self):
        broker = ExampleBroker()
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.get_balance()

    @pytest.mark.asyncio
    async def test_is_market_open_raises(self):
        broker = ExampleBroker()
        with pytest.raises(NotImplementedError, match="Faz 4"):
            await broker.is_market_open()


# ---------------------------------------------------------------------------
# DTO dataclasses
# ---------------------------------------------------------------------------


class TestDTOs:
    def test_broker_order_required_fields(self):
        from decimal import Decimal
        o = BrokerOrder(
            broker_order_id="a",
            ticker="X",
            side="buy",
            quantity=Decimal("1"),
            price=Decimal("100"),
            order_type="market",
        )
        assert o.broker_order_id == "a"
        assert o.side == "buy"

    def test_broker_balance_fields(self):
        from decimal import Decimal
        b = BrokerBalance(
            cash=Decimal("1000"), total_equity=Decimal("2000"), currency="TRY"
        )
        assert b.cash == Decimal("1000")
        assert b.currency == "TRY"

    def test_broker_position_fields(self):
        from decimal import Decimal
        p = BrokerPosition(
            ticker="AAPL", quantity=Decimal("10"), avg_cost=Decimal("100"),
        )
        assert p.ticker == "AAPL"
        assert p.market_value is None
