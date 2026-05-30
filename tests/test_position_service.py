"""PositionService (app/portfolio/positions.py) birim testleri.

Weighted-average cost mantığı ve realized P&L formülü test edilir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.portfolio.account import AccountService
from app.portfolio.positions import PositionService
from app.portfolio.wallets import WalletService
from app.db.models import Instrument, OpenPosition, Portfolio


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
async def setup_account_and_wallet(session_factory):
    asvc = AccountService(session_factory)
    wsvc = WalletService(session_factory)
    aid = await asvc.get_or_create(initial_balance=Decimal("100000"))
    matrix = await wsvc.init_matrix(aid)
    wallet_id = matrix[("user", "short")]
    await wsvc.allocate(wallet_id, Decimal("50000"))

    # Bir Instrument seed et
    with session_factory() as s:
        instr = Instrument(ticker="AAPL", name="Apple")
        s.add(instr)
        s.commit()
        instrument_id = int(instr.id)

    return wallet_id, instrument_id


# ---------------------------------------------------------------------------
# apply_buy
# ---------------------------------------------------------------------------


class TestApplyBuy:
    @pytest.mark.asyncio
    async def test_first_buy_inserts_open_position(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id,
            instrument_id=instr_id,
            quantity=Decimal("10"),
            price=Decimal("100"),
            commission=Decimal("2"),
            transaction_at=datetime.now(timezone.utc),
        )

        with session_factory() as s:
            pos = s.execute(
                select(OpenPosition).where(OpenPosition.wallet_id == wallet_id)
            ).scalar_one()
            assert float(pos.quantity) == pytest.approx(10.0)
            assert float(pos.avg_cost) == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_second_buy_uses_weighted_average(
        self, session_factory, setup_account_and_wallet
    ):
        """10@100 sonra 5@110 → avg = (10*100 + 5*110)/15 = 103.33."""
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("5"), price=Decimal("110"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )

        with session_factory() as s:
            pos = s.execute(
                select(OpenPosition).where(OpenPosition.wallet_id == wallet_id)
            ).scalar_one()
            assert float(pos.quantity) == pytest.approx(15.0)
            # (10*100 + 5*110)/15 = 1550/15 = 103.333
            assert float(pos.avg_cost) == pytest.approx(103.3333, abs=0.01)

    @pytest.mark.asyncio
    async def test_buy_decreases_wallet_cash(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        wsvc = WalletService(session_factory)
        psvc = PositionService(session_factory)
        await psvc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("2"),
            transaction_at=datetime.now(timezone.utc),
        )
        snap = await wsvc.compute_snapshot(wallet_id, current_prices={})
        # cash = 50000 - (10*100 + 2) = 50000 - 1002 = 48998
        assert snap.cash_balance == pytest.approx(Decimal("48998"))

    @pytest.mark.asyncio
    async def test_buy_writes_portfolio_row(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("2"),
            transaction_at=datetime.now(timezone.utc),
        )

        with session_factory() as s:
            rows = s.execute(
                select(Portfolio).where(Portfolio.wallet_id == wallet_id)
            ).scalars().all()
            assert len(rows) == 1
            assert rows[0].action == "BUY"
            assert rows[0].followed_bot is False

    @pytest.mark.asyncio
    async def test_followed_bot_flag_preserved(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("5"), price=Decimal("100"),
            commission=Decimal("1"),
            transaction_at=datetime.now(timezone.utc),
            followed_bot=True,
        )

        with session_factory() as s:
            row = s.execute(
                select(Portfolio).where(Portfolio.wallet_id == wallet_id)
            ).scalar_one()
            assert row.followed_bot is True


# ---------------------------------------------------------------------------
# apply_sell
# ---------------------------------------------------------------------------


class TestApplySell:
    @pytest.mark.asyncio
    async def test_full_sell_deletes_open_position(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        await svc.apply_sell(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("110"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )

        with session_factory() as s:
            pos = s.execute(
                select(OpenPosition).where(OpenPosition.wallet_id == wallet_id)
            ).scalar_one_or_none()
            assert pos is None  # SİLİNDİ

    @pytest.mark.asyncio
    async def test_partial_sell_updates_qty_keeps_avg(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        await svc.apply_sell(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("4"), price=Decimal("120"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )

        with session_factory() as s:
            pos = s.execute(
                select(OpenPosition).where(OpenPosition.wallet_id == wallet_id)
            ).scalar_one()
            assert float(pos.quantity) == pytest.approx(6.0)
            assert float(pos.avg_cost) == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_sell_realized_pnl_formula(
        self, session_factory, setup_account_and_wallet
    ):
        """realized_pnl = (sell_price - avg_cost) * qty - commission."""
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        tx_id, realized = await svc.apply_sell(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("120"),
            commission=Decimal("5"),
            transaction_at=datetime.now(timezone.utc),
        )
        # (120-100)*10 - 5 = 200 - 5 = 195
        assert realized == pytest.approx(Decimal("195"))

    @pytest.mark.asyncio
    async def test_sell_increases_wallet_cash(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        wsvc = WalletService(session_factory)
        psvc = PositionService(session_factory)
        await psvc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        # cash = 49000
        await psvc.apply_sell(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("110"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        # cash += 10*110 = 1100 → 50100
        snap = await wsvc.compute_snapshot(wallet_id, current_prices={})
        assert snap.cash_balance == pytest.approx(Decimal("50100"))

    @pytest.mark.asyncio
    async def test_sell_more_than_held_raises(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        await svc.apply_buy(
            wallet_id=wallet_id, instrument_id=instr_id,
            quantity=Decimal("5"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )
        with pytest.raises(ValueError):
            await svc.apply_sell(
                wallet_id=wallet_id, instrument_id=instr_id,
                quantity=Decimal("10"), price=Decimal("100"),
                commission=Decimal("0"),
                transaction_at=datetime.now(timezone.utc),
            )

    @pytest.mark.asyncio
    async def test_sell_without_position_raises(
        self, session_factory, setup_account_and_wallet
    ):
        wallet_id, instr_id  = setup_account_and_wallet
        svc = PositionService(session_factory)
        with pytest.raises(LookupError):
            await svc.apply_sell(
                wallet_id=wallet_id, instrument_id=instr_id,
                quantity=Decimal("1"), price=Decimal("100"),
                commission=Decimal("0"),
                transaction_at=datetime.now(timezone.utc),
            )
