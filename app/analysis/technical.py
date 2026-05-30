"""TA-Lib teknik analiz indikatörleri (vectorized pandas).

Doküman §3.5 ve §11 Faz 1 referans alınarak yazılmıştır.

Tasarım kuralları:
- Tüm hesaplamalar pandas DataFrame üzerinde **vectorized** — satır loop YOK.
- Birincil backend ``TA-Lib`` (``talib``). Kurulamazsa fallback olarak
  ``pandas_ta`` denenir; o da yoksa ``BACKEND=None`` kalır (test mock'ı için).
- Tüm hesaplamalar ``verified_close`` üzerinden çalışmak için tasarlanmıştır
  (çağıran tarafın DataFrame'i bu kolondan türetmesi beklenir). Yoksa
  ``close`` kullanılır.
- Eksik veri (lookback yetmediği için) ``NaN`` olarak bırakılır — drop EDİLMEZ.
  DB'ye yazım sırasında ``NaN`` -> ``None`` dönüşümü yapılır.
- Para hesaplaması yok; yalnızca teknik indikatör.

İndikatör listesi (12 seri):
    RSI(14), MACD(12,26,9) [line, signal, hist], Bollinger(20, 2) [upper,
    middle, lower], EMA(20/50/200), Stochastic(14, 3, 3) [%K, %D], ATR(14).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

# ---------------------------------------------------------------------------
# Backend seçimi (talib > pandas_ta > None)
# ---------------------------------------------------------------------------

BACKEND: Optional[str]

try:  # pragma: no cover - import side-effect
    import talib  # type: ignore[import-not-found]

    BACKEND = "talib"
except ImportError:  # pragma: no cover - fallback path
    try:
        import pandas_ta as _pta  # type: ignore[import-not-found]  # noqa: F401

        BACKEND = "pandas_ta"
    except ImportError:
        # Üçüncü kademe: pure pandas (numpy + ewm/rolling). TA-Lib doğruluğunda
        # değil ama uygulama çalışsın diye yeterli (RSI, MACD, EMA, BB, ATR).
        BACKEND = "pandas_pure"
        logger.info(
            "Ne TA-Lib ne pandas_ta yüklü; pure-pandas backend kullanılacak "
            "(RSI/MACD/EMA/BB/ATR/Stoch — Wilder/SMA approximation)."
        )


# ---------------------------------------------------------------------------
# Veri sınıfları
# ---------------------------------------------------------------------------


@dataclass
class TechnicalIndicators:
    """Hesaplanan tüm indikatörlerin pandas Series koleksiyonu.

    Tüm seriler, kaynak DataFrame ile aynı uzunlukta ve aynı index'tedir.
    Lookback yetmeyen baş satırlar NaN olarak kalır.
    """

    rsi: pd.Series  # RSI(14)
    macd: pd.Series  # MACD çizgisi (EMA12 - EMA26)
    macd_signal: pd.Series  # MACD sinyal çizgisi (EMA9)
    macd_hist: pd.Series  # MACD histogramı (macd - signal)
    bb_upper: pd.Series  # Bollinger üst bandı (20, 2)
    bb_middle: pd.Series  # Bollinger orta bandı (SMA 20)
    bb_lower: pd.Series  # Bollinger alt bandı (20, 2)
    ema_20: pd.Series
    ema_50: pd.Series
    ema_200: pd.Series
    stoch_k: pd.Series  # Stochastic %K (14, 3)
    stoch_d: pd.Series  # Stochastic %D (3)
    atr: pd.Series  # ATR(14) — risk yönetimi için


# ---------------------------------------------------------------------------
# Yardımcı işlevler
# ---------------------------------------------------------------------------


def _pick_close(df: pd.DataFrame) -> pd.Series:
    """``verified_close`` varsa onu, yoksa ``close`` kolonunu döndürür."""

    if "verified_close" in df.columns and df["verified_close"].notna().any():
        return df["verified_close"].astype("float64")
    if "close" in df.columns:
        return df["close"].astype("float64")
    raise KeyError(
        "DataFrame 'verified_close' veya 'close' kolonu içermiyor; "
        "TA-Lib indikatörü hesaplanamaz."
    )


def _require_columns(df: pd.DataFrame, cols: tuple[str, ...]) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(
            f"TechnicalAnalyzer: DataFrame'de eksik kolon(lar): {missing}"
        )


def _nan_to_none(value):
    """``NaN`` / ``pd.NA`` değerlerini DB-uyumlu ``None``'a çevirir."""

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):  # pragma: no cover - savunmacı
        return value
    return value


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class TechnicalAnalyzer:
    """Vectorized teknik analiz hesaplayıcısı.

    Backend ``talib`` veya ``pandas_ta`` olabilir. Test ortamında ``backend=None``
    geçirilirse herhangi bir indikatör fonksiyonu çağırmadan önce
    ``RuntimeError`` fırlatır — bu sayede test-engineer fonksiyonları monkey-patch
    ile kolayca mock'layabilir.
    """

    def __init__(self, backend: str | None = None) -> None:
        # backend=None ise modül BACKEND'i kullan; AMA test'lerin explicit `backend=None`
        # geçirebilmesi için sentinel kullan
        self.backend = backend if backend is not None else BACKEND
        if self.backend not in {"talib", "pandas_ta", "pandas_pure", None}:
            raise ValueError(
                f"Geçersiz backend: {self.backend!r}. "
                "'talib', 'pandas_ta', 'pandas_pure' veya None olmalı."
            )
        if self.backend is None:
            logger.debug(
                "TechnicalAnalyzer backend=None ile başlatıldı; gerçek "
                "indikatör çağrıları RuntimeError verir (sadece mock için)."
            )

    # ------------------------------------------------------------------ API

    def compute_all(self, df: pd.DataFrame) -> TechnicalIndicators:
        """Tüm indikatörleri tek geçişte hesaplar.

        Parameters
        ----------
        df:
            ``open``, ``high``, ``low``, ``close`` (veya ``verified_close``)
            ve ``volume`` kolonlarına sahip pandas DataFrame.

        Returns
        -------
        TechnicalIndicators
            Tüm seriler ``df`` ile aynı uzunlukta; lookback yetmeyen satırlar
            NaN.
        """

        if self.backend is None:
            raise RuntimeError(
                "TechnicalAnalyzer: backend=None — gerçek hesaplama yapılamaz. "
                "Testte sınıf metodlarını monkey-patch ile mock'layın."
            )

        _require_columns(df, ("open", "high", "low"))
        close = _pick_close(df)
        high = df["high"].astype("float64")
        low = df["low"].astype("float64")

        if self.backend == "talib":
            return self._compute_talib(high, low, close)
        if self.backend == "pandas_pure":
            return self._compute_pandas_pure(df, high, low, close)
        return self._compute_pandas_ta(df, high, low, close)

    # ------------------------------------------------------------------ pure pandas
    def _compute_pandas_pure(
        self, df: pd.DataFrame, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> TechnicalIndicators:
        """TA-Lib veya pandas_ta yokken pure-pandas indikatör hesabı.

        Approximation — Wilder'in EMA(α=1/n) varyantı yerine standart EMA, RSI
        Wilder smoothing yerine basit EMA. Production'da talib tercih edilir.
        """
        # EMA serileri
        ema_20 = close.ewm(span=20, adjust=False).mean()
        ema_50 = close.ewm(span=50, adjust=False).mean()
        ema_200 = close.ewm(span=200, adjust=False).mean()

        # RSI(14) — Wilder approximation via ewm(alpha=1/14)
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))

        # MACD(12,26,9)
        ema_12 = close.ewm(span=12, adjust=False).mean()
        ema_26 = close.ewm(span=26, adjust=False).mean()
        macd = ema_12 - ema_26
        macd_signal = macd.ewm(span=9, adjust=False).mean()
        macd_hist = macd - macd_signal

        # Bollinger Bands (20, 2)
        bb_middle = close.rolling(window=20, min_periods=20).mean()
        bb_std = close.rolling(window=20, min_periods=20).std(ddof=0)
        bb_upper = bb_middle + 2 * bb_std
        bb_lower = bb_middle - 2 * bb_std

        # Stochastic (14, 3, 3)
        low_14 = low.rolling(window=14, min_periods=14).min()
        high_14 = high.rolling(window=14, min_periods=14).max()
        stoch_k_raw = 100 * (close - low_14) / (high_14 - low_14).replace(0, np.nan)
        stoch_k = stoch_k_raw.rolling(window=3, min_periods=3).mean()
        stoch_d = stoch_k.rolling(window=3, min_periods=3).mean()

        # ATR(14) — true range Wilder smoothing
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()

        return TechnicalIndicators(
            rsi=rsi.rename("rsi"),
            macd=macd.rename("macd"),
            macd_signal=macd_signal.rename("macd_signal"),
            macd_hist=macd_hist.rename("macd_hist"),
            bb_upper=bb_upper.rename("bb_upper"),
            bb_middle=bb_middle.rename("bb_middle"),
            bb_lower=bb_lower.rename("bb_lower"),
            ema_20=ema_20.rename("ema_20"),
            ema_50=ema_50.rename("ema_50"),
            ema_200=ema_200.rename("ema_200"),
            stoch_k=stoch_k.rename("stoch_k"),
            stoch_d=stoch_d.rename("stoch_d"),
            atr=atr.rename("atr"),
        )

    def latest_snapshot(self, df: pd.DataFrame) -> dict:
        """En son satırın indikatör değerlerini ``TechnicalSignal``'a hazır dict olarak döndürür.

        DB-uyumlu: tüm ``NaN`` değerler ``None``'a dönüştürülür. ``timestamp``
        alanı, DataFrame'in son index'i (DatetimeIndex ise) ya da
        ``df['timestamp']`` son değeri; ikisi de yoksa ``datetime.now(UTC)``.
        """

        ind = self.compute_all(df)
        last = -1

        snapshot: dict = {
            "rsi": _nan_to_none(float(ind.rsi.iloc[last])) if len(ind.rsi) else None,
            "macd": _nan_to_none(float(ind.macd.iloc[last])) if len(ind.macd) else None,
            "macd_signal": (
                _nan_to_none(float(ind.macd_signal.iloc[last]))
                if len(ind.macd_signal)
                else None
            ),
            "macd_hist": (
                _nan_to_none(float(ind.macd_hist.iloc[last]))
                if len(ind.macd_hist)
                else None
            ),
            "bb_upper": (
                _nan_to_none(float(ind.bb_upper.iloc[last]))
                if len(ind.bb_upper)
                else None
            ),
            "bb_middle": (
                _nan_to_none(float(ind.bb_middle.iloc[last]))
                if len(ind.bb_middle)
                else None
            ),
            "bb_lower": (
                _nan_to_none(float(ind.bb_lower.iloc[last]))
                if len(ind.bb_lower)
                else None
            ),
            "ema_20": (
                _nan_to_none(float(ind.ema_20.iloc[last]))
                if len(ind.ema_20)
                else None
            ),
            "ema_50": (
                _nan_to_none(float(ind.ema_50.iloc[last]))
                if len(ind.ema_50)
                else None
            ),
            "ema_200": (
                _nan_to_none(float(ind.ema_200.iloc[last]))
                if len(ind.ema_200)
                else None
            ),
            "stoch_k": (
                _nan_to_none(float(ind.stoch_k.iloc[last]))
                if len(ind.stoch_k)
                else None
            ),
            "stoch_d": (
                _nan_to_none(float(ind.stoch_d.iloc[last]))
                if len(ind.stoch_d)
                else None
            ),
            "atr": _nan_to_none(float(ind.atr.iloc[last])) if len(ind.atr) else None,
            "timestamp": self._extract_timestamp(df),
        }
        return snapshot

    def save_signals(
        self,
        session,
        instrument_id: int,
        snapshot: dict,
        source: str = "computed",
    ):
        """Snapshot'ı ``technical_signals`` tablosuna yazar.

        ``macd_hist``, ``stoch_k``, ``stoch_d``, ``atr``, ``bb_middle`` alanları
        DB modelinde yok — saklanmaz. Sadece DB'de var olan kolonlar yazılır.
        """

        # İçe aktarmayı geciktiriyoruz ki bu modül ``app.db`` yoksa bile import
        # edilebilsin (testlerde stub session yeterli olur).
        from app.db.models import TechnicalSignal  # noqa: WPS433

        row = TechnicalSignal(
            instrument_id=instrument_id,
            source=source,
            timestamp=snapshot.get("timestamp"),
            rsi=snapshot.get("rsi"),
            macd=snapshot.get("macd"),
            macd_signal=snapshot.get("macd_signal"),
            bb_upper=snapshot.get("bb_upper"),
            bb_lower=snapshot.get("bb_lower"),
            ema_20=snapshot.get("ema_20"),
            ema_50=snapshot.get("ema_50"),
            ema_200=snapshot.get("ema_200"),
        )
        session.add(row)
        return row

    # ------------------------------------------------------------------ talib

    def _compute_talib(
        self, high: pd.Series, low: pd.Series, close: pd.Series
    ) -> TechnicalIndicators:
        h = high.to_numpy(dtype=np.float64)
        l_ = low.to_numpy(dtype=np.float64)
        c = close.to_numpy(dtype=np.float64)

        rsi = talib.RSI(c, timeperiod=14)
        macd, macd_signal, macd_hist = talib.MACD(
            c, fastperiod=12, slowperiod=26, signalperiod=9
        )
        bb_upper, bb_middle, bb_lower = talib.BBANDS(
            c, timeperiod=20, nbdevup=2.0, nbdevdn=2.0, matype=0
        )
        ema_20 = talib.EMA(c, timeperiod=20)
        ema_50 = talib.EMA(c, timeperiod=50)
        ema_200 = talib.EMA(c, timeperiod=200)
        stoch_k, stoch_d = talib.STOCH(
            h,
            l_,
            c,
            fastk_period=14,
            slowk_period=3,
            slowk_matype=0,
            slowd_period=3,
            slowd_matype=0,
        )
        atr = talib.ATR(h, l_, c, timeperiod=14)

        idx = close.index
        return TechnicalIndicators(
            rsi=pd.Series(rsi, index=idx, name="rsi"),
            macd=pd.Series(macd, index=idx, name="macd"),
            macd_signal=pd.Series(macd_signal, index=idx, name="macd_signal"),
            macd_hist=pd.Series(macd_hist, index=idx, name="macd_hist"),
            bb_upper=pd.Series(bb_upper, index=idx, name="bb_upper"),
            bb_middle=pd.Series(bb_middle, index=idx, name="bb_middle"),
            bb_lower=pd.Series(bb_lower, index=idx, name="bb_lower"),
            ema_20=pd.Series(ema_20, index=idx, name="ema_20"),
            ema_50=pd.Series(ema_50, index=idx, name="ema_50"),
            ema_200=pd.Series(ema_200, index=idx, name="ema_200"),
            stoch_k=pd.Series(stoch_k, index=idx, name="stoch_k"),
            stoch_d=pd.Series(stoch_d, index=idx, name="stoch_d"),
            atr=pd.Series(atr, index=idx, name="atr"),
        )

    # ---------------------------------------------------------------- pandas_ta

    def _compute_pandas_ta(
        self,
        df: pd.DataFrame,
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
    ) -> TechnicalIndicators:
        import pandas_ta as ta  # type: ignore[import-not-found]

        rsi = ta.rsi(close, length=14)
        macd_df = ta.macd(close, fast=12, slow=26, signal=9)
        # pandas_ta kolon adları: MACD_12_26_9, MACDh_12_26_9, MACDs_12_26_9
        macd = macd_df.iloc[:, 0]
        macd_hist = macd_df.iloc[:, 1]
        macd_signal = macd_df.iloc[:, 2]

        bb_df = ta.bbands(close, length=20, std=2.0)
        # Kolonlar: BBL_20_2.0, BBM_20_2.0, BBU_20_2.0, BBB_20_2.0, BBP_20_2.0
        bb_lower = bb_df.iloc[:, 0]
        bb_middle = bb_df.iloc[:, 1]
        bb_upper = bb_df.iloc[:, 2]

        ema_20 = ta.ema(close, length=20)
        ema_50 = ta.ema(close, length=50)
        ema_200 = ta.ema(close, length=200)

        stoch_df = ta.stoch(high, low, close, k=14, d=3, smooth_k=3)
        stoch_k = stoch_df.iloc[:, 0]
        stoch_d = stoch_df.iloc[:, 1]

        atr = ta.atr(high, low, close, length=14)

        idx = close.index

        def _align(s: pd.Series | None, name: str) -> pd.Series:
            if s is None:
                return pd.Series(np.nan, index=idx, name=name)
            return s.reindex(idx).rename(name)

        return TechnicalIndicators(
            rsi=_align(rsi, "rsi"),
            macd=_align(macd, "macd"),
            macd_signal=_align(macd_signal, "macd_signal"),
            macd_hist=_align(macd_hist, "macd_hist"),
            bb_upper=_align(bb_upper, "bb_upper"),
            bb_middle=_align(bb_middle, "bb_middle"),
            bb_lower=_align(bb_lower, "bb_lower"),
            ema_20=_align(ema_20, "ema_20"),
            ema_50=_align(ema_50, "ema_50"),
            ema_200=_align(ema_200, "ema_200"),
            stoch_k=_align(stoch_k, "stoch_k"),
            stoch_d=_align(stoch_d, "stoch_d"),
            atr=_align(atr, "atr"),
        )

    # ------------------------------------------------------------------ utils

    @staticmethod
    def _extract_timestamp(df: pd.DataFrame) -> datetime:
        """En son satırın timestamp'ini UTC ``datetime``'a çevirir."""

        if isinstance(df.index, pd.DatetimeIndex) and len(df.index):
            ts = df.index[-1]
        elif "timestamp" in df.columns and len(df):
            ts = df["timestamp"].iloc[-1]
        else:
            return datetime.now(tz=timezone.utc)
        if isinstance(ts, pd.Timestamp):
            return ts.to_pydatetime() if ts.tzinfo else ts.tz_localize("UTC").to_pydatetime()
        if isinstance(ts, datetime):
            return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
        return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# Sinyal yorumlama yardımcısı
# ---------------------------------------------------------------------------


def signal_interpretation(
    snapshot: dict, prev_snapshot: dict | None = None
) -> dict[str, str]:
    """Snapshot'tan okunabilir sinyal etiketleri üretir.

    Döndürülen sözlükteki olası anahtarlar:
        ``rsi`` : ``"oversold"`` (<30), ``"overbought"`` (>70), ``"neutral"``
        ``macd`` : ``"bullish_cross"``, ``"bearish_cross"`` (prev gerekli)
        ``bollinger`` : ``"squeeze"`` (band genişliği <%5 SMA), ``"breakout_upper"``,
                         ``"breakout_lower"``, ``"inside"``
        ``trend`` : ``"uptrend"`` (EMA20 > EMA50 > EMA200), ``"downtrend"``,
                    ``"mixed"``
    """

    out: dict[str, str] = {}

    rsi = snapshot.get("rsi")
    if rsi is not None:
        if rsi < 30:
            out["rsi"] = "oversold"
        elif rsi > 70:
            out["rsi"] = "overbought"
        else:
            out["rsi"] = "neutral"

    macd = snapshot.get("macd")
    signal = snapshot.get("macd_signal")
    if macd is not None and signal is not None and prev_snapshot:
        prev_macd = prev_snapshot.get("macd")
        prev_signal = prev_snapshot.get("macd_signal")
        if prev_macd is not None and prev_signal is not None:
            if prev_macd <= prev_signal and macd > signal:
                out["macd"] = "bullish_cross"
            elif prev_macd >= prev_signal and macd < signal:
                out["macd"] = "bearish_cross"

    upper = snapshot.get("bb_upper")
    middle = snapshot.get("bb_middle")
    lower = snapshot.get("bb_lower")
    close = snapshot.get("close")  # opsiyonel — çağıran sağlarsa
    if upper is not None and middle is not None and lower is not None and middle:
        width_pct = (upper - lower) / middle * 100.0
        if width_pct < 5.0:
            out["bollinger"] = "squeeze"
        elif close is not None:
            if close > upper:
                out["bollinger"] = "breakout_upper"
            elif close < lower:
                out["bollinger"] = "breakout_lower"
            else:
                out["bollinger"] = "inside"

    e20 = snapshot.get("ema_20")
    e50 = snapshot.get("ema_50")
    e200 = snapshot.get("ema_200")
    if e20 is not None and e50 is not None and e200 is not None:
        if e20 > e50 > e200:
            out["trend"] = "uptrend"
        elif e20 < e50 < e200:
            out["trend"] = "downtrend"
        else:
            out["trend"] = "mixed"

    return out


__all__ = [
    "BACKEND",
    "TechnicalIndicators",
    "TechnicalAnalyzer",
    "signal_interpretation",
]
