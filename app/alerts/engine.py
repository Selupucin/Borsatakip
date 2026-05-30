"""Alarm motoru.

Doküman §11 Faz 1 (alarm sistemi) ve §9 ``alerts`` tablosu referans alınmıştır.

Alarm tipleri:
    - ``price_above``  : Fiyat eşiği aşarsa tetiklenir.
    - ``price_below``  : Fiyat eşiğin altına düşerse tetiklenir.
    - ``pct_change``   : Günlük yüzde değişim eşiği aşarsa (|pct| >= threshold).
    - ``signal``       : Belirli bir teknik sinyal oluşursa
      (``threshold`` kolonu kullanılmıyor — ``alert_type`` ``"signal:<kind>"``
      formunda saklanır, örn. ``"signal:rsi_oversold"``).

Tetiklenen alarm: ``alerts.triggered_at = now()`` güncellenir; ``is_active``
False yapılmaz — kullanıcı tekrar tetiklenmesini isteyebilir.

NOT: Bu modül DB session'ı için ``session_factory`` çağrılabilir nesnesini
bekler (sync). Async kullanım gerekirse ileride genişletilir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Iterable, Optional

from loguru import logger
from sqlalchemy import select

from app.db.models import Alert

# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

ALERT_PRICE_ABOVE = "price_above"
ALERT_PRICE_BELOW = "price_below"
ALERT_PCT_CHANGE = "pct_change"
ALERT_SIGNAL_PREFIX = "signal:"

# Sinyal tipleri (signal_interpretation çıktısıyla aynı vocabulary)
SIGNAL_RSI_OVERSOLD = "rsi_oversold"
SIGNAL_RSI_OVERBOUGHT = "rsi_overbought"
SIGNAL_MACD_BULLISH_CROSS = "macd_bullish_cross"
SIGNAL_MACD_BEARISH_CROSS = "macd_bearish_cross"
SIGNAL_BB_SQUEEZE = "bb_squeeze"
SIGNAL_BB_BREAKOUT_UPPER = "bb_breakout_upper"
SIGNAL_BB_BREAKOUT_LOWER = "bb_breakout_lower"


# ---------------------------------------------------------------------------
# Veri sınıfı
# ---------------------------------------------------------------------------


@dataclass
class TriggeredAlert:
    """Tetiklenen alarmın UI/bildirim katmanına aktarılan temsili."""

    alert_id: int
    instrument_id: int
    alert_type: str  # 'price_above', 'price_below', 'pct_change', 'signal:<kind>'
    triggered_value: Decimal
    threshold: Decimal
    message: str
    triggered_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------


class AlertEngine:
    """Aktif alarmları DB'den okur, eşleşenleri tetikler ve notifier'a iletir.

    Parameters
    ----------
    session_factory:
        Çağrıldığında bir SQLAlchemy ``Session`` döndüren callable
        (``sessionmaker`` veya ``contextmanager`` döndüren bir fabrika).
        Engine, her çağrıda kısa süreli yeni session açar.
    notifier:
        ``Notifier`` benzeri nesne; her tetiklenen alarm için ``notify(...)``
        çağrısı yapılır. ``None`` ise sadece loglanır.
    """

    def __init__(
        self,
        session_factory: Callable[[], "object"],
        notifier: Optional["object"] = None,
    ) -> None:
        self._session_factory = session_factory
        self._notifier = notifier

    # ---------------------------------------------------------------- public

    async def check_price_alerts(
        self, instrument_id: int, current_price: Decimal
    ) -> list[TriggeredAlert]:
        """``price_above`` / ``price_below`` alarmlarını değerlendirir."""

        price = Decimal(str(current_price))
        triggered: list[TriggeredAlert] = []

        with self._open_session() as session:
            alerts = self._fetch_active_alerts(
                session,
                instrument_id,
                {ALERT_PRICE_ABOVE, ALERT_PRICE_BELOW},
            )
            for alert in alerts:
                threshold = Decimal(str(alert.threshold)) if alert.threshold is not None else None
                if threshold is None:
                    continue

                hit = (
                    alert.alert_type == ALERT_PRICE_ABOVE and price >= threshold
                ) or (
                    alert.alert_type == ALERT_PRICE_BELOW and price <= threshold
                )
                if not hit:
                    continue

                message = self._format_price_message(
                    alert.alert_type, instrument_id, price, threshold
                )
                triggered.append(
                    self._mark_triggered(
                        session, alert, price, threshold, message
                    )
                )
            session.commit()

        self._dispatch(triggered)
        return triggered

    async def check_pct_change_alerts(
        self, instrument_id: int, pct_change: Decimal
    ) -> list[TriggeredAlert]:
        """``pct_change`` alarmlarını değerlendirir.

        Tetikleme koşulu: ``abs(pct_change) >= threshold``. ``pct_change`` yüzde
        cinsinden (örn. 3.5 = %3.5).
        """

        pct = Decimal(str(pct_change))
        abs_pct = abs(pct)
        triggered: list[TriggeredAlert] = []

        with self._open_session() as session:
            alerts = self._fetch_active_alerts(
                session, instrument_id, {ALERT_PCT_CHANGE}
            )
            for alert in alerts:
                threshold = (
                    Decimal(str(alert.threshold)) if alert.threshold is not None else None
                )
                if threshold is None or abs_pct < threshold:
                    continue
                message = (
                    f"Hisse #{instrument_id}: günlük değişim %{pct} "
                    f"({'+' if pct >= 0 else '-'}{abs_pct}), eşik %{threshold}."
                )
                triggered.append(
                    self._mark_triggered(session, alert, pct, threshold, message)
                )
            session.commit()

        self._dispatch(triggered)
        return triggered

    async def check_signal_alerts(
        self, instrument_id: int, signal_type: str
    ) -> list[TriggeredAlert]:
        """``signal:<kind>`` alarmlarını değerlendirir.

        ``signal_type`` örnek değerler: ``"rsi_oversold"``,
        ``"macd_bullish_cross"``. DB'de ``alert_type`` ``"signal:<kind>"``
        olarak saklanır.
        """

        target = f"{ALERT_SIGNAL_PREFIX}{signal_type}"
        triggered: list[TriggeredAlert] = []

        with self._open_session() as session:
            alerts = self._fetch_active_alerts(session, instrument_id, {target})
            for alert in alerts:
                threshold = (
                    Decimal(str(alert.threshold))
                    if alert.threshold is not None
                    else Decimal("0")
                )
                message = (
                    f"Hisse #{instrument_id}: teknik sinyal tetiklendi — "
                    f"{signal_type}."
                )
                triggered.append(
                    self._mark_triggered(
                        session, alert, Decimal("0"), threshold, message
                    )
                )
            session.commit()

        self._dispatch(triggered)
        return triggered

    async def evaluate(
        self, instrument_id: int, snapshot: dict
    ) -> list[TriggeredAlert]:
        """Tüm alarm tiplerini tek noktadan değerlendirir.

        ``snapshot`` aşağıdaki opsiyonel anahtarları içerebilir:
            ``price`` (Decimal/float) — fiyat alarmları için
            ``pct_change`` (Decimal/float) — yüzde değişim alarmları için
            ``signals`` (Iterable[str]) — tetiklenmiş teknik sinyal isimleri
              (örn. ``["rsi_oversold", "macd_bullish_cross"]``)
        """

        triggered: list[TriggeredAlert] = []

        price = snapshot.get("price")
        if price is not None:
            triggered.extend(
                await self.check_price_alerts(instrument_id, Decimal(str(price)))
            )

        pct = snapshot.get("pct_change")
        if pct is not None:
            triggered.extend(
                await self.check_pct_change_alerts(
                    instrument_id, Decimal(str(pct))
                )
            )

        signals: Iterable[str] = snapshot.get("signals") or ()
        for sig in signals:
            triggered.extend(await self.check_signal_alerts(instrument_id, sig))

        return triggered

    # --------------------------------------------------------------- helpers

    def _open_session(self):
        """``session_factory`` döner; context manager olup olmadığını tespit eder."""

        sess = self._session_factory()
        if hasattr(sess, "__enter__"):
            return sess
        return _SessionContext(sess)

    @staticmethod
    def _fetch_active_alerts(
        session, instrument_id: int, alert_types: set[str]
    ) -> list[Alert]:
        stmt = (
            select(Alert)
            .where(Alert.instrument_id == instrument_id)
            .where(Alert.is_active.is_(True))
            .where(Alert.alert_type.in_(alert_types))
        )
        return list(session.execute(stmt).scalars().all())

    def _mark_triggered(
        self,
        session,
        alert: Alert,
        value: Decimal,
        threshold: Decimal,
        message: str,
    ) -> TriggeredAlert:
        now = datetime.now(tz=timezone.utc)
        alert.triggered_at = now
        # is_active KAPATILMAZ — tekrar tetiklenmeye açık.
        session.add(alert)
        logger.info(
            "Alarm tetiklendi: id={} instrument={} type={} value={} threshold={}",
            alert.id,
            alert.instrument_id,
            alert.alert_type,
            value,
            threshold,
        )
        return TriggeredAlert(
            alert_id=alert.id,
            instrument_id=alert.instrument_id,
            alert_type=alert.alert_type,
            triggered_value=value,
            threshold=threshold,
            message=message,
            triggered_at=now,
        )

    def _dispatch(self, triggered: list[TriggeredAlert]) -> None:
        if not triggered or self._notifier is None:
            return
        for ta in triggered:
            try:
                self._notifier.notify(ta)
            except Exception as exc:  # noqa: BLE001 — notifier hatası alarmı yutmasın
                logger.exception(
                    "Notifier.notify hata verdi (alert_id={}): {}", ta.alert_id, exc
                )

    @staticmethod
    def _format_price_message(
        alert_type: str,
        instrument_id: int,
        price: Decimal,
        threshold: Decimal,
    ) -> str:
        if alert_type == ALERT_PRICE_ABOVE:
            return (
                f"Hisse #{instrument_id}: fiyat {price} eşik {threshold} "
                f"seviyesini YUKARI kırdı."
            )
        return (
            f"Hisse #{instrument_id}: fiyat {price} eşik {threshold} "
            f"seviyesinin ALTINA indi."
        )


# ---------------------------------------------------------------------------
# Çıplak session destekleyici context wrapper
# ---------------------------------------------------------------------------


class _SessionContext:
    """``session_factory`` çağrılabilir ama context manager değilse sarmalar."""

    def __init__(self, session) -> None:
        self._session = session

    def __enter__(self):
        return self._session

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            self._session.close()
        except Exception:  # noqa: BLE001
            pass


__all__ = [
    "ALERT_PRICE_ABOVE",
    "ALERT_PRICE_BELOW",
    "ALERT_PCT_CHANGE",
    "ALERT_SIGNAL_PREFIX",
    "SIGNAL_RSI_OVERSOLD",
    "SIGNAL_RSI_OVERBOUGHT",
    "SIGNAL_MACD_BULLISH_CROSS",
    "SIGNAL_MACD_BEARISH_CROSS",
    "SIGNAL_BB_SQUEEZE",
    "SIGNAL_BB_BREAKOUT_UPPER",
    "SIGNAL_BB_BREAKOUT_LOWER",
    "TriggeredAlert",
    "AlertEngine",
]
