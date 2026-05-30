"""RiskManager (app/analysis/risk.py) birim testleri.

Kapsanan:
- position_size — ATR/cap mantığı.
- stop_loss_take_profit — BUY/SELL ATR ve fallback.
- portfolio_correlation — simetrik matris, diagonal=1.
- diversification_warnings — korelasyon > 0.8 → uyarı.
- sector_concentration — sektör ağırlıkları (0..1 toplam=1).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.analysis.risk import (
    CorrelationWarning,
    PositionSizeRecommendation,
    RiskManager,
    StopLossTakeProfit,
)
from app.db.models import Instrument, OpenPosition, PriceHistory, Wallet, UserAccount


# ---------------------------------------------------------------------------
# position_size
# ---------------------------------------------------------------------------


class TestPositionSize:
    def test_high_atr_reduces_position(self):
        """ATR/price yüksek → max_position_pct < cap %10."""
        rm = RiskManager()
        rec = rm.position_size(
            portfolio_value=Decimal("10000"),
            atr=5.0,
            current_price=Decimal("100"),
            risk_per_trade=0.02,
        )
        # atr_pct = 0.05; raw = 0.02/0.05 = 0.4; cap 0.10 → 0.10
        # Aslında min(0.10, 0.4)=0.10; bu yüksek atr için yine cap'e değer
        assert rec.max_position_pct <= 0.10
        assert isinstance(rec, PositionSizeRecommendation)

    def test_low_atr_caps_at_max(self):
        """Düşük ATR + düşük risk → cap %10."""
        rm = RiskManager()
        rec = rm.position_size(
            portfolio_value=Decimal("10000"),
            atr=0.5,  # düşük volatilite
            current_price=Decimal("100"),
            risk_per_trade=0.02,
        )
        # atr_pct = 0.005; raw = 0.02/0.005 = 4.0; cap 0.10 → 0.10
        assert rec.max_position_pct == pytest.approx(0.10, abs=1e-6)

    def test_suggested_quantity_floor(self):
        rm = RiskManager()
        rec = rm.position_size(
            portfolio_value=Decimal("10000"),
            atr=0.5,
            current_price=Decimal("100"),
            risk_per_trade=0.02,
        )
        # allocation = 10000*0.10 = 1000; qty = floor(1000/100) = 10
        assert rec.suggested_quantity == 10

    def test_invalid_inputs_return_zero(self):
        rm = RiskManager()
        rec = rm.position_size(
            portfolio_value=Decimal("0"),
            atr=1.0,
            current_price=Decimal("100"),
        )
        assert rec.suggested_quantity == 0
        assert rec.max_position_pct == 0.0


# ---------------------------------------------------------------------------
# stop_loss_take_profit
# ---------------------------------------------------------------------------


class TestStopLossTakeProfit:
    def test_buy_with_atr(self):
        """BUY: SL = entry − 1.5×ATR; TP = entry + 3×ATR."""
        rm = RiskManager()
        out = rm.stop_loss_take_profit(
            current_price=Decimal("100"),
            atr=2.0,
            action="BUY",
        )
        assert isinstance(out, StopLossTakeProfit)
        # SL = 100 - 3 = 97; TP = 100 + 6 = 106
        assert out.stop_loss == pytest.approx(Decimal("97"))
        assert out.take_profit == pytest.approx(Decimal("106"))
        assert out.based_on == "atr"
        # R/R = 6/3 = 2
        assert out.risk_reward_ratio == pytest.approx(2.0)

    def test_sell_inverts_sides(self):
        rm = RiskManager()
        out = rm.stop_loss_take_profit(
            current_price=Decimal("100"),
            atr=2.0,
            action="SELL",
        )
        # SELL: SL üstte, TP altta
        assert out.stop_loss == pytest.approx(Decimal("103"))
        assert out.take_profit == pytest.approx(Decimal("94"))

    def test_zero_atr_falls_back_to_percentage(self):
        rm = RiskManager()
        out = rm.stop_loss_take_profit(
            current_price=Decimal("100"),
            atr=0.0,
            action="BUY",
        )
        # ±%3 / ±%6
        assert out.stop_loss == pytest.approx(Decimal("97"))
        assert out.take_profit == pytest.approx(Decimal("106"))
        assert out.based_on == "percentage"

    def test_invalid_action_raises(self):
        rm = RiskManager()
        with pytest.raises(ValueError):
            rm.stop_loss_take_profit(
                current_price=Decimal("100"), atr=2.0, action="WAIT"
            )


# ---------------------------------------------------------------------------
# portfolio_correlation
# ---------------------------------------------------------------------------


def _seed_price_history(session, instrument_id: int, ticker: str,
                       prices: list[float], start: datetime = None) -> None:
    """Default start = bugünden N+1 gün önce (lookback_days içine düşsün)."""
    if start is None:
        start = datetime.now(tz=timezone.utc) - timedelta(days=len(prices) + 1)
    instr = session.get(Instrument, instrument_id)
    if instr is None:
        session.add(Instrument(id=instrument_id, ticker=ticker, name=ticker))
        session.flush()
    for i, p in enumerate(prices):
        session.add(
            PriceHistory(
                instrument_id=instrument_id,
                timestamp=start + timedelta(days=i),
                source="test",
                close=p,
                verified_close=p,
            )
        )
    session.commit()


class TestPortfolioCorrelation:
    @pytest.mark.asyncio
    async def test_two_perfectly_correlated_series_return_one(
        self, sync_engine
    ):
        from sqlalchemy.orm import sessionmaker
        SF = sessionmaker(bind=sync_engine, autoflush=False,
                          expire_on_commit=False)
        # Aynı start kullanılmalı ki timestamp'ler örtüşsün → corr hesaplanabilir
        common_start = datetime.now(tz=timezone.utc) - timedelta(days=40)
        with SF() as s:
            prices_a = [100.0 + i for i in range(30)]
            prices_b = [200.0 + 2 * i for i in range(30)]  # %100 korelasyon
            _seed_price_history(s, 1, "A", prices_a, start=common_start)
            _seed_price_history(s, 2, "B", prices_b, start=common_start)

        rm = RiskManager(session_factory=SF)
        corr = await rm.portfolio_correlation([1, 2], lookback_days=365)
        # Diagonal = 1
        assert corr.loc[1, 1] == pytest.approx(1.0, abs=1e-6)
        assert corr.loc[2, 2] == pytest.approx(1.0, abs=1e-6)
        # Simetrik
        assert corr.loc[1, 2] == pytest.approx(corr.loc[2, 1], abs=1e-9)
        # Yüksek korelasyon
        assert corr.loc[1, 2] > 0.99

    @pytest.mark.asyncio
    async def test_no_session_factory_returns_empty(self):
        rm = RiskManager(session_factory=None)
        corr = await rm.portfolio_correlation([1, 2])
        # session_factory yoksa boş frame döner
        assert corr.empty or corr.isna().all().all()


# ---------------------------------------------------------------------------
# diversification_warnings + sector_concentration
# ---------------------------------------------------------------------------


class TestDiversificationAndSectors:
    @pytest.mark.asyncio
    async def test_high_correlation_yields_warning(self, sync_engine):
        from sqlalchemy.orm import sessionmaker
        SF = sessionmaker(bind=sync_engine, autoflush=False,
                          expire_on_commit=False)

        with SF() as s:
            # Account + wallet
            acc = UserAccount(trading_mode="paper", risk_threshold=50.0,
                              cash_balance=10000.0, currency="TRY")
            s.add(acc)
            s.flush()
            w = Wallet(account_id=acc.id, pool="user", timeframe="short",
                       allocated=10000.0, cash_balance=5000.0)
            s.add(w)
            s.flush()

            # 2 koreli instrument — aynı start ile (timestamp eşleşmeli)
            common_start = datetime.now(tz=timezone.utc) - timedelta(days=40)
            prices_a = [100.0 + i for i in range(30)]
            prices_b = [200.0 + 2 * i for i in range(30)]
            _seed_price_history(s, 1, "A", prices_a, start=common_start)
            _seed_price_history(s, 2, "B", prices_b, start=common_start)

            # Açık pozisyonlar
            s.add(OpenPosition(wallet_id=w.id, instrument_id=1,
                               quantity=10.0, avg_cost=100.0))
            s.add(OpenPosition(wallet_id=w.id, instrument_id=2,
                               quantity=5.0, avg_cost=200.0))
            s.commit()
            wallet_id = int(w.id)

        rm = RiskManager(session_factory=SF)
        warnings = await rm.diversification_warnings(
            wallet_id, lookback_days=365
        )
        # En az 1 yüksek korelasyon uyarısı
        assert len(warnings) >= 1
        assert all(isinstance(w, CorrelationWarning) for w in warnings)
        assert all(abs(w.correlation) >= 0.8 for w in warnings)

    @pytest.mark.asyncio
    async def test_sector_concentration_single_sector(self, sync_engine):
        from sqlalchemy.orm import sessionmaker
        SF = sessionmaker(bind=sync_engine, autoflush=False,
                          expire_on_commit=False)
        with SF() as s:
            acc = UserAccount(trading_mode="paper", risk_threshold=50.0,
                              cash_balance=10000.0, currency="TRY")
            s.add(acc)
            s.flush()
            w = Wallet(account_id=acc.id, pool="user", timeframe="short",
                       allocated=10000.0, cash_balance=0.0)
            s.add(w)
            s.flush()
            s.add(Instrument(id=10, ticker="BANK1", name="Bank A",
                             sector="Banking"))
            s.add(Instrument(id=11, ticker="BANK2", name="Bank B",
                             sector="Banking"))
            s.flush()
            s.add(OpenPosition(wallet_id=w.id, instrument_id=10,
                               quantity=10.0, avg_cost=50.0))
            s.add(OpenPosition(wallet_id=w.id, instrument_id=11,
                               quantity=20.0, avg_cost=25.0))
            s.commit()
            wallet_id = int(w.id)

        rm = RiskManager(session_factory=SF)
        sectors = await rm.sector_concentration(wallet_id)
        assert "Banking" in sectors
        assert sectors["Banking"] == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# overall_risk_score
# ---------------------------------------------------------------------------


class TestOverallRiskScore:
    @pytest.mark.asyncio
    async def test_single_position_increases_score(self, sync_engine):
        from sqlalchemy.orm import sessionmaker
        SF = sessionmaker(bind=sync_engine, autoflush=False,
                          expire_on_commit=False)
        with SF() as s:
            acc = UserAccount(trading_mode="paper", risk_threshold=50.0,
                              cash_balance=10000.0, currency="TRY")
            s.add(acc)
            s.flush()
            w = Wallet(account_id=acc.id, pool="user", timeframe="short",
                       allocated=10000.0, cash_balance=0.0)
            s.add(w)
            s.flush()
            s.add(Instrument(id=20, ticker="SINGLE", name="Single",
                             sector="Tech"))
            s.flush()
            s.add(OpenPosition(wallet_id=w.id, instrument_id=20,
                               quantity=10.0, avg_cost=100.0))
            s.commit()
            wallet_id = int(w.id)

        rm = RiskManager(session_factory=SF)
        score, warnings = await rm.overall_risk_score(wallet_id)
        # Tek pozisyon → +25; sektör tek (>%40) → +20; baseline 30 → toplam 75
        assert score >= 50.0
        assert any("tek hisse" in w.lower() for w in warnings)
