"""PnLCalculator (app/portfolio/pnl.py) birim testleri."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.portfolio.account import AccountService
from app.portfolio.pnl import PnLBreakdown, PnLCalculator
from app.portfolio.positions import PositionService
from app.portfolio.wallets import WalletService
from app.db.models import FxRate, Instrument


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
async def setup_try_position(session_factory):
    """TRY hisse + 10@100 BUY → 10@120 SELL."""
    asvc = AccountService(session_factory)
    wsvc = WalletService(session_factory)
    aid = await asvc.get_or_create(initial_balance=Decimal("100000"))
    matrix = await wsvc.init_matrix(aid)
    wallet_id = matrix[("user", "short")]
    await wsvc.allocate(wallet_id, Decimal("50000"))

    with session_factory() as s:
        s.add(Instrument(ticker="THYAO", name="THY", currency="TRY"))
        s.commit()
        instr_id = int(
            s.query(Instrument).filter_by(ticker="THYAO").one().id
        )

    psvc = PositionService(session_factory)
    await psvc.apply_buy(
        wallet_id=wallet_id, instrument_id=instr_id,
        quantity=Decimal("10"), price=Decimal("100"),
        commission=Decimal("1"),
        transaction_at=datetime.now(timezone.utc),
    )
    tx_id, realized = await psvc.apply_sell(
        wallet_id=wallet_id, instrument_id=instr_id,
        quantity=Decimal("10"), price=Decimal("120"),
        commission=Decimal("2"),
        transaction_at=datetime.now(timezone.utc),
    )
    return wallet_id, instr_id, tx_id, realized


# ---------------------------------------------------------------------------
# closed_trade_pnl
# ---------------------------------------------------------------------------


class TestClosedTradePnl:
    @pytest.mark.asyncio
    async def test_try_stock_no_fx_pnl(
        self, session_factory, setup_try_position
    ):
        _wallet, _instr, sell_tx_id, _realized  = setup_try_position
        calc = PnLCalculator(session_factory)
        br = await calc.closed_trade_pnl(sell_tx_id)
        assert isinstance(br, PnLBreakdown)
        # TRY → fx_pnl=0
        assert br.fx_pnl == Decimal("0")
        # (120-100)*10 - 2 = 198
        assert br.instrument_pnl_local == pytest.approx(Decimal("198"))
        assert br.total_pnl_try == pytest.approx(Decimal("198"))

    @pytest.mark.asyncio
    async def test_commission_in_breakdown(
        self, session_factory, setup_try_position
    ):
        _wallet, _instr, sell_tx_id, _realized  = setup_try_position
        calc = PnLCalculator(session_factory)
        br = await calc.closed_trade_pnl(sell_tx_id)
        assert br.commission_total == pytest.approx(Decimal("2"))


# ---------------------------------------------------------------------------
# position_pnl
# ---------------------------------------------------------------------------


class TestPositionPnl:
    @pytest.mark.asyncio
    async def test_position_pnl_unrealized(self, session_factory):
        """Açık TRY pozisyon, mevcut fiyat = 110."""
        asvc = AccountService(session_factory)
        wsvc = WalletService(session_factory)
        aid = await asvc.get_or_create(initial_balance=Decimal("50000"))
        matrix = await wsvc.init_matrix(aid)
        wid = matrix[("user", "short")]
        await wsvc.allocate(wid, Decimal("20000"))

        with session_factory() as s:
            s.add(Instrument(ticker="X", name="X", currency="TRY"))
            s.commit()
            instr_id = int(
                s.query(Instrument).filter_by(ticker="X").one().id
            )

        psvc = PositionService(session_factory)
        await psvc.apply_buy(
            wallet_id=wid, instrument_id=instr_id,
            quantity=Decimal("10"), price=Decimal("100"),
            commission=Decimal("0"),
            transaction_at=datetime.now(timezone.utc),
        )

        calc = PnLCalculator(session_factory)
        br = await calc.position_pnl(
            wallet_id=wid,
            instrument_id=instr_id,
            current_price=Decimal("110"),
        )
        # unrealized = (110 - 100) * 10 = 100
        assert br.instrument_pnl_local == pytest.approx(Decimal("100"))
        assert br.fx_pnl == Decimal("0")


# ---------------------------------------------------------------------------
# wallet_total_pnl
# ---------------------------------------------------------------------------


class TestWalletTotalPnl:
    @pytest.mark.asyncio
    async def test_wallet_total_realized_only(
        self, session_factory, setup_try_position
    ):
        wallet_id, _instr, _tx, _r  = setup_try_position
        calc = PnLCalculator(session_factory)
        br = await calc.wallet_total_pnl(
            wallet_id=wallet_id, current_prices={}
        )
        # Sadece realized → 198
        assert br.instrument_pnl_local == pytest.approx(Decimal("198"))
