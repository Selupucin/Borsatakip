"""Toast bildirimi — sağ üstte beliren, kendiliğinden kaybolan bildirim balonu.

Doküman §8.4 (Bildirim sistemi (toast)) referans alınmıştır.

Bileşenler:
- ``ToastNotification`` — tek bir mesaj balonu; fade-in/out animasyonu.
- ``ToastManager`` — singleton yönetici; ana pencereye göre konumlandırır,
  üst üste binmeyen yığın halinde gösterir.

Kullanım::

    from app.ui.components.toast import ToastManager
    ToastManager.instance().attach(self)  # main window içinde bir kez
    ToastManager.instance().show("Kaydedildi", level="success")
    ToastManager.instance().show("Bağlantı yok", level="error", duration_ms=5000)

Animasyon QPropertyAnimation üzerinden çalışır; PySide6'da
``windowOpacity`` yerine ``QGraphicsOpacityEffect`` daha güvenilirdir,
fakat fade-in için ``setWindowOpacity`` ile widget düzeyinde de çalışır.
Burada child widget olduğu için ``QGraphicsOpacityEffect`` kullanılır.
"""

from __future__ import annotations

from typing import Literal, Optional

from PySide6.QtCore import (
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:  # pragma: no cover - opsiyonel
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False


LevelT = Literal["info", "success", "warning", "error"]


#: level -> (bg, fg, icon_name, fallback_glyph)
_LEVEL_STYLE: dict[str, tuple[str, str, str, str]] = {
    "info": ("#1976D2", "#FFFFFF", "fa5s.info-circle", "ⓘ"),
    "success": ("#2E7D32", "#FFFFFF", "fa5s.check-circle", "✓"),
    "warning": ("#F57C00", "#FFFFFF", "fa5s.exclamation-triangle", "⚠"),
    "error": ("#C62828", "#FFFFFF", "fa5s.times-circle", "✕"),
}


TOAST_MARGIN = 16
TOAST_SPACING = 8
TOAST_WIDTH_MIN = 280
TOAST_WIDTH_MAX = 420


class ToastNotification(QFrame):
    """Tek bir toast balonu.

    Kendi başına animasyon (fade-in/out) ve auto-dismiss zamanlayıcısı
    barındırır. ``ToastManager`` tarafından yaratılıp konumlandırılır.
    """

    def __init__(
        self,
        message: str,
        level: LevelT = "info",
        duration_ms: int = 3000,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(TOAST_WIDTH_MIN)
        self.setMaximumWidth(TOAST_WIDTH_MAX)

        self._duration_ms = max(800, int(duration_ms))
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._build_ui(message, level)
        self._fade_in = self._make_fade(0.0, 1.0, 220)
        self._fade_out = self._make_fade(1.0, 0.0, 320)
        self._fade_out.finished.connect(self._on_fade_out_done)

        self._dismiss_timer = QTimer(self)
        self._dismiss_timer.setSingleShot(True)
        self._dismiss_timer.timeout.connect(self._fade_out.start)

    # ------------------------------------------------------------------
    def _build_ui(self, message: str, level: LevelT) -> None:
        bg, fg, icon_name, fallback = _LEVEL_STYLE.get(level, _LEVEL_STYLE["info"])
        self.setStyleSheet(
            f"#toast {{ background-color: {bg}; color: {fg}; "
            f"border-radius: 10px; }}"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(10)

        icon_lbl = QLabel()
        icon_lbl.setFixedWidth(20)
        if QTAWESOME_AVAILABLE:
            try:
                pm = qta.icon(icon_name, color=fg).pixmap(18, 18)
                icon_lbl.setPixmap(pm)
            except Exception:  # noqa: BLE001
                icon_lbl.setText(fallback)
                icon_lbl.setStyleSheet(f"color: {fg}; font-weight: 700;")
        else:
            icon_lbl.setText(fallback)
            icon_lbl.setStyleSheet(f"color: {fg}; font-weight: 700;")
        root.addWidget(icon_lbl)

        msg_lbl = QLabel(message)
        msg_lbl.setWordWrap(True)
        msg_font = QFont(msg_lbl.font())
        msg_font.setBold(True)
        msg_lbl.setFont(msg_font)
        msg_lbl.setStyleSheet(f"color: {fg};")
        root.addWidget(msg_lbl, stretch=1)

    # ------------------------------------------------------------------
    def _make_fade(self, start: float, end: float, duration: int) -> QPropertyAnimation:
        anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        anim.setDuration(duration)
        anim.setStartValue(start)
        anim.setEndValue(end)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        return anim

    # ------------------------------------------------------------------
    def show_toast(self) -> None:
        """Fade-in + auto-dismiss zincirini başlat."""
        self.show()
        self._fade_in.start()
        self._dismiss_timer.start(self._duration_ms)

    def _on_fade_out_done(self) -> None:
        # Yığından kendini çıkar (ToastManager dinler)
        self.deleteLater()

    # Kullanıcı tıklayınca da kapansın
    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        self._dismiss_timer.stop()
        self._fade_out.start()
        super().mousePressEvent(event)


class ToastManager(QObject):
    """Toast yığını yöneticisi — singleton.

    Bir ``QWidget`` (genelde ``MainWindow``) attach edilir; toast'lar o
    widget'ın sağ üst köşesine konumlanır, alt alta dizilir.
    """

    _instance: Optional["ToastManager"] = None

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._host: Optional[QWidget] = None
        self._stack: list[ToastNotification] = []

    @classmethod
    def instance(cls) -> "ToastManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # ------------------------------------------------------------------
    def attach(self, host: QWidget) -> None:
        """Ana pencereyi (veya başka bir host widget'ı) bağla.

        Önceki host varsa toast'lar yeni host'a taşınmaz; sadece sonraki
        ``show`` çağrıları yeni host kullanır.
        """
        self._host = host

    # ------------------------------------------------------------------
    def show(
        self,
        message: str,
        level: LevelT = "info",
        duration_ms: int = 3000,
    ) -> None:
        """Yeni bir toast göster.

        ``attach`` çağrılmadıysa sessizce no-op olur (uygulama açılış
        sırasında erken çağırmaya karşı güvenli).
        """
        if self._host is None:
            return
        toast = ToastNotification(message, level=level, duration_ms=duration_ms, parent=self._host)
        toast.destroyed.connect(lambda *_: self._on_toast_destroyed(toast))
        self._stack.append(toast)
        self._reposition()
        toast.show_toast()

    # ------------------------------------------------------------------
    def _on_toast_destroyed(self, toast: ToastNotification) -> None:
        try:
            self._stack.remove(toast)
        except ValueError:
            pass
        self._reposition()

    def _reposition(self) -> None:
        if self._host is None:
            return
        # Host sağ üst köşesinden başla
        host_w = self._host.width()
        x_right = host_w - TOAST_MARGIN
        y = TOAST_MARGIN
        for t in list(self._stack):
            t.adjustSize()
            tw = max(TOAST_WIDTH_MIN, min(TOAST_WIDTH_MAX, t.sizeHint().width()))
            th = t.sizeHint().height()
            t.setFixedWidth(tw)
            t.move(x_right - tw, y)
            y += th + TOAST_SPACING


__all__ = ["ToastNotification", "ToastManager", "LevelT"]
