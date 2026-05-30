"""Tema ve accent renk yönetimi.

``qdarktheme`` (paket adı ``pyqtdarktheme``) kütüphanesi runtime'da dark / light
geçişi sağlar — uygulama restart edilmesi gerekmez. Tercih ``QSettings`` ile
disk üzerinde saklanır, bir sonraki açılışta otomatik uygulanır.

qdarktheme yüklü değilse (örn. wheel kurulumu başarısız), ``QApplication.setStyle("Fusion")``
fallback'i ile uygulama yine de çalışır — palette manuel olarak koyu/açık
ayarlanır. Bu sayede UI hiçbir koşulda crash etmez.

Doküman §8.2 (Dark / Light Mod) ve §3.2 (Arayüz teknoloji yığını) referans alınmıştır.
"""

from __future__ import annotations

from typing import Final

from loguru import logger
from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

try:  # qdarktheme opsiyonel — yoksa Fusion fallback
    import qdarktheme  # type: ignore[import-not-found]

    QDARKTHEME_AVAILABLE = True
except ImportError:
    qdarktheme = None  # type: ignore[assignment]
    QDARKTHEME_AVAILABLE = False
    logger.warning("qdarktheme bulunamadı — Fusion fallback kullanılacak")


class ThemeManager(QObject):
    """Tema (dark/light/auto) ve accent renk yönetimi.

    Sinyaller:
        theme_changed(str): Tema değiştiğinde 'dark' veya 'light' yayınlar
            (auto seçiminde de çözümlenmiş efektif tema gönderilir).
        accent_changed(str): Accent renk hex değeri değiştiğinde yayınlar.
    """

    theme_changed = Signal(str)
    accent_changed = Signal(str)

    THEMES: Final[tuple[str, ...]] = ("dark", "light", "auto")
    # Mavi, yeşil, mor, turuncu, kırmızı (Material tonlamaları).
    ACCENTS: Final[tuple[str, ...]] = (
        "#1E88E5",  # mavi (varsayılan)
        "#43A047",  # yeşil
        "#8E24AA",  # mor
        "#FB8C00",  # turuncu
        "#E53935",  # kırmızı
    )
    DEFAULT_THEME: Final[str] = "auto"
    DEFAULT_ACCENT: Final[str] = "#1E88E5"

    _SETTINGS_KEY_THEME: Final[str] = "appearance/theme"
    _SETTINGS_KEY_ACCENT: Final[str] = "appearance/accent"

    def __init__(self, app: QApplication) -> None:
        super().__init__()
        self._app = app
        self._settings = QSettings()  # QApplication adı/org'undan otomatik konum
        self._theme: str = self.DEFAULT_THEME
        self._accent: str = self.DEFAULT_ACCENT

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def apply_saved_or_default(self) -> None:
        """Diskteki tercihleri yükleyip uygular; yoksa varsayılanları kullanır."""
        theme = str(self._settings.value(self._SETTINGS_KEY_THEME, self.DEFAULT_THEME))
        accent = str(self._settings.value(self._SETTINGS_KEY_ACCENT, self.DEFAULT_ACCENT))

        if theme not in self.THEMES:
            theme = self.DEFAULT_THEME
        if accent not in self.ACCENTS:
            accent = self.DEFAULT_ACCENT

        # set_theme zaten accent'i de yeniden uyguladığı için sıralama önemli.
        self._accent = accent
        self.set_theme(theme)

    def set_theme(self, name: str) -> None:
        """Tema uygula ve kaydet.

        Args:
            name: 'dark', 'light' veya 'auto'.
        """
        if name not in self.THEMES:
            logger.warning("Geçersiz tema adı: {} — 'auto' kullanılacak", name)
            name = self.DEFAULT_THEME

        self._theme = name
        self._settings.setValue(self._SETTINGS_KEY_THEME, name)

        if QDARKTHEME_AVAILABLE:
            try:
                qdarktheme.setup_theme(
                    name,
                    custom_colors={"primary": self._accent},
                )
            except Exception as exc:  # qdarktheme bazen sürüm uyumsuzluğu fırlatabilir
                logger.error("qdarktheme uygulanamadı: {} — Fusion fallback", exc)
                self._apply_fusion_fallback()
        else:
            self._apply_fusion_fallback()

        logger.info("Tema uygulandı: {} (accent={})", name, self._accent)
        self.theme_changed.emit(self.current_effective_theme())

    def set_accent(self, color: str) -> None:
        """Accent (vurgu) rengini değiştirir ve kaydeder.

        Args:
            color: Hex renk kodu (örn. '#1E88E5').
        """
        if color not in self.ACCENTS:
            logger.warning("Geçersiz accent rengi: {} — varsayılan kullanılacak", color)
            color = self.DEFAULT_ACCENT

        self._accent = color
        self._settings.setValue(self._SETTINGS_KEY_ACCENT, color)
        # Temayı yeniden uygula ki custom_colors güncellensin.
        self.set_theme(self._theme)
        self.accent_changed.emit(color)

    def toggle_theme(self) -> None:
        """Dark <-> Light arasında geçiş yapar. 'auto' ise efektif moda göre toggle eder."""
        current = self.current_effective_theme()
        new_theme = "light" if current == "dark" else "dark"
        self.set_theme(new_theme)

    # ------------------------------------------------------------------
    # Getter'lar
    # ------------------------------------------------------------------
    def current_theme(self) -> str:
        """Kullanıcının kaydettiği tema seçimi ('dark' | 'light' | 'auto')."""
        return self._theme

    def current_effective_theme(self) -> str:
        """'auto' modunda bile çözümlenmiş gerçek temayı döndürür ('dark' | 'light')."""
        if self._theme != "auto":
            return self._theme
        # qdarktheme auto modda kendi system detection'ını yapar; biz palette üzerinden
        # çözümlüyoruz.
        palette = self._app.palette()
        window_color = palette.color(QPalette.ColorRole.Window)
        return "dark" if window_color.lightness() < 128 else "light"

    def current_accent(self) -> str:
        """Kullanıcının seçtiği accent hex değeri."""
        return self._accent

    def is_dark(self) -> bool:
        """Efektif tema dark mı?"""
        return self.current_effective_theme() == "dark"

    # ------------------------------------------------------------------
    # Fallback (qdarktheme yoksa)
    # ------------------------------------------------------------------
    def _apply_fusion_fallback(self) -> None:
        """qdarktheme yoksa Fusion + manuel palette ile basit tema uygular."""
        self._app.setStyle("Fusion")
        palette = QPalette()
        accent = QColor(self._accent)

        if self._theme == "dark" or (self._theme == "auto" and self._detect_system_dark()):
            # Koyu palette
            palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
            palette.setColor(QPalette.ColorRole.WindowText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Base, QColor(20, 20, 20))
            palette.setColor(QPalette.ColorRole.AlternateBase, QColor(40, 40, 40))
            palette.setColor(QPalette.ColorRole.Text, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Button, QColor(45, 45, 45))
            palette.setColor(QPalette.ColorRole.ButtonText, QColor(220, 220, 220))
            palette.setColor(QPalette.ColorRole.Highlight, accent)
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
        else:
            # Aydınlık palette (Fusion varsayılanına yakın)
            palette = self._app.style().standardPalette()
            palette.setColor(QPalette.ColorRole.Highlight, accent)
            palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))

        self._app.setPalette(palette)

    @staticmethod
    def _detect_system_dark() -> bool:
        """Basit fallback sistem tema tespiti (varsayılan: dark)."""
        # Daha sağlam tespit için ileride QStyleHints.colorScheme() (Qt 6.5+) kullanılabilir.
        return True
