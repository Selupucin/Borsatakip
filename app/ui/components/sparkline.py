"""Sparkline — mini trend grafiği (~80x24 px) liste satırları için.

Doküman §8.4 (Modern UI Bileşenleri) — "liste satırlarında mini trend
grafikleri" için.

Tasarım:
- pyqtgraph varsa onun ``PlotWidget``'ı (eksen/grid gizlenmiş, sadece çizgi).
- pyqtgraph yoksa QPainter ile basit çizgi grafik fallback.
- Renk: ``positive=True`` ise yeşil, ``False`` ise kırmızı, ``None`` ise
  son değer ilk değerin altında/üstündeyse otomatik karar verir.

Kullanım::

    s = Sparkline()
    s.set_data([100, 102, 101, 105, 110])  # otomatik yeşil
    layout.addWidget(s)
"""

from __future__ import annotations

from typing import Iterable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget


try:
    import pyqtgraph as pg  # type: ignore[import-not-found]

    PYQTGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover - opsiyonel
    pg = None  # type: ignore[assignment]
    PYQTGRAPH_AVAILABLE = False


POSITIVE_COLOR = "#2E7D32"
NEGATIVE_COLOR = "#C62828"
NEUTRAL_COLOR = "#888888"


DEFAULT_WIDTH = 80
DEFAULT_HEIGHT = 24


def _decide_color(values: list[float], positive: Optional[bool]) -> str:
    if positive is True:
        return POSITIVE_COLOR
    if positive is False:
        return NEGATIVE_COLOR
    if not values or len(values) < 2:
        return NEUTRAL_COLOR
    return POSITIVE_COLOR if values[-1] >= values[0] else NEGATIVE_COLOR


class _PainterSparkline(QWidget):
    """pyqtgraph yoksa kullanılan basit QPainter tabanlı fallback."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._values: list[float] = []
        self._color: str = NEUTRAL_COLOR
        self.setFixedSize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_data(self, values: Iterable[float], positive: Optional[bool] = None) -> None:
        self._values = [float(v) for v in values]
        self._color = _decide_color(self._values, positive)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)

        if len(self._values) < 2:
            # Düz bir tire çiz
            pen = QPen(QColor(NEUTRAL_COLOR))
            pen.setWidth(1)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(rect.left(), rect.center().y()),
                QPointF(rect.right(), rect.center().y()),
            )
            return

        lo = min(self._values)
        hi = max(self._values)
        span = hi - lo if hi != lo else 1.0
        n = len(self._values)
        dx = rect.width() / (n - 1)

        points: list[QPointF] = []
        for i, v in enumerate(self._values):
            x = rect.left() + i * dx
            # y aşağıdan yukarı tersine (Qt yukarıdan aşağı +y)
            y = rect.bottom() - ((v - lo) / span) * rect.height()
            points.append(QPointF(x, y))

        pen = QPen(QColor(self._color))
        pen.setWidthF(1.5)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)

        for a, b in zip(points, points[1:]):
            painter.drawLine(a, b)


class Sparkline(QWidget):
    """Mini trend grafiği widget'ı.

    pyqtgraph kuruluysa onun üzerinden çizer (yüksek perf); yoksa
    ``QPainter`` fallback'ine düşer. API her iki yolda da aynıdır.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self._impl: QWidget
        self._plot = None
        self._curve = None

        if PYQTGRAPH_AVAILABLE:
            self._build_pyqtgraph()
        else:
            self._impl = _PainterSparkline(self)
            # Ana widget aynı boyutta — child widget tam doldursun.
            self._impl.setParent(self)
            self._impl.move(0, 0)

    # ------------------------------------------------------------------
    def _build_pyqtgraph(self) -> None:
        # PlotWidget eksenleri kapat
        plot = pg.PlotWidget(parent=self, background=None)
        plot.setFixedSize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        plot.hideAxis("bottom")
        plot.hideAxis("left")
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.setContentsMargins(0, 0, 0, 0)
        # Veri yoksa placeholder yatay çizgi
        self._plot = plot
        self._impl = plot

    # ------------------------------------------------------------------
    def set_data(self, values: Iterable[float], positive: Optional[bool] = None) -> None:
        """Sparkline verisini günceller.

        Parameters
        ----------
        values:
            Trend değerleri (float listesi). En az 2 değer gerekli;
            tek/0 değer geldiyse düz çizgi (nötr) gösterilir.
        positive:
            ``True`` zorla yeşil, ``False`` zorla kırmızı, ``None`` ise
            ilk-son değer karşılaştırmasıyla otomatik.
        """
        vals = [float(v) for v in values]
        color = _decide_color(vals, positive)

        if PYQTGRAPH_AVAILABLE and self._plot is not None:
            self._plot.clear()
            if len(vals) >= 1:
                # x ekseni 0..n-1
                xs = list(range(len(vals)))
                pen = pg.mkPen(color=color, width=1.5)
                self._curve = self._plot.plot(xs, vals, pen=pen)
            # Eksen aralığı verileri tam kapsasın
            if len(vals) >= 2:
                lo = min(vals)
                hi = max(vals)
                pad = (hi - lo) * 0.1 if hi != lo else 1.0
                self._plot.setYRange(lo - pad, hi + pad, padding=0)
                self._plot.setXRange(0, len(vals) - 1, padding=0)
        else:
            # Fallback'e devret
            if isinstance(self._impl, _PainterSparkline):
                self._impl.set_data(vals, positive=positive)


__all__ = ["Sparkline", "POSITIVE_COLOR", "NEGATIVE_COLOR", "NEUTRAL_COLOR"]
