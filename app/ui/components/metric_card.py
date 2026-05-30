"""MetricCard — Başlık + değer + alt etiket + opsiyonel trend göstergesi.

Doküman §8.4 (Modern UI Bileşenleri) ve §8.1 (Tasarım İlkeleri) referans
alınmıştır.

Tasarım:
- Flat tasarım: ince kenarlık (gölge yok), iç padding.
- Başlık küçük ve gri.
- Değer büyük ve bold.
- Alt etiket küçük (ipucu / birim) — gri.
- Trend göstergesi: ``'up'`` → yeşil ok yukarı, ``'down'`` → kırmızı ok aşağı,
  ``'neutral'`` → gri tire.

qtawesome graceful degradation: yoksa unicode arrow karakterleriyle fallback.
Boyutlandırma yatay olarak ``Expanding`` (kart şeridi dengeli yayılsın).

Kullanım::

    card = MetricCard("Toplam Bakiye", "₺12.450,80", "5 cüzdan", trend="up")
    layout.addWidget(card)

    # Daha sonra anlık güncelleme:
    card.set_value("₺13.120,40", subtitle="+%5,38 bugün", trend="up")
"""

from __future__ import annotations

from typing import Literal

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
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


TrendT = Literal["up", "down", "neutral"]


#: Trend göstergesi rengi + qtawesome ikon adı + unicode fallback karakteri.
_TREND_STYLE: dict[str, tuple[str, str, str]] = {
    # trend -> (color, icon_name, fallback_char)
    "up": ("#2E7D32", "fa5s.arrow-up", "▲"),       # ▲
    "down": ("#C62828", "fa5s.arrow-down", "▼"),   # ▼
    "neutral": ("#888888", "fa5s.minus", "—"),     # —
}


class MetricCard(QFrame):
    """Tek metrik için kart.

    Parameters
    ----------
    title:
        Üstte küçük gri başlık (örn. "Toplam Bakiye").
    value:
        Büyük bold ana değer (örn. "₺12.450,80"). Henüz veri yoksa "—".
    subtitle:
        Altta küçük açıklama (örn. "Tüm cüzdanlar dahil").
    trend:
        ``'up'`` | ``'down'`` | ``'neutral'`` — sağda mini ok ikonu.
    parent:
        Standart Qt parent.
    """

    def __init__(
        self,
        title: str,
        value: str = "—",
        subtitle: str = "",
        trend: TrendT = "neutral",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(96)
        # Flat: ince kenarlık. QSS objectName ile tema bağımsız çalışır.
        self.setStyleSheet(
            "#metricCard { border: 1px solid rgba(120,120,120,90); "
            "border-radius: 8px; padding: 0px; }"
        )

        self._build_ui()
        # Başlangıç değerlerini ata
        self._title_label.setText(title)
        self.set_value(value, subtitle=subtitle, trend=trend)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(4)

        # Üst satır: başlık (sol) + trend ikonu (sağ)
        top_row = QHBoxLayout()
        top_row.setSpacing(6)

        self._title_label = QLabel("")
        title_font = QFont(self._title_label.font())
        title_font.setPointSize(max(8, title_font.pointSize() - 1))
        self._title_label.setFont(title_font)
        self._title_label.setStyleSheet("color: #9E9E9E;")  # küçük gri
        top_row.addWidget(self._title_label)

        top_row.addStretch(1)

        self._trend_label = QLabel("")
        self._trend_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._trend_label.setMinimumWidth(20)
        top_row.addWidget(self._trend_label)

        root.addLayout(top_row)

        # Orta: değer (büyük, bold)
        self._value_label = QLabel("—")
        value_font = QFont(self._value_label.font())
        value_font.setPointSize(value_font.pointSize() + 8)
        value_font.setBold(True)
        self._value_label.setFont(value_font)
        self._value_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        # Uzun değerlerde tek satırda kalsın
        self._value_label.setWordWrap(False)
        root.addWidget(self._value_label)

        # Alt: subtitle (küçük gri)
        self._subtitle_label = QLabel("")
        sub_font = QFont(self._subtitle_label.font())
        sub_font.setPointSize(max(8, sub_font.pointSize() - 1))
        self._subtitle_label.setFont(sub_font)
        self._subtitle_label.setStyleSheet("color: #9E9E9E;")
        self._subtitle_label.setWordWrap(True)
        root.addWidget(self._subtitle_label)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def set_value(
        self,
        value: str,
        subtitle: str = "",
        trend: TrendT = "neutral",
    ) -> None:
        """Kartın değerini, alt etiketini ve trend göstergesini günceller."""
        self._value_label.setText(value if value else "—")
        self._subtitle_label.setText(subtitle)
        self._subtitle_label.setVisible(bool(subtitle))
        self._apply_trend(trend)

    def set_title(self, title: str) -> None:
        """Başlığı runtime'da değiştir (örn. dil değiştir)."""
        self._title_label.setText(title)

    # ------------------------------------------------------------------
    def _apply_trend(self, trend: TrendT) -> None:
        color, icon_name, fallback = _TREND_STYLE.get(
            trend, _TREND_STYLE["neutral"]
        )
        # Değer rengini de trend rengiyle hafifçe ton ver — okunabilirlik için
        # sadece up/down'da renkli, neutral'da default kalsın.
        if trend in ("up", "down"):
            # Tema-uyumlu kalsın diye font color override (style ile)
            self._value_label.setStyleSheet(f"color: {color};")
        else:
            self._value_label.setStyleSheet("")

        if QTAWESOME_AVAILABLE:
            try:
                pm = qta.icon(icon_name, color=color).pixmap(16, 16)
                self._trend_label.setPixmap(pm)
                self._trend_label.setText("")
                return
            except Exception:  # noqa: BLE001
                pass
        # Fallback: unicode glyph + renkli stylesheet
        self._trend_label.setText(fallback)
        self._trend_label.setStyleSheet(f"color: {color}; font-weight: 700;")


__all__ = ["MetricCard", "TrendT"]
