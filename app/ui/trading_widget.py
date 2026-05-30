"""İşlem & Otomasyon ekranı — Faz 3 ilk versiyon (placeholder veri).

Doküman §7 (İşlem Modları, Otomasyon ve Aracı Kurum) referans alınmıştır.

Yapı:
- Üstte trading mode seçici (``QComboBox``) + Türkçe açıklama metni.
  Faz 4 modları (``semi_auto``, ``full_auto``) **devre dışı** + "Yakında - Faz 4"
  rozeti ile gösterilir.
- Risk eşiği slider (0-100) + açıklama metni.
- Sağ üstte BÜYÜK KIRMIZI KILL SWITCH — onay diyaloğu ile.
- "Bekleyen Öneri Onayları" listesi — her satırda "Onayla" / "Reddet" butonu.
- "Manuel-Eşli İşlemi İşaretle" paneli (sadece ``manual_parallel`` modda görünür):
  pending recommendation → gerçek fiyat + adet + komisyon → mark_applied.
- Safety durumu kartı: kill_switch_active, daily_trade_count/limit,
  drawdown_pct/max.
- Bot dinleme istatistiği (``bot_follow_rate``).

Faz 3 TODO (backend bağlantısı):
    # - AccountService.get_account() → trading_mode, risk_threshold
    # - AccountService.set_trading_mode(account_id, mode)
    # - AccountService.set_risk_threshold(account_id, value)
    # - ManualParallelService.list_pending_recommendations(account_id)
    # - ManualParallelService.mark_applied(marker) / mark_skipped
    # - ManualParallelService.get_follow_rate(account_id)
    # - SafetyEngine.get_state(account_id) / activate_kill_switch
    # - AutoGate.decide(...) — Faz 4
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.trading.execution_modes import (
    MODE_REGISTRY,
    TradingMode,
    is_active_in_current_phase,
)
from app.ui._format import fmt_money, fmt_pct
from app.ui.components import HelpBanner, MetricCard, ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:  # pragma: no cover
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False


# ---------------------------------------------------------------------------
# Türkçe etiketler
# ---------------------------------------------------------------------------

MODE_LABEL_TR: dict[TradingMode, str] = {
    TradingMode.MANUAL_PARALLEL: "Manuel-Eşli (paralel)",
    TradingMode.SEMI_AUTO: "Yarı-Otomatik",
    TradingMode.FULL_AUTO: "Tam Otomatik",
    TradingMode.PAPER: "Sanal (Paper)",
}


# ---------------------------------------------------------------------------
# Placeholder view-model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PendingRecRow:
    """``PendingRecommendationView`` ile birebir eşlenecek."""

    rec_id: int
    ticker: str
    action: str          # 'BUY' | 'SELL' | 'HOLD'
    timeframe: str
    confidence: float
    target_price: Optional[Decimal]
    generated_at: datetime
    summary: str


def _sample_pending() -> list[PendingRecRow]:
    now = datetime.utcnow()
    return [
        PendingRecRow(101, "THYAO", "BUY", "short", 78.0, Decimal("275"), now, "RSI dipte, hacim yükseliyor."),
        PendingRecRow(102, "ASELS", "BUY", "mid", 71.5, Decimal("102"), now, "EMA çaprazlaması + pozitif haber."),
        PendingRecRow(103, "GARAN", "SELL", "short", 65.0, None, now, "Direnç bölgesinde tepe formasyonu."),
    ]


# ---------------------------------------------------------------------------
# Yardımcı widget'lar
# ---------------------------------------------------------------------------


def _action_badge(action: str) -> QLabel:
    palette = {
        "BUY": ("#2E7D32", "AL"),
        "SELL": ("#C62828", "SAT"),
        "HOLD": ("#9E9E9E", "BEKLE"),
    }
    bg, txt = palette.get(action, ("#9E9E9E", action))
    lbl = QLabel(txt)
    lbl.setStyleSheet(
        f"background-color: {bg}; color: white; "
        f"border-radius: 6px; padding: 2px 10px; font-weight: 700;"
    )
    return lbl


def _phase4_badge() -> QLabel:
    lbl = QLabel("Yakında - Faz 4")
    lbl.setStyleSheet(
        "background-color: #F57C00; color: white; "
        "border-radius: 6px; padding: 2px 8px; font-weight: 600;"
    )
    return lbl


class KillSwitchButton(QPushButton):
    """Büyük kırmızı acil durdurma butonu.

    Onay diyaloğu ile çalışır — yanlışlıkla tıklamayı önler.
    Sinyal: ``activated`` (onaydan sonra).
    """

    activated = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("KILL SWITCH", parent)
        self.setMinimumHeight(64)
        self.setMinimumWidth(180)
        self.setStyleSheet(
            "QPushButton { background-color: #C62828; color: white; "
            "font-size: 16pt; font-weight: 800; border-radius: 10px; "
            "padding: 0 18px; }"
            "QPushButton:hover { background-color: #B71C1C; }"
            "QPushButton:pressed { background-color: #8B0000; }"
        )
        if QTAWESOME_AVAILABLE:
            try:
                self.setIcon(qta.icon("fa5s.exclamation-triangle", color="white"))
            except Exception:  # noqa: BLE001
                pass
        self.clicked.connect(self._confirm_and_emit)

    @Slot()
    def _confirm_and_emit(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Acil Durdurma",
            "TÜM otomatik işlemleri DURDURMAK üzeresin.\n\n"
            "• Açık otomatik emir akışı durur.\n"
            "• Tekrar açılana kadar bot emir gönderemez.\n\n"
            "Devam edilsin mi?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            # SafetyEngine.activate_kill_switch — TradingWidget veya MainWindow
            # 'activated' sinyalini yakalar ve gerçek aktivasyonu yapar.
            self.activated.emit()


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class TradingWidget(QWidget):
    """İşlem & Otomasyon ekranı (Faz 3 — placeholder data ile)."""

    #: Trading mode değişimi — Faz 4'te AccountService bağlanacak.
    trading_mode_changed = Signal(str)
    #: Risk eşiği değişimi.
    risk_threshold_changed = Signal(float)
    #: Pozisyon mutasyonu (BUY/SELL uygulandı / mark_applied başarılı oldu)
    #: → MainWindow PortfolioWidget / BudgetWidget / HistoryWidget refresh
    #: tetikler. Diğer widget'lar da bu sinyali dinleyebilir.
    position_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._pending: list[PendingRecRow] = []
        self._current_mode: TradingMode = TradingMode.MANUAL_PARALLEL
        self._build_ui()
        self._refresh_pending_list()
        self._update_manual_panel_visibility()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Üst kabuk: başlık + Kill Switch sabit, alt içerik scroll edilebilir.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 8)
        outer.setSpacing(8)

        # Başlık + Kill Switch (toolbar — sabit kalır)
        header = QHBoxLayout()
        title = QLabel("İşlem & Otomasyon")
        tf = QFont(title.font())
        tf.setPointSize(tf.pointSize() + 6)
        tf.setBold(True)
        title.setFont(tf)
        header.addWidget(title)
        header.addStretch(1)
        self._kill_switch = KillSwitchButton(self)
        self._kill_switch.activated.connect(self._on_kill_switch_activated)
        header.addWidget(self._kill_switch)
        outer.addLayout(header)

        # Onboarding HelpBanner
        outer.addWidget(
            HelpBanner(
                text=(
                    "Bot her ~10 dakikada bir yeni öneriler üretir. "
                    "Manuel-eşli modda bot sadece öneri verir; alımı/satımı sen "
                    "kendi aracı kurumunda yaparsın ve burada 'Uyguladım' diye "
                    "işaretlersin. 'Atladım' ise bu öneriyi yapmadığını kayda geçer."
                ),
                key="trading",
                parent=self,
            )
        )

        # Scroll alanı içine asıl içerik
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget(scroll)
        root = QVBoxLayout(content)
        root.setContentsMargins(0, 0, 8, 12)
        root.setSpacing(12)

        # Mod + risk eşiği satırı
        config_box = QGroupBox("İşlem Modu ve Risk Eşiği")
        config_layout = QGridLayout(config_box)
        config_layout.setHorizontalSpacing(12)
        config_layout.setVerticalSpacing(8)

        # Mod combo
        config_layout.addWidget(QLabel("İşlem Modu:"), 0, 0)
        self._mode_combo = QComboBox()
        for mode in (
            TradingMode.MANUAL_PARALLEL,
            TradingMode.PAPER,
            TradingMode.SEMI_AUTO,
            TradingMode.FULL_AUTO,
        ):
            label = MODE_LABEL_TR.get(mode, mode.value)
            self._mode_combo.addItem(label, userData=mode.value)
            if not is_active_in_current_phase(mode):
                # Disabled item — Qt'da QComboBox item enabled toggle:
                idx = self._mode_combo.count() - 1
                self._mode_combo.model().item(idx).setEnabled(False)
                self._mode_combo.setItemData(
                    idx, "Yakında - Faz 4 (aracı kurum entegrasyonu)",
                    Qt.ItemDataRole.ToolTipRole,
                )
        self._mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        config_layout.addWidget(self._mode_combo, 0, 1)

        # Faz 4 rozeti yanına
        config_layout.addWidget(_phase4_badge(), 0, 2)

        # Açıklama
        self._mode_description = QLabel("")
        self._mode_description.setWordWrap(True)
        self._mode_description.setStyleSheet("color: gray; font-style: italic;")
        config_layout.addWidget(self._mode_description, 1, 0, 1, 3)

        # Risk eşiği slider
        config_layout.addWidget(QLabel("Risk Eşiği:"), 2, 0)
        risk_row = QHBoxLayout()
        self._risk_slider = QSlider(Qt.Orientation.Horizontal)
        self._risk_slider.setRange(0, 100)
        self._risk_slider.setValue(50)
        self._risk_slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self._risk_slider.setTickInterval(10)
        self._risk_value_lbl = QLabel("50")
        self._risk_value_lbl.setMinimumWidth(30)
        self._risk_slider.valueChanged.connect(self._on_risk_slider)
        risk_row.addWidget(self._risk_slider, stretch=1)
        risk_row.addWidget(self._risk_value_lbl)
        risk_container = QWidget()
        risk_container.setLayout(risk_row)
        config_layout.addWidget(risk_container, 2, 1, 1, 2)

        risk_hint = QLabel(
            "Bu skorun ÜSTÜNDEKİ işlemler — tam otomatik modda bile — kullanıcı onayı ister."
        )
        risk_hint.setStyleSheet("color: gray; font-style: italic;")
        risk_hint.setWordWrap(True)
        config_layout.addWidget(risk_hint, 3, 0, 1, 3)

        root.addWidget(config_box)
        # İlk mode açıklamasını set et
        self._update_mode_description(TradingMode.MANUAL_PARALLEL)

        # Safety durumu + bot follow rate KPI satırı
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        # TODO: SafetyEngine.get_state → values
        self._kpi_kill = MetricCard(
            "Kill Switch", "Pasif", "Sistem normal", trend="up"
        )
        self._kpi_daily = MetricCard(
            "Günlük İşlem", "3 / 10", "Limit dolmadan", trend="neutral"
        )
        self._kpi_drawdown = MetricCard(
            "Drawdown", "—", "Maks. henüz yok", trend="neutral"
        )
        # TODO: ManualParallelService.get_follow_rate → follow_rate
        self._kpi_follow = MetricCard(
            "Bot Dinleme Oranı", "—", "Son 30 öneri", trend="neutral"
        )
        kpi_row.addWidget(self._kpi_kill)
        kpi_row.addWidget(self._kpi_daily)
        kpi_row.addWidget(self._kpi_drawdown)
        kpi_row.addWidget(self._kpi_follow)
        root.addLayout(kpi_row)

        # Alt: bekleyen öneriler + manuel-eşli işaretleme paneli
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Sol — pending list (başlıkta (?) ve Şimdi Yenile)
        pending_box = QGroupBox("Bekleyen Öneri Onayları")
        pending_layout = QVBoxLayout(pending_box)

        # Başlık ek satırı — açıklama tooltip + manuel refresh butonu
        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        help_btn = QToolButton()
        help_btn.setText("(?)")
        help_btn.setAutoRaise(True)
        help_btn.setCursor(Qt.CursorShape.WhatsThisCursor)
        help_btn.setToolTip(
            "Bot her ~10 dakikada yeni öneri üretir (Scheduler).\n"
            "Bu listede son üretilen ve henüz işaretlenmemiş öneriler bulunur.\n\n"
            "• 'Uyguladım' = işlemi kendi aracı kurumunda yaptım.\n"
            "• 'Atladım' = bu öneriyi yapmadım.\n"
            "• 'Onayla / Reddet' = Faz 4 otomatik emir akışı için yer tutucu."
        )

        def _show_help():
            QMessageBox.information(
                self,
                "Bekleyen Öneriler Hakkında",
                "Bot her ~10 dakikada yeni öneri üretir (Scheduler).\n"
                "Bu listede son üretilen ve henüz işaretlenmemiş öneriler bulunur.\n\n"
                "• 'Uyguladım' = işlemi kendi aracı kurumunda gerçekleştirdim.\n"
                "• 'Atladım' = bu öneriyi yapmadım — bot dinleme oranı düşer.\n"
                "• 'Onayla / Reddet' = Faz 4 otomatik emir akışı için yer tutucu.",
            )

        help_btn.clicked.connect(_show_help)
        title_row.addWidget(help_btn)

        title_row.addStretch(1)

        self._btn_refresh_pending = QPushButton("Şimdi Yenile")
        if QTAWESOME_AVAILABLE:
            try:
                self._btn_refresh_pending.setIcon(qta.icon("fa5s.sync"))
            except Exception:  # noqa: BLE001
                pass
        self._btn_refresh_pending.setToolTip("Bekleyen önerileri tekrar çek")
        self._btn_refresh_pending.clicked.connect(self.refresh)
        title_row.addWidget(self._btn_refresh_pending)
        pending_layout.addLayout(title_row)
        self._pending_table = QTableWidget(0, 6, pending_box)
        self._pending_table.setHorizontalHeaderLabels(
            ["Ticker", "Aksiyon", "Vade", "Güven", "Hedef", "İşlem"]
        )
        self._pending_table.verticalHeader().setVisible(False)
        self._pending_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._pending_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Tablo genişliğine yayılan kolonlar — kullanıcı pencereyi büyütünce
        # ölçeklesin. Ticker / Vade / Güven / Hedef küçük; Aksiyon, İşlem
        # rozet/buton kolonları sabit. Geri kalan boşluk son kolona yayılır.
        _ph = self._pending_table.horizontalHeader()
        _ph.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)  # Ticker
        _ph.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)  # Aksiyon
        _ph.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)  # Vade
        _ph.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)  # Güven
        _ph.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)           # Hedef
        _ph.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)           # İşlem butonları
        _ph.setStretchLastSection(True)
        self._pending_table.itemSelectionChanged.connect(self._on_pending_selection)
        pending_layout.addWidget(self._pending_table)
        splitter.addWidget(pending_box)

        # Sağ — manuel-eşli işaretleme paneli
        self._manual_panel = QGroupBox("Manuel-Eşli — Uyguladım İşaretle")
        manual_layout = QFormLayout(self._manual_panel)

        self._selected_rec_label = QLabel("Bir öneri seç…")
        self._selected_rec_label.setStyleSheet("font-weight: 700; color: gray;")
        manual_layout.addRow(self._selected_rec_label)

        self._actual_price = QDoubleSpinBox()
        self._actual_price.setRange(0.0, 1_000_000.0)
        self._actual_price.setDecimals(4)
        self._actual_price.setPrefix("₺ ")
        manual_layout.addRow("Gerçek Fiyat:", self._actual_price)

        self._actual_qty = QSpinBox()
        self._actual_qty.setRange(0, 1_000_000)
        manual_layout.addRow("Gerçek Adet:", self._actual_qty)

        self._commission = QDoubleSpinBox()
        self._commission.setRange(0.0, 10_000.0)
        self._commission.setDecimals(2)
        self._commission.setPrefix("₺ ")
        manual_layout.addRow("Komisyon:", self._commission)

        btn_row = QHBoxLayout()
        self._btn_mark_applied = QPushButton("Uyguladım")
        self._btn_mark_applied.setStyleSheet(
            "background-color: #2E7D32; color: white; font-weight: 700; padding: 6px 14px;"
        )
        self._btn_mark_applied.clicked.connect(self._on_mark_applied)
        self._btn_skip = QPushButton("Atladım")
        self._btn_skip.setStyleSheet(
            "background-color: #757575; color: white; font-weight: 700; padding: 6px 14px;"
        )
        self._btn_skip.clicked.connect(self._on_mark_skipped)
        btn_row.addWidget(self._btn_mark_applied)
        btn_row.addWidget(self._btn_skip)
        manual_layout.addRow(btn_row)

        for btn in (self._btn_mark_applied, self._btn_skip):
            btn.setEnabled(False)

        splitter.addWidget(self._manual_panel)
        splitter.setSizes([700, 380])
        root.addWidget(splitter, stretch=1)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        # Otomatik refresh — 30 saniye (önceki >60sn yerine)
        from PySide6.QtCore import QTimer  # noqa: WPS433

        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(30_000)
        self._auto_refresh_timer.timeout.connect(self.refresh)
        self._auto_refresh_timer.start()

    # ------------------------------------------------------------------
    def _refresh_pending_list(self) -> None:
        self._pending_table.setRowCount(len(self._pending))
        for ridx, rec in enumerate(self._pending):
            # Ticker
            t_item = QTableWidgetItem(rec.ticker)
            tf = QFont(t_item.font())
            tf.setBold(True)
            t_item.setFont(tf)
            t_item.setData(Qt.ItemDataRole.UserRole, rec.rec_id)
            self._pending_table.setItem(ridx, 0, t_item)

            # Action badge
            self._pending_table.setCellWidget(ridx, 1, _action_badge(rec.action))

            # Vade
            tf_map = {"short": "Kısa", "mid": "Orta", "long": "Uzun"}
            self._pending_table.setItem(
                ridx, 2, QTableWidgetItem(tf_map.get(rec.timeframe, rec.timeframe))
            )

            # Güven
            self._pending_table.setItem(
                ridx, 3, QTableWidgetItem(fmt_pct(rec.confidence, decimals=0))
            )

            # Hedef
            tgt = fmt_money(rec.target_price, "TRY") if rec.target_price else "—"
            self._pending_table.setItem(ridx, 4, QTableWidgetItem(tgt))

            # İşlem butonları (Onayla / Reddet)
            btn_widget = QWidget()
            br = QHBoxLayout(btn_widget)
            br.setContentsMargins(2, 0, 2, 0)
            br.setSpacing(4)
            approve_btn = QPushButton("Onayla")
            approve_btn.setStyleSheet("background-color: #2E7D32; color: white;")
            approve_btn.clicked.connect(
                lambda _c=False, r=rec: self._on_approve(r)
            )
            reject_btn = QPushButton("Reddet")
            reject_btn.setStyleSheet("background-color: #C62828; color: white;")
            reject_btn.clicked.connect(
                lambda _c=False, r=rec: self._on_reject(r)
            )
            br.addWidget(approve_btn)
            br.addWidget(reject_btn)
            self._pending_table.setCellWidget(ridx, 5, btn_widget)

        # Header'da Stretch + ResizeToContents karışık tanımlı — manuel
        # resizeColumnsToContents Stretch ile çakışır, çağırmıyoruz.
        self._pending_table.resizeRowsToContents()

    # ------------------------------------------------------------------
    @Slot(int)
    def _on_mode_changed(self, index: int) -> None:
        code = self._mode_combo.itemData(index)
        if not isinstance(code, str):
            return
        try:
            mode = TradingMode(code)
        except ValueError:
            return
        self._current_mode = mode
        self._update_mode_description(mode)
        self._update_manual_panel_visibility()
        self.trading_mode_changed.emit(mode.value)
        # AccountService.set_trading_mode — async
        if self._bridge and self._bridge.available and self._bridge.account_id is not None:
            bridge = self._bridge
            account_id = int(bridge.account_id)
            bridge.run_async(
                lambda: bridge.account.set_trading_mode(account_id, mode.value),
                on_success=lambda _r: ToastManager.instance().show(
                    f"Mod güncellendi: {MODE_LABEL_TR.get(mode, mode.value)}",
                    level="success",
                ),
                on_error=lambda exc: ToastManager.instance().show(
                    f"Mod değiştirilemedi: {exc}", level="error"
                ),
            )

    def _update_mode_description(self, mode: TradingMode) -> None:
        desc = MODE_REGISTRY[mode].description_tr
        self._mode_description.setText(desc)

    def _update_manual_panel_visibility(self) -> None:
        is_manual = self._current_mode == TradingMode.MANUAL_PARALLEL
        self._manual_panel.setVisible(is_manual)

    @Slot(int)
    def _on_risk_slider(self, value: int) -> None:
        """Slider hareket ederken sadece UI güncelle; DB'ye yazımı 350ms debounce."""
        from PySide6.QtCore import QTimer

        self._risk_value_lbl.setText(str(value))
        self.risk_threshold_changed.emit(float(value))

        # Debounce timer'ı lazy yarat
        if not hasattr(self, "_risk_debounce_timer"):
            self._risk_debounce_timer = QTimer(self)
            self._risk_debounce_timer.setSingleShot(True)
            self._risk_debounce_timer.timeout.connect(self._commit_risk_threshold)

        self._risk_pending_value = float(value)
        # Slider hareket halindeyken her yeni değişim önceki bekleyen kaydı erteler
        self._risk_debounce_timer.start(350)

    def _commit_risk_threshold(self) -> None:
        """Debounce sonrası: son slider değerini DB'ye yaz."""
        value = getattr(self, "_risk_pending_value", None)
        if value is None:
            return
        if self._bridge and self._bridge.available and self._bridge.account_id is not None:
            bridge = self._bridge
            account_id = int(bridge.account_id)
            bridge.run_async(
                lambda: bridge.account.set_risk_threshold(account_id, value),
                on_success=lambda _r: None,
                on_error=lambda exc: logger.debug("set_risk_threshold hatası: {}", exc),
            )

    @Slot()
    def _on_pending_selection(self) -> None:
        items = self._pending_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rec = self._pending[row]
        self._selected_rec_label.setText(
            f"Seçili: {rec.ticker} · {rec.action} · %{rec.confidence:.0f}"
        )
        self._selected_rec_label.setStyleSheet("font-weight: 700; color: #1976D2;")
        if rec.target_price:
            self._actual_price.setValue(float(rec.target_price))
        for btn in (self._btn_mark_applied, self._btn_skip):
            btn.setEnabled(True)

    @Slot()
    def _on_mark_applied(self) -> None:
        items = self._pending_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rec = self._pending[row]
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        # Manuel-eşli için bot havuzunda mid wallet'ı varsayılan olarak
        # kullanıyoruz; gerçek UI'da kullanıcı cüzdan seçer (Faz 4 enhancement).
        bridge = self._bridge
        price = Decimal(str(self._actual_price.value()))
        qty = Decimal(str(self._actual_qty.value()))
        commission = Decimal(str(self._commission.value()))
        if price <= 0 or qty <= 0:
            ToastManager.instance().show(
                "Fiyat ve adet pozitif olmalı.", level="warning"
            )
            return

        # Wallet'ı önce ensure et — bot/mid varsayılan; sonra mark_applied.
        # ManualParallelService.mark_applied → PositionService.apply_buy/sell
        # delege ediyor; PositionService apply_* artık transaction_at None
        # iken UTC now varsayar, dolayısıyla burada explicit geçmek zorunda
        # değiliz. Hata olursa toast ile bildiriyoruz.
        async def _apply_async():
            account_id = int(bridge.account_id)
            wallet_id = await bridge.wallets.get_wallet(account_id, "bot", "mid")
            return await bridge.manual.mark_applied(
                wallet_id=wallet_id,
                recommendation_id=int(rec.rec_id),
                actual_price=price,
                actual_quantity=qty,
                commission=commission,
                notes=f"UI: trading_widget mark_applied @ {datetime.now(tz=timezone.utc).isoformat()}",
            )

        def _on_applied(_result) -> None:
            self._after_pending_action(
                f"{rec.ticker} uygulandı olarak işaretlendi."
            )
            # Diğer ekranların (Portfolio, Budget, History) refresh olması için
            self.position_changed.emit()

        def _on_apply_error(exc) -> None:
            logger.warning("mark_applied hata: {}", exc)
            ToastManager.instance().show(
                f"İşaretleme hatası: {exc}", level="error", duration_ms=6000
            )

        bridge.run_async(_apply_async, on_success=_on_applied, on_error=_on_apply_error)

    @Slot()
    def _on_mark_skipped(self) -> None:
        items = self._pending_table.selectedItems()
        if not items:
            return
        row = items[0].row()
        rec = self._pending[row]
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.manual.mark_skipped(int(rec.rec_id), "UI skip"),
            on_success=lambda _r: self._after_pending_action(
                f"{rec.ticker} atlandı olarak işaretlendi."
            ),
            on_error=lambda exc: ToastManager.instance().show(
                f"Atlama hatası: {exc}", level="error"
            ),
        )

    @Slot()
    def _on_approve(self, rec: PendingRecRow) -> None:
        """Satır-bazlı "Onayla" — küçük modal ile fiyat/adet alıp mark_applied."""
        from PySide6.QtWidgets import (
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QHBoxLayout,
            QLabel,
            QVBoxLayout,
        )

        from app.ui._format import apply_tr_locale

        dlg = QDialog(self)
        dlg.setWindowTitle(f"{rec.ticker} — {rec.action} Onayı")
        dlg.setMinimumWidth(380)
        layout = QVBoxLayout(dlg)

        # Bilgi başlık
        info = QLabel(
            f"<b>{rec.ticker}</b> — {rec.action} (vade: {rec.timeframe}, güven %{rec.confidence:.0f})"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(info)

        form = QFormLayout()
        # Adet (varsayılan 1)
        qty_spin = QDoubleSpinBox()
        qty_spin.setRange(0.0001, 1_000_000)
        qty_spin.setDecimals(4)
        qty_spin.setValue(1.0)
        qty_spin.setSuffix(" lot")
        apply_tr_locale(qty_spin)
        form.addRow("Adet:", qty_spin)

        # Fiyat (target varsa onu kullan, yoksa 0)
        price_spin = QDoubleSpinBox()
        price_spin.setRange(0.0001, 10_000_000)
        price_spin.setDecimals(4)
        price_spin.setValue(float(rec.target_price) if rec.target_price else 0.0)
        price_spin.setSuffix(" ₺")
        apply_tr_locale(price_spin)
        form.addRow("Gerçek fiyat:", price_spin)

        # Komisyon
        comm_spin = QDoubleSpinBox()
        comm_spin.setRange(0.0, 100_000)
        comm_spin.setDecimals(2)
        comm_spin.setValue(0.0)
        comm_spin.setSuffix(" ₺")
        apply_tr_locale(comm_spin)
        form.addRow("Komisyon:", comm_spin)

        layout.addLayout(form)
        layout.addWidget(QLabel(
            "<i>Bot bu işlemi pozisyonuna ekleyecek. Sen gerçek alım/satımı "
            "kendi aracı kurumunda yapmış olmalısın.</i>"
        ))
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Uygula")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("İptal")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        layout.addWidget(buttons)

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        qty = Decimal(str(qty_spin.value()))
        price = Decimal(str(price_spin.value()))
        comm = Decimal(str(comm_spin.value()))
        if price <= 0:
            ToastManager.instance().show("Geçerli bir fiyat girin.", level="error")
            return

        if not self._bridge or not self._bridge.available:
            ToastManager.instance().show("Backend hazır değil.", level="error")
            return

        bridge = self._bridge
        # Varsayılan cüzdan: bot havuzu, recommendation timeframe'i
        # WalletService ile resolve edelim
        async def _do_approve():
            account_id = await bridge.ensure_account()
            wallet_id = await bridge.wallets.get_wallet(account_id, "bot", rec.timeframe)
            return await bridge.manual.mark_applied(
                wallet_id=int(wallet_id),
                recommendation_id=int(rec.rec_id),
                actual_price=price,
                actual_quantity=qty,
                commission=comm,
                notes=f"UI approve: {rec.ticker} {rec.action} {rec.timeframe}",
            )

        bridge.run_async(
            _do_approve,
            on_success=lambda _r: (
                ToastManager.instance().show(
                    f"{rec.ticker} uygulandı — pozisyonlarda görünecek.", level="success"
                ),
                self._refresh_pending_list(),
                self.position_changed.emit(),
            ),
            on_error=lambda exc: (
                ToastManager.instance().show(
                    f"Uygulanamadı: {exc}", level="error", duration_ms=6000
                ),
                logger.warning("approve hata: {}", exc),
            ),
        )

    @Slot()
    def _on_reject(self, rec: PendingRecRow) -> None:
        if not self._bridge or not self._bridge.available:
            ToastManager.instance().show(
                f"{rec.ticker} reddedildi (sadece UI).", level="info"
            )
            self._pending = [r for r in self._pending if r.rec_id != rec.rec_id]
            self._refresh_pending_list()
            return
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.manual.mark_skipped(int(rec.rec_id), "UI reject"),
            on_success=lambda _r: (
                ToastManager.instance().show(
                    f"{rec.ticker} reddedildi.", level="info"
                ),
                self._refresh_pending_list(),
            ),
            on_error=lambda exc: (
                ToastManager.instance().show(
                    f"Reddetme hatası: {exc}", level="error"
                ),
                logger.warning("mark_skipped hata: {}", exc),
            ),
        )

    @Slot()
    def _on_kill_switch_activated(self) -> None:
        # SafetyEngine.activate_kill_switch — process-yerel state
        if self._bridge and self._bridge.available and self._bridge.safety is not None:
            try:
                self._bridge.safety.activate_kill_switch(reason="user_trading_widget")
            except Exception as exc:  # noqa: BLE001
                logger.warning("activate_kill_switch hatası: {}", exc)
        self._kpi_kill.set_value("AKTİF", "Tüm otomatik durduruldu", trend="down")

    # ------------------------------------------------------------------
    # Backend refresh
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        if self._bridge.account_id is None:
            return
        account_id = int(self._bridge.account_id)
        bridge = self._bridge

        # AccountService.get_snapshot → mode + risk slider state senkronize
        bridge.run_async(
            lambda: bridge.account.get_snapshot(account_id),
            on_success=self._on_account_ready,
            on_error=self._on_refresh_error,
        )
        # Bekleyen öneriler
        bridge.run_async(
            lambda: bridge.manual.pending_recommendations(account_id),
            on_success=self._on_pending_ready,
            on_error=self._on_refresh_error,
        )
        # Bot dinleme oranı
        bridge.run_async(
            lambda: bridge.manual.bot_follow_rate(account_id),
            on_success=self._on_follow_rate_ready,
            on_error=self._on_refresh_error,
        )
        # Safety durumu
        bridge.run_async(
            lambda: bridge.safety.get_state(account_id),
            on_success=self._on_safety_ready,
            on_error=self._on_refresh_error,
        )

    def _on_account_ready(self, snapshot) -> None:
        try:
            # Mode combo + risk slider'ı DB değerleriyle senkronize et
            mode = snapshot.trading_mode
            for i in range(self._mode_combo.count()):
                if self._mode_combo.itemData(i) == mode:
                    self._mode_combo.blockSignals(True)
                    self._mode_combo.setCurrentIndex(i)
                    self._mode_combo.blockSignals(False)
                    try:
                        self._current_mode = TradingMode(mode)
                        self._update_mode_description(self._current_mode)
                        self._update_manual_panel_visibility()
                    except ValueError:
                        pass
                    break
            self._risk_slider.blockSignals(True)
            self._risk_slider.setValue(int(snapshot.risk_threshold))
            self._risk_value_lbl.setText(str(int(snapshot.risk_threshold)))
            self._risk_slider.blockSignals(False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("_on_account_ready hata: {}", exc)

    def _on_pending_ready(self, pending_views) -> None:
        try:
            self._pending = [
                PendingRecRow(
                    rec_id=p.recommendation_id,
                    ticker=p.ticker,
                    action=p.action,
                    timeframe=p.timeframe,
                    confidence=p.confidence,
                    target_price=p.target_price,
                    generated_at=p.generated_at,
                    summary=p.summary,
                )
                for p in (pending_views or [])
            ]
            self._refresh_pending_list()
        except Exception as exc:  # noqa: BLE001
            logger.debug("_on_pending_ready hata: {}", exc)

    def _on_follow_rate_ready(self, rate) -> None:
        try:
            pct = float(rate.follow_rate) * 100.0
            self._kpi_follow.set_value(
                fmt_pct(pct, decimals=0),
                f"{rate.applied}/{rate.applied + rate.skipped + rate.modified} öneri",
                trend="up" if pct >= 50 else "neutral",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_on_follow_rate_ready hata: {}", exc)

    def _on_safety_ready(self, state) -> None:
        try:
            self._kpi_kill.set_value(
                "AKTİF" if state.kill_switch_active else "Pasif",
                "Tüm otomatik durduruldu" if state.kill_switch_active else "Sistem normal",
                trend="down" if state.kill_switch_active else "up",
            )
            self._kpi_daily.set_value(
                f"{state.daily_trade_count} / {state.daily_trade_limit}",
                "Bugünkü işlem",
                trend="neutral",
            )
            dd = float(state.portfolio_drawdown_pct)
            self._kpi_drawdown.set_value(
                fmt_pct(dd),
                f"Maks. {fmt_pct(float(state.max_drawdown_pct))}",
                trend="down" if dd >= float(state.max_drawdown_pct) else "neutral",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("_on_safety_ready hata: {}", exc)

    def _on_refresh_error(self, exc) -> None:
        logger.debug("TradingWidget refresh hata: {}", exc)

    def _after_pending_action(self, message: str) -> None:
        ToastManager.instance().show(message, level="success")
        self.refresh()




__all__ = ["TradingWidget", "KillSwitchButton", "PendingRecRow"]
