"""SkeletonLoader — yükleme iskeleti (shimmer animasyonlu placeholder).

Doküman §8.4 (Yükleme iskeletleri (skeleton loaders)) referans alınmıştır.

Tasarım:
- N satırlı dikey blok; her satır farklı genişlikte (gerçek liste
  hissini taklit eder).
- Shimmer animasyonu: ``QPropertyAnimation`` ile soldan sağa hareket
  eden açık renkli bant; ``QTimer`` üzerinden frame tick'i çekilir.
- ``start()`` / ``stop()`` çağrılarıyla animasyon kontrol edilir.

Kullanım::

    skel = SkeletonLoader(rows=5)
    layout.addWidget(skel)
    skel.start()
    # veri geldiğinde:
    skel.stop()
    skel.hide()
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


# Satır şekilleri için sabit oranlar (varyasyon için)
_ROW_WIDTH_RATIOS: tuple[float, ...] = (0.92, 0.78, 0.85, 0.65, 0.90, 0.72, 0.88)

ROW_HEIGHT = 14
ROW_SPACING = 10
ROW_RADIUS = 4

# Renkler — tema fark etmeksizin görünür kalsın diye yarı saydam griler
BASE_COLOR = QColor(120, 120, 120, 60)
SHIMMER_COLOR_LIGHT = QColor(255, 255, 255, 70)
SHIMMER_COLOR_EDGE = QColor(255, 255, 255, 0)


class SkeletonLoader(QWidget):
    """Yükleme iskeleti widget'ı.

    Parameters
    ----------
    rows:
        Kaç satırlık iskelet çizilecek (varsayılan 3).
    parent:
        Standart Qt parent.
    """

    SHIMMER_TICK_MS = 30   # frame periyodu
    SHIMMER_STEP = 0.03    # her tick'te kayma oranı

    def __init__(self, rows: int = 3, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows = max(1, int(rows))
        self._shimmer_pos: float = -0.3  # -1..1 aralığında

        self._timer = QTimer(self)
        self._timer.setInterval(self.SHIMMER_TICK_MS)
        self._timer.timeout.connect(self._on_tick)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(self._rows * (ROW_HEIGHT + ROW_SPACING))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def start(self) -> None:
        """Shimmer animasyonunu başlat."""
        if not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """Animasyonu durdur (widget gizleneceği zaman çağrılmalı)."""
        self._timer.stop()

    def set_rows(self, rows: int) -> None:
        """Satır sayısını runtime'da güncelle."""
        self._rows = max(1, int(rows))
        self.setMinimumHeight(self._rows * (ROW_HEIGHT + ROW_SPACING))
        self.update()

    # ------------------------------------------------------------------
    def _on_tick(self) -> None:
        self._shimmer_pos += self.SHIMMER_STEP
        if self._shimmer_pos > 1.3:
            self._shimmer_pos = -0.3
        self.update()

    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = self.width()
        y = 4
        for i in range(self._rows):
            ratio = _ROW_WIDTH_RATIOS[i % len(_ROW_WIDTH_RATIOS)]
            row_w = max(40.0, w * ratio)
            rect = QRectF(0.0, float(y), row_w, float(ROW_HEIGHT))

            # Base satır
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(BASE_COLOR))
            painter.drawRoundedRect(rect, ROW_RADIUS, ROW_RADIUS)

            # Shimmer band — yatay gradient, pozisyona göre kayar
            if self._timer.isActive():
                shim_w = rect.width() * 0.35
                shim_x = rect.left() + self._shimmer_pos * rect.width() - shim_w / 2
                shim_rect = QRectF(shim_x, rect.top(), shim_w, rect.height())
                # Gradient: edge->light->edge
                grad = QLinearGradient(shim_rect.left(), 0, shim_rect.right(), 0)
                grad.setColorAt(0.0, SHIMMER_COLOR_EDGE)
                grad.setColorAt(0.5, SHIMMER_COLOR_LIGHT)
                grad.setColorAt(1.0, SHIMMER_COLOR_EDGE)
                # Sadece satır içinde kalsın (clip)
                painter.save()
                painter.setClipRect(rect)
                painter.setBrush(QBrush(grad))
                painter.drawRoundedRect(shim_rect, ROW_RADIUS, ROW_RADIUS)
                painter.restore()

            y += ROW_HEIGHT + ROW_SPACING


__all__ = ["SkeletonLoader"]
