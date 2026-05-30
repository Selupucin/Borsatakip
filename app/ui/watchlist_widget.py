"""Takip Listesi (Watchlist) ekranı — Faz 2 ilk versiyon (placeholder veri).

Doküman §6.2 (Kullanıcı Takip Listesi) ve §8.3 ekran listesi referans alınmıştır.

Yapı:
- Üstte: aktif liste seçici ``QComboBox`` + Yeni / Sil / Yeniden Adlandır + Hisse Ekle.
- Ana tablo: ``QTableWidget`` — Ticker, Ad, Borsa, Fiyat, Günlük %, Hacim,
  Bot Sinyali (renkli rozet + confidence), Sparkline (Faz 3 placeholder).
- Sürükle-bırak ile satır sıralama.
- Sağ tık menüsü: Sil, Grafik aç (Faz 3'te aktif), Cüzdana ekle (Faz 3'te aktif).

Faz 2'de placeholder veri kullanılır. Faz 3'te:
    # Faz 3 TODO:
    # - WatchlistService.list_watchlists() / list_items() / async fiyat çekimi
    # - DataCollector.fetch_quote(ticker) snapshot ile real-time fiyat
    # - ShortTermRecommender çıktısı 'Bot Sinyali' kolonunu doldurur
    # - QThreadPool worker ile periyodik yenileme (60sn)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import apply_tr_locale, fmt_money, fmt_pct, fmt_volume
from app.ui.components import HelpBanner, ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge

try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

POSITIVE_COLOR = "#2E7D32"   # yeşil
NEGATIVE_COLOR = "#C62828"   # kırmızı
NEUTRAL_COLOR = "#9E9E9E"    # gri

#: Bot sinyali rozet renkleri.
SIGNAL_PALETTE: dict[str, tuple[str, str, str]] = {
    # action -> (bg color, fg color, Türkçe etiket)
    "BUY": (POSITIVE_COLOR, "#FFFFFF", "AL"),
    "HOLD": (NEUTRAL_COLOR, "#FFFFFF", "BEKLE"),
    "SELL": (NEGATIVE_COLOR, "#FFFFFF", "SAT"),
}

COLUMNS = (
    "Ticker",
    "Ad",
    "Borsa",
    "Fiyat",
    "Günlük %",
    "Hacim",
    "Kısa",     # bot signal — kısa vade
    "Orta",     # bot signal — orta vade
    "Uzun",     # bot signal — uzun vade
    "Sparkline",
)

# Kolon indeksleri (kod okunabilirliği için)
COL_TICKER = 0
COL_NAME = 1
COL_EXCHANGE = 2
COL_PRICE = 3
COL_DAILY_PCT = 4
COL_VOLUME = 5
COL_SIGNAL_SHORT = 6
COL_SIGNAL_MID = 7
COL_SIGNAL_LONG = 8
COL_SPARKLINE = 9


# ---------------------------------------------------------------------------
# View-model
# ---------------------------------------------------------------------------


@dataclass(frozen=False, slots=True)
class WatchlistItemView:
    """UI satır gösterim modeli — Faz 3'te ``WatchlistService`` çıktısına eşlenir."""

    ticker: str
    name: str
    exchange: str          # 'BIST' | 'NASDAQ' | 'NYSE'
    price: float
    daily_change_pct: float
    volume: int
    bot_action: str        # 'BUY' | 'HOLD' | 'SELL' (en güncel — geriye uyumluluk)
    bot_confidence: float  # 0..100
    currency: str = "TRY"
    sparkline_data: tuple[float, ...] = ()  # Faz 3 sparkline için
    instrument_id: Optional[int] = None
    # Vade bazlı sinyaller — None ise "—" gösterilir
    bot_action_short: Optional[str] = None
    bot_confidence_short: Optional[float] = None
    bot_action_mid: Optional[str] = None
    bot_confidence_mid: Optional[float] = None
    bot_action_long: Optional[str] = None
    bot_confidence_long: Optional[float] = None


@dataclass
class _WatchlistDef:
    """Bir isimli takip listesi (Faz 3'te DB ``watchlists`` tablosuna eşlenir)."""

    name: str
    items: list[WatchlistItemView] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Placeholder veri
# ---------------------------------------------------------------------------


def _sample_watchlists() -> list[_WatchlistDef]:
    """Faz 2 placeholder — 3 isimli liste, BIST + ABD karışık 8-10 ticker."""
    long_term = _WatchlistDef(
        name="Uzun Vade",
        items=[
            WatchlistItemView(
                "THYAO", "Türk Hava Yolları", "BIST", 312.40, 1.85,
                42_180_000, "BUY", 78.0, "TRY",
            ),
            WatchlistItemView(
                "ASELS", "Aselsan", "BIST", 168.50, 2.42,
                18_240_500, "BUY", 71.0, "TRY",
            ),
            WatchlistItemView(
                "AKBNK", "Akbank", "BIST", 71.85, -0.34,
                95_300_000, "HOLD", 52.0, "TRY",
            ),
            WatchlistItemView(
                "AAPL", "Apple Inc.", "NASDAQ", 218.34, 0.65,
                51_400_000, "HOLD", 58.0, "USD",
            ),
            WatchlistItemView(
                "MSFT", "Microsoft Corp.", "NASDAQ", 412.80, 1.12,
                21_800_000, "BUY", 74.0, "USD",
            ),
        ],
    )
    dividend = _WatchlistDef(
        name="Temettü",
        items=[
            WatchlistItemView(
                "GARAN", "Garanti BBVA", "BIST", 142.30, 0.85,
                88_500_000, "BUY", 66.0, "TRY",
            ),
            WatchlistItemView(
                "TUPRS", "Tüpraş", "BIST", 198.40, -1.20,
                4_180_000, "HOLD", 49.0, "TRY",
            ),
            WatchlistItemView(
                "KO", "Coca-Cola", "NYSE", 71.20, 0.21,
                13_400_000, "HOLD", 54.0, "USD",
            ),
        ],
    )
    watchlist = _WatchlistDef(
        name="Gözlem",
        items=[
            WatchlistItemView(
                "TSLA", "Tesla Inc.", "NASDAQ", 248.75, -4.20,
                108_200_000, "SELL", 68.0, "USD",
            ),
            WatchlistItemView(
                "NVDA", "Nvidia Corp.", "NASDAQ", 135.62, -3.05,
                240_500_000, "HOLD", 51.0, "USD",
            ),
            WatchlistItemView(
                "PETKM", "Petkim", "BIST", 18.42, 3.95,
                25_800_000, "BUY", 64.0, "TRY",
            ),
        ],
    )
    return [long_term, dividend, watchlist]


# ---------------------------------------------------------------------------
# Hisse Ekle diyaloğu
# ---------------------------------------------------------------------------


class _AddTickerDialog(QDialog):
    """Hisse ekleme diyaloğu — üstte arama kutusu, altta filtrelenen liste."""

    def __init__(
        self,
        parent: QWidget | None = None,
        available_instruments: list[tuple[str, str, str]] | None = None,
    ) -> None:
        """available_instruments: list of (ticker, name, exchange) tuples."""
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import (
            QAbstractItemView,
            QListWidget,
            QListWidgetItem,
        )

        super().__init__(parent)
        self.setWindowTitle("Hisse Ekle")
        self.setMinimumSize(520, 480)

        self._available = available_instruments or []
        self._selected: tuple[str, str] | None = None  # (ticker, exchange)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # Üst başlık
        title = QLabel(f"DB'de {len(self._available)} hisse mevcut. Aramak için yazmaya başlayın.")
        title.setStyleSheet("color: gray;")
        title.setWordWrap(True)
        layout.addWidget(title)

        # Arama kutusu
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Ticker veya ad ile ara (örn. THYAO, Apple, türk hava)")
        self._search_input.setClearButtonEnabled(True)
        # Klavye fokusu doğrudan input'a
        self._search_input.setFocus()
        layout.addWidget(self._search_input)

        # Sonuç sayısı etiketi
        self._count_label = QLabel("")
        self._count_label.setStyleSheet("color: gray; font-size: 11px;")
        layout.addWidget(self._count_label)

        # Liste
        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setAlternatingRowColors(True)
        self._list.setUniformItemSizes(True)
        layout.addWidget(self._list, stretch=1)

        # Liste doldur
        for ticker, name, exchange in self._available:
            display = f"{ticker:<8}  {name or '—'}    [{exchange}]"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, (ticker, exchange))
            self._list.addItem(item)

        self._update_count(len(self._available), len(self._available))

        # Olaylar
        self._search_input.textChanged.connect(self._on_search_changed)
        self._list.itemDoubleClicked.connect(lambda *_: self._accept_if_selected())

        # Butonlar
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Ekle")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("İptal")
        buttons.accepted.connect(self._accept_if_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # İlk satırı seç
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    @Slot(str)
    def _on_search_changed(self, text: str) -> None:
        """Arama metnine göre liste itemlarını filtrele (case-insensitive, substring)."""
        q = (text or "").strip().casefold()
        visible = 0
        for i in range(self._list.count()):
            item = self._list.item(i)
            if not q:
                item.setHidden(False)
                visible += 1
                continue
            hay = item.text().casefold()
            match = q in hay
            item.setHidden(not match)
            if match:
                visible += 1
        self._update_count(visible, self._list.count())

        # İlk görünür satırı seç (yoksa hiçbir şey)
        for i in range(self._list.count()):
            if not self._list.item(i).isHidden():
                self._list.setCurrentRow(i)
                break

    def _update_count(self, visible: int, total: int) -> None:
        if visible == total:
            self._count_label.setText(f"{total} hisse")
        else:
            self._count_label.setText(f"{visible} / {total} hisse eşleşti")

    def _accept_if_selected(self) -> None:
        item = self._list.currentItem()
        if item is None or item.isHidden():
            return
        data = item.data(0x0100)  # UserRole
        if not data:
            return
        ticker, exchange = data
        self._selected = (str(ticker).upper(), str(exchange))
        self.accept()

    def result_data(self) -> Optional[tuple[str, str]]:
        return self._selected


# ---------------------------------------------------------------------------
# Cüzdana ekle diyaloğu
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _WalletChoice:
    """Combobox userData için cüzdan tanımı."""

    wallet_id: int
    pool: str
    timeframe: str
    label: str


class _AddToWalletDialog(QDialog):
    """Bir tickerı 6 cüzdandan birine BUY/SELL olarak yaz.

    Faz 3 - Watchlist sağ tık menüsünden açılır. PositionService.apply_buy /
    apply_sell çağrılarına uygun girdileri toplar.
    """

    POOL_LABEL_TR = {"bot": "Bot", "user": "Ben"}
    TIMEFRAME_LABEL_TR = {"short": "Kısa", "mid": "Orta", "long": "Uzun"}

    def __init__(
        self,
        ticker: str,
        wallets: list[_WalletChoice],
        default_price: float = 0.0,
        default_commission_pct: float = 0.2,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Cüzdana Ekle — {ticker}")
        self.setMinimumWidth(380)
        self._ticker = ticker
        self._result: Optional[dict] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 10)
        root.setSpacing(10)

        form = QFormLayout()
        form.setSpacing(8)

        # Hisse (kilitli)
        ticker_lbl = QLabel(ticker)
        f = QFont(ticker_lbl.font())
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        ticker_lbl.setFont(f)
        form.addRow("Hisse:", ticker_lbl)

        # Cüzdan
        self._wallet_combo = QComboBox()
        if not wallets:
            self._wallet_combo.addItem("(Cüzdan bulunamadı)", userData=None)
            self._wallet_combo.setEnabled(False)
        else:
            for w in wallets:
                self._wallet_combo.addItem(w.label, userData=w)
        form.addRow("Cüzdan:", self._wallet_combo)

        # İşlem
        self._action_combo = QComboBox()
        self._action_combo.addItem("AL (BUY)", userData="BUY")
        self._action_combo.addItem("SAT (SELL)", userData="SELL")
        form.addRow("İşlem:", self._action_combo)

        # Adet
        self._qty_spin = QDoubleSpinBox()
        self._qty_spin.setDecimals(4)
        self._qty_spin.setMinimum(0.0001)
        self._qty_spin.setMaximum(1_000_000.0)
        self._qty_spin.setValue(1.0)
        self._qty_spin.setSingleStep(1.0)
        apply_tr_locale(self._qty_spin)
        form.addRow("Adet:", self._qty_spin)

        # Fiyat
        self._price_spin = QDoubleSpinBox()
        self._price_spin.setDecimals(4)
        self._price_spin.setMinimum(0.0001)
        self._price_spin.setMaximum(1_000_000.0)
        self._price_spin.setValue(max(0.01, float(default_price or 0.0)))
        self._price_spin.setSingleStep(0.01)
        apply_tr_locale(self._price_spin)
        form.addRow("Fiyat:", self._price_spin)

        # Komisyon
        self._comm_spin = QDoubleSpinBox()
        self._comm_spin.setDecimals(2)
        self._comm_spin.setMinimum(0.0)
        self._comm_spin.setMaximum(1_000_000.0)
        self._comm_spin.setValue(0.0)
        self._comm_spin.setSingleStep(0.5)
        self._comm_spin.setToolTip(
            f"Komisyon tutarı (varsayılan oran %{default_commission_pct})"
        )
        apply_tr_locale(self._comm_spin)
        form.addRow("Komisyon:", self._comm_spin)

        # Bot uyumlu
        self._followed_check = QCheckBox("Bot önerisine göre yapıldı")
        form.addRow("", self._followed_check)

        root.addLayout(form)

        # Toplam tahmini etiketi (canlı güncelleme)
        self._total_lbl = QLabel("")
        self._total_lbl.setStyleSheet("color: gray; font-style: italic;")
        root.addWidget(self._total_lbl)

        # Otomatik komisyon hesabı (kullanıcı değiştirmediği sürece)
        self._auto_commission = True
        self._default_commission_pct = float(default_commission_pct)

        # Bağlantılar
        self._qty_spin.valueChanged.connect(self._on_inputs_changed)
        self._price_spin.valueChanged.connect(self._on_inputs_changed)
        self._comm_spin.valueChanged.connect(self._on_user_edited_commission)
        self._action_combo.currentIndexChanged.connect(self._on_inputs_changed)
        self._on_inputs_changed()

        # Butonlar
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Uygula")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("İptal")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _on_inputs_changed(self, *_args) -> None:
        gross = self._qty_spin.value() * self._price_spin.value()
        if self._auto_commission:
            # Komisyon = gross * (pct/100)
            comm = gross * (self._default_commission_pct / 100.0)
            self._comm_spin.blockSignals(True)
            self._comm_spin.setValue(comm)
            self._comm_spin.blockSignals(False)
        action = self._action_combo.currentData() or "BUY"
        if action == "BUY":
            total = gross + self._comm_spin.value()
            self._total_lbl.setText(
                f"Toplam alış maliyeti: {fmt_money(total, 'TRY')} "
                f"(gross {fmt_money(gross)} + komisyon)"
            )
        else:
            net = gross - self._comm_spin.value()
            self._total_lbl.setText(
                f"Net satış geliri: {fmt_money(net, 'TRY')} "
                f"(gross {fmt_money(gross)} − komisyon)"
            )

    def _on_user_edited_commission(self, _value: float) -> None:
        # Kullanıcı komisyonu elle değiştirdi → otomatik hesabı kapat
        self._auto_commission = False

    def _on_accept(self) -> None:
        wallet = self._wallet_combo.currentData()
        if wallet is None:
            return
        self._result = {
            "wallet": wallet,
            "action": self._action_combo.currentData() or "BUY",
            "quantity": Decimal(str(self._qty_spin.value())),
            "price": Decimal(str(self._price_spin.value())),
            "commission": Decimal(str(self._comm_spin.value())),
            "followed_bot": self._followed_check.isChecked(),
        }
        self.accept()

    def result_data(self) -> Optional[dict]:
        return self._result


# ---------------------------------------------------------------------------
# Bot sinyali rozet hücresi
# ---------------------------------------------------------------------------


def _make_signal_widget(action: str, confidence: float) -> QWidget:
    """BUY/HOLD/SELL renkli rozet + güven yüzdesi (geniş kolon için)."""
    bg, fg, label = SIGNAL_PALETTE.get(action, SIGNAL_PALETTE["HOLD"])
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(4, 2, 4, 2)
    lay.setSpacing(6)

    badge = QLabel(label)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(
        f"background-color: {bg}; color: {fg}; "
        f"border-radius: 8px; padding: 2px 10px; font-weight: 700;"
    )
    badge.setMinimumWidth(54)

    conf_lbl = QLabel(f"%{confidence:.0f}")
    conf_lbl.setStyleSheet("color: gray;")

    lay.addWidget(badge)
    lay.addWidget(conf_lbl)
    lay.addStretch(1)
    return container


_TF_NAME_TR: dict[str, str] = {"short": "Kısa vade", "mid": "Orta vade", "long": "Uzun vade"}
_ACTION_NAME_TR: dict[str, str] = {"BUY": "AL", "SELL": "SAT", "HOLD": "BEKLE"}


def _make_mini_signal_widget(
    action: Optional[str],
    confidence: Optional[float],
    timeframe_key: str = "short",
) -> QWidget:
    """Vade kolonu için açık etiketli rozet — ▲ AL %72 / ▼ SAT %65 / ● BEKLE %50.

    ``action`` None / boş ise "Yetersiz veri" (gri italik) gösterilir.
    Tüm hücre tooltip ile hangi vade + aksiyon olduğunu anlatır.
    """
    container = QWidget()
    lay = QHBoxLayout(container)
    lay.setContentsMargins(4, 1, 4, 1)
    lay.setSpacing(4)

    tf_label_tr = _TF_NAME_TR.get(timeframe_key, timeframe_key)

    if not action:
        empty = QLabel("Yetersiz veri")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setStyleSheet("color: gray; font-style: italic; font-size: 9pt;")
        empty.setToolTip(
            f"{tf_label_tr} için yeterli teknik / sentiment verisi yok."
        )
        lay.addWidget(empty)
        lay.addStretch(1)
        return container

    # İkon + renk + Türkçe etiket
    if action == "BUY":
        symbol, color = "▲", POSITIVE_COLOR
    elif action == "SELL":
        symbol, color = "▼", NEGATIVE_COLOR
    else:
        symbol, color = "●", NEUTRAL_COLOR

    action_tr = _ACTION_NAME_TR.get(action, action)
    conf_text = f"%{confidence:.0f}" if confidence is not None else ""

    badge = QLabel(f"{symbol} {action_tr}")
    badge.setStyleSheet(
        f"color: {color}; font-weight: 800; font-size: 10pt;"
    )
    badge.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
    lay.addWidget(badge)

    if conf_text:
        conf_lbl = QLabel(conf_text)
        conf_lbl.setStyleSheet("color: gray; font-size: 9pt;")
        lay.addWidget(conf_lbl)
    lay.addStretch(1)

    # Açıklayıcı tooltip
    tooltip = (
        f"{tf_label_tr} önerisi: <b>{action_tr}</b>"
        + (f" — güven {conf_text}" if conf_text else "")
    )
    container.setToolTip(tooltip)
    badge.setToolTip(tooltip)
    return container


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class WatchlistWidget(QWidget):
    """Takip listesi yönetim ekranı (Faz 2)."""

    #: Cüzdana ekleme isteğinde (sağ tık menüsü) tetiklenir — backend
    #: bridge varsa diyalog buradan açılır + uygulanır.
    add_to_wallet_requested = Signal(str)  # ticker
    #: Grafik açma isteği — MainWindow ``_goto_chart_for(ticker)`` dinler.
    open_chart_requested = Signal(str)  # ticker
    #: Faz 3 cilası: yeni standart sinyal (MainWindow tarafından kullanılan).
    chart_requested = Signal(str)  # ticker
    #: Pozisyon mutasyonu (TradeAdviceDialog veya add_to_wallet) sonrası
    #: MainWindow Portfolio/Budget/History refresh için.
    position_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        # Backend yoksa placeholder göster; bridge varsa refresh() ezecek.
        self._lists: list[_WatchlistDef] = (
            [] if (bridge and bridge.available) else _sample_watchlists()
        )
        # Backend watchlist_id'leri (sıralı _lists ile aynı index).
        self._list_ids: list[int] = []
        self._current_idx: int = 0
        self._build_ui()
        self._refresh_list_selector()
        self._render_table()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        title = QLabel("Takip Listesi")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Onboarding ipucu
        root.addWidget(
            HelpBanner(
                text=(
                    "Bu ekran takip ettiğin hisseleri ve bot sinyallerini gösterir. "
                    "Bir hisseye <b>sağ tıklayıp</b> '<b>İşlem öner ve aç</b>' diyerek "
                    "bota bütçene göre kaç lot/adet alman gerektiğini sorabilirsin. "
                    "Kısa / Orta / Uzun kolonları ayrı vade önerileridir."
                ),
                key="watchlist",
                parent=self,
            )
        )

        # Üst kontrol barı
        top_bar = QHBoxLayout()
        top_bar.setSpacing(8)

        self._list_combo = QComboBox()
        self._list_combo.setMinimumWidth(180)
        self._list_combo.currentIndexChanged.connect(self._on_list_changed)

        self._new_btn = QPushButton("Yeni Liste")
        self._rename_btn = QPushButton("Yeniden Adlandır")
        self._delete_btn = QPushButton("Sil")
        self._add_ticker_btn = QPushButton("Hisse Ekle")

        if QTAWESOME_AVAILABLE:
            try:
                self._new_btn.setIcon(qta.icon("fa5s.plus"))
                self._rename_btn.setIcon(qta.icon("fa5s.edit"))
                self._delete_btn.setIcon(qta.icon("fa5s.trash"))
                self._add_ticker_btn.setIcon(qta.icon("fa5s.plus-circle"))
            except Exception:  # noqa: BLE001
                pass

        self._new_btn.clicked.connect(self._on_new_list)
        self._rename_btn.clicked.connect(self._on_rename_list)
        self._delete_btn.clicked.connect(self._on_delete_list)
        self._add_ticker_btn.clicked.connect(self._on_add_ticker)

        top_bar.addWidget(QLabel("Liste:"))
        top_bar.addWidget(self._list_combo)
        top_bar.addWidget(self._new_btn)
        top_bar.addWidget(self._rename_btn)
        top_bar.addWidget(self._delete_btn)
        top_bar.addStretch(1)
        top_bar.addWidget(self._add_ticker_btn)
        root.addLayout(top_bar)

        # Tablo
        self._table = QTableWidget(0, len(COLUMNS), self)
        self._table.setHorizontalHeaderLabels(list(COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)
        # Ticker / sayı kolonlarına göre sıralanabilir
        self._table.setSortingEnabled(True)

        # Sürükle-bırak sıralama (Doküman §6.2)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._table.setDragDropOverwriteMode(False)
        self._table.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._table.setDragEnabled(True)
        self._table.viewport().setAcceptDrops(True)
        self._table.setDropIndicatorShown(True)

        # Sağ tık menüsü
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        header = self._table.horizontalHeader()
        # Tüm kolonlar Interactive — kullanıcı sürükleyerek genişletebilsin.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionsMovable(True)  # kolon sırasını sürükleyip değiştirebilsin
        header.setStretchLastSection(True)  # son kolon (Sparkline) kalan alanı doldursun

        # Minimum section size — kullanıcı kolonu tamamen "yok edemez"
        header.setMinimumSectionSize(40)

        # Akıllı varsayılan kolon genişlikleri (px)
        default_widths: dict[int, int] = {
            COL_TICKER: 70,
            COL_NAME: 180,
            COL_EXCHANGE: 70,
            COL_PRICE: 100,
            COL_DAILY_PCT: 85,
            COL_VOLUME: 80,
            COL_SIGNAL_SHORT: 110,
            COL_SIGNAL_MID: 110,
            COL_SIGNAL_LONG: 110,
            COL_SPARKLINE: 100,
        }
        for col, w in default_widths.items():
            self._table.setColumnWidth(col, w)

        root.addWidget(self._table, stretch=1)

    # ------------------------------------------------------------------ render
    def _refresh_list_selector(self) -> None:
        self._list_combo.blockSignals(True)
        self._list_combo.clear()
        for wl in self._lists:
            self._list_combo.addItem(wl.name)
        if self._lists:
            self._list_combo.setCurrentIndex(self._current_idx)
        self._list_combo.blockSignals(False)
        # Liste yoksa kontrolleri kapat
        enabled = bool(self._lists)
        for btn in (self._rename_btn, self._delete_btn, self._add_ticker_btn):
            btn.setEnabled(enabled)

    def _render_table(self) -> None:
        # Sıralamayı geçici kapat ki insertRow sırasında bozulmasın
        was_sorting = self._table.isSortingEnabled()
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)
        if not self._lists:
            self._table.setSortingEnabled(was_sorting)
            return
        watchlist = self._lists[self._current_idx]

        for row, item in enumerate(watchlist.items):
            self._table.insertRow(row)

            ticker_item = QTableWidgetItem(item.ticker)
            font = QFont(ticker_item.font())
            font.setBold(True)
            ticker_item.setFont(font)
            self._table.setItem(row, 0, ticker_item)

            self._table.setItem(row, 1, QTableWidgetItem(item.name))
            self._table.setItem(row, 2, QTableWidgetItem(item.exchange))

            price_item = QTableWidgetItem(fmt_money(item.price, item.currency))
            price_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, 3, price_item)

            pct_item = QTableWidgetItem(
                fmt_pct(item.daily_change_pct, with_sign=True)
            )
            pct_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            color = self._pct_color(item.daily_change_pct)
            pct_item.setForeground(QBrush(QColor(color)))
            pct_font = QFont(pct_item.font())
            pct_font.setBold(True)
            pct_item.setFont(pct_font)
            self._table.setItem(row, 4, pct_item)

            volume_item = QTableWidgetItem(fmt_volume(item.volume))
            volume_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self._table.setItem(row, 5, volume_item)

            # Bot sinyalleri — 3 vade için ayrı kompakt rozet
            # Geri uyumluluk: vade-spesifik alan yoksa item.bot_action'ı
            # "kısa vade" varsayar (mevcut WatchlistService.list_items
            # son recommendation'ı vade ayırt etmeden dolduruyor).
            short_action = item.bot_action_short or item.bot_action or None
            short_conf = (
                item.bot_confidence_short
                if item.bot_confidence_short is not None
                else item.bot_confidence
            )
            self._table.setCellWidget(
                row, COL_SIGNAL_SHORT,
                _make_mini_signal_widget(short_action, short_conf, "short"),
            )
            self._table.setCellWidget(
                row, COL_SIGNAL_MID,
                _make_mini_signal_widget(
                    item.bot_action_mid, item.bot_confidence_mid, "mid"
                ),
            )
            self._table.setCellWidget(
                row, COL_SIGNAL_LONG,
                _make_mini_signal_widget(
                    item.bot_action_long, item.bot_confidence_long, "long"
                ),
            )

            # Sparkline placeholder (Faz 3 — pyqtgraph mini grafik)
            spark_item = QTableWidgetItem("▁▂▃▅▆▇" if item.daily_change_pct >= 0 else "▇▆▅▃▂▁")
            spark_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            spark_item.setToolTip("Sparkline grafiği Faz 4'te eklenecek.")
            self._table.setItem(row, COL_SPARKLINE, spark_item)

        self._table.resizeRowsToContents()
        # Sıralama geri aç
        self._table.setSortingEnabled(was_sorting)

    # ------------------------------------------------------------------ slots — list mgmt
    @Slot(int)
    def _on_list_changed(self, idx: int) -> None:
        if 0 <= idx < len(self._lists):
            self._current_idx = idx
            self._render_table()
            # Backend varsa içeriği tazele
            if self._bridge and self._bridge.available and self._list_ids:
                self._load_current_items()

    # ------------------------------------------------------------------ backend
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.watchlist.list_all(),
            on_success=self._on_watchlists_ready,
            on_error=self._on_refresh_error,
        )

    def _on_watchlists_ready(self, summaries) -> None:
        try:
            self._lists = [
                _WatchlistDef(name=s.name, items=[]) for s in (summaries or [])
            ]
            self._list_ids = [int(s.id) for s in (summaries or [])]
            if not self._lists:
                self._current_idx = 0
                self._refresh_list_selector()
                self._render_table()
                return
            self._current_idx = max(0, min(self._current_idx, len(self._lists) - 1))
            self._refresh_list_selector()
            # Aktif liste için items çek
            self._load_current_items()
        except Exception as exc:  # noqa: BLE001
            logger.debug("watchlist._on_watchlists_ready hata: {}", exc)

    def _load_current_items(self) -> None:
        if not self._list_ids or self._bridge is None:
            return
        wl_id = self._list_ids[self._current_idx]
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.watchlist.list_items(wl_id, with_quotes=True),
            on_success=self._on_items_ready,
            on_error=self._on_refresh_error,
        )

    def _on_items_ready(self, item_views) -> None:
        try:
            items: list[WatchlistItemView] = []
            for v in (item_views or []):
                price = float(v.current_price) if v.current_price is not None else 0.0
                items.append(
                    WatchlistItemView(
                        ticker=v.ticker,
                        name=v.name,
                        exchange=v.exchange,
                        price=price,
                        daily_change_pct=float(v.daily_change_pct or 0),
                        volume=int(v.volume or 0),
                        bot_action=v.bot_action or "HOLD",
                        bot_confidence=float(v.bot_confidence or 0),
                        currency="TRY" if v.exchange == "BIST" else "USD",
                        instrument_id=int(v.instrument_id) if getattr(v, "instrument_id", None) is not None else None,
                    )
                )
            if 0 <= self._current_idx < len(self._lists):
                self._lists[self._current_idx].items = items
                self._render_table()
                # Vade bazlı sinyalleri arka planda doldur
                self._fetch_timeframe_signals(items)
        except Exception as exc:  # noqa: BLE001
            logger.debug("watchlist._on_items_ready hata: {}", exc)

    def _fetch_timeframe_signals(self, items: list[WatchlistItemView]) -> None:
        """Her satır için 3 vade ayrı recommendation çek (raw SQL — backend bozulmasın)."""
        if not items or self._bridge is None or not self._bridge.available:
            return
        instr_ids = [it.instrument_id for it in items if it.instrument_id is not None]
        if not instr_ids:
            return
        session_factory = self._bridge.session_factory
        target_idx = self._current_idx  # snapshot — callback'te yarış olmasın

        def _fetch():
            from sqlalchemy import and_, func, select  # noqa: WPS433
            from app.db.models import Recommendation  # noqa: WPS433

            session = session_factory()
            try:
                # Her (instrument_id, timeframe) için en yeni recommendation
                subq = (
                    select(
                        Recommendation.instrument_id,
                        Recommendation.timeframe,
                        func.max(Recommendation.generated_at).label("max_gen"),
                    )
                    .where(Recommendation.instrument_id.in_(instr_ids))
                    .group_by(Recommendation.instrument_id, Recommendation.timeframe)
                    .subquery()
                )
                stmt = (
                    select(
                        Recommendation.instrument_id,
                        Recommendation.timeframe,
                        Recommendation.action,
                        Recommendation.confidence,
                    )
                    .join(
                        subq,
                        and_(
                            Recommendation.instrument_id == subq.c.instrument_id,
                            Recommendation.timeframe == subq.c.timeframe,
                            Recommendation.generated_at == subq.c.max_gen,
                        ),
                    )
                )
                rows = session.execute(stmt).all()
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            # {instrument_id: {timeframe: (action, confidence)}}
            result: dict[int, dict[str, tuple[str, float]]] = {}
            for r in rows:
                iid = int(r[0])
                tf = str(r[1] or "")
                result.setdefault(iid, {})[tf] = (
                    str(r[2] or ""),
                    float(r[3] or 0),
                )
            return result

        self._bridge.run_async(
            _fetch,
            on_success=lambda data, idx=target_idx: self._apply_timeframe_signals(idx, data),
            on_error=lambda exc: logger.debug("vade sinyali çekilemedi: {}", exc),
        )

    def _apply_timeframe_signals(self, target_idx: int, data: dict) -> None:
        """Async sonuç UI'a yansıt."""
        if not (0 <= target_idx < len(self._lists)):
            return
        if target_idx != self._current_idx:
            return  # liste değişti, çıktıyı atla
        items = self._lists[target_idx].items
        for it in items:
            if it.instrument_id is None:
                continue
            tf_map = data.get(int(it.instrument_id), {})
            if "short" in tf_map:
                it.bot_action_short, it.bot_confidence_short = tf_map["short"]
            if "mid" in tf_map:
                it.bot_action_mid, it.bot_confidence_mid = tf_map["mid"]
            if "long" in tf_map:
                it.bot_action_long, it.bot_confidence_long = tf_map["long"]
        self._render_table()

    def _on_refresh_error(self, exc) -> None:
        logger.debug("WatchlistWidget refresh hata: {}", exc)

    @Slot()
    def _on_new_list(self) -> None:
        name, ok = QInputDialog.getText(
            self, "Yeni Liste", "Liste adı:", text="Yeni Liste"
        )
        if not ok or not name.strip():
            return
        if self._bridge is None or not self._bridge.available:
            # Backend yok — sadece UI'da tut
            self._lists.append(_WatchlistDef(name=name.strip(), items=[]))
            self._current_idx = len(self._lists) - 1
            self._refresh_list_selector()
            self._render_table()
            return
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.watchlist.create(name.strip()),
            on_success=lambda _wid: (
                ToastManager.instance().show("Liste oluşturuldu.", level="success"),
                self.refresh(),
            ),
            on_error=lambda exc: ToastManager.instance().show(
                f"Liste oluşturulamadı: {exc}", level="error"
            ),
        )

    @Slot()
    def _on_rename_list(self) -> None:
        if not self._lists:
            return
        current = self._lists[self._current_idx]
        new_name, ok = QInputDialog.getText(
            self, "Yeniden Adlandır", "Yeni ad:", text=current.name
        )
        if not ok or not new_name.strip():
            return
        current.name = new_name.strip()
        self._refresh_list_selector()

    @Slot()
    def _on_delete_list(self) -> None:
        if not self._lists:
            return
        current = self._lists[self._current_idx]
        reply = QMessageBox.question(
            self,
            "Listeyi Sil",
            f"'{current.name}' listesi silinsin mi?\nBu işlem geri alınamaz.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if (
            self._bridge is None
            or not self._bridge.available
            or self._current_idx >= len(self._list_ids)
        ):
            del self._lists[self._current_idx]
            if self._list_ids and self._current_idx < len(self._list_ids):
                del self._list_ids[self._current_idx]
            self._current_idx = max(0, min(self._current_idx, len(self._lists) - 1))
            self._refresh_list_selector()
            self._render_table()
            return
        wl_id = self._list_ids[self._current_idx]
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.watchlist.delete(wl_id),
            on_success=lambda _r: (
                ToastManager.instance().show("Liste silindi.", level="success"),
                self.refresh(),
            ),
            on_error=lambda exc: ToastManager.instance().show(
                f"Silme hatası: {exc}", level="error"
            ),
        )

    @Slot()
    def _on_add_ticker(self) -> None:
        if not self._lists:
            return

        # instruments tablosundan tüm hisseleri çek (autocomplete için)
        # BackendBridge.cached_instruments 60sn cache ile sunar — dialog açılışı
        # her sefer DB'ye gitmez (Faz 4 batch 2 cilası).
        available: list[tuple[str, str, str]] = []
        if self._bridge is not None:
            try:
                available = self._bridge.cached_instruments()
            except Exception as exc:  # noqa: BLE001
                logger.debug("watchlist cached_instruments hatası: {}", exc)

        dlg = _AddTickerDialog(self, available_instruments=available)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.result_data()
        if result is None:
            return
        ticker, exchange = result
        if (
            self._bridge is None
            or not self._bridge.available
            or self._current_idx >= len(self._list_ids)
        ):
            new_item = WatchlistItemView(
                ticker=ticker,
                name=f"{ticker} (yeni)",
                exchange=exchange,
                price=0.0,
                daily_change_pct=0.0,
                volume=0,
                bot_action="HOLD",
                bot_confidence=0.0,
                currency="TRY" if exchange == "BIST" else "USD",
            )
            self._lists[self._current_idx].items.append(new_item)
            self._render_table()
            return
        wl_id = self._list_ids[self._current_idx]
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.watchlist.add_item(wl_id, ticker),
            on_success=lambda _r, t=ticker, ex=exchange: self._on_ticker_added(t, ex),
            on_error=lambda exc: ToastManager.instance().show(
                f"Eklenemedi: {exc}", level="error"
            ),
        )

    def _on_ticker_added(self, ticker: str, exchange: str) -> None:
        """Backend'e eklendi → UI'a hemen yansıt (optimistic) + arka planda senkronize."""
        from PySide6.QtCore import QTimer

        ToastManager.instance().show(f"{ticker} eklendi.", level="success")

        # Optimistic update: hemen tabloya bir placeholder satır koy
        if 0 <= self._current_idx < len(self._lists):
            already = any(it.ticker == ticker for it in self._lists[self._current_idx].items)
            if not already:
                new_item = WatchlistItemView(
                    ticker=ticker,
                    name="…",  # gerçek ad refresh'te dolar
                    exchange=exchange,
                    price=0.0,
                    daily_change_pct=0.0,
                    volume=0,
                    bot_action="HOLD",
                    bot_confidence=0.0,
                    currency="TRY" if exchange == "BIST" else "USD",
                )
                self._lists[self._current_idx].items.append(new_item)
                self._render_table()

        # Backend ile asıl senkronizasyon — Qt event loop'una postla ki çağrı
        # callback thread'den değil ana thread'den başlasın (slot queue garanti)
        QTimer.singleShot(150, self._load_current_items)

    # ------------------------------------------------------------------ context menu
    @Slot(QPoint)
    def _on_context_menu(self, pos: QPoint) -> None:
        if not self._lists:
            return
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        watchlist = self._lists[self._current_idx]
        if row >= len(watchlist.items):
            return
        item = watchlist.items[row]

        menu = QMenu(self)
        delete_action = QAction("Sil", menu)
        open_chart_action = QAction("Grafik aç", menu)
        add_wallet_action = QAction("Cüzdana ekle", menu)
        trade_advice_action = QAction("İşlem öner ve aç", menu)

        if QTAWESOME_AVAILABLE:
            try:
                delete_action.setIcon(qta.icon("fa5s.trash"))
                open_chart_action.setIcon(qta.icon("fa5s.chart-line"))
                add_wallet_action.setIcon(qta.icon("fa5s.wallet"))
                trade_advice_action.setIcon(qta.icon("fa5s.robot"))
            except Exception:  # noqa: BLE001
                pass

        delete_action.triggered.connect(lambda: self._delete_ticker_row(row))
        open_chart_action.triggered.connect(lambda: self._emit_chart_request(item.ticker))
        add_wallet_action.triggered.connect(lambda: self._open_add_to_wallet(item))
        trade_advice_action.triggered.connect(lambda: self._open_trade_advice(item))

        menu.addAction(open_chart_action)
        menu.addAction(trade_advice_action)
        menu.addAction(add_wallet_action)
        menu.addSeparator()
        menu.addAction(delete_action)

        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _open_trade_advice(self, item: WatchlistItemView) -> None:
        """Yeni TradeAdviceDialog'u aç — RiskManager.position_size çıktısı ile öneri."""
        from app.ui._trade_dialogs import _TradeAdviceDialog

        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show(
                "Backend bağlantısı yok — işlem önerisi üretilemiyor.",
                level="warning",
            )
            return

        price = Decimal(str(item.price or 0))
        if price <= 0:
            ToastManager.instance().show(
                f"{item.ticker}: güncel fiyat yok, önce listeyi tazeleyin.",
                level="warning",
            )
            return

        dlg = _TradeAdviceDialog(
            ticker=item.ticker,
            name=item.name,
            current_price=price,
            instrument_id=item.instrument_id,
            bridge=self._bridge,
            parent=self,
        )
        dlg.position_changed.connect(self.position_changed.emit)
        dlg.exec()

    def _emit_chart_request(self, ticker: str) -> None:
        """Hem yeni hem eski sinyal adlarını yay (geriye uyumluluk)."""
        self.chart_requested.emit(ticker)
        self.open_chart_requested.emit(ticker)

    def _open_add_to_wallet(self, item: WatchlistItemView) -> None:
        """Cüzdana ekle diyaloğu — backend ile gerçek BUY/SELL uygular."""
        # Bridge yoksa kullanıcıya bildir
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show(
                "Backend bağlantısı yok — cüzdan işlemi yapılamıyor.",
                level="warning",
            )
            self.add_to_wallet_requested.emit(item.ticker)
            return
        if self._bridge.account_id is None:
            ToastManager.instance().show(
                "Hesap henüz hazır değil, lütfen birkaç saniye bekleyin.",
                level="warning",
            )
            return

        bridge = self._bridge
        account_id = int(bridge.account_id)

        # Cüzdanları sync olarak çek (UI thread'i — küçük sorgu, kabul edilebilir).
        # Daha temiz yol: async ama dialog'u open etmeden önce wallet listesine ihtiyaç var.
        wallets_choices = self._load_wallet_choices_sync(account_id)
        if not wallets_choices:
            ToastManager.instance().show(
                "Cüzdan matrisi bulunamadı — Bütçe ekranından oluşturun.",
                level="warning",
            )
            return

        # Varsayılan komisyon oranı
        try:
            from app.config import settings  # noqa: WPS433

            default_comm_pct = float(getattr(settings, "default_commission_pct", 0.2))
        except Exception:  # noqa: BLE001
            default_comm_pct = 0.2

        dlg = _AddToWalletDialog(
            ticker=item.ticker,
            wallets=wallets_choices,
            default_price=float(item.price or 0.0),
            default_commission_pct=default_comm_pct,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if data is None:
            return

        # instrument_id'yi al
        instrument_id = item.instrument_id
        if instrument_id is None:
            instrument_id = self._resolve_instrument_id_sync(item.ticker)
        if instrument_id is None:
            ToastManager.instance().show(
                f"{item.ticker}: instrument bulunamadı.", level="error"
            )
            return

        wallet: _WalletChoice = data["wallet"]
        action = data["action"]
        qty = data["quantity"]
        price = data["price"]
        comm = data["commission"]
        followed_bot = data["followed_bot"]
        now = datetime.now(tz=timezone.utc)

        # Backend çağrısı (async)
        if action == "BUY":
            coro = lambda: bridge.positions.apply_buy(  # noqa: E731
                wallet_id=wallet.wallet_id,
                instrument_id=int(instrument_id),
                quantity=qty,
                price=price,
                commission=comm,
                transaction_at=now,
                followed_bot=followed_bot,
                notes="UI: watchlist → cüzdana ekle",
            )
        else:
            coro = lambda: bridge.positions.apply_sell(  # noqa: E731
                wallet_id=wallet.wallet_id,
                instrument_id=int(instrument_id),
                quantity=qty,
                price=price,
                commission=comm,
                transaction_at=now,
                followed_bot=followed_bot,
                notes="UI: watchlist → cüzdana ekle",
            )

        def _on_success(_result) -> None:
            ToastManager.instance().show(
                f"{action} {qty} {item.ticker} → {wallet.label} uygulandı.",
                level="success",
            )

        def _on_error(exc) -> None:
            ToastManager.instance().show(
                f"İşlem hatası: {exc}", level="error", duration_ms=5000
            )

        bridge.run_async(coro, on_success=_on_success, on_error=_on_error)

    def _load_wallet_choices_sync(self, account_id: int) -> list[_WalletChoice]:
        """Hesabın cüzdanlarını sync çek — küçük sorgu."""
        try:
            from sqlalchemy import select  # noqa: WPS433
            from app.db.models import Wallet  # noqa: WPS433

            session = self._bridge.session_factory()
            try:
                rows = session.execute(
                    select(Wallet.id, Wallet.pool, Wallet.timeframe)
                    .where(Wallet.account_id == account_id)
                    .order_by(Wallet.pool, Wallet.timeframe)
                ).all()
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            order = {
                ("bot", "short"): 0, ("bot", "mid"): 1, ("bot", "long"): 2,
                ("user", "short"): 3, ("user", "mid"): 4, ("user", "long"): 5,
            }
            choices: list[_WalletChoice] = []
            for r in rows:
                pool = str(r[1])
                tf = str(r[2])
                pool_label = _AddToWalletDialog.POOL_LABEL_TR.get(pool, pool)
                tf_label = _AddToWalletDialog.TIMEFRAME_LABEL_TR.get(tf, tf)
                choices.append(
                    _WalletChoice(
                        wallet_id=int(r[0]),
                        pool=pool,
                        timeframe=tf,
                        label=f"{pool_label} — {tf_label}",
                    )
                )
            choices.sort(key=lambda w: order.get((w.pool, w.timeframe), 99))
            return choices
        except Exception as exc:  # noqa: BLE001
            logger.warning("Cüzdan listesi çekilemedi: {}", exc)
            return []

    def _resolve_instrument_id_sync(self, ticker: str) -> Optional[int]:
        """Ticker → instrument_id sync lookup."""
        try:
            from sqlalchemy import select  # noqa: WPS433
            from app.db.models import Instrument  # noqa: WPS433

            session = self._bridge.session_factory()
            try:
                row = session.execute(
                    select(Instrument.id).where(Instrument.ticker == ticker.upper())
                ).first()
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            return int(row[0]) if row else None
        except Exception as exc:  # noqa: BLE001
            logger.debug("instrument_id lookup hata: {}", exc)
            return None

    def _delete_ticker_row(self, row: int) -> None:
        watchlist = self._lists[self._current_idx]
        if not (0 <= row < len(watchlist.items)):
            return
        item = watchlist.items[row]
        # Backend varsa kalıcı sil
        if (
            self._bridge
            and self._bridge.available
            and self._current_idx < len(self._list_ids)
        ):
            wl_id = self._list_ids[self._current_idx]
            bridge = self._bridge
            # ticker -> instrument_id için ek sorgu gerekiyor; basitleştirmek
            # için tickerlı remove yerine list_items'tan instrument_id bilgisini
            # tutmuyoruz. Pragmatik: instrument_id Faz 4'te WatchlistItemView'a
            # eklendiğinde kullanılır. Şu an sadece UI'dan kaldır + uyar.
            del watchlist.items[row]
            self._render_table()
            ToastManager.instance().show(
                f"{item.ticker} listeden kaldırıldı (DB güncellemesi Faz 4'te).",
                level="info",
            )
            return
        del watchlist.items[row]
        self._render_table()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _pct_color(pct: float) -> str:
        if pct > 0.01:
            return POSITIVE_COLOR
        if pct < -0.01:
            return NEGATIVE_COLOR
        return NEUTRAL_COLOR

    @staticmethod
    def _format_volume(vol: int) -> str:
        if vol >= 1_000_000_000:
            return f"{vol / 1_000_000_000:.2f}B"
        if vol >= 1_000_000:
            return f"{vol / 1_000_000:.2f}M"
        if vol >= 1_000:
            return f"{vol / 1_000:.1f}K"
        return f"{vol:,}"


__all__ = [
    "WatchlistWidget",
    "WatchlistItemView",
    "SIGNAL_PALETTE",
    "COLUMNS",
]
