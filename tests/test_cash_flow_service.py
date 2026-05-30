"""CashFlowService (app/portfolio/cash_flows.py) birim testleri."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.portfolio.account import AccountService
from app.portfolio.cash_flows import CashFlowService


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
async def account_id(session_factory):
    svc = AccountService(session_factory)
    return await svc.get_or_create(initial_balance=Decimal("10000"))


class TestDeposit:
    @pytest.mark.asyncio
    async def test_deposit_increases_account_cash(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        await cf.deposit(aid, Decimal("5000"))

        asvc = AccountService(session_factory)
        snap = await asvc.get_snapshot(aid)
        assert snap.cash_balance == Decimal("15000")

    @pytest.mark.asyncio
    async def test_deposit_records_with_null_wallet_id(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        await cf.deposit(aid, Decimal("1000"))
        flows = await cf.list_flows(aid)
        assert flows[0]["wallet_id"] is None
        assert flows[0]["flow_type"] == "deposit"
        assert flows[0]["amount"] == Decimal("1000")

    @pytest.mark.asyncio
    async def test_deposit_negative_amount_raises(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        with pytest.raises(ValueError):
            await cf.deposit(aid, Decimal("-100"))


class TestWithdrawal:
    @pytest.mark.asyncio
    async def test_withdrawal_reduces_cash(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        await cf.withdrawal(aid, Decimal("3000"))

        asvc = AccountService(session_factory)
        snap = await asvc.get_snapshot(aid)
        assert snap.cash_balance == Decimal("7000")

    @pytest.mark.asyncio
    async def test_withdrawal_recorded_as_negative(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        await cf.withdrawal(aid, Decimal("1000"))
        flows = await cf.list_flows(aid)
        # Negative amount audit kaydı
        wd = next(f for f in flows if f["flow_type"] == "withdrawal")
        assert wd["amount"] == Decimal("-1000")

    @pytest.mark.asyncio
    async def test_withdrawal_insufficient_cash_raises(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        with pytest.raises(ValueError):
            await cf.withdrawal(aid, Decimal("99999"))


class TestNetDeposited:
    @pytest.mark.asyncio
    async def test_net_deposited_account_level(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        await cf.deposit(aid, Decimal("5000"))
        await cf.deposit(aid, Decimal("2000"))
        await cf.withdrawal(aid, Decimal("1000"))
        # 5000 + 2000 - 1000 = 6000
        net = await cf.net_deposited(aid)
        assert net == Decimal("6000")


class TestListFlows:
    @pytest.mark.asyncio
    async def test_list_flows_ordered_desc(
        self, session_factory, account_id
    ):
        aid  = account_id
        cf = CashFlowService(session_factory)
        await cf.deposit(aid, Decimal("100"))
        await cf.deposit(aid, Decimal("200"))
        await cf.deposit(aid, Decimal("300"))
        flows = await cf.list_flows(aid, limit=10)
        assert len(flows) == 3
        # DESC sıralama: id'ler küçükten büyüğe insert edildi → reverse
        # occurred_at server_default=now() — id de DESC; en son insert ilk
        assert flows[0]["amount"] == Decimal("300")
        assert flows[-1]["amount"] == Decimal("100")
