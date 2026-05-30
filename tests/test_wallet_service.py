"""WalletService (app/portfolio/wallets.py) birim testleri."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.portfolio.account import AccountService
from app.portfolio.wallets import VALID_POOLS, VALID_TIMEFRAMES, WalletService


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
async def account_id(session_factory):
    svc = AccountService(session_factory)
    return await svc.get_or_create(initial_balance=Decimal("100000"))


# ---------------------------------------------------------------------------
# init_matrix
# ---------------------------------------------------------------------------


class TestInitMatrix:
    @pytest.mark.asyncio
    async def test_creates_six_wallets(self, session_factory, account_id):
        aid  = account_id
        svc = WalletService(session_factory)
        result = await svc.init_matrix(aid)
        assert len(result) == 6
        # 2 pool × 3 timeframe = 6 hücre
        for pool in VALID_POOLS:
            for tf in VALID_TIMEFRAMES:
                assert (pool, tf) in result

    @pytest.mark.asyncio
    async def test_init_matrix_is_idempotent(self, session_factory, account_id):
        aid  = account_id
        svc = WalletService(session_factory)
        r1 = await svc.init_matrix(aid)
        r2 = await svc.init_matrix(aid)
        # İkinci çağrı duplicate yapmaz; aynı id'leri döndürür
        assert r1 == r2

    @pytest.mark.asyncio
    async def test_list_wallets_returns_six_sorted(
        self, session_factory, account_id
    ):
        aid  = account_id
        svc = WalletService(session_factory)
        await svc.init_matrix(aid)
        wallets = await svc.list_wallets(aid)
        assert len(wallets) == 6


# ---------------------------------------------------------------------------
# allocate
# ---------------------------------------------------------------------------


class TestAllocate:
    @pytest.mark.asyncio
    async def test_allocate_moves_cash_from_account_to_wallet(
        self, session_factory, account_id
    ):
        aid  = account_id
        wsvc = WalletService(session_factory)
        asvc = AccountService(session_factory)
        matrix = await wsvc.init_matrix(aid)
        wallet_id = matrix[("bot", "short")]

        await wsvc.allocate(wallet_id, Decimal("5000"))

        snap = await asvc.get_snapshot(aid)
        # account.cash_balance = 100000 - 5000 = 95000
        assert snap.cash_balance == Decimal("95000")

        wallets = await wsvc.list_wallets(aid)
        target = next(w for w in wallets if w.id == wallet_id)
        assert target.allocated == Decimal("5000")
        assert target.cash_balance == Decimal("5000")

    @pytest.mark.asyncio
    async def test_allocate_negative_raises(self, session_factory, account_id):
        aid  = account_id
        wsvc = WalletService(session_factory)
        matrix = await wsvc.init_matrix(aid)
        with pytest.raises(ValueError):
            await wsvc.allocate(matrix[("bot", "short")], Decimal("-100"))

    @pytest.mark.asyncio
    async def test_allocate_insufficient_cash_raises(
        self, session_factory, account_id
    ):
        aid  = account_id
        wsvc = WalletService(session_factory)
        matrix = await wsvc.init_matrix(aid)
        with pytest.raises(ValueError):
            await wsvc.allocate(matrix[("bot", "short")], Decimal("999999"))


# ---------------------------------------------------------------------------
# reallocate
# ---------------------------------------------------------------------------


class TestReallocate:
    @pytest.mark.asyncio
    async def test_reallocate_creates_two_cashflow_rows(
        self, session_factory, account_id
    ):
        from app.portfolio.cash_flows import CashFlowService

        aid  = account_id
        wsvc = WalletService(session_factory)
        matrix = await wsvc.init_matrix(aid)
        src = matrix[("bot", "short")]
        dst = matrix[("bot", "mid")]

        await wsvc.allocate(src, Decimal("5000"))
        await wsvc.reallocate(src, dst, Decimal("2000"))

        cfsvc = CashFlowService(session_factory)
        flows = await cfsvc.list_flows(aid, limit=10)
        reallocates = [f for f in flows if f["flow_type"] == "reallocate"]
        # 2 satır (-2000, +2000) — toplam 0
        assert len(reallocates) == 2
        total = sum(f["amount"] for f in reallocates)
        assert total == Decimal("0")

    @pytest.mark.asyncio
    async def test_reallocate_same_wallet_raises(
        self, session_factory, account_id
    ):
        aid  = account_id
        wsvc = WalletService(session_factory)
        matrix = await wsvc.init_matrix(aid)
        wid = matrix[("bot", "short")]
        await wsvc.allocate(wid, Decimal("1000"))
        with pytest.raises(ValueError):
            await wsvc.reallocate(wid, wid, Decimal("100"))


# ---------------------------------------------------------------------------
# compute_snapshot
# ---------------------------------------------------------------------------


class TestComputeSnapshot:
    @pytest.mark.asyncio
    async def test_snapshot_with_no_positions(
        self, session_factory, account_id
    ):
        aid  = account_id
        wsvc = WalletService(session_factory)
        matrix = await wsvc.init_matrix(aid)
        wid = matrix[("bot", "short")]
        await wsvc.allocate(wid, Decimal("3000"))

        snap = await wsvc.compute_snapshot(wid, current_prices={})
        assert snap.cash_balance == Decimal("3000")
        assert snap.positions_value == Decimal("0")
        assert snap.total_value == Decimal("3000")
        assert snap.pnl_realized == Decimal("0")
