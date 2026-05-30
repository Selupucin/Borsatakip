"""SafetyEngine (app/broker/safety.py) birim testleri."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.broker.safety import SafetyEngine, SafetyState
from app.portfolio.account import AccountService


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_activate_sets_state_true(self, session_factory):
        eng = SafetyEngine(session_factory)
        eng.activate_kill_switch("test reason")
        assert eng.kill_switch_active is True

    def test_deactivate_without_confirmation_is_noop(self, session_factory):
        eng = SafetyEngine(session_factory)
        eng.activate_kill_switch("test")
        eng.deactivate_kill_switch(False)
        # Onay yok → hâlâ aktif
        assert eng.kill_switch_active is True

    def test_deactivate_with_confirmation_clears(self, session_factory):
        eng = SafetyEngine(session_factory)
        eng.activate_kill_switch("test")
        eng.deactivate_kill_switch(True)
        assert eng.kill_switch_active is False


# ---------------------------------------------------------------------------
# Daily trade limit
# ---------------------------------------------------------------------------


class TestDailyTradeLimit:
    @pytest.mark.asyncio
    async def test_below_limit_returns_true(self, session_factory):
        asvc = AccountService(session_factory)
        aid = await asvc.get_or_create()
        eng = SafetyEngine(session_factory, max_daily_trades=10)
        assert await eng.check_daily_trade_limit(aid) is True


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    @pytest.mark.asyncio
    async def test_below_max_drawdown_returns_true(self, session_factory):
        asvc = AccountService(session_factory)
        aid = await asvc.get_or_create(initial_balance=Decimal("10000"))
        eng = SafetyEngine(session_factory, max_drawdown_pct=Decimal("15"))
        # İlk çağrı zirveyi belirler
        ok = await eng.check_circuit_breaker(aid, Decimal("10000"))
        assert ok is True
        # Aynı seviyede → tetiklenmedi
        ok2 = await eng.check_circuit_breaker(aid, Decimal("9500"))
        assert ok2 is True

    @pytest.mark.asyncio
    async def test_above_max_drawdown_triggers(self, session_factory):
        asvc = AccountService(session_factory)
        aid = await asvc.get_or_create(initial_balance=Decimal("10000"))
        eng = SafetyEngine(session_factory, max_drawdown_pct=Decimal("15"))
        await eng.check_circuit_breaker(aid, Decimal("10000"))
        # %20 düşüş → %15 eşik aşıldı
        ok = await eng.check_circuit_breaker(aid, Decimal("8000"))
        assert ok is False


# ---------------------------------------------------------------------------
# can_trade — birleşik kontrol
# ---------------------------------------------------------------------------


class TestCanTrade:
    @pytest.mark.asyncio
    async def test_all_ok_returns_true(self, session_factory):
        asvc = AccountService(session_factory)
        aid = await asvc.get_or_create()
        eng = SafetyEngine(session_factory)
        ok, reasons = await eng.can_trade(aid)
        assert ok is True
        assert reasons == []

    @pytest.mark.asyncio
    async def test_kill_switch_blocks(self, session_factory):
        asvc = AccountService(session_factory)
        aid = await asvc.get_or_create()
        eng = SafetyEngine(session_factory)
        eng.activate_kill_switch("manual test")
        ok, reasons = await eng.can_trade(aid)
        assert ok is False
        assert any("Kill switch" in r for r in reasons)


# ---------------------------------------------------------------------------
# audit_log
# ---------------------------------------------------------------------------


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_log_runs_without_error(self, session_factory):
        eng = SafetyEngine(session_factory)
        # loguru çağrısı; return None
        await eng.audit_log(
            account_id=1,
            action="order_placed",
            details={"instrument_id": 1, "qty": 10},
        )


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class TestState:
    @pytest.mark.asyncio
    async def test_get_state_returns_safety_state(self, session_factory):
        asvc = AccountService(session_factory)
        aid = await asvc.get_or_create()
        eng = SafetyEngine(session_factory)
        state = await eng.get_state(aid)
        assert isinstance(state, SafetyState)
        assert state.kill_switch_active is False
        assert state.daily_trade_count >= 0
