"""HelpBanner — Dismissible onboarding bilgilendirme kartı.

Kullanıcı her ekran için kısa bir bilgilendirme metni görür; sağ üstte X
butonu ile kalıcı olarak gizleyebilir. Gizleme bilgisi ``QSettings`` ile
``help_dismissed/<key>`` altında saklanır; sonraki açılışlarda gösterilmez.

Kullanım::

    from app.ui.components.help_banner import HelpBanner

    banner = HelpBanner(
        text="Bu ekran takip listeni gösterir...",
        key="watchlist",
        parent=self,
    )
    layout.addWidget(banner)  # eğer dismissed ise zaten hidden gelir
"""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


SETTINGS_KEY_PREFIX = "help_dismissed/"


class HelpBanner(QFrame):
    """Tek satır gizlenebilir bilgi kartı.

    Args:
        text: Gösterilecek Türkçe açıklama metni (uzun olabilir; word-wrap).
        key: ``QSettings`` altında dismiss state için kullanılacak benzersiz
            anahtar. Örn. ``"watchlist"``, ``"bot_picks"``.
        parent: Qt parent widget.
        icon: Sol başta gösterilecek emoji veya kısa metin (default: 💡).
        dismissable: True ise sağ üstte X butonu gösterilir.
    """

    dismissed = Signal()

    def __init__(
        self,
        text: str,
        key: str,
        parent: QWidget | None = None,
        icon: str = "💡",
        dismissable: bool = True,
    ) -> None:
        super().__init__(parent)
        self._key = key
        self.setObjectName("helpBanner")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setStyleSheet(
            "#helpBanner { "
            "background-color: rgba(25, 118, 210, 24); "
            "border: 1px solid rgba(25, 118, 210, 90); "
            "border-radius: 8px; "
            "}"
        )

        root = QHBoxLayout(self)
        root.setContentsMargins(12, 8, 8, 8)
        root.setSpacing(10)

        # Sol ikon (emoji)
        icon_lbl = QLabel(icon)
        icon_font = QFont(icon_lbl.font())
        icon_font.setPointSize(icon_font.pointSize() + 4)
        icon_lbl.setFont(icon_font)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignTop)
        icon_lbl.setFixedWidth(28)
        root.addWidget(icon_lbl)

        # Orta metin (word-wrap)
        self._text_lbl = QLabel(text)
        self._text_lbl.setWordWrap(True)
        self._text_lbl.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self._text_lbl.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        root.addWidget(self._text_lbl, stretch=1)

        # Sağ X butonu
        if dismissable:
            close_btn = QPushButton("×")
            close_btn.setFlat(True)
            close_btn.setFixedSize(24, 24)
            close_btn.setToolTip("Bu ipucunu bir daha gösterme")
            close_btn.setStyleSheet(
                "QPushButton { "
                "color: rgba(120,120,120,200); "
                "background-color: transparent; "
                "border: none; "
                "font-size: 18px; font-weight: 700; "
                "} "
                "QPushButton:hover { color: #C62828; }"
            )
            close_btn.clicked.connect(self._on_dismissed)
            root.addWidget(close_btn, alignment=Qt.AlignmentFlag.AlignTop)

        # Başlangıçta gizli olabilir
        if self._is_dismissed():
            self.hide()

    # ------------------------------------------------------------------
    def _is_dismissed(self) -> bool:
        settings = QSettings()
        return bool(
            settings.value(SETTINGS_KEY_PREFIX + self._key, False, type=bool)
        )

    def _on_dismissed(self) -> None:
        settings = QSettings()
        settings.setValue(SETTINGS_KEY_PREFIX + self._key, True)
        self.hide()
        self.dismissed.emit()

    # ------------------------------------------------------------------
    @staticmethod
    def reset_all() -> None:
        """Tüm ``help_dismissed/*`` bayraklarını sıfırla (debug için)."""
        settings = QSettings()
        for key in list(settings.allKeys()):
            if key.startswith(SETTINGS_KEY_PREFIX):
                settings.remove(key)


__all__ = ["HelpBanner", "SETTINGS_KEY_PREFIX"]
