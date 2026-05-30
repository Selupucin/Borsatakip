"""BenchmarkService (app/portfolio/benchmark.py) birim testleri."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.portfolio.account import AccountService
from app.portfolio.benchmark import BenchmarkComparison, BenchmarkService
from app.portfolio.cash_flows import CashFlowService
from app.portfolio.wallets import WalletService
from app.db.models import Instrument, PriceHistory


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


class TestTimeWeightedReturn:
    @pytest.mark.asyncio
    async def test_no_cashflow_returns_zero_when_no_change(
        self, session_factory
    ):
        asvc = AccountService(session_factory)
        wsvc = WalletService(session_factory)
        aid = await asvc.get_or_create(initial_balance=Decimal("10000"))
        await wsvc.init_matrix(aid)

        bs = BenchmarkService(session_factory)
        twr = await bs.time_weighted_return(account_id=aid)
        # 10000 başlangıç, hiç hareket → 0%
        assert twr == pytest.approx(Decimal("0"), abs=Decimal("0.01"))

    @pytest.mark.asyncio
    async def test_deposit_does_not_inflate_return(self, session_factory):
        """5000 deposit yapsak da TWR 0 olmalı (para hareketi return değil)."""
        asvc = AccountService(session_factory)
        wsvc = WalletService(session_factory)
        cf = CashFlowService(session_factory)

        aid = await asvc.get_or_create(initial_balance=Decimal("10000"))
        await wsvc.init_matrix(aid)
        await cf.deposit(aid, Decimal("5000"))

        bs = BenchmarkService(session_factory)
        twr = await bs.time_weighted_return(account_id=aid)
        # V_end = 15000, net_external = 5000, V_start = 10000
        # TWR = (15000-5000)/10000 - 1 = 0
        assert abs(twr) < Decimal("0.1")


class TestBenchmarkReturn:
    @pytest.mark.asyncio
    async def test_benchmark_return_with_seeded_prices(self, session_factory):
        # XU100 fiyat tarihçesi seed et
        with session_factory() as s:
            instr = Instrument(ticker="XU100", name="BIST 100")
            s.add(instr)
            s.commit()
            iid = int(instr.id)
            start = datetime(2024, 1, 1, tzinfo=timezone.utc)
            for i, p in enumerate([100.0, 105.0, 110.0]):
                s.add(PriceHistory(
                    instrument_id=iid,
                    timestamp=start + timedelta(days=i),
                    source="test",
                    close=p,
                    verified_close=p,
                ))
            s.commit()

        bs = BenchmarkService(session_factory)
        ret = await bs.benchmark_return(
            "XU100", date(2024, 1, 1), date(2024, 1, 5)
        )
        # 100 → 110 = +%10
        assert ret == pytest.approx(Decimal("10"), abs=Decimal("0.01"))

    @pytest.mark.asyncio
    async def test_unknown_ticker_returns_zero(self, session_factory):
        bs = BenchmarkService(session_factory)
        ret = await bs.benchmark_return(
            "NOPE", date(2024, 1, 1), date(2024, 1, 5)
        )
        assert ret == Decimal("0")


class TestCompare:
    @pytest.mark.asyncio
    async def test_compare_yields_beat_benchmark_bool(self, session_factory):
        asvc = AccountService(session_factory)
        wsvc = WalletService(session_factory)
        aid = await asvc.get_or_create(initial_balance=Decimal("10000"))
        await wsvc.init_matrix(aid)

        # Benchmark price data seed
        with session_factory() as s:
            instr = Instrument(ticker="XU100", name="BIST 100")
            s.add(instr)
            s.commit()
            iid = int(instr.id)
            for i, p in enumerate([100.0, 110.0]):
                s.add(PriceHistory(
                    instrument_id=iid,
                    timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc)
                    + timedelta(days=i * 10),
                    source="test",
                    close=p,
                    verified_close=p,
                ))
            s.commit()

        bs = BenchmarkService(session_factory)
        out = await bs.compare(
            account_id=aid,
            benchmark_ticker="XU100",
            start=date(2024, 1, 1),
            end=date(2024, 1, 20),
        )
        assert isinstance(out, BenchmarkComparison)
        # alpha = portfolio - benchmark
        assert out.alpha_pct == out.portfolio_return_pct - out.benchmark_return_pct
        assert out.beat_benchmark == (out.portfolio_return_pct > out.benchmark_return_pct)
