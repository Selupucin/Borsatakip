"""Backtester — geçmiş veriyle öneri motoru doğrulama.

Doküman §5.4 (Backtesting) ve agent kuralları (vectorbt birincil, walk-
forward, look-ahead bias yasak) referans alınarak yazılmıştır.

Tasarım kuralları:
- ``vectorbt`` opsiyonel; yoksa saf pandas/numpy ile çalışan fallback yol
  kullanılır. Bu fallback öneri tabanlı (signal-driven) basit bir
  long-only execution mantığıdır.
- **Look-ahead bias YASAK:** Her gün için sinyal üretirken yalnızca **o
  güne kadar olan** veri kullanılır (``prices.loc[:date]`` kesimi). Bu
  guard ``_signal_window`` yardımcısında tek noktada uygulanır ve testle
  doğrulanabilir.
- Para ``Decimal``; equity_curve içindeki değerler ``Decimal``.
- ``BacktestRunResult`` DB modeli ``BacktestResult`` ile aynı isim
  olmasın diye ``RunResult`` ekiyle yazıldı.
- ``walk_forward`` overfitting tespiti için çoklu pencere döndürür.
- ``compute_metrics`` saf static — equity_curve + trades ile çalışır,
  bağımlılık yok (testte direkt kullanılır).

Komisyon: ``commission_pct`` her işlem (alış + satış) için ayrı uygulanır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd
from loguru import logger
from sqlalchemy import and_, select


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class BacktestConfig:
    """Tek backtest çalışmasının konfigürasyonu.

    Attributes
    ----------
    strategy_name:
        Veritabanına yazılacak strateji adı (örn. ``"short_term_default"``).
    timeframe:
        ``'short'`` | ``'mid'`` | ``'long'`` — hangi recommender kullanılacak.
    start_date / end_date:
        Backtest dönemi (inclusive).
    initial_capital:
        Başlangıç sermayesi (Decimal). Varsayılan 100.000.
    weights:
        Recommender ağırlık override'ı. ``None`` ise varsayılan ağırlıklar.
    commission_pct:
        İşlem başına komisyon yüzdesi (0.002 = %0.2).
    risk_threshold:
        Risk skoru bunun üstündeki önerilerde işlem yapılmaz (otomasyon
        eşiği simülasyonu).
    """

    strategy_name: str
    timeframe: str
    start_date: date
    end_date: date
    initial_capital: Decimal = Decimal("100000")
    weights: Optional[dict] = None
    commission_pct: float = 0.002
    risk_threshold: float = 50.0


@dataclass
class BacktestRunResult:
    """Tek backtest çalışmasının sonucu.

    DB modelindeki ``BacktestResult`` ile karışmaması için ``RunResult``.

    Attributes
    ----------
    equity_curve:
        ``[(date, equity_decimal), ...]`` zaman serisi.
    trades:
        Her işlem için dict: ``{date, instrument_id, action, price, qty,
        commission, pnl}``.
    params:
        DB ``backtest_results.params`` JSONB kolonuna serileştirilecek
        ayarlar (ağırlıklar + komisyon + risk eşiği).
    """

    strategy_name: str
    timeframe: str
    start_date: date
    end_date: date
    total_return_pct: float
    sharpe_ratio: float
    max_drawdown_pct: float
    win_rate: float
    total_trades: int
    avg_hold_days: float
    final_capital: Decimal
    equity_curve: list[tuple[date, Decimal]] = field(default_factory=list)
    trades: list[dict] = field(default_factory=list)
    params: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Yardımcı sabitler
# ---------------------------------------------------------------------------

#: Yıllık trading günü (Sharpe annualization).
TRADING_DAYS_PER_YEAR = 252


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class Backtester:
    """Backtest motoru — vectorbt opsiyonel, pandas fallback varsayılan.

    Parameters
    ----------
    session_factory:
        ``PriceHistory`` panelini çekmek için kullanılır. None geçilirse
        DB tabanlı metodlar (``run``, ``walk_forward``, ``_build_price_panel``)
        çalışmaz — yalnızca ``compute_metrics`` static yardımcı kullanılır.
    """

    def __init__(
        self, session_factory: Optional[Callable[[], object]] = None
    ) -> None:
        self.session_factory = session_factory
        try:  # pragma: no cover - import side-effect
            import vectorbt as vbt  # type: ignore[import-not-found]
            self._vbt = vbt
        except ImportError:
            self._vbt = None
            logger.debug(
                "Backtester: vectorbt yok; pandas/numpy fallback kullanılacak."
            )

    # ------------------------------------------------------------- panel

    def _build_price_panel(
        self,
        instrument_ids: list[int],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        """``(timestamp × instrument_id)`` verified_close panelini DB'den çeker.

        Returns
        -------
        pd.DataFrame
            ``index=DatetimeIndex (UTC, asc)``, ``columns=instrument_id``.
            Boş ise empty DataFrame.
        """

        if self.session_factory is None:
            raise RuntimeError(
                "Backtester._build_price_panel: session_factory=None "
                "— DB erişimi yapılamaz."
            )

        from app.db.models import PriceHistory  # noqa: WPS433

        start_dt = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(end, datetime.max.time(), tzinfo=timezone.utc)

        session = self.session_factory()
        try:
            stmt = select(
                PriceHistory.instrument_id,
                PriceHistory.timestamp,
                PriceHistory.verified_close,
            ).where(
                and_(
                    PriceHistory.instrument_id.in_(instrument_ids),
                    PriceHistory.timestamp >= start_dt,
                    PriceHistory.timestamp <= end_dt,
                    PriceHistory.verified_close.isnot(None),
                )
            )
            rows = session.execute(stmt).all()
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(
            rows, columns=["instrument_id", "timestamp", "verified_close"]
        )
        df["verified_close"] = df["verified_close"].astype("float64")
        panel = df.pivot_table(
            index="timestamp",
            columns="instrument_id",
            values="verified_close",
            aggfunc="last",
        ).sort_index()
        return panel

    # ------------------------------------------------------------- sinyaller

    @staticmethod
    def _signal_window(
        prices: pd.DataFrame, as_of: pd.Timestamp
    ) -> pd.DataFrame:
        """**Look-ahead bias guard** — yalnızca ``as_of``'a kadar olan dilim.

        Bu metod backtest sırasında sinyal üretiminin TEK kapısıdır. Her
        sinyal hesabı buradan geçer; gelecekteki bir tarihteki veriye
        erişim mümkün değildir. Test edilebilir saf fonksiyon.

        Parameters
        ----------
        prices:
            Tüm dönem fiyat paneli (timestamp index).
        as_of:
            Hangi tarihe kadar geçmiş bilgi kullanılabilir (inclusive).

        Returns
        -------
        pd.DataFrame
            ``prices`` panelinin ``index <= as_of`` dilimi.
        """

        if prices is None or prices.empty:
            return prices
        return prices.loc[:as_of]

    def _generate_signals(
        self,
        prices: pd.DataFrame,
        config: BacktestConfig,
        signal_fn: Optional[Callable[[pd.DataFrame, pd.Timestamp, int], str]] = None,
    ) -> pd.DataFrame:
        """Her tarih × enstrüman için ``BUY/HOLD/SELL`` sinyal matrisi.

        Parameters
        ----------
        prices:
            ``_build_price_panel`` çıktısı.
        config:
            Backtest konfigürasyonu (timeframe, weights, vs.).
        signal_fn:
            Opsiyonel custom callable ``(window_df, as_of, instrument_id)
            -> 'BUY'|'HOLD'|'SELL'``. Verilmezse ``_default_signal`` ile
            basit teknik heuristik (kısa MA çaprazlaması) kullanılır.

        **Kritik:** Her ``as_of`` için yalnızca ``_signal_window(prices,
        as_of)`` dilimi sinyal fonksiyonuna geçirilir — look-ahead yok.
        """

        if prices.empty:
            return pd.DataFrame()

        fn = signal_fn or self._default_signal
        # Vectorize edemediğimiz (her gün state lazım) — gün döngüsü, ama
        # her enstrüman için inner loop yerine her gün tek seferde tüm
        # enstrümanları çağırırız.
        signals = pd.DataFrame(
            "HOLD", index=prices.index, columns=prices.columns, dtype=object
        )
        for as_of in prices.index:
            window = self._signal_window(prices, as_of)
            if window.shape[0] < 2:
                continue  # En az 2 gün veri olmadan sinyal üretmiyoruz.
            for instrument_id in prices.columns:
                series = window[instrument_id].dropna()
                if series.shape[0] < 2:
                    continue
                signals.loc[as_of, instrument_id] = fn(
                    window, as_of, instrument_id
                )
        return signals

    @staticmethod
    def _default_signal(
        window: pd.DataFrame, as_of: pd.Timestamp, instrument_id: int
    ) -> str:
        """Saf-pandas default sinyal: SMA(5) > SMA(20) → BUY, ters → SELL.

        Bu fallback recommender'ın tam mantığını çalıştırmaz; production
        kullanım için ``signal_fn=`` ile ShortTermRecommender vb. enjekte
        edilir. Bu sayede ağır bağımlılıklar (talib + transformers)
        olmadan backtester'ın çekirdek mantığı test edilebilir.
        """

        series = window[instrument_id].dropna()
        if series.shape[0] < 20:
            return "HOLD"
        sma_short = series.tail(5).mean()
        sma_long = series.tail(20).mean()
        if sma_short > sma_long * 1.005:  # %0.5 buffer
            return "BUY"
        if sma_short < sma_long * 0.995:
            return "SELL"
        return "HOLD"

    # ------------------------------------------------------------- run

    def run(
        self,
        config: BacktestConfig,
        instrument_ids: list[int],
        signal_fn: Optional[Callable[..., str]] = None,
    ) -> BacktestRunResult:
        """Backtest çalıştırır — equity curve + metrikler döndürür.

        Basit execution modeli:
        - Tek pozisyon / enstrüman; sermaye eşit dağıtılır.
        - BUY sinyali → varsa nakitle aç (komisyon düş).
        - SELL sinyali → mevcut pozisyonu tamamen kapat (komisyon düş).
        - HOLD → değişiklik yok.
        - Hisse fiyatı NaN ise o gün işlem yapılmaz; pozisyon değer
          önceki günden taşınır.
        """

        prices = self._build_price_panel(
            instrument_ids, config.start_date, config.end_date
        )
        if prices.empty:
            logger.warning(
                "Backtester.run: {} enstrüman için {}..{} arası veri yok.",
                instrument_ids, config.start_date, config.end_date,
            )
            return self._empty_result(config)

        signals = self._generate_signals(prices, config, signal_fn=signal_fn)
        return self._simulate(prices, signals, config)

    def _simulate(
        self,
        prices: pd.DataFrame,
        signals: pd.DataFrame,
        config: BacktestConfig,
    ) -> BacktestRunResult:
        """Saf-pandas signal-driven execution."""

        commission = float(config.commission_pct)
        initial = float(config.initial_capital)
        cash = initial
        # Her enstrüman için: {'qty': float, 'avg_price': float, 'open_date': date}
        positions: dict[Any, dict] = {}
        trades: list[dict] = []
        equity_curve: list[tuple[date, Decimal]] = []

        n_instruments = max(1, len(prices.columns))
        per_slot = initial / n_instruments  # Eşit slot tahsisi (basit).

        for as_of in prices.index:
            day_prices = prices.loc[as_of]
            day_signals = signals.loc[as_of] if as_of in signals.index else None

            # SELL'leri önce işle — nakdi serbest bırakır.
            if day_signals is not None:
                for inst_id in prices.columns:
                    sig = str(day_signals.get(inst_id, "HOLD"))
                    price = day_prices.get(inst_id, np.nan)
                    if pd.isna(price):
                        continue
                    if sig == "SELL" and inst_id in positions:
                        pos = positions.pop(inst_id)
                        gross = pos["qty"] * float(price)
                        comm = gross * commission
                        cash += gross - comm
                        pnl = gross - comm - (pos["qty"] * pos["avg_price"])
                        trades.append(
                            {
                                "date": as_of.date()
                                if hasattr(as_of, "date") else as_of,
                                "instrument_id": int(inst_id),
                                "action": "SELL",
                                "price": float(price),
                                "qty": pos["qty"],
                                "commission": comm,
                                "pnl": pnl,
                                "hold_days": (
                                    (as_of.date() - pos["open_date"]).days
                                    if hasattr(as_of, "date")
                                    else 0
                                ),
                            }
                        )

                # BUY'lar.
                for inst_id in prices.columns:
                    sig = str(day_signals.get(inst_id, "HOLD"))
                    price = day_prices.get(inst_id, np.nan)
                    if pd.isna(price) or float(price) <= 0:
                        continue
                    if sig == "BUY" and inst_id not in positions:
                        # Slot kadar nakit harca (cash sınırlı).
                        allocation = min(per_slot, cash)
                        if allocation <= 0:
                            continue
                        comm = allocation * commission
                        invest = allocation - comm
                        qty = invest / float(price)
                        if qty <= 0:
                            continue
                        cash -= allocation
                        positions[inst_id] = {
                            "qty": qty,
                            "avg_price": float(price),
                            "open_date": as_of.date()
                            if hasattr(as_of, "date") else as_of,
                        }
                        trades.append(
                            {
                                "date": as_of.date()
                                if hasattr(as_of, "date") else as_of,
                                "instrument_id": int(inst_id),
                                "action": "BUY",
                                "price": float(price),
                                "qty": qty,
                                "commission": comm,
                                "pnl": 0.0,
                                "hold_days": 0,
                            }
                        )

            # Equity = cash + tüm pozisyonların güncel değeri.
            pos_value = 0.0
            for inst_id, pos in positions.items():
                price = day_prices.get(inst_id, np.nan)
                if pd.isna(price):
                    # Fiyat yoksa son maliyetten değerle.
                    pos_value += pos["qty"] * pos["avg_price"]
                else:
                    pos_value += pos["qty"] * float(price)

            equity = cash + pos_value
            equity_curve.append(
                (
                    as_of.date() if hasattr(as_of, "date") else as_of,
                    Decimal(str(equity)),
                )
            )

        final = equity_curve[-1][1] if equity_curve else Decimal(str(initial))
        metrics = self.compute_metrics(equity_curve, trades)

        params = {
            "weights": config.weights or {},
            "commission_pct": config.commission_pct,
            "risk_threshold": config.risk_threshold,
            "n_instruments": n_instruments,
        }

        return BacktestRunResult(
            strategy_name=config.strategy_name,
            timeframe=config.timeframe,
            start_date=config.start_date,
            end_date=config.end_date,
            total_return_pct=metrics["total_return_pct"],
            sharpe_ratio=metrics["sharpe_ratio"],
            max_drawdown_pct=metrics["max_drawdown_pct"],
            win_rate=metrics["win_rate"],
            total_trades=int(metrics["total_trades"]),
            avg_hold_days=metrics["avg_hold_days"],
            final_capital=final,
            equity_curve=equity_curve,
            trades=trades,
            params=params,
        )

    @staticmethod
    def _empty_result(config: BacktestConfig) -> BacktestRunResult:
        return BacktestRunResult(
            strategy_name=config.strategy_name,
            timeframe=config.timeframe,
            start_date=config.start_date,
            end_date=config.end_date,
            total_return_pct=0.0,
            sharpe_ratio=0.0,
            max_drawdown_pct=0.0,
            win_rate=0.0,
            total_trades=0,
            avg_hold_days=0.0,
            final_capital=config.initial_capital,
            equity_curve=[],
            trades=[],
            params={
                "weights": config.weights or {},
                "commission_pct": config.commission_pct,
                "risk_threshold": config.risk_threshold,
            },
        )

    # ------------------------------------------------------------- walk-forward

    def walk_forward(
        self,
        config: BacktestConfig,
        instrument_ids: list[int],
        window_days: int = 90,
        step_days: int = 30,
        signal_fn: Optional[Callable[..., str]] = None,
    ) -> list[BacktestRunResult]:
        """Walk-forward analizi — çoklu pencere koşar.

        Overfitting tespiti için: ``start_date`` → ``end_date`` aralığı
        ``window_days`` boyutunda ve ``step_days`` adımıyla kayan
        pencerelere bölünür. Her pencere için ayrı backtest çalıştırılır.

        Sonuç listesindeki varyans yüksekse strateji overfit demektir.
        """

        if window_days <= 0 or step_days <= 0:
            raise ValueError("window_days ve step_days > 0 olmalı")

        results: list[BacktestRunResult] = []
        cursor = config.start_date
        while cursor + timedelta(days=window_days) <= config.end_date:
            window_end = cursor + timedelta(days=window_days)
            window_cfg = BacktestConfig(
                strategy_name=f"{config.strategy_name}_wf_{cursor.isoformat()}",
                timeframe=config.timeframe,
                start_date=cursor,
                end_date=window_end,
                initial_capital=config.initial_capital,
                weights=config.weights,
                commission_pct=config.commission_pct,
                risk_threshold=config.risk_threshold,
            )
            try:
                result = self.run(window_cfg, instrument_ids, signal_fn=signal_fn)
                results.append(result)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "walk_forward penceresi {}..{} hata verdi: {}",
                    cursor, window_end, exc,
                )
            cursor = cursor + timedelta(days=step_days)
        return results

    # ------------------------------------------------------------- persist

    def save_result(self, session, result: BacktestRunResult) -> int:
        """``backtest_results`` tablosuna kayıt ekler — yeni satırın id'sini döner.

        Caller commit'i kendi sorumluluğundadır. Flush ile id alınır.
        """

        from app.db.models import BacktestResult  # noqa: WPS433

        row = BacktestResult(
            strategy_name=result.strategy_name,
            timeframe=result.timeframe,
            start_date=result.start_date,
            end_date=result.end_date,
            total_return=float(result.total_return_pct),
            sharpe_ratio=float(result.sharpe_ratio),
            max_drawdown=float(result.max_drawdown_pct),
            win_rate=float(result.win_rate),
            avg_hold_days=float(result.avg_hold_days),
            params=result.params,
        )
        session.add(row)
        flush = getattr(session, "flush", None)
        if callable(flush):
            flush()
        return int(row.id) if getattr(row, "id", None) is not None else 0

    # ------------------------------------------------------------- metrikler

    @staticmethod
    def compute_metrics(
        equity_curve: list[tuple[date, Decimal]],
        trades: list[dict],
    ) -> dict:
        """Equity curve + işlem listesinden metrik özet üretir.

        Returns
        -------
        dict
            ``total_return_pct``, ``sharpe_ratio``, ``max_drawdown_pct``,
            ``win_rate``, ``total_trades``, ``avg_hold_days``.

        Hesaplar:
        - **total_return_pct** = ``(final / initial - 1) × 100``
        - **sharpe_ratio** = ``mean(daily_returns) / std × sqrt(252)``
          (risk-free=0). std=0 ise 0 döner.
        - **max_drawdown_pct** = en büyük peak-to-trough düşüş.
        - **win_rate** = ``SELL`` işlemlerinin pnl>0 olan oranı (%).
        - **avg_hold_days** = kapalı pozisyonların ``hold_days`` ortalaması.
        """

        if not equity_curve:
            return {
                "total_return_pct": 0.0,
                "sharpe_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "win_rate": 0.0,
                "total_trades": 0,
                "avg_hold_days": 0.0,
            }

        equities = np.array(
            [float(e[1]) for e in equity_curve], dtype="float64"
        )
        initial = equities[0]
        final = equities[-1]
        total_return = ((final / initial) - 1.0) * 100.0 if initial > 0 else 0.0

        # Günlük getiriler.
        if equities.shape[0] >= 2:
            returns = np.diff(equities) / equities[:-1]
            returns = returns[np.isfinite(returns)]
            if returns.size and float(returns.std(ddof=0)) > 0:
                sharpe = (
                    float(returns.mean()) / float(returns.std(ddof=0))
                ) * np.sqrt(TRADING_DAYS_PER_YEAR)
            else:
                sharpe = 0.0
        else:
            sharpe = 0.0

        # Max drawdown (peak-to-trough).
        peak = np.maximum.accumulate(equities)
        drawdowns = (equities - peak) / np.where(peak > 0, peak, 1.0)
        max_dd = float(drawdowns.min()) * 100.0 if drawdowns.size else 0.0
        max_dd = abs(max_dd)  # Pozitif yüzde olarak rapor.

        # Win rate — SELL işlemlerinin pnl'sine bakar.
        sell_trades = [t for t in trades if t.get("action") == "SELL"]
        if sell_trades:
            winners = sum(1 for t in sell_trades if float(t.get("pnl", 0.0)) > 0)
            win_rate = (winners / len(sell_trades)) * 100.0
            holds = [
                float(t.get("hold_days", 0.0)) for t in sell_trades
                if t.get("hold_days") is not None
            ]
            avg_hold = float(np.mean(holds)) if holds else 0.0
        else:
            win_rate = 0.0
            avg_hold = 0.0

        return {
            "total_return_pct": float(total_return),
            "sharpe_ratio": float(sharpe),
            "max_drawdown_pct": float(max_dd),
            "win_rate": float(win_rate),
            "total_trades": len(trades),
            "avg_hold_days": float(avg_hold),
        }


__all__ = [
    "TRADING_DAYS_PER_YEAR",
    "BacktestConfig",
    "BacktestRunResult",
    "Backtester",
]
