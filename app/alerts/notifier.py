"""Alarm bildirim katmanı.

Doküman §11 Faz 1 alarm sistemi ve §8.4 toast bildirimleri referans alınmıştır.

Tasarım:
- ``Notifier`` her ``TriggeredAlert`` için 3 hedefe yayım yapar:
    1. ``loguru.logger.warning`` — kalıcı log kaydı.
    2. Qt ``Signal`` (``alert_triggered``) — UI tarafı toast bildirim için
       bu sinyale bağlanır (ui-developer'ın görevi).
    3. Opsiyonel sistem (OS) bildirimi — ``plyer`` ya da ``win10toast``
       varsa kullanılır; yoksa sessizce atlanır.

- PySide6 yüklü değilse (test ortamı, headless CI) ``QObject`` / ``Signal``
  import'u try/except ile sarmalanır ve Notifier saf-Python "stub signal" ile
  çalışır. Bu, motorun UI olmadan da test edilebilir kalmasını sağlar.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from loguru import logger

# ---------------------------------------------------------------------------
# PySide6 import fallback
# ---------------------------------------------------------------------------

try:  # pragma: no cover - environment dependent
    from PySide6.QtCore import QObject, Signal  # type: ignore[import-not-found]

    _QT_AVAILABLE = True
except Exception:  # noqa: BLE001 — Qt eksikse, başlatma da hata verebilir
    _QT_AVAILABLE = False

    class QObject:  # type: ignore[no-redef]
        """PySide6 yokken kullanılan minimal taban sınıf."""

        def __init__(self, *args, **kwargs) -> None:  # noqa: D401
            pass

    class _StubSignal:
        """Qt ``Signal`` sınıfı yerine geçen no-op çağrılabilir."""

        def __init__(self, *_types: Any) -> None:
            self._slots: list[Callable[[Any], None]] = []

        def connect(self, slot: Callable[[Any], None]) -> None:
            self._slots.append(slot)

        def disconnect(self, slot: Callable[[Any], None] | None = None) -> None:
            if slot is None:
                self._slots.clear()
            else:
                self._slots = [s for s in self._slots if s is not slot]

        def emit(self, payload: Any) -> None:
            for slot in list(self._slots):
                try:
                    slot(payload)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("Slot çağrısında hata: {}", exc)

    # PySide6 ile aynı çağrı imzası: ``Signal(dict)`` -> örnek üretiyor
    def Signal(*types: Any):  # type: ignore[no-redef]  # noqa: N802
        return _StubSignal(*types)


# ---------------------------------------------------------------------------
# Sistem bildirimi (opsiyonel)
# ---------------------------------------------------------------------------

_SYSTEM_NOTIFY: Callable[[str, str], None] | None = None

try:  # pragma: no cover - environment dependent
    from plyer import notification as _plyer_notification  # type: ignore[import-not-found]

    def _plyer_notify(title: str, body: str) -> None:
        _plyer_notification.notify(title=title, message=body, app_name="Borsa Bot")

    _SYSTEM_NOTIFY = _plyer_notify
except Exception:  # noqa: BLE001
    try:  # pragma: no cover
        from win10toast import ToastNotifier  # type: ignore[import-not-found]

        _toaster = ToastNotifier()

        def _win10_notify(title: str, body: str) -> None:
            _toaster.show_toast(title, body, duration=5, threaded=True)

        _SYSTEM_NOTIFY = _win10_notify
    except Exception:  # noqa: BLE001
        _SYSTEM_NOTIFY = None


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------


class Notifier(QObject):
    """Alarm bildirim yayıncısı.

    UI tarafı:
        ``notifier.alert_triggered.connect(my_toast_slot)`` ile bağlanır;
        ``my_toast_slot(payload: dict)`` her tetiklenen alarm için çağrılır.

    Payload formatı (``TriggeredAlert``'in ``asdict`` çıktısı):
        ``alert_id``, ``instrument_id``, ``alert_type``, ``triggered_value``,
        ``threshold``, ``message``, ``triggered_at``.
    """

    # PySide6 yüklü değilse `_StubSignal` örneği döner — API aynı.
    alert_triggered = Signal(dict)

    def __init__(self, enable_system_notifications: bool = True) -> None:
        super().__init__()
        self._enable_system = bool(enable_system_notifications) and _SYSTEM_NOTIFY is not None
        if not _QT_AVAILABLE:
            logger.debug(
                "PySide6 bulunamadı; Notifier stub Signal ile çalışıyor (UI'a "
                "bağlanılamaz, ama test ve log düzeyinde sorunsuz)."
            )

    # ---------------------------------------------------------------- public

    def notify(self, alert: Any) -> None:
        """Tetiklenen alarmı log + Qt sinyal + (opsiyonel) sistem bildirimi olarak yayar.

        ``alert`` ``TriggeredAlert`` dataclass'ı ya da uyumlu bir nesne/sözlük
        olabilir.
        """

        payload = self._to_payload(alert)
        message = str(payload.get("message", "Alarm"))

        # 1) Log
        logger.warning("ALARM: {}", message)

        # 2) Qt sinyal — UI dinleyicisi varsa toast gösterir
        try:
            self.alert_triggered.emit(payload)
        except Exception as exc:  # noqa: BLE001
            logger.exception("alert_triggered.emit hata verdi: {}", exc)

        # 3) Opsiyonel sistem bildirimi
        if self._enable_system and _SYSTEM_NOTIFY is not None:
            try:
                _SYSTEM_NOTIFY("Borsa Bot — Alarm", message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sistem bildirimi başarısız (yutuldu): {}", exc)

    # --------------------------------------------------------------- helpers

    @staticmethod
    def _to_payload(alert: Any) -> dict:
        if isinstance(alert, dict):
            return dict(alert)
        if is_dataclass(alert):
            data = asdict(alert)
        else:
            data = {
                k: getattr(alert, k)
                for k in (
                    "alert_id",
                    "instrument_id",
                    "alert_type",
                    "triggered_value",
                    "threshold",
                    "message",
                    "triggered_at",
                )
                if hasattr(alert, k)
            }
        # ``Decimal`` ve ``datetime`` Qt sinyalinde sorun çıkarmaz ama dict
        # tüketicileri (JSON vb.) için string formuna çevirilebilir hale getir.
        return {k: _qt_safe(v) for k, v in data.items()}


def _qt_safe(value: Any) -> Any:
    """Decimal / datetime gibi tipleri Qt/JSON dostu hale çevirir."""

    from datetime import datetime as _dt  # local — modül başı şişmesin
    from decimal import Decimal as _Decimal

    if isinstance(value, _Decimal):
        return str(value)
    if isinstance(value, _dt):
        return value.isoformat()
    return value


__all__ = ["Notifier"]
