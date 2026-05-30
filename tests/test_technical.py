"""``TechnicalAnalyzer`` birim testleri.

Mock stratejisi:
- TA-Lib gerçekten kurulu olmayabilir; backend=None için RuntimeError beklenir.
- ``_compute_talib`` / ``_compute_pandas_ta`` doğrudan monkey-patch ile
  deterministic sabit Series döndürür — gerçek indikatör matematiği test
  EDİLMEZ (lib zaten test edildi).
- Sinyal yorumlama saf-Python; gerçek değerlerle test edilir.
"""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.analysis.technical import (
    TechnicalAnalyzer,
    TechnicalIndicators,
    signal_interpretation,
)


# ---------------------------------------------------------------------------
# Yardımcı: Deterministic TechnicalIndicators üretici
# ---------------------------------------------------------------------------


def _fake_indicators(df: pd.DataFrame) -> TechnicalIndicators:
    """Tüm seriler 100 uzunluğunda sabit + bilinen değerler döndürür."""
    n = len(df)
    idx = df.index
    rsi = pd.Series([np.nan] * 13 + [50.0] * (n - 13), index=idx, name="rsi")
    macd = pd.Series([0.5] * n, index=idx, name="macd")
    macd_signal = pd.Series([0.3] * n, index=idx, name="macd_signal")
    macd_hist = pd.Series([0.2] * n, index=idx, name="macd_hist")
    bb_upper = pd.Series([110.0] * n, index=idx, name="bb_upper")
    bb_middle = pd.Series([100.0] * n, index=idx, name="bb_middle")
    bb_lower = pd.Series([90.0] * n, index=idx, name="bb_lower")
    ema_20 = pd.Series([99.0] * n, index=idx, name="ema_20")
    ema_50 = pd.Series([98.0] * n, index=idx, name="ema_50")
    ema_200 = pd.Series([97.0] * n, index=idx, name="ema_200")
    stoch_k = pd.Series([55.0] * n, index=idx, name="stoch_k")
    stoch_d = pd.Series([50.0] * n, index=idx, name="stoch_d")
    atr = pd.Series([1.5] * n, index=idx, name="atr")
    return TechnicalIndicators(
        rsi=rsi,
        macd=macd,
        macd_signal=macd_signal,
        macd_hist=macd_hist,
        bb_upper=bb_upper,
        bb_middle=bb_middle,
        bb_lower=bb_lower,
        ema_20=ema_20,
        ema_50=ema_50,
        ema_200=ema_200,
        stoch_k=stoch_k,
        stoch_d=stoch_d,
        atr=atr,
    )


# ---------------------------------------------------------------------------
# Backend = None
# ---------------------------------------------------------------------------


class TestBackendNone:
    def test_backend_none_compute_all_raises_runtime_error(self, sample_ohlcv):
        analyzer = TechnicalAnalyzer(backend=None)
        # Manuel None set; constructor BACKEND'i alabiliyor
        analyzer.backend = None
        with pytest.raises(RuntimeError):
            analyzer.compute_all(sample_ohlcv)

    def test_invalid_backend_raises_value_error(self):
        with pytest.raises(ValueError):
            TechnicalAnalyzer(backend="xyz_unknown")


# ---------------------------------------------------------------------------
# compute_all (mock backend)
# ---------------------------------------------------------------------------


class TestComputeAll:
    def test_compute_all_returns_indicators_with_full_length(
        self, sample_ohlcv, monkeypatch
    ):
        analyzer = TechnicalAnalyzer(backend="talib")
        monkeypatch.setattr(
            analyzer,
            "_compute_talib",
            lambda h, l_, c: _fake_indicators(sample_ohlcv),
        )
        ind = analyzer.compute_all(sample_ohlcv)
        assert isinstance(ind, TechnicalIndicators)
        # Her seri 100 uzunluğunda
        assert len(ind.rsi) == 100
        assert len(ind.macd) == 100
        assert len(ind.ema_200) == 100

    def test_compute_all_preserves_nan_at_lookback_start(
        self, sample_ohlcv, monkeypatch
    ):
        """Lookback yetmeyen başlangıç satırları NaN olarak kalır, drop YOK."""
        analyzer = TechnicalAnalyzer(backend="talib")
        monkeypatch.setattr(
            analyzer,
            "_compute_talib",
            lambda h, l_, c: _fake_indicators(sample_ohlcv),
        )
        ind = analyzer.compute_all(sample_ohlcv)
        # _fake_indicators RSI'da ilk 13 satırı NaN bırakır
        assert ind.rsi.iloc[:13].isna().all()
        # Geri kalan dolu
        assert ind.rsi.iloc[13:].notna().all()
        # DataFrame uzunluğu DEĞİŞMEZ
        assert len(ind.rsi) == len(sample_ohlcv)

    def test_compute_all_missing_required_columns_raises(self, monkeypatch):
        analyzer = TechnicalAnalyzer(backend="talib")
        bad_df = pd.DataFrame({"close": [1, 2, 3]})
        # `_compute_talib` çağrılmadan önce kolon kontrolü patlar
        with pytest.raises(KeyError):
            analyzer.compute_all(bad_df)


# ---------------------------------------------------------------------------
# latest_snapshot
# ---------------------------------------------------------------------------


class TestLatestSnapshot:
    def test_latest_snapshot_contains_expected_keys(
        self, sample_ohlcv, monkeypatch
    ):
        analyzer = TechnicalAnalyzer(backend="talib")
        monkeypatch.setattr(
            analyzer,
            "_compute_talib",
            lambda h, l_, c: _fake_indicators(sample_ohlcv),
        )
        snap = analyzer.latest_snapshot(sample_ohlcv)
        for key in ("rsi", "macd", "ema_20", "ema_50", "ema_200"):
            assert key in snap, f"Snapshot'ta {key} eksik"

    def test_latest_snapshot_returns_native_floats(
        self, sample_ohlcv, monkeypatch
    ):
        analyzer = TechnicalAnalyzer(backend="talib")
        monkeypatch.setattr(
            analyzer,
            "_compute_talib",
            lambda h, l_, c: _fake_indicators(sample_ohlcv),
        )
        snap = analyzer.latest_snapshot(sample_ohlcv)
        assert isinstance(snap["rsi"], float)
        assert snap["rsi"] == 50.0
        assert snap["ema_20"] == 99.0

    def test_latest_snapshot_includes_timestamp(self, sample_ohlcv, monkeypatch):
        analyzer = TechnicalAnalyzer(backend="talib")
        monkeypatch.setattr(
            analyzer,
            "_compute_talib",
            lambda h, l_, c: _fake_indicators(sample_ohlcv),
        )
        snap = analyzer.latest_snapshot(sample_ohlcv)
        assert "timestamp" in snap
        assert isinstance(snap["timestamp"], datetime)


# ---------------------------------------------------------------------------
# save_signals
# ---------------------------------------------------------------------------


class TestSaveSignals:
    def test_save_signals_inserts_technical_signal_row(self, db_session):
        from app.db.models import Instrument, TechnicalSignal

        instr = Instrument(ticker="GARAN")
        db_session.add(instr)
        db_session.commit()

        analyzer = TechnicalAnalyzer(backend="talib")
        snapshot = {
            "rsi": 55.0,
            "macd": 0.5,
            "macd_signal": 0.3,
            "bb_upper": 110.0,
            "bb_lower": 90.0,
            "ema_20": 99.0,
            "ema_50": 98.0,
            "ema_200": 97.0,
            "timestamp": datetime.now(tz=timezone.utc),
        }
        row = analyzer.save_signals(db_session, instrument_id=instr.id, snapshot=snapshot)
        db_session.commit()
        assert isinstance(row, TechnicalSignal)
        # DB roundtrip
        found = (
            db_session.query(TechnicalSignal)
            .filter_by(instrument_id=instr.id)
            .one()
        )
        assert float(found.rsi) == 55.0
        assert float(found.ema_200) == 97.0


# ---------------------------------------------------------------------------
# signal_interpretation
# ---------------------------------------------------------------------------


class TestSignalInterpretation:
    def test_rsi_oversold(self):
        out = signal_interpretation({"rsi": 25.0})
        assert out["rsi"] == "oversold"

    def test_rsi_overbought(self):
        out = signal_interpretation({"rsi": 75.0})
        assert out["rsi"] == "overbought"

    def test_rsi_neutral(self):
        out = signal_interpretation({"rsi": 50.0})
        assert out["rsi"] == "neutral"

    def test_trend_uptrend(self):
        out = signal_interpretation(
            {"ema_20": 110.0, "ema_50": 105.0, "ema_200": 100.0}
        )
        assert out["trend"] == "uptrend"

    def test_trend_downtrend(self):
        out = signal_interpretation(
            {"ema_20": 90.0, "ema_50": 95.0, "ema_200": 100.0}
        )
        assert out["trend"] == "downtrend"

    def test_macd_bullish_cross(self):
        out = signal_interpretation(
            {"macd": 1.0, "macd_signal": 0.5},
            prev_snapshot={"macd": 0.2, "macd_signal": 0.4},
        )
        assert out["macd"] == "bullish_cross"

    def test_macd_bearish_cross(self):
        out = signal_interpretation(
            {"macd": 0.2, "macd_signal": 0.5},
            prev_snapshot={"macd": 0.8, "macd_signal": 0.5},
        )
        assert out["macd"] == "bearish_cross"

    def test_bollinger_squeeze(self):
        out = signal_interpretation(
            {"bb_upper": 102.0, "bb_middle": 100.0, "bb_lower": 98.0}
        )
        # (102-98)/100*100 = 4 < 5 -> squeeze
        assert out["bollinger"] == "squeeze"
