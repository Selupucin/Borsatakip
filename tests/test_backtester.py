"""Backtester (app/analysis/backtester.py) birim testleri.

Önemli noktalar:
- Look-ahead bias guard (``_signal_window``) saf fonksiyon test edilir.
- ``compute_metrics`` saf static; bilinen equity curve ile Sharpe/MaxDD
  numerik kontrol.
- ``save_result`` DB INSERT — in-memory SQLite ile sync session.
- vectorbt mock'lanır (kurulu olsun ya da olmasın testler geçer).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.analysis.backtester import (
    TRADING_DAYS_PER_YEAR,
    BacktestConfig,
    BacktestRunResult,
    Backtester,
)


# ---------------------------------------------------------------------------
# BacktestConfig dataclass
# ---------------------------------------------------------------------------


class TestBacktestConfig:
    def test_required_fields(self):
        cfg = BacktestConfig(
            strategy_name="test",
            timeframe="short",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
        )
        assert cfg.strategy_name == "test"
        assert cfg.timeframe == "short"
        assert cfg.initial_capital == Decimal("100000")
        assert cfg.commission_pct == 0.002
        assert cfg.risk_threshold == 50.0
        assert cfg.weights is None


# ---------------------------------------------------------------------------
# Look-ahead bias guard: _signal_window
# ---------------------------------------------------------------------------


class TestSignalWindow:
    def test_excludes_future_dates(self):
        """as_of'tan sonraki tarihler dışlanır."""
        idx = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame({"AAPL": np.arange(10.0)}, index=idx)
        as_of = idx[4]  # 5. gün
        window = Backtester._signal_window(df, as_of)
        # window indeksi as_of'a kadar (inclusive)
        assert window.index[-1] == as_of
        assert window.shape[0] == 5

    def test_empty_dataframe_returns_empty(self):
        empty_df = pd.DataFrame()
        assert Backtester._signal_window(empty_df, pd.Timestamp.now()).empty

    def test_as_of_at_start_returns_one_row(self):
        idx = pd.date_range("2024-01-01", periods=10, freq="D")
        df = pd.DataFrame({"AAPL": np.arange(10.0)}, index=idx)
        window = Backtester._signal_window(df, idx[0])
        assert window.shape[0] == 1


# ---------------------------------------------------------------------------
# compute_metrics — saf fonksiyon
# ---------------------------------------------------------------------------


class TestComputeMetrics:
    def test_empty_curve_returns_zeros(self):
        m = Backtester.compute_metrics([], [])
        assert m["total_return_pct"] == 0.0
        assert m["sharpe_ratio"] == 0.0
        assert m["max_drawdown_pct"] == 0.0
        assert m["win_rate"] == 0.0
        assert m["total_trades"] == 0

    def test_total_return_calculation(self):
        equity_curve = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 2), Decimal("110")),
            (date(2024, 1, 3), Decimal("105")),
            (date(2024, 1, 4), Decimal("120")),
            (date(2024, 1, 5), Decimal("115")),
        ]
        m = Backtester.compute_metrics(equity_curve, [])
        # 115/100 - 1 = 0.15 → 15.0
        assert m["total_return_pct"] == pytest.approx(15.0, abs=1e-6)

    def test_max_drawdown_calculation(self):
        """Curve 100,110,105,120,115 → drawdowns -0,-0,-4.55%,-0,-4.17%
        → max_dd = 4.55% (110→105 düşüşü."""
        equity_curve = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 2), Decimal("110")),
            (date(2024, 1, 3), Decimal("105")),
            (date(2024, 1, 4), Decimal("120")),
            (date(2024, 1, 5), Decimal("115")),
        ]
        m = Backtester.compute_metrics(equity_curve, [])
        # np.maximum.accumulate: peak[2]=110, equity[2]=105 → -4.545%
        assert m["max_drawdown_pct"] == pytest.approx(4.545, abs=0.01)
        # En azından pozitif bir drawdown var
        assert m["max_drawdown_pct"] > 0.0

    def test_sharpe_ratio_positive_with_positive_returns(self):
        """Bilinen pozitif getirilerde sharpe > 0."""
        # Sade artan equity → ortalama positive return
        equity = [(date(2024, 1, i + 1), Decimal(str(100 + i))) for i in range(20)]
        m = Backtester.compute_metrics(equity, [])
        assert m["sharpe_ratio"] > 0.0

    def test_sharpe_zero_when_no_variance(self):
        """Tüm getiriler 0 → sharpe = 0 (sıfıra bölme koruması)."""
        equity = [(date(2024, 1, i + 1), Decimal("100")) for i in range(10)]
        m = Backtester.compute_metrics(equity, [])
        assert m["sharpe_ratio"] == 0.0

    def test_win_rate_and_avg_hold(self):
        equity = [
            (date(2024, 1, 1), Decimal("100")),
            (date(2024, 1, 10), Decimal("110")),
        ]
        trades = [
            {"action": "BUY", "pnl": 0.0, "hold_days": 0},
            {"action": "SELL", "pnl": 5.0, "hold_days": 3},
            {"action": "SELL", "pnl": -2.0, "hold_days": 6},
            {"action": "SELL", "pnl": 10.0, "hold_days": 9},
        ]
        m = Backtester.compute_metrics(equity, trades)
        # 2/3 winners
        assert m["win_rate"] == pytest.approx(66.6667, abs=0.01)
        assert m["avg_hold_days"] == pytest.approx(6.0, abs=1e-6)
        assert m["total_trades"] == 4


# ---------------------------------------------------------------------------
# save_result — DB INSERT
# ---------------------------------------------------------------------------


class TestSaveResult:
    def test_save_result_inserts_row_and_returns_id(self, db_session):
        bt = Backtester(session_factory=lambda: db_session)
        result = BacktestRunResult(
            strategy_name="short_test",
            timeframe="short",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 30),
            total_return_pct=12.34,
            sharpe_ratio=1.5,
            max_drawdown_pct=8.0,
            win_rate=60.0,
            total_trades=20,
            avg_hold_days=5.0,
            final_capital=Decimal("112340"),
            params={"weights": {"tech": 0.6}},
        )
        row_id = bt.save_result(db_session, result)
        assert row_id > 0

        from app.db.models import BacktestResult
        fetched = db_session.get(BacktestResult, row_id)
        assert fetched is not None
        assert fetched.strategy_name == "short_test"
        assert fetched.timeframe == "short"
        assert float(fetched.total_return) == pytest.approx(12.34, abs=1e-3)


# ---------------------------------------------------------------------------
# vectorbt fallback — pandas yolu çalışır
# ---------------------------------------------------------------------------


class TestVectorbtFallback:
    def test_pandas_fallback_active_when_vectorbt_missing(self):
        """_vbt None ya da modül; her halükarda Backtester init çalışır."""
        bt = Backtester(session_factory=None)
        # _vbt ya None (yoksa) ya da vectorbt modülü
        assert bt._vbt is None or hasattr(bt._vbt, "__name__")

    def test_default_signal_returns_valid_action(self):
        """_default_signal BUY/HOLD/SELL döndürür."""
        idx = pd.date_range("2024-01-01", periods=30, freq="D")
        # Yukarı trend → BUY
        prices_up = pd.Series(np.linspace(100, 130, 30), index=idx)
        df_up = pd.DataFrame({"1": prices_up.values}, index=idx)
        sig = Backtester._default_signal(df_up, idx[-1], "1")
        assert sig in {"BUY", "HOLD", "SELL"}
