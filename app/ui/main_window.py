"""Ana pencere ve sidebar navigasyon (Faz 1 → Faz 3 cilası).

Doküman §8.3 ekran listesi ve §8.4 (yan navigasyon menüsü) referans alınmıştır.

Yapı:
- Sol: daraltılabilir sidebar (``QListWidget``, ikon + etiket). Daraltıldığında
  yalnızca ikonlar kalır.
- Merkez: ``QStackedWidget`` — sidebar seçimi ile ekranlar arası geçiş.
- Üst toolbar: sidebar toggle, tema toggle, **Kill Switch (sağa sabit)**,
  ayarlar kısayolu.
- Alt status bar: bağlantı durumu, son veri güncelleme, sürüm.

Faz 3'te Portföy, Bütçe, İşlem & Otomasyon, İşlem Geçmişi ekranları aktif
edildi (placeholder veri ile).

Klavye kısayolları:
- Ctrl+1..9 → sidebar item geçişi
- Ctrl+, → Ayarlar
- Ctrl+L → tema toggle (önceden Ctrl+T'ydi; Ctrl+T artık Trading)
- Ctrl+B → sidebar daralt/genişlet
- Ctrl+Q → çıkış
- Ctrl+N → Haberler
- Ctrl+W → Takip Listesi
- Ctrl+P → Bot Önerileri
- **Ctrl+M → Portföy** (Money / portföy)
- **Ctrl+G → Bütçe** (Günlük bütçe — Ctrl+B sidebar toggle ile çakışmasın)
- **Ctrl+T → İşlem & Otomasyon** (Trading)
- **Ctrl+H → İşlem Geçmişi** (History)

Kill Switch: toolbar'ın sağ tarafında her zaman erişilebilir; onay
diyaloğu ile aktif olur. ``Ctrl+Shift+K`` kısayolu da bulunur.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import QSettings, QSize, Qt
from PySide6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QStackedWidget,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from app.ui._backend_bridge import BackendBridge, get_bridge
from app.ui.alerts_widget import AlertsWidget
from app.ui.bot_picks_widget import BotPicksWidget
from app.ui.budget_widget import BudgetWidget
from app.ui.chart_widget import ChartWidget
from app.ui.components import ToastManager
from app.ui.dashboard import DashboardWidget
from app.ui.history_widget import HistoryWidget
from app.ui.news_widget import NewsWidget
from app.ui.portfolio_widget import PortfolioWidget
from app.ui.settings_widget import SettingsWidget
from app.ui.trading_widget import KillSwitchButton, TradingWidget
from app.ui.watchlist_widget import WatchlistWidget

if TYPE_CHECKING:
    from app.ui.theme_manager import ThemeManager


# qtawesome opsiyonel — yoksa basit metin ikonları kullanılır
try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False
    logger.warning("qtawesome bulunamadı — sidebar metin-only çalışacak")


APP_VERSION = "0.1.0"


@dataclass(frozen=True)
class _NavItem:
    """Sidebar girdisi tanımı."""

    key: str
    label: str
    icon_name: str  # qtawesome ikon adı (ör. 'fa5s.tachometer-alt')
    enabled: bool  # Faz 1'de aktif mi? (Pasif olanlar 'Yakında' placeholder gösterir)


# Doküman §8.3 sırasına göre tam ekran listesi.
# Faz 3'te tüm portföy/bütçe/trading/history ekranları aktif (placeholder data ile).
NAV_ITEMS: tuple[_NavItem, ...] = (
    _NavItem("dashboard", "Dashboard", "fa5s.tachometer-alt", enabled=True),
    _NavItem("watchlist", "Takip Listesi", "fa5s.star", enabled=True),
    _NavItem("bot_picks", "Bot Önerileri", "fa5s.robot", enabled=True),
    _NavItem("chart", "Grafik", "fa5s.chart-line", enabled=True),
    _NavItem("portfolio", "Portföy", "fa5s.briefcase", enabled=True),
    _NavItem("budget", "Bütçe", "fa5s.coins", enabled=True),
    _NavItem("trading", "İşlem & Otomasyon", "fa5s.exchange-alt", enabled=True),
    _NavItem("history", "İşlem Geçmişi", "fa5s.history", enabled=True),
    _NavItem("news", "Haberler", "fa5s.newspaper", enabled=True),
    _NavItem("alerts", "Alarmlar", "fa5s.bell", enabled=True),
    _NavItem("settings", "Ayarlar", "fa5s.cog", enabled=True),
)


SIDEBAR_EXPANDED_WIDTH = 200
SIDEBAR_COLLAPSED_WIDTH = 56


class _ComingSoonWidget(QWidget):
    """Henüz implement edilmemiş ekranlar için placeholder."""

    def __init__(self, screen_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title = QLabel(screen_name)
        f = title.font()
        f.setPointSize(f.pointSize() + 8)
        f.setBold(True)
        title.setFont(f)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("Bu ekran Faz 2/3'te eklenecek.")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(hint)


class MainWindow(QMainWindow):
    """Borsa Bot ana penceresi."""

    _SETTINGS_GEOMETRY = "main_window/geometry"
    _SETTINGS_STATE = "main_window/state"
    _SETTINGS_SIDEBAR_COLLAPSED = "main_window/sidebar_collapsed"

    def __init__(
        self,
        theme_manager: "ThemeManager",
        parent: QWidget | None = None,
        bridge: BackendBridge | None = None,
    ) -> None:
        super().__init__(parent)
        self._theme_manager = theme_manager
        self._bridge = bridge if bridge is not None else get_bridge()
        self._sidebar_collapsed = False
        self._screens: dict[str, QWidget] = {}

        self.setWindowTitle("Borsa Bot")
        self.setMinimumSize(1100, 720)

        self._build_central()
        self._build_toolbar()
        self._build_status_bar()
        self._install_shortcuts()
        self._restore_window_state()

        # ToastManager'ı ana pencereye bağla — diğer widget'lar
        # ToastManager.instance().show("...") çağırınca buraya gelir.
        ToastManager.instance().attach(self)

        # Tema değişimlerini grafiğe yansıt
        self._theme_manager.theme_changed.connect(self._on_theme_changed)

        # Açılışta ilk aktif ekrana git
        self._sidebar.setCurrentRow(0)

        # Backend hazırsa hesabı garanti et (idempotent); başarılı olursa
        # tüm widget'lara bilgi ver — her widget kendi refresh'ini yapar.
        self._ensure_account_async()

    # ------------------------------------------------------------------
    # UI inşası
    # ------------------------------------------------------------------
    def _build_central(self) -> None:
        central = QWidget(self)
        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sidebar
        self._sidebar = QListWidget(central)
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setIconSize(QSize(20, 20))
        self._sidebar.setSpacing(2)
        self._sidebar.setFixedWidth(SIDEBAR_EXPANDED_WIDTH)
        self._sidebar.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._sidebar.currentRowChanged.connect(self._on_nav_changed)

        # Stack
        self._stack = QStackedWidget(central)

        # Sidebar item'ları ve bunlara hızlı erişim (rozet için)
        self._sidebar_items: dict[str, QListWidgetItem] = {}
        for nav in NAV_ITEMS:
            list_item = QListWidgetItem(nav.label)
            list_item.setData(Qt.ItemDataRole.UserRole, nav.key)
            list_item.setToolTip(nav.label if nav.enabled else f"{nav.label} (yakında)")
            if QTAWESOME_AVAILABLE:
                try:
                    list_item.setIcon(qta.icon(nav.icon_name))
                except Exception as exc:  # ikon adı sürüm değişikliğinde bozulabilir
                    logger.debug("qta.icon('{}') başarısız: {}", nav.icon_name, exc)
                    list_item.setIcon(QIcon())
            self._sidebar.addItem(list_item)
            self._sidebar_items[nav.key] = list_item

            # Ekran widget'ı
            if nav.enabled:
                widget = self._build_screen(nav.key)
            else:
                widget = _ComingSoonWidget(nav.label)
            self._screens[nav.key] = widget
            self._stack.addWidget(widget)

        layout.addWidget(self._sidebar)
        layout.addWidget(self._stack, stretch=1)
        self.setCentralWidget(central)

    def _build_screen(self, key: str) -> QWidget:
        """Aktif ekranların gerçek widget'larını üretir.

        Bridge mevcut tüm widget'lara `bridge=` parametresi ile iletilir;
        widget içinde defansif kontrol var (bridge None ise placeholder
        data ile kalır).
        """
        if key == "dashboard":
            return DashboardWidget(parent=self, bridge=self._bridge)
        if key == "chart":
            return ChartWidget(parent=self, bridge=self._bridge)
        if key == "alerts":
            return AlertsWidget(parent=self, bridge=self._bridge)
        if key == "settings":
            return SettingsWidget(self._theme_manager, parent=self, bridge=self._bridge)
        # Faz 2'de eklenen ekranlar
        if key == "watchlist":
            wl = WatchlistWidget(parent=self, bridge=self._bridge)
            # Sağ tık menüsü → Grafik aç
            wl.chart_requested.connect(self._goto_chart_for)
            # TradeAdviceDialog veya AddToWallet sonrası diğer widget'lar tazelensin
            wl.position_changed.connect(self._on_position_changed)
            return wl
        if key == "bot_picks":
            bp = BotPicksWidget(parent=self, bridge=self._bridge)
            bp.position_changed.connect(self._on_position_changed)
            return bp
        if key == "news":
            return NewsWidget(parent=self, bridge=self._bridge)
        # Faz 3'te eklenen ekranlar
        if key == "portfolio":
            pf = PortfolioWidget(parent=self, bridge=self._bridge)
            pf.chart_requested.connect(self._goto_chart_for)
            pf.position_changed.connect(self._on_position_changed)
            return pf
        if key == "budget":
            return BudgetWidget(parent=self, bridge=self._bridge)
        if key == "trading":
            tw = TradingWidget(parent=self, bridge=self._bridge)
            # Pozisyon mutasyonu → Portfolio / Budget / History refresh
            tw.position_changed.connect(self._on_position_changed)
            return tw
        if key == "history":
            return HistoryWidget(parent=self, bridge=self._bridge)
        # Buraya hiç düşmemeli — defensive fallback
        return _ComingSoonWidget(key.capitalize(), self)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Ana Araç Çubuğu", self)
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)

        # Sidebar daralt/genişlet
        toggle_sidebar = QAction("Menüyü Daralt", self)
        toggle_sidebar.setShortcut(QKeySequence("Ctrl+B"))
        toggle_sidebar.setToolTip("Yan menüyü daralt / genişlet (Ctrl+B)")
        if QTAWESOME_AVAILABLE:
            toggle_sidebar.setIcon(qta.icon("fa5s.bars"))
        toggle_sidebar.triggered.connect(self._toggle_sidebar)
        toolbar.addAction(toggle_sidebar)
        self._toggle_sidebar_action = toggle_sidebar

        toolbar.addSeparator()

        # Tema toggle — Ctrl+T artık Trading'e ayrıldı, tema için Ctrl+L (Light)
        self._theme_action = QAction("Tema Değiştir", self)
        self._theme_action.setShortcut(QKeySequence("Ctrl+L"))
        self._theme_action.setToolTip("Karanlık / aydınlık tema (Ctrl+L)")
        self._refresh_theme_icon()
        self._theme_action.triggered.connect(self._theme_manager.toggle_theme)
        toolbar.addAction(self._theme_action)

        # Esnek boşluk — sağdaki bot durumu + ayarlar action'ını sağ kenara iter
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        toolbar.addWidget(spacer)

        # BOT DURUM GÖSTERGESİ — kill switch yerine artık burada.
        # Kill switch tek kontrol noktası Ayarlar ekranındadır.
        # Yeşil "● Bot Aktif" = scheduler çalışıyor ve kill switch kapalı.
        # Kırmızı "● Bot Durduruldu" = kill switch aktif veya scheduler dursurulmuş.
        self._bot_status_label = QLabel("● Bot Aktif")
        self._bot_status_label.setStyleSheet(
            "QLabel { color: #2E7D32; font-weight: 800; font-size: 11pt; "
            "padding: 4px 12px; }"
        )
        self._bot_status_label.setToolTip(
            "Bot scheduler durumu — Yeşil: çalışıyor, Kırmızı: durduruldu.\n"
            "Durdurmak için: Ayarlar → Kill Switch."
        )
        toolbar.addWidget(self._bot_status_label)

        toolbar.addSeparator()

        # Ayarlar kısayolu
        settings_action = QAction("Ayarlar", self)
        settings_action.setShortcut(QKeySequence("Ctrl+,"))
        settings_action.setToolTip("Ayarlar (Ctrl+,)")
        if QTAWESOME_AVAILABLE:
            settings_action.setIcon(qta.icon("fa5s.cog"))
        settings_action.triggered.connect(self._goto_settings)
        toolbar.addAction(settings_action)

    def _build_status_bar(self) -> None:
        status = QStatusBar(self)
        self.setStatusBar(status)

        self._connection_label = QLabel("Bağlantı: —")
        self._last_update_label = QLabel("Son güncelleme: —")
        version_label = QLabel(f"v{APP_VERSION}")

        status.addWidget(self._connection_label)
        status.addWidget(self._last_update_label, 1)
        status.addPermanentWidget(version_label)

    def _install_shortcuts(self) -> None:
        """Ctrl+1..9 sidebar geçişleri + Faz 2 hızlı erişim + Ctrl+Q çıkış."""
        # Yalnızca ilk 9 nav item için kısayol (klavye kuralı)
        for idx in range(min(9, len(NAV_ITEMS))):
            shortcut = QShortcut(QKeySequence(f"Ctrl+{idx + 1}"), self)
            shortcut.activated.connect(lambda i=idx: self._sidebar.setCurrentRow(i))

        quit_shortcut = QShortcut(QKeySequence("Ctrl+Q"), self)
        quit_shortcut.activated.connect(self.close)

        # Faz 2 + Faz 3 hızlı erişim — anlamlı harf kısayolları.
        # Ctrl+B (sidebar toggle) ile çakışmaması için Bütçe → Ctrl+G,
        # Bot Picks → Ctrl+P, Trading → Ctrl+T.
        for key, sequence in (
            ("news", "Ctrl+N"),
            ("watchlist", "Ctrl+W"),
            ("bot_picks", "Ctrl+P"),
            ("portfolio", "Ctrl+M"),     # Money / portföy
            ("budget", "Ctrl+G"),        # Günlük bütçe (G — Ctrl+B çakışmasın)
            ("trading", "Ctrl+T"),       # Trading
            ("history", "Ctrl+H"),       # History
        ):
            sc = QShortcut(QKeySequence(sequence), self)
            sc.activated.connect(lambda k=key: self._goto_screen(k))

        # Kill Switch global kısayolu — her ekrandan tetiklenir
        kill_sc = QShortcut(QKeySequence("Ctrl+Shift+K"), self)
        kill_sc.activated.connect(self._kill_switch_btn.click)

    # ------------------------------------------------------------------
    # Davranış
    # ------------------------------------------------------------------
    def _on_nav_changed(self, row: int) -> None:
        if row < 0:
            return
        item = self._sidebar.item(row)
        if item is None:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        widget = self._screens.get(key)
        if widget is not None:
            self._stack.setCurrentWidget(widget)
            logger.debug("Ekran değişti: {}", key)

    def _toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        width = SIDEBAR_COLLAPSED_WIDTH if self._sidebar_collapsed else SIDEBAR_EXPANDED_WIDTH
        self._sidebar.setFixedWidth(width)

        # Daraltılmış modda yalnızca ikonlar — etiketleri gizle
        for i in range(self._sidebar.count()):
            item = self._sidebar.item(i)
            nav = NAV_ITEMS[i]
            item.setText("" if self._sidebar_collapsed else nav.label)

        # Toolbar action etiketi güncelle
        self._toggle_sidebar_action.setText(
            "Menüyü Genişlet" if self._sidebar_collapsed else "Menüyü Daralt"
        )

    def _goto_settings(self) -> None:
        # Ayarlar her zaman son sırada
        last_idx = len(NAV_ITEMS) - 1
        self._sidebar.setCurrentRow(last_idx)

    def _goto_screen(self, key: str) -> None:
        """Bir nav anahtarına göre sidebar'da o satırı seçer."""
        for idx, nav in enumerate(NAV_ITEMS):
            if nav.key == key:
                self._sidebar.setCurrentRow(idx)
                return
        logger.debug("Bilinmeyen ekran anahtarı: {}", key)

    def _goto_chart_for(self, ticker: str) -> None:
        """Grafik ekranına geç ve verilen ticker'ı yükle.

        Watchlist / bot picks sağ tık menüsünden çağrılır.
        """
        if not ticker:
            return
        self._goto_screen("chart")
        chart = self._screens.get("chart")
        if isinstance(chart, ChartWidget):
            try:
                chart.load_ticker(ticker)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ChartWidget.load_ticker hata: {}", exc)

    def set_bot_status(self, active: bool, reason: str = "") -> None:
        """Toolbar'daki bot durum göstergesini güncelle.

        ``active=True`` → yeşil "Bot Aktif"
        ``active=False`` → kırmızı "Bot Durduruldu" (+ tooltip neden)
        """
        if active:
            self._bot_status_label.setText("● Bot Aktif")
            self._bot_status_label.setStyleSheet(
                "QLabel { color: #2E7D32; font-weight: 800; font-size: 11pt; "
                "padding: 4px 12px; }"
            )
            self._bot_status_label.setToolTip(
                "Bot scheduler çalışıyor — fiyat, indikatör ve öneri\n"
                "döngüleri aktif. Durdurmak için: Ayarlar → Kill Switch."
            )
        else:
            self._bot_status_label.setText("● Bot Durduruldu")
            self._bot_status_label.setStyleSheet(
                "QLabel { color: #C62828; font-weight: 800; font-size: 11pt; "
                "padding: 4px 12px; }"
            )
            tip = "Bot durduruldu — otomatik işlemler devre dışı."
            if reason:
                tip += f"\nSebep: {reason}"
            tip += "\nYeniden başlatmak için: Ayarlar → Kill Switch."
            self._bot_status_label.setToolTip(tip)

    def set_update_available(self, latest_version: str | None) -> None:
        """Sidebar 'Ayarlar' item'ına 'Güncelleme ●' rozet uygula/kaldır.

        ``latest_version=None`` → rozet temizlenir.
        """
        item = self._sidebar_items.get("settings")
        if item is None:
            return
        if latest_version:
            item.setText(f"Ayarlar  ● {latest_version}")
            item.setToolTip(f"Yeni sürüm mevcut: {latest_version}")
            try:
                from PySide6.QtGui import QBrush, QColor
                item.setForeground(QBrush(QColor("#E53935")))
            except Exception:  # noqa: BLE001
                pass
        else:
            item.setText("Ayarlar")
            item.setToolTip("Ayarlar")
            try:
                from PySide6.QtGui import QBrush
                item.setForeground(QBrush())  # default
            except Exception:  # noqa: BLE001
                pass

    def _on_position_changed(self) -> None:
        """TradingWidget veya bir diyalog pozisyon mutasyonu uyguladığında
        tetiklenir. Portfolio, Budget, History ve Dashboard widget'ları
        açıksa veya görünür değilse de bir sonraki showEvent kendi refresh
        çağrılarını yapacak; biz yine de hemen güncelliyoruz ki kullanıcı
        ekran değiştirmeden taze veriyi görsün.
        """

        for key in ("portfolio", "budget", "history", "dashboard"):
            w = self._screens.get(key)
            if w is None:
                continue
            refresh = getattr(w, "refresh", None) or getattr(w, "refresh_data", None)
            if callable(refresh):
                try:
                    refresh()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("{} refresh hatası: {}", key, exc)

    def _on_global_kill_switch(self) -> None:
        """Toolbar Kill Switch tetiklendiğinde — SafetyEngine.activate +
        toast + trading ekran KPI güncellemesi.
        """
        try:
            if self._bridge and self._bridge.available and self._bridge.safety is not None:
                self._bridge.safety.activate_kill_switch(reason="user_global")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Global kill switch SafetyEngine'e gönderilemedi: {}", exc)

        ToastManager.instance().show(
            "Kill Switch aktif — tüm otomatik işlemler durduruldu.",
            level="error",
            duration_ms=5000,
        )
        trading = self._screens.get("trading")
        if isinstance(trading, TradingWidget):
            trading._on_kill_switch_activated()  # noqa: SLF001 — intentional cross-widget

    def _ensure_account_async(self) -> None:
        """Backend bridge varsa ``UserAccount`` + wallet matrisini garanti et.

        Sonuçta widget'lar yeniden çizilir (her widget kendi refresh'ini
        zaten tab geçişinde yapacak); burada yalnızca açılışta async
        warm-up yapıyoruz.
        """

        if not self._bridge or not self._bridge.available:
            return

        def _on_ready(_account_id) -> None:
            logger.info("BackendBridge.ensure_account OK: id={}", _account_id)
            # Dashboard ilk açılışta açıkça yenilensin (showEvent muhtemelen
            # zaten tetiklenmiştir ama ensure_account daha sonra döndüyse
            # ilk render boş kalmış olabilir).
            dashboard = self._screens.get("dashboard")
            if dashboard is not None and hasattr(dashboard, "refresh_data"):
                try:
                    dashboard.refresh_data()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("dashboard.refresh_data hatası: {}", exc)

        def _on_error(exc) -> None:
            logger.warning("BackendBridge.ensure_account başarısız: {}", exc)
            ToastManager.instance().show(
                f"Veritabanına bağlanılamadı: {exc}",
                level="warning",
                duration_ms=5000,
            )

        self._bridge.run_async(
            self._bridge.ensure_account, on_success=_on_ready, on_error=_on_error
        )

    def _on_theme_changed(self, effective: str) -> None:
        """Tema değişiminde icon'u ve grafiği güncelle."""
        self._refresh_theme_icon()
        chart = self._screens.get("chart")
        if isinstance(chart, ChartWidget):
            chart.set_theme(is_dark=(effective == "dark"))

    def _refresh_theme_icon(self) -> None:
        if not QTAWESOME_AVAILABLE:
            return
        # Dark mod aktifse "güneş" (light'a geç), light ise "ay" (dark'a geç)
        icon_name = "fa5s.sun" if self._theme_manager.is_dark() else "fa5s.moon"
        try:
            self._theme_action.setIcon(qta.icon(icon_name))
        except Exception as exc:
            logger.debug("Tema ikonu yüklenemedi: {}", exc)

    # ------------------------------------------------------------------
    # Pencere durumu (QSettings ile kalıcı)
    # ------------------------------------------------------------------
    def _restore_window_state(self) -> None:
        settings = QSettings()
        geometry = settings.value(self._SETTINGS_GEOMETRY)
        state = settings.value(self._SETTINGS_STATE)
        sidebar_collapsed = settings.value(self._SETTINGS_SIDEBAR_COLLAPSED, False, type=bool)

        if geometry is not None:
            self.restoreGeometry(geometry)
        if state is not None:
            self.restoreState(state)
        if sidebar_collapsed:
            self._toggle_sidebar()

    def closeEvent(self, event) -> None:  # noqa: N802 — Qt API ismi
        """Pencere boyutu/konumu ve sidebar durumunu kaydet."""
        settings = QSettings()
        settings.setValue(self._SETTINGS_GEOMETRY, self.saveGeometry())
        settings.setValue(self._SETTINGS_STATE, self.saveState())
        settings.setValue(self._SETTINGS_SIDEBAR_COLLAPSED, self._sidebar_collapsed)
        logger.info("Borsa Bot kapatılıyor — pencere durumu kaydedildi")
        super().closeEvent(event)
