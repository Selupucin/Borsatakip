"""``AlertEngine`` birim testleri.

Doğrulanan davranışlar:
1. ``check_price_alerts`` price_above tetiklenmesi
2. ``check_price_alerts`` price_below tetiklenmesi
3. ``check_pct_change_alerts`` abs eşik
4. ``check_signal_alerts`` signal: prefix eşleşmesi
5. Tetiklenen alarmın ``triggered_at`` güncellenir, ``is_active`` TRUE kalır
6. ``evaluate`` çoklu tip tek seferde değerlendirir
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from app.alerts.engine import (
    ALERT_PCT_CHANGE,
    ALERT_PRICE_ABOVE,
    ALERT_PRICE_BELOW,
    ALERT_SIGNAL_PREFIX,
    AlertEngine,
    TriggeredAlert,
)
from app.db.models import Alert, Instrument


@pytest.fixture
def session_factory(sync_engine):
    """``AlertEngine`` için sync session_factory döndürür.

    Engine'in connection'ı her çağrıda yeni Session açar — gerçek dünyadaki
    ``sessionmaker`` davranışı.
    """
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, expire_on_commit=False)
    return SessionLocal


@pytest.fixture
def instrument(sync_engine):
    """Test için bir instrument insert et."""
    from sqlalchemy.orm import sessionmaker

    SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as s:
        instr = Instrument(ticker="AAPL", name="Apple")
        s.add(instr)
        s.commit()
        s.refresh(instr)
        return instr.id


class TestPriceAlerts:
    @pytest.mark.asyncio
    async def test_price_above_triggers_when_price_exceeds_threshold(
        self, session_factory, instrument
    ):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PRICE_ABOVE,
                    threshold=100.0,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory)
        triggered = await engine.check_price_alerts(instrument, Decimal("105"))
        assert len(triggered) == 1
        assert triggered[0].alert_type == ALERT_PRICE_ABOVE
        assert isinstance(triggered[0], TriggeredAlert)

    @pytest.mark.asyncio
    async def test_price_above_does_not_trigger_below_threshold(
        self, session_factory, instrument
    ):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PRICE_ABOVE,
                    threshold=200.0,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory)
        triggered = await engine.check_price_alerts(instrument, Decimal("150"))
        assert triggered == []

    @pytest.mark.asyncio
    async def test_price_below_triggers_when_price_drops(
        self, session_factory, instrument
    ):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PRICE_BELOW,
                    threshold=50.0,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory)
        triggered = await engine.check_price_alerts(instrument, Decimal("45"))
        assert len(triggered) == 1
        assert triggered[0].alert_type == ALERT_PRICE_BELOW


class TestPctChangeAlerts:
    @pytest.mark.asyncio
    async def test_pct_change_triggers_when_abs_exceeds_threshold(
        self, session_factory, instrument
    ):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PCT_CHANGE,
                    threshold=3.0,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory)
        # +5% tetikler
        triggered = await engine.check_pct_change_alerts(instrument, Decimal("5"))
        assert len(triggered) == 1
        # -5% de tetikler (abs)
        triggered2 = await engine.check_pct_change_alerts(instrument, Decimal("-5"))
        assert len(triggered2) == 1

    @pytest.mark.asyncio
    async def test_pct_change_does_not_trigger_below_threshold(
        self, session_factory, instrument
    ):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PCT_CHANGE,
                    threshold=10.0,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory)
        triggered = await engine.check_pct_change_alerts(instrument, Decimal("3"))
        assert triggered == []


class TestSignalAlerts:
    @pytest.mark.asyncio
    async def test_signal_alert_matches_on_prefixed_type(
        self, session_factory, instrument
    ):
        """DB'de ``signal:rsi_oversold`` aktifse RSI oversold sinyalinde tetiklenir."""
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=f"{ALERT_SIGNAL_PREFIX}rsi_oversold",
                    threshold=None,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory)
        triggered = await engine.check_signal_alerts(instrument, "rsi_oversold")
        assert len(triggered) == 1
        assert triggered[0].alert_type == f"{ALERT_SIGNAL_PREFIX}rsi_oversold"

    @pytest.mark.asyncio
    async def test_signal_alert_does_not_match_different_signal(
        self, session_factory, instrument
    ):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=f"{ALERT_SIGNAL_PREFIX}rsi_oversold",
                    threshold=None,
                    is_active=True,
                )
            )
            s.commit()
        engine = AlertEngine(session_factory)
        triggered = await engine.check_signal_alerts(
            instrument, "macd_bullish_cross"
        )
        assert triggered == []


class TestTriggeredStateUpdate:
    @pytest.mark.asyncio
    async def test_triggered_at_is_updated_and_is_active_stays_true(
        self, session_factory, instrument
    ):
        """Tetiklenen alarm ``triggered_at`` set edilir; ``is_active`` AÇIK kalır."""
        with session_factory() as s:
            a = Alert(
                instrument_id=instrument,
                alert_type=ALERT_PRICE_ABOVE,
                threshold=100.0,
                is_active=True,
            )
            s.add(a)
            s.commit()
            alert_id = a.id

        engine = AlertEngine(session_factory)
        triggered = await engine.check_price_alerts(instrument, Decimal("150"))
        assert len(triggered) == 1

        # DB'den oku
        with session_factory() as s:
            row = s.get(Alert, alert_id)
            assert row.triggered_at is not None
            assert row.is_active is True  # AÇIK KALMALI


class TestEvaluate:
    @pytest.mark.asyncio
    async def test_evaluate_runs_all_alert_kinds(self, session_factory, instrument):
        """``evaluate`` price + pct + signal tek seferde değerlendirir."""
        with session_factory() as s:
            s.add_all(
                [
                    Alert(
                        instrument_id=instrument,
                        alert_type=ALERT_PRICE_ABOVE,
                        threshold=100.0,
                        is_active=True,
                    ),
                    Alert(
                        instrument_id=instrument,
                        alert_type=ALERT_PCT_CHANGE,
                        threshold=2.0,
                        is_active=True,
                    ),
                    Alert(
                        instrument_id=instrument,
                        alert_type=f"{ALERT_SIGNAL_PREFIX}rsi_oversold",
                        threshold=None,
                        is_active=True,
                    ),
                ]
            )
            s.commit()

        engine = AlertEngine(session_factory)
        triggered = await engine.evaluate(
            instrument,
            {
                "price": Decimal("120"),
                "pct_change": Decimal("3"),
                "signals": ["rsi_oversold"],
            },
        )
        # 3 farklı tipte alarm tetiklenir
        types = {t.alert_type for t in triggered}
        assert ALERT_PRICE_ABOVE in types
        assert ALERT_PCT_CHANGE in types
        assert f"{ALERT_SIGNAL_PREFIX}rsi_oversold" in types

    @pytest.mark.asyncio
    async def test_evaluate_calls_notifier_for_each_triggered(
        self, session_factory, instrument
    ):
        """Notifier.notify her tetiklenen alarm için tek tek çağrılır."""
        from unittest.mock import MagicMock

        notifier = MagicMock()

        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PRICE_ABOVE,
                    threshold=100.0,
                    is_active=True,
                )
            )
            s.commit()

        engine = AlertEngine(session_factory, notifier=notifier)
        await engine.check_price_alerts(instrument, Decimal("105"))
        notifier.notify.assert_called_once()

    @pytest.mark.asyncio
    async def test_inactive_alerts_not_triggered(self, session_factory, instrument):
        with session_factory() as s:
            s.add(
                Alert(
                    instrument_id=instrument,
                    alert_type=ALERT_PRICE_ABOVE,
                    threshold=100.0,
                    is_active=False,
                )
            )
            s.commit()
        engine = AlertEngine(session_factory)
        triggered = await engine.check_price_alerts(instrument, Decimal("200"))
        assert triggered == []
