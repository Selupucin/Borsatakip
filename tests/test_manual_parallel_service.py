"""ManualParallelService (app/trading/manual_parallel.py) birim testleri."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.portfolio.account import AccountService
from app.portfolio.wallets import WalletService
from app.trading.manual_parallel import (
    BotFollowRate,
    ManualParallelService,
    PendingRecommendationView,
)
from app.db.models import Instrument, Portfolio, Recommendation


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
async def setup_account_with_recommendation(session_factory):
    asvc = AccountService(session_factory)
    wsvc = WalletService(session_factory)
    aid = await asvc.get_or_create(initial_balance=Decimal("100000"))
    matrix = await wsvc.init_matrix(aid)
    wid = matrix[("bot", "short")]
    await wsvc.allocate(wid, Decimal("50000"))

    with session_factory() as s:
        s.add(Instrument(ticker="AAPL", name="Apple"))
        s.commit()
        instr_id = int(s.query(Instrument).filter_by(ticker="AAPL").one().id)
        rec = Recommendation(
            instrument_id=instr_id,
            generated_at=datetime.now(timezone.utc),
            action="BUY",
            timeframe="short",
            confidence=75.0,
            risk_level="low",
            risk_score=20.0,
            target_price=110.0,
            tech_score=70.0,
            sentiment_score=60.0,
        )
        s.add(rec)
        s.commit()
        rec_id = int(rec.id)

    return aid, wid, instr_id, rec_id


class TestPendingRecommendations:
    @pytest.mark.asyncio
    async def test_lists_pending_when_no_portfolio_row(
        self, session_factory, setup_account_with_recommendation
    ):
        aid, _wid, _instr, rec_id  = setup_account_with_recommendation
        svc = ManualParallelService(session_factory)
        pending = await svc.pending_recommendations(aid)
        assert len(pending) >= 1
        assert any(p.recommendation_id == rec_id for p in pending)
        assert all(isinstance(p, PendingRecommendationView) for p in pending)


class TestMarkApplied:
    @pytest.mark.asyncio
    async def test_mark_applied_inserts_portfolio(
        self, session_factory, setup_account_with_recommendation
    ):
        aid, wid, instr_id, rec_id  = setup_account_with_recommendation
        svc = ManualParallelService(session_factory)
        new_id = await svc.mark_applied(
            wallet_id=wid,
            recommendation_id=rec_id,
            actual_price=Decimal("100"),
            actual_quantity=Decimal("10"),
            commission=Decimal("1"),
        )
        assert new_id > 0
        with session_factory() as s:
            row = s.get(Portfolio, new_id)
            assert row is not None
            assert row.action == "BUY"
            assert row.followed_bot is True

    @pytest.mark.asyncio
    async def test_mark_applied_invalid_quantity_raises(
        self, session_factory, setup_account_with_recommendation
    ):
        aid, wid, _instr, rec_id  = setup_account_with_recommendation
        svc = ManualParallelService(session_factory)
        with pytest.raises(ValueError):
            await svc.mark_applied(
                wallet_id=wid, recommendation_id=rec_id,
                actual_price=Decimal("100"), actual_quantity=Decimal("0"),
            )

    @pytest.mark.asyncio
    async def test_mark_applied_unknown_rec_raises(
        self, session_factory, setup_account_with_recommendation
    ):
        aid, wid, _instr, _rec  = setup_account_with_recommendation
        svc = ManualParallelService(session_factory)
        with pytest.raises(LookupError):
            await svc.mark_applied(
                wallet_id=wid, recommendation_id=99999,
                actual_price=Decimal("100"), actual_quantity=Decimal("5"),
            )


class TestMarkSkipped:
    @pytest.mark.asyncio
    async def test_mark_skipped_returns_none(self, session_factory):
        svc = ManualParallelService(session_factory)
        result = await svc.mark_skipped(recommendation_id=1, notes="test")
        # Sadece audit log; bir şey döndürmüyor
        assert result is None


class TestBotFollowRate:
    @pytest.mark.asyncio
    async def test_follow_rate_returns_struct(
        self, session_factory, setup_account_with_recommendation
    ):
        aid, wid, _instr, rec_id  = setup_account_with_recommendation
        svc = ManualParallelService(session_factory)
        # Bir öneriyi uygula
        await svc.mark_applied(
            wallet_id=wid, recommendation_id=rec_id,
            actual_price=Decimal("100"), actual_quantity=Decimal("5"),
            commission=Decimal("0"),
        )
        rate = await svc.bot_follow_rate(aid)
        assert isinstance(rate, BotFollowRate)
        assert rate.total_recommendations >= 1
        assert rate.applied >= 1
        assert 0.0 <= rate.follow_rate <= 1.0
