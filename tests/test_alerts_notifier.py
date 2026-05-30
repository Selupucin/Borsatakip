"""``Notifier`` birim testleri.

Doğrulanan davranışlar:
1. PySide6 yoksa stub Signal kullanılır — ``notify()`` çökmez.
2. ``notify()`` ``loguru.warning`` çağırır.
3. ``notify()`` ``alert_triggered`` Qt sinyali emit eder (slot mock dinler).
4. ``enable_system_notifications=False`` -> sistem bildirimi çağrılmaz.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.alerts.engine import TriggeredAlert
from app.alerts.notifier import Notifier


@pytest.fixture
def sample_triggered():
    return TriggeredAlert(
        alert_id=1,
        instrument_id=42,
        alert_type="price_above",
        triggered_value=Decimal("105.5"),
        threshold=Decimal("100"),
        message="TEST alarmı",
        triggered_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


class TestNotifierBasics:
    def test_notify_does_not_raise_with_stub_signal(self, sample_triggered):
        """PySide6 yokken Notifier hatasız çalışır (stub Signal)."""
        notifier = Notifier(enable_system_notifications=False)
        # Hata fırlamamalı
        notifier.notify(sample_triggered)

    def test_notify_calls_loguru_warning(self, sample_triggered):
        """``logger.warning`` çağrılır."""
        notifier = Notifier(enable_system_notifications=False)
        with patch("app.alerts.notifier.logger") as mock_logger:
            notifier.notify(sample_triggered)
            mock_logger.warning.assert_called_once()
            # Mesajda "ALARM" geçmeli
            args, _ = mock_logger.warning.call_args
            assert "ALARM" in args[0]

    def test_notify_emits_alert_triggered_signal(self, sample_triggered):
        """``alert_triggered.emit(payload)`` çağrılır; mock slot dinler."""
        notifier = Notifier(enable_system_notifications=False)
        received = []
        notifier.alert_triggered.connect(lambda payload: received.append(payload))
        notifier.notify(sample_triggered)
        assert len(received) == 1
        payload = received[0]
        assert isinstance(payload, dict)
        assert payload["alert_id"] == 1
        assert payload["instrument_id"] == 42
        assert payload["alert_type"] == "price_above"
        assert payload["message"] == "TEST alarmı"


class TestNotifierSystemNotifications:
    def test_system_notification_disabled_when_flag_false(self, sample_triggered):
        """``enable_system_notifications=False`` -> sistem notify çağrılmaz."""
        with patch("app.alerts.notifier._SYSTEM_NOTIFY") as mock_sys:
            notifier = Notifier(enable_system_notifications=False)
            notifier.notify(sample_triggered)
            mock_sys.assert_not_called()

    def test_system_notification_disabled_when_backend_unavailable(
        self, sample_triggered
    ):
        """``_SYSTEM_NOTIFY=None`` ise flag=True olsa bile çağrılmaz."""
        with patch("app.alerts.notifier._SYSTEM_NOTIFY", None):
            notifier = Notifier(enable_system_notifications=True)
            # Hata olmamalı
            notifier.notify(sample_triggered)

    def test_system_notification_called_when_enabled_and_backend_present(
        self, sample_triggered
    ):
        """Flag=True + backend var -> _SYSTEM_NOTIFY çağrılır."""
        fake_notify = MagicMock()
        with patch("app.alerts.notifier._SYSTEM_NOTIFY", fake_notify):
            notifier = Notifier(enable_system_notifications=True)
            notifier.notify(sample_triggered)
            fake_notify.assert_called_once()
            title, body = fake_notify.call_args[0]
            assert "Borsa Bot" in title
            assert "TEST alarmı" in body


class TestNotifierPayloadConversion:
    def test_to_payload_handles_dataclass(self, sample_triggered):
        """TriggeredAlert (dataclass) -> dict'e dönüştürülür."""
        payload = Notifier._to_payload(sample_triggered)
        assert payload["alert_id"] == 1
        # Decimal stringe çevrilir
        assert payload["triggered_value"] == "105.5"
        assert payload["threshold"] == "100"
        # datetime ISO string'e çevrilir
        assert isinstance(payload["triggered_at"], str)
        assert "2024-01-01" in payload["triggered_at"]

    def test_to_payload_handles_dict_input(self):
        """Doğrudan dict verirsen kopyası dönmeli."""
        in_data = {"alert_id": 9, "message": "x"}
        out = Notifier._to_payload(in_data)
        assert out == in_data
        assert out is not in_data  # kopya

    def test_to_payload_handles_arbitrary_object(self):
        """Dataclass olmayan obje: getattr ile alanlar çekilir."""

        class _Obj:
            alert_id = 7
            instrument_id = 3
            alert_type = "price_below"
            triggered_value = Decimal("10")
            threshold = Decimal("20")
            message = "obj"
            triggered_at = datetime(2024, 6, 1, tzinfo=timezone.utc)

        out = Notifier._to_payload(_Obj())
        assert out["alert_id"] == 7
        assert out["triggered_value"] == "10"
