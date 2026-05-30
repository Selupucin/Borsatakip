"""AccountService (app/portfolio/account.py) birim testleri."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.portfolio.account import AccountService, AccountSnapshot


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


class TestAccountService:
    @pytest.mark.asyncio
    async def test_get_or_create_is_idempotent(self, session_factory):
        svc = AccountService(session_factory)
        id1 = await svc.get_or_create(initial_balance=Decimal("15000"))
        id2 = await svc.get_or_create(initial_balance=Decimal("999999"))
        assert id1 == id2

    @pytest.mark.asyncio
    async def test_get_or_create_returns_int(self, session_factory):
        svc = AccountService(session_factory)
        aid = await svc.get_or_create()
        assert isinstance(aid, int)
        assert aid > 0

    @pytest.mark.asyncio
    async def test_set_trading_mode_valid(self, session_factory):
        svc = AccountService(session_factory)
        aid = await svc.get_or_create()
        await svc.set_trading_mode(aid, "paper")
        snap = await svc.get_snapshot(aid)
        assert snap.trading_mode == "paper"

    @pytest.mark.asyncio
    async def test_set_trading_mode_invalid_raises(self, session_factory):
        svc = AccountService(session_factory)
        aid = await svc.get_or_create()
        with pytest.raises(ValueError):
            await svc.set_trading_mode(aid, "bogus_mode")

    @pytest.mark.asyncio
    async def test_set_risk_threshold_valid(self, session_factory):
        svc = AccountService(session_factory)
        aid = await svc.get_or_create()
        await svc.set_risk_threshold(aid, 75.0)
        snap = await svc.get_snapshot(aid)
        assert snap.risk_threshold == pytest.approx(75.0)

    @pytest.mark.asyncio
    async def test_set_risk_threshold_out_of_range_raises(self, session_factory):
        svc = AccountService(session_factory)
        aid = await svc.get_or_create()
        with pytest.raises(ValueError):
            await svc.set_risk_threshold(aid, 150.0)
        with pytest.raises(ValueError):
            await svc.set_risk_threshold(aid, -5.0)

    @pytest.mark.asyncio
    async def test_get_snapshot_returns_all_fields(self, session_factory):
        svc = AccountService(session_factory)
        aid = await svc.get_or_create(
            initial_balance=Decimal("25000"), currency="USD"
        )
        snap = await svc.get_snapshot(aid)
        assert isinstance(snap, AccountSnapshot)
        assert snap.account_id == aid
        assert snap.initial_balance == Decimal("25000")
        assert snap.cash_balance == Decimal("25000")
        assert snap.currency == "USD"
        assert snap.trading_mode == "manual_parallel"  # default
        assert snap.risk_threshold == pytest.approx(50.0)  # default

    @pytest.mark.asyncio
    async def test_get_snapshot_unknown_account_raises(self, session_factory):
        svc = AccountService(session_factory)
        with pytest.raises(LookupError):
            await svc.get_snapshot(99999)
