"""Ayarlar ekranı — Faz 1 → Faz 3 cilası.

Doküman §8.3 "Ayarlar", §7 (İşlem Modları & Güvenlik) referans alınmıştır.

Faz 3 eklemeleri:
- "Trading & Güvenlik" grup kutusu — trading mode (Faz 4 modları disabled
  + rozetli), risk eşiği slider, günlük işlem limiti, max drawdown.
- Kill switch toggle (büyük kırmızı).

Backend TODO:
    # - AccountService.set_trading_mode(account_id, mode)
    # - AccountService.set_risk_threshold(account_id, value)
    # - SafetyEngine instance config — max_daily_trades, max_drawdown_pct
    # - SafetyEngine.activate_kill_switch / deactivate_kill_switch

Önemli güvenlik notu: API anahtarları kalıcı olarak ``QSettings`` ile
saklanmaz — Faz 3'te ``keyring`` (OS güvenli depo) ile şifreli saklama
yapılacak. Faz 1'de yalnızca giriş alanları gösterilir, kaydetme bağlı değil.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.trading.execution_modes import (
    MODE_REGISTRY,
    TradingMode,
    is_active_in_current_phase,
)
from app.ui.components import ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge
    from app.ui.theme_manager import ThemeManager


MODE_LABEL_TR: dict[TradingMode, str] = {
    TradingMode.MANUAL_PARALLEL: "Manuel-Eşli (paralel)",
    TradingMode.SEMI_AUTO: "Yarı-Otomatik",
    TradingMode.FULL_AUTO: "Tam Otomatik",
    TradingMode.PAPER: "Sanal (Paper)",
}


# QComboBox kullanıcı etiketi -> ThemeManager.set_theme() kodu
_THEME_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Otomatik (sistem)", "auto"),
    ("Karanlık", "dark"),
    ("Aydınlık", "light"),
)

_ACCENT_LABELS: tuple[tuple[str, str], ...] = (
    ("Mavi", "#1E88E5"),
    ("Yeşil", "#43A047"),
    ("Mor", "#8E24AA"),
    ("Turuncu", "#FB8C00"),
    ("Kırmızı", "#E53935"),
)

_DATA_SOURCES: tuple[str, ...] = (
    "yfinance",
    "Stooq",
    "Alpha Vantage",
    "İş Yatırım",
    "KAP",
    "TradingView",
    "Investing.com",
    "Finviz",
    "TCMB (USD/TRY)",
    "Reddit",
)


class SettingsWidget(QWidget):
    """Tema, dil, veri kaynağı, bildirim ve API anahtarı ayarları (Faz 1 iskelet)."""

    def __init__(
        self,
        theme_manager: "ThemeManager",
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._bridge = bridge
        self._build_ui()
        self._sync_from_theme_manager()
        # Backend varsa trading mode + risk threshold'u DB'den oku
        if bridge and bridge.available:
            self._load_from_backend()

    def _load_from_backend(self) -> None:
        if self._bridge is None or self._bridge.account_id is None:
            return
        bridge = self._bridge
        account_id = int(bridge.account_id)
        bridge.run_async(
            lambda: bridge.account.get_snapshot(account_id),
            on_success=self._on_account_loaded,
            on_error=lambda exc: logger.debug("settings _load hata: {}", exc),
        )

    def _on_account_loaded(self, snapshot) -> None:
        try:
            mode = snapshot.trading_mode
            for i in range(self._trading_mode_combo.count()):
                if self._trading_mode_combo.itemData(i) == mode:
                    self._trading_mode_combo.blockSignals(True)
                    self._trading_mode_combo.setCurrentIndex(i)
                    self._trading_mode_combo.blockSignals(False)
                    break
            self._risk_slider.blockSignals(True)
            self._risk_slider.setValue(int(snapshot.risk_threshold))
            self._risk_value_lbl.setText(str(int(snapshot.risk_threshold)))
            self._risk_slider.blockSignals(False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("settings._on_account_loaded hata: {}", exc)

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Ana layout: başlık + scroll alanı (içerik uzun olduğunda küçük
        # ekranlarda dipteki API anahtarları kaybolmasın).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 8)
        outer.setSpacing(10)

        title = QLabel("Ayarlar")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        outer.addWidget(title)

        # Scroll alanı içine asıl içerik widget'ı
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 8, 16)
        root.setSpacing(16)

        # --- Görünüm ----------------------------------------------------
        appearance_box = QGroupBox("Görünüm")
        appearance_layout = QFormLayout(appearance_box)

        self._theme_combo = QComboBox()
        for label, code in _THEME_OPTIONS:
            self._theme_combo.addItem(label, userData=code)
        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        appearance_layout.addRow("Tema:", self._theme_combo)

        accent_row = QHBoxLayout()
        self._accent_buttons: list[QPushButton] = []
        for label, color in _ACCENT_LABELS:
            btn = QPushButton()
            btn.setToolTip(label)
            btn.setFixedSize(32, 32)
            btn.setCheckable(True)
            btn.setAutoExclusive(True)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {color}; border: 1px solid #555;"
                f" border-radius: 4px; }}"
                f"QPushButton:checked {{ border: 2px solid white; }}"
            )
            btn.clicked.connect(lambda _checked=False, c=color: self._on_accent_changed(c))
            accent_row.addWidget(btn)
            self._accent_buttons.append(btn)
        accent_row.addStretch(1)
        appearance_layout.addRow("Vurgu Rengi:", accent_row)

        root.addWidget(appearance_box)

        # --- Dil --------------------------------------------------------
        lang_box = QGroupBox("Dil")
        lang_layout = QFormLayout(lang_box)
        self._lang_combo = QComboBox()
        self._lang_combo.addItem("Türkçe", userData="tr")
        self._lang_combo.addItem("English (yakında)", userData="en")
        # English şimdilik aktif değil
        self._lang_combo.model().item(1).setEnabled(False)
        lang_layout.addRow("Arayüz Dili:", self._lang_combo)
        root.addWidget(lang_box)

        # --- Trading & Güvenlik (Faz 3) ---------------------------------
        trading_box = QGroupBox("Trading ve Güvenlik")
        trading_layout = QFormLayout(trading_box)

        # Trading mode combo
        self._trading_mode_combo = QComboBox()
        for mode in (
            TradingMode.MANUAL_PARALLEL,
            TradingMode.PAPER,
            TradingMode.SEMI_AUTO,
            TradingMode.FULL_AUTO,
        ):
            label = MODE_LABEL_TR.get(mode, mode.value)
            if not is_active_in_current_phase(mode):
                label = f"{label}  (Yakında - Faz 4)"
            self._trading_mode_combo.addItem(label, userData=mode.value)
            if not is_active_in_current_phase(mode):
                idx = self._trading_mode_combo.count() - 1
                self._trading_mode_combo.model().item(idx).setEnabled(False)
                self._trading_mode_combo.setItemData(
                    idx, "Faz 4 — aracı kurum entegrasyonu gerekli",
                    Qt.ItemDataRole.ToolTipRole,
                )
        self._trading_mode_combo.currentIndexChanged.connect(self._on_trading_mode_changed)
        trading_layout.addRow("İşlem Modu:", self._trading_mode_combo)

        # Risk eşiği slider
        risk_row = QHBoxLayout()
        self._risk_slider = QSlider(Qt.Orientation.Horizontal)
        self._risk_slider.setRange(0, 100)
        self._risk_slider.setValue(50)
        self._risk_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._risk_slider.setTickInterval(10)
        self._risk_value_lbl = QLabel("50")
        self._risk_value_lbl.setMinimumWidth(30)
        self._risk_slider.valueChanged.connect(self._on_risk_threshold_changed)
        risk_row.addWidget(self._risk_slider, stretch=1)
        risk_row.addWidget(self._risk_value_lbl)
        risk_container = QWidget()
        risk_container.setLayout(risk_row)
        trading_layout.addRow("Risk Eşiği:", risk_container)

        # Günlük işlem limiti
        self._daily_limit = QSpinBox()
        self._daily_limit.setRange(1, 1000)
        self._daily_limit.setValue(10)
        self._daily_limit.setSuffix(" işlem/gün")
        trading_layout.addRow("Günlük Maksimum İşlem:", self._daily_limit)

        # Max drawdown
        self._max_drawdown = QDoubleSpinBox()
        self._max_drawdown.setRange(1.0, 99.0)
        self._max_drawdown.setValue(15.0)
        self._max_drawdown.setDecimals(1)
        self._max_drawdown.setSuffix(" %")
        trading_layout.addRow("Maks. Drawdown:", self._max_drawdown)

        # Kill switch toggle
        self._kill_switch_toggle = QCheckBox("Kill Switch — tüm otomatik işlemleri durdur")
        self._kill_switch_toggle.setStyleSheet(
            "QCheckBox { color: #C62828; font-weight: 700; }"
        )
        self._kill_switch_toggle.toggled.connect(self._on_kill_switch_toggled)
        trading_layout.addRow(self._kill_switch_toggle)

        trading_hint = QLabel(
            "Trading ayarları runtime'da uygulanır. Faz 4'te AccountService "
            "ve SafetyEngine ile DB'ye yazılacak."
        )
        trading_hint.setWordWrap(True)
        trading_hint.setStyleSheet("color: gray; font-style: italic;")
        trading_layout.addRow(trading_hint)

        root.addWidget(trading_box)

        # --- Veri Kaynakları --------------------------------------------
        sources_box = QGroupBox("Veri Kaynakları")
        sources_layout = QVBoxLayout(sources_box)
        self._sources_list = QListWidget()
        for name in _DATA_SOURCES:
            item = QListWidgetItem(name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self._sources_list.addItem(item)
        sources_layout.addWidget(self._sources_list)
        sources_hint = QLabel(
            "Kaynakların etkin/devre dışı yönetimi Faz 2'de ``DataCollector`` ile bağlanır."
        )
        sources_hint.setStyleSheet("color: gray; font-style: italic;")
        sources_layout.addWidget(sources_hint)
        root.addWidget(sources_box)

        # --- Bildirimler ------------------------------------------------
        notify_box = QGroupBox("Bildirimler")
        notify_layout = QVBoxLayout(notify_box)
        self._notify_alerts = QCheckBox("Alarm bildirimlerini göster")
        self._notify_alerts.setChecked(True)
        self._notify_system = QCheckBox("Sistem bildirimlerini kullan (opsiyonel)")
        notify_layout.addWidget(self._notify_alerts)
        notify_layout.addWidget(self._notify_system)
        root.addWidget(notify_box)

        # --- API anahtarları --------------------------------------------
        api_box = QGroupBox("API Anahtarları")
        api_layout = QFormLayout(api_box)
        self._api_alpha = QLineEdit()
        self._api_alpha.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_alpha.setPlaceholderText("Alpha Vantage API anahtarı")
        api_layout.addRow("Alpha Vantage:", self._api_alpha)

        self._api_reddit_id = QLineEdit()
        self._api_reddit_id.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_reddit_id.setPlaceholderText("Reddit Client ID")
        api_layout.addRow("Reddit Client ID:", self._api_reddit_id)

        self._api_reddit_secret = QLineEdit()
        self._api_reddit_secret.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_reddit_secret.setPlaceholderText("Reddit Client Secret")
        api_layout.addRow("Reddit Client Secret:", self._api_reddit_secret)

        api_hint = QLabel(
            "API anahtarları Faz 3'te ``keyring`` ile OS güvenli deposunda saklanacak. "
            "Şu an yalnızca form görüntülenmektedir; kaydetme bağlı değildir."
        )
        api_hint.setWordWrap(True)
        api_hint.setStyleSheet("color: gray; font-style: italic;")
        api_layout.addRow(api_hint)

        root.addWidget(api_box)

        # --- Güncellemeler ----------------------------------------------
        from app.__version__ import __version__ as _APP_VERSION

        update_box = QGroupBox("Güncellemeler")
        update_layout = QFormLayout(update_box)

        self._update_current_lbl = QLabel(_APP_VERSION)
        self._update_current_lbl.setStyleSheet("font-weight: 600;")
        update_layout.addRow("Mevcut sürüm:", self._update_current_lbl)

        self._update_latest_lbl = QLabel("Henüz kontrol edilmedi")
        self._update_latest_lbl.setStyleSheet("color: gray;")
        update_layout.addRow("En son sürüm:", self._update_latest_lbl)

        self._update_published_lbl = QLabel("—")
        self._update_published_lbl.setStyleSheet("color: gray;")
        update_layout.addRow("Yayın tarihi:", self._update_published_lbl)

        # Butonlar
        upd_btn_row = QHBoxLayout()
        self._update_check_btn = QPushButton("🔄 Şimdi Kontrol Et")
        self._update_check_btn.clicked.connect(self._on_check_update_clicked)
        self._update_download_btn = QPushButton("⬇ İndir & Kur")
        self._update_download_btn.setEnabled(False)
        self._update_download_btn.setStyleSheet(
            "QPushButton:enabled { background-color: #2E7D32; color: white; font-weight: 600; }"
        )
        self._update_download_btn.clicked.connect(self._on_download_update_clicked)
        upd_btn_row.addWidget(self._update_check_btn)
        upd_btn_row.addWidget(self._update_download_btn)
        upd_btn_row.addStretch(1)
        update_layout.addRow(upd_btn_row)

        # Otomatik kontrol checkbox
        self._update_auto_check = QCheckBox("Otomatik güncelleme kontrolü (her 6 saatte bir)")
        self._update_auto_check.setChecked(True)
        update_layout.addRow(self._update_auto_check)

        # Release notes
        from PySide6.QtWidgets import QTextEdit

        self._update_notes = QTextEdit()
        self._update_notes.setReadOnly(True)
        self._update_notes.setMaximumHeight(180)
        self._update_notes.setPlaceholderText("Yeni sürüm bulunduğunda sürüm notları burada görünecek.")
        update_layout.addRow("Sürüm notları:", self._update_notes)

        self._update_info = None  # type: ignore[assignment]
        self._downloaded_installer = None  # type: ignore[assignment]

        root.addWidget(update_box)
        root.addStretch(1)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        # Genel boyutlandırma
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------
    # ThemeManager senkronizasyonu
    # ------------------------------------------------------------------
    def _sync_from_theme_manager(self) -> None:
        """ThemeManager'daki mevcut değerleri UI'a yansıt."""
        current_theme = self._theme_manager.current_theme()
        for i in range(self._theme_combo.count()):
            if self._theme_combo.itemData(i) == current_theme:
                self._theme_combo.blockSignals(True)
                self._theme_combo.setCurrentIndex(i)
                self._theme_combo.blockSignals(False)
                break

        current_accent = self._theme_manager.current_accent()
        for btn, (_label, color) in zip(self._accent_buttons, _ACCENT_LABELS, strict=True):
            btn.setChecked(color.lower() == current_accent.lower())

    @Slot(int)
    def _on_theme_changed(self, index: int) -> None:
        code = self._theme_combo.itemData(index)
        if isinstance(code, str):
            self._theme_manager.set_theme(code)

    def _on_accent_changed(self, color: str) -> None:
        self._theme_manager.set_accent(color)

    # ------------------------------------------------------------------
    # Trading & güvenlik slot'ları (Faz 3 — placeholder)
    # ------------------------------------------------------------------
    @Slot(int)
    def _on_trading_mode_changed(self, index: int) -> None:
        code = self._trading_mode_combo.itemData(index)
        if not isinstance(code, str):
            return
        if (
            self._bridge is None
            or not self._bridge.available
            or self._bridge.account_id is None
        ):
            return
        bridge = self._bridge
        account_id = int(bridge.account_id)
        bridge.run_async(
            lambda: bridge.account.set_trading_mode(account_id, code),
            on_success=lambda _r: ToastManager.instance().show(
                "İşlem modu güncellendi.", level="success"
            ),
            on_error=lambda exc: ToastManager.instance().show(
                f"Güncelleme hatası: {exc}", level="error"
            ),
        )

    @Slot(int)
    def _on_risk_threshold_changed(self, value: int) -> None:
        """Slider hareket ederken UI; DB'ye yazımı 350ms debounce."""
        from PySide6.QtCore import QTimer

        self._risk_value_lbl.setText(str(value))

        if not hasattr(self, "_risk_debounce_timer"):
            self._risk_debounce_timer = QTimer(self)
            self._risk_debounce_timer.setSingleShot(True)
            self._risk_debounce_timer.timeout.connect(self._commit_risk_threshold)
        self._risk_pending_value = float(value)
        self._risk_debounce_timer.start(350)

    def _commit_risk_threshold(self) -> None:
        value = getattr(self, "_risk_pending_value", None)
        if value is None:
            return
        if (
            self._bridge is None
            or not self._bridge.available
            or self._bridge.account_id is None
        ):
            return
        bridge = self._bridge
        account_id = int(bridge.account_id)
        bridge.run_async(
            lambda: bridge.account.set_risk_threshold(account_id, value),
            on_success=lambda _r: None,
            on_error=lambda exc: logger.debug("risk_threshold hata: {}", exc),
        )

    @Slot(bool)
    def _on_kill_switch_toggled(self, checked: bool) -> None:
        if checked:
            confirm = QMessageBox.question(
                self,
                "Kill Switch Aktifleştir",
                "Tüm otomatik işlemleri DURDURMAK üzeresin.\nDevam edilsin mi?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                self._kill_switch_toggle.blockSignals(True)
                self._kill_switch_toggle.setChecked(False)
                self._kill_switch_toggle.blockSignals(False)
                return
            if self._bridge and self._bridge.available and self._bridge.safety:
                try:
                    self._bridge.safety.activate_kill_switch(reason="settings_toggle")
                    ToastManager.instance().show(
                        "Kill Switch aktif edildi.", level="warning"
                    )
                except Exception as exc:  # noqa: BLE001
                    ToastManager.instance().show(
                        f"Kill switch hatası: {exc}", level="error"
                    )
        else:
            if self._bridge and self._bridge.available and self._bridge.safety:
                try:
                    self._bridge.safety.deactivate_kill_switch(user_confirmation=True)
                    ToastManager.instance().show(
                        "Kill Switch pasif edildi.", level="success"
                    )
                except Exception as exc:  # noqa: BLE001
                    ToastManager.instance().show(
                        f"Kill switch deaktif hatası: {exc}", level="error"
                    )

    # ------------------------------------------------------------------
    # Güncelleme kontrolü
    # ------------------------------------------------------------------
    @Slot()
    def _on_check_update_clicked(self) -> None:
        """Manuel "Şimdi Kontrol Et" tıklaması."""
        from app.services.updater import UpdateChecker

        self._update_check_btn.setEnabled(False)
        self._update_check_btn.setText("Kontrol ediliyor…")
        ToastManager.instance().show("Güncelleme kontrol ediliyor…", level="info")

        if self._bridge and self._bridge.available:
            self._bridge.run_async(
                lambda: UpdateChecker().check_for_updates(),
                on_success=self._on_update_info_ready,
                on_error=self._on_update_check_error,
            )
        else:
            self._on_update_check_error(RuntimeError("Backend hazır değil"))

    def _on_update_info_ready(self, info) -> None:
        from app.__version__ import __version__ as _APP_VERSION

        self._update_check_btn.setEnabled(True)
        self._update_check_btn.setText("🔄 Şimdi Kontrol Et")

        if info is None:
            self._update_latest_lbl.setText("Bağlantı hatası — daha sonra dene")
            self._update_latest_lbl.setStyleSheet("color: #C62828;")
            ToastManager.instance().show("Güncelleme sunucusuna ulaşılamadı.", level="error")
            return

        self._update_info = info
        self._update_latest_lbl.setText(info.latest_version)
        if info.published_at is not None:
            self._update_published_lbl.setText(info.published_at.strftime("%Y-%m-%d %H:%M"))
        if info.release_notes:
            self._update_notes.setMarkdown(info.release_notes)

        if info.is_newer:
            self._update_latest_lbl.setStyleSheet("color: #2E7D32; font-weight: 600;")
            self._update_download_btn.setEnabled(True)
            self._update_download_btn.setText(f"⬇ İndir & Kur ({info.latest_version})")
            ToastManager.instance().show(
                f"Yeni sürüm: {info.latest_version}", level="success", duration_ms=6000
            )
        else:
            self._update_latest_lbl.setStyleSheet("color: gray;")
            self._update_download_btn.setEnabled(False)
            self._update_download_btn.setText("⬇ Güncel sürüm — yükleme gerekmez")
            ToastManager.instance().show(
                f"En son sürüm zaten kurulu ({_APP_VERSION}).", level="info"
            )

    def _on_update_check_error(self, exc) -> None:
        self._update_check_btn.setEnabled(True)
        self._update_check_btn.setText("🔄 Şimdi Kontrol Et")
        self._update_latest_lbl.setText("Hata")
        self._update_latest_lbl.setStyleSheet("color: #C62828;")
        logger.warning("Update check hata: {}", exc)
        ToastManager.instance().show(f"Güncelleme kontrolü başarısız: {exc}", level="error")

    @Slot()
    def _on_download_update_clicked(self) -> None:
        """İndir & Kur akışı."""
        info = self._update_info
        if info is None or not info.is_newer or not info.download_url:
            return

        confirm = QMessageBox.question(
            self,
            "Güncelleme",
            f"Sürüm {info.latest_version} indirilecek (~{int((info.asset_size_bytes or 0)/1024/1024)} MB).\n"
            f"İndirme tamamlanınca uygulama kapanıp installer açılacak.\n\nDevam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        from app.services.updater import UpdateChecker

        self._update_download_btn.setEnabled(False)
        self._update_download_btn.setText("İndiriliyor… %0")
        ToastManager.instance().show(
            f"{info.latest_version} indiriliyor — birkaç dakika sürebilir.",
            level="info", duration_ms=6000,
        )

        def _progress(downloaded: int, total: int) -> None:
            if total > 0:
                pct = int(downloaded * 100 / total)
                # Qt thread'i değil; sadece son değeri sakla
                self._download_progress_pct = pct

        async def _do():
            return await UpdateChecker().download(info, progress=_progress)

        if self._bridge and self._bridge.available:
            self._bridge.run_async(
                _do,
                on_success=self._on_download_complete,
                on_error=self._on_download_error,
            )

    def _on_download_complete(self, installer_path) -> None:
        from app.services.updater import UpdateChecker

        self._downloaded_installer = installer_path
        self._update_download_btn.setText("✓ İndirildi — Kurulum başlatılıyor")
        ToastManager.instance().show(
            "İndirme tamam — installer açılıyor, uygulama kapanacak.",
            level="success", duration_ms=4000,
        )
        # Kısa bir gecikme ile installer başlat (toast görünsün)
        from PySide6.QtCore import QTimer

        def _launch():
            try:
                UpdateChecker.install(installer_path)
            except Exception as exc:  # noqa: BLE001
                ToastManager.instance().show(
                    f"Installer başlatılamadı: {exc}", level="error"
                )
                self._update_download_btn.setEnabled(True)
                self._update_download_btn.setText("⬇ İndir & Kur")

        QTimer.singleShot(1500, _launch)

    def _on_download_error(self, exc) -> None:
        self._update_download_btn.setEnabled(True)
        self._update_download_btn.setText("⬇ İndir & Kur")
        ToastManager.instance().show(f"İndirme hatası: {exc}", level="error", duration_ms=6000)
        logger.warning("Update download hata: {}", exc)

    # ------------------------------------------------------------------
    # Debug helper — palette farkını görsel olarak kontrol etmek için ileride
    # ------------------------------------------------------------------
    @staticmethod
    def _qcolor_for(hex_str: str) -> QColor:
        return QColor(hex_str)

    @staticmethod
    def _palette_window_color(palette: QPalette) -> QColor:
        return palette.color(QPalette.ColorRole.Window)
