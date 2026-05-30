"""``PriceComparator`` birim testleri.

Doğrulanan davranışlar:
1. 2 source aynı fiyat -> diff_pct=0, discrepancy yok.
2. %0.6 fark -> discrepancy oluşur, is_alert=False (eşik %2 değil).
3. %2.5 fark -> discrepancy + is_alert=True.
4. Reliability ağırlıklı ortalama matematiği.
5. ``update_reliability(success=True)`` -> +0.1 (max 100).
6. ``update_reliability(success=False)`` -> -1.0 (min 0).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.data.base_source import Quote
from app.data.comparator import DiscrepancyRecord, PriceComparator
from app.db.models import DataSource


def _q(source: str, price: str, ts: datetime | None = None) -> Quote:
    return Quote(
        ticker="AAPL",
        price=Decimal(price),
        timestamp=ts or datetime(2024, 1, 15, 16, 0, tzinfo=timezone.utc),
        source=source,
    )


class TestCompareQuotes:
    @pytest.mark.asyncio
    async def test_same_price_no_discrepancy(
        self, async_session_factory, clean_settings
    ):
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        verified, discrepancies = comp.compare_quotes(
            instrument_id=1,
            quotes=[_q("yfinance", "100"), _q("stooq", "100")],
        )
        assert verified == Decimal("100")
        assert discrepancies == []

    @pytest.mark.asyncio
    async def test_small_pct_diff_creates_non_alert_discrepancy(
        self, async_session_factory, clean_settings
    ):
        """100 vs 100.60 -> %0.6 diff: warn eşiği (0.5) geçer, alert (2.0) geçmez."""
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        _, discs = comp.compare_quotes(
            instrument_id=1,
            quotes=[_q("yfinance", "100.00"), _q("stooq", "100.60")],
        )
        assert len(discs) == 1
        d = discs[0]
        assert d.is_alert is False
        # %0.6 civarı
        assert Decimal("0.59") < d.diff_pct < Decimal("0.61")

    @pytest.mark.asyncio
    async def test_large_pct_diff_creates_alert_discrepancy(
        self, async_session_factory, clean_settings
    ):
        """100 vs 102.5 -> %2.5 diff: alert tetiklenmeli."""
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        _, discs = comp.compare_quotes(
            instrument_id=1,
            quotes=[_q("yfinance", "100.00"), _q("stooq", "102.50")],
        )
        assert len(discs) == 1
        assert discs[0].is_alert is True
        assert discs[0].diff_pct >= Decimal("2.0")

    @pytest.mark.asyncio
    async def test_below_warn_threshold_no_discrepancy(
        self, async_session_factory, clean_settings
    ):
        """100 vs 100.1 (%0.1) -> warn eşiği (0.5) altı, hiç discrepancy yok."""
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        _, discs = comp.compare_quotes(
            instrument_id=1,
            quotes=[_q("yfinance", "100"), _q("stooq", "100.10")],
        )
        assert discs == []

    @pytest.mark.asyncio
    async def test_single_quote_returns_as_verified(
        self, async_session_factory, clean_settings
    ):
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        verified, discs = comp.compare_quotes(1, [_q("yfinance", "123.45")])
        assert verified == Decimal("123.45")
        assert discs == []


class TestWeightedAverage:
    @pytest.mark.asyncio
    async def test_weighted_average_with_different_reliability(
        self, async_session_factory, clean_settings
    ):
        """A=100 puanlı (fiyat 100), B=50 puanlı (fiyat 110) -> verified ≈ 103.33."""
        # DB'ye DataSource ile reliability ekle
        async with async_session_factory() as session:
            session.add_all(
                [
                    DataSource(name="src_a", reliability_score=100.0),
                    DataSource(name="src_b", reliability_score=50.0),
                ]
            )
            await session.commit()

        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()

        verified, _ = comp.compare_quotes(
            1,
            [_q("src_a", "100"), _q("src_b", "110")],
        )
        # (100*100 + 110*50) / 150 = 15500/150 = 103.333...
        assert abs(verified - Decimal("103.333333333333333333333333333")) < Decimal(
            "0.001"
        )

    @pytest.mark.asyncio
    async def test_weighted_average_falls_back_to_default_100_when_unknown_source(
        self, async_session_factory, clean_settings
    ):
        """DB'de yoksa default reliability=100; eşit ağırlık (basit ortalama)."""
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        verified, _ = comp.compare_quotes(
            1, [_q("xxx", "100"), _q("yyy", "200")]
        )
        # Her ikisi de 100 ağırlığında -> ortalama
        assert verified == Decimal("150")


class TestUpdateReliability:
    @pytest.mark.asyncio
    async def test_success_adds_0_1_capped_at_100(self, async_session_factory):
        async with async_session_factory() as session:
            session.add(DataSource(name="foo", reliability_score=99.95))
            await session.commit()

        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()

        await comp.update_reliability("foo", success=True)
        # 99.95 + 0.1 = 100.05 -> max 100
        async with async_session_factory() as session:
            ds = (
                await session.execute(select(DataSource).where(DataSource.name == "foo"))
            ).scalar_one()
            assert Decimal(str(ds.reliability_score)) == Decimal("100.00")

    @pytest.mark.asyncio
    async def test_success_increments_by_0_1_normal_case(self, async_session_factory):
        async with async_session_factory() as session:
            session.add(DataSource(name="foo", reliability_score=80.0))
            await session.commit()

        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        await comp.update_reliability("foo", success=True)

        async with async_session_factory() as session:
            ds = (
                await session.execute(select(DataSource).where(DataSource.name == "foo"))
            ).scalar_one()
            assert Decimal(str(ds.reliability_score)) == Decimal("80.10")

    @pytest.mark.asyncio
    async def test_failure_subtracts_1_0_floored_at_0(self, async_session_factory):
        async with async_session_factory() as session:
            session.add(DataSource(name="bar", reliability_score=0.5))
            await session.commit()

        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        await comp.update_reliability("bar", success=False)

        async with async_session_factory() as session:
            ds = (
                await session.execute(select(DataSource).where(DataSource.name == "bar"))
            ).scalar_one()
            assert Decimal(str(ds.reliability_score)) == Decimal("0.00")

    @pytest.mark.asyncio
    async def test_failure_subtracts_1_0_normal_case(self, async_session_factory):
        async with async_session_factory() as session:
            session.add(DataSource(name="baz", reliability_score=50.0))
            await session.commit()

        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        await comp.update_reliability("baz", success=False)

        async with async_session_factory() as session:
            ds = (
                await session.execute(select(DataSource).where(DataSource.name == "baz"))
            ).scalar_one()
            assert Decimal(str(ds.reliability_score)) == Decimal("49.00")

    @pytest.mark.asyncio
    async def test_update_reliability_creates_row_if_missing(self, async_session_factory):
        comp = PriceComparator(async_session_factory)
        await comp.load_reliability()
        await comp.update_reliability("newsource", success=True)

        async with async_session_factory() as session:
            ds = (
                await session.execute(
                    select(DataSource).where(DataSource.name == "newsource")
                )
            ).scalar_one()
            # Default 100 + 0.1 = 100 (cap)
            assert Decimal(str(ds.reliability_score)) == Decimal("100.00")
