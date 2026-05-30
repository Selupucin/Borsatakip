"""PaperTradingService (app/portfolio/paper_trading.py) birim testleri."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.portfolio.account import AccountService
from app.portfolio.paper_trading import PAPER_MODE, PaperTradingService
from app.portfolio.positions import PositionService
from app.portfolio.wallets import WalletService
from app.db.models import (
    BotPick,
    CashFlow,
    Instrument,
    OpenPosition,
    Portfolio,
    UserAccount,
)


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
async def paper_account(session_factory):
    asvc = AccountService(session_factory)
    wsvc = WalletService(session_factory)
    aid = await asvc.get_or_create(initial_balance=Decimal("100000"))
    await asvc.set_trading_mode(aid, "paper")
    matrix = await wsvc.init_matrix(aid)
    wid = matrix[("bot", "short")]
    await wsvc.allocate(wid, Decimal("50000"))

    with session_factory() as s:
        s.add(Instrument(ticker="AAPL", name="Apple", currency="USD"))
        s.commit()
        instr_id = int(s.query(Instrument).filter_by(ticker="AAPL").one().id)

    return aid, wid, instr_id


# ---------------------------------------------------------------------------
# reset_paper_account
# ---------------------------------------------------------------------------


class TestResetPaperAccount:
    @pytest.mark.asyncio
    async def test_reset_clears_positions_and_resets_balance(
        self, session_factory, paper_account
    ):
        aid, wid, instr_id  = paper_account
        psvc = PositionService(session_factory)
        # Bir buy yap → portfolio + open_positions + cash_flow
        await psvc.apply_buy(
            wallet_id=wid, instrument_id=instr_id,
            quantity=Decimal("5"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )

        paper = PaperTradingService(session_factory, psvc)
        await paper.reset_paper_account(aid, initial_balance=Decimal("75000"))

        with session_factory() as s:
            assert s.execute(select(Portfolio)).all() == []
            assert s.execute(select(OpenPosition)).all() == []
            assert s.execute(select(CashFlow)).all() == []
            acc = s.get(UserAccount, aid)
            assert acc.trading_mode == PAPER_MODE
            assert float(acc.cash_balance) == pytest.approx(75000.0)


# ---------------------------------------------------------------------------
# execute_paper_trade
# ---------------------------------------------------------------------------


class TestExecutePaperTrade:
    @pytest.mark.asyncio
    async def test_execute_buy_in_paper_mode(
        self, session_factory, paper_account
    ):
        aid, wid, instr_id  = paper_account
        psvc = PositionService(session_factory)
        paper = PaperTradingService(session_factory, psvc)

        tx_id = await paper.execute_paper_trade(
            wallet_id=wid,
            instrument_id=instr_id,
            action="BUY",
            quantity=Decimal("10"),
            price=Decimal("100"),
        )
        assert tx_id > 0

        with session_factory() as s:
            pos = s.execute(select(OpenPosition)).scalar_one_or_none()
            assert pos is not None
            assert float(pos.quantity) == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_execute_outside_paper_mode_raises(
        self, session_factory, paper_account
    ):
        aid, wid, instr_id  = paper_account
        asvc = AccountService(session_factory)
        await asvc.set_trading_mode(aid, "manual_parallel")
        psvc = PositionService(session_factory)
        paper = PaperTradingService(session_factory, psvc)
        with pytest.raises(ValueError):
            await paper.execute_paper_trade(
                wallet_id=wid, instrument_id=instr_id,
                action="BUY", quantity=Decimal("1"), price=Decimal("100"),
            )

    @pytest.mark.asyncio
    async def test_invalid_action_raises(
        self, session_factory, paper_account
    ):
        aid, wid, instr_id  = paper_account
        psvc = PositionService(session_factory)
        paper = PaperTradingService(session_factory, psvc)
        with pytest.raises(ValueError):
            await paper.execute_paper_trade(
                wallet_id=wid, instrument_id=instr_id,
                action="HODL", quantity=Decimal("1"), price=Decimal("100"),
            )


# ---------------------------------------------------------------------------
# simulate_bot_picks
# ---------------------------------------------------------------------------


class TestSimulateBotPicks:
    @pytest.mark.asyncio
    async def test_simulate_processes_bot_picks(
        self, session_factory, paper_account
    ):
        aid, wid, instr_id  = paper_account
        # Bot pick seed et (BUY, kapanmış hit_target)
        with session_factory() as s:
            now = datetime.now(tz=timezone.utc)
            s.add(BotPick(
                instrument_id=instr_id,
                timeframe="short",
                action="BUY",
                confidence=70.0,
                price_at_pick=100.0,
                target_price=110.0,
                picked_at=now - timedelta(days=5),
                is_open=False,
                closed_at=now - timedelta(days=1),
                price_at_close=110.0,
                outcome="hit_target",
                return_pct=10.0,
            ))
            s.commit()

        psvc = PositionService(session_factory)
        paper = PaperTradingService(session_factory, psvc)
        stats = await paper.simulate_bot_picks(
            account_id=aid,
            since=date.today() - timedelta(days=30),
            wallet_id=wid,
        )
        assert isinstance(stats, dict)
        assert stats["picks_total"] == 1
        assert stats["buys_executed"] == 1
        assert stats["sells_executed"] == 1
        assert stats["hit_target"] == 1

    @pytest.mark.asyncio
    async def test_simulate_outside_paper_mode_raises(
        self, session_factory, paper_account
    ):
        aid, wid, instr_id  = paper_account
        asvc = AccountService(session_factory)
        await asvc.set_trading_mode(aid, "manual_parallel")
        psvc = PositionService(session_factory)
        paper = PaperTradingService(session_factory, psvc)
        with pytest.raises(ValueError):
            await paper.simulate_bot_picks(
                account_id=aid,
                since=date.today() - timedelta(days=10),
                wallet_id=wid,
            )
