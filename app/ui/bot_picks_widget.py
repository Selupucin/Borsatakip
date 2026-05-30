"""Bot Önerileri (Bot Picks) ekranı — Faz 2 ilk versiyon (placeholder veri).

Doküman §6.3 (Bot Öneri Listesi) ve §8.3 ekran listesi referans alınmıştır.

Yapı:
- Üstte vade sekmesi (``QTabWidget``): Kısa Vade, Orta Vade, Uzun Vade, Tümü.
- Her sekmede ayrı tablo: Ticker, Aksiyon, Güven, Öneri Tarihi, Öneri Fiyatı,
  Hedef, Şimdiki, Getiri %, Durum.
- Üstte global checkbox: "Sadece açık olanlar".
- Sağ tarafta istatistik kartı (her vade için ayrı):
  Toplam pick / Açık / Kapanan / Başarı oranı / Ortalama getiri / Ort. tutma günü.
- Satıra tıklayınca ``RecommendationDialog`` ile detay modal açılır.

Faz 2'de placeholder veri. Faz 3'te:
    # Faz 3 TODO:
    # - BotPicksService.list_picks(timeframe) async API
    # - BotPicksStats dataclass'ı buraya beslenecek
    # - ShortTermRecommender.summary() ile modal detay
    # - QThreadPool worker ile periyodik refresh
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QAction, QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import fmt_money, fmt_pct
from app.ui.components import HelpBanner
from app.ui.recommendation_widget import (
    ACTION_PALETTE,
    RecommendationDialog,
    RecommendationView,
)

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

#: outcome -> (bg color, fg color, Türkçe etiket)
OUTCOME_PALETTE: dict[str, tuple[str, str, str]] = {
    "open": ("#1976D2", "#FFFFFF", "Açık"),
    "hit_target": ("#2E7D32", "#FFFFFF", "Hedefe Ulaştı"),
    "stopped": ("#C62828", "#FFFFFF", "Stop"),
    "expired": ("#757575", "#FFFFFF", "Süresi Doldu"),
}

TIMEFRAME_TABS = (
    ("short", "Kısa Vade"),
    ("mid", "Orta Vade"),
    ("long", "Uzun Vade"),
    ("all", "Tümü"),
)

POSITIVE_COLOR = "#2E7D32"
NEGATIVE_COLOR = "#C62828"
NEUTRAL_COLOR = "#9E9E9E"

TIMEFRAME_TR: dict[str, str] = {
    "short": "Kısa Vade",
    "mid": "Orta Vade",
    "long": "Uzun Vade",
}


# ---------------------------------------------------------------------------
# View-model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BotPickView:
    """UI gösterim modeli — Faz 3'te ``BotPicksService`` çıktısı ile eşlenir."""

    ticker: str
    timeframe: str         # 'short' | 'mid' | 'long'
    action: str            # 'BUY' | 'HOLD' | 'SELL'
    confidence: float      # 0..100
    picked_at: datetime
    price_at_pick: Decimal
    target_price: Optional[Decimal]
    current_price: Decimal
    return_pct: float
    outcome: str           # 'open' | 'hit_target' | 'stopped' | 'expired'
    currency: str = "TRY"
    summary: str = ""      # ShortTermRecommender.summary metni (detay modal için)
    contributions: dict[str, float] = field(default_factory=dict)
    risk_level: str = "medium"
    risk_score: float = 50.0


@dataclass(frozen=True, slots=True)
class BotPicksStats:
    """Vade bazlı istatistik kartı modeli."""

    total: int
    open_count: int
    closed_count: int
    success_rate_pct: float   # 0..100
    avg_return_pct: float
    avg_hold_days: float


# ---------------------------------------------------------------------------
# Placeholder veri
# ---------------------------------------------------------------------------


def _sample_picks() -> list[BotPickView]:
    """Faz 2 placeholder — her vadede 3-5 örnek."""
    now = datetime.now(tz=timezone.utc)
    return [
        # ---------------- KISA VADE -------------------
        BotPickView(
            ticker="THYAO", timeframe="short", action="BUY", confidence=78.0,
            picked_at=now - timedelta(days=2),
            price_at_pick=Decimal("302.50"),
            target_price=Decimal("325.00"),
            current_price=Decimal("312.40"),
            return_pct=3.27,
            outcome="open",
            currency="TRY",
            summary=(
                "Kısa vade AL önerisi (güven %78). Teknik analiz pozitif "
                "(RSI=42, MACD pozitif cross, trend yukarı). Sentiment "
                "pozitif (4 haberden +0.55). Mutabakat: TV=BUY, INV=BUY. "
                "Risk: orta."
            ),
            contributions={"tech": 46.8, "sentiment": 22.5, "consensus": 7.5},
            risk_level="medium", risk_score=48.0,
        ),
        BotPickView(
            ticker="ASELS", timeframe="short", action="BUY", confidence=71.0,
            picked_at=now - timedelta(days=4),
            price_at_pick=Decimal("160.20"),
            target_price=Decimal("175.00"),
            current_price=Decimal("168.50"),
            return_pct=5.18,
            outcome="open",
            currency="TRY",
            summary=(
                "Kısa vade AL önerisi (güven %71). Yeni savunma ihalesi "
                "haberi sonrası teknik göstergeler güçlendi."
            ),
            contributions={"tech": 42.0, "sentiment": 21.3, "consensus": 7.5},
            risk_level="medium", risk_score=51.0,
        ),
        BotPickView(
            ticker="TSLA", timeframe="short", action="SELL", confidence=68.0,
            picked_at=now - timedelta(days=1),
            price_at_pick=Decimal("258.10"),
            target_price=None,
            current_price=Decimal("248.75"),
            return_pct=3.62,  # SELL pozisyonu — fiyat düşünce kazanç
            outcome="open",
            currency="USD",
            summary=(
                "Kısa vade SAT önerisi (güven %68). Üretim hedefi düşüşü "
                "olumsuz sentiment yarattı; RSI aşırı alımdan dönüyor."
            ),
            contributions={"tech": 24.0, "sentiment": 9.0, "consensus": 2.5},
            risk_level="high", risk_score=72.0,
        ),
        BotPickView(
            ticker="PETKM", timeframe="short", action="BUY", confidence=64.0,
            picked_at=now - timedelta(days=12),
            price_at_pick=Decimal("17.50"),
            target_price=Decimal("19.50"),
            current_price=Decimal("19.62"),
            return_pct=12.11,
            outcome="hit_target",
            currency="TRY",
            summary="Kısa vade AL — hedef fiyat 12 günde tutturuldu.",
            contributions={"tech": 38.4, "sentiment": 19.2, "consensus": 6.4},
            risk_level="medium", risk_score=55.0,
        ),
        BotPickView(
            ticker="KCHOL", timeframe="short", action="BUY", confidence=62.0,
            picked_at=now - timedelta(days=20),
            price_at_pick=Decimal("250.00"),
            target_price=Decimal("268.00"),
            current_price=Decimal("231.30"),
            return_pct=-7.48,
            outcome="stopped",
            currency="TRY",
            summary="Kısa vade AL — stop seviyesi vuruldu.",
            contributions={"tech": 37.2, "sentiment": 18.6, "consensus": 6.2},
            risk_level="medium", risk_score=58.0,
        ),
        # ---------------- ORTA VADE -------------------
        BotPickView(
            ticker="MSFT", timeframe="mid", action="BUY", confidence=74.0,
            picked_at=now - timedelta(days=18),
            price_at_pick=Decimal("395.10"),
            target_price=Decimal("445.00"),
            current_price=Decimal("412.80"),
            return_pct=4.48,
            outcome="open",
            currency="USD",
            summary=(
                "Orta vade AL önerisi (güven %74). AI bulut gelirleri "
                "büyümeyi destekliyor; teknik kanal yukarı."
            ),
            contributions={"tech": 29.6, "sentiment": 18.5, "consensus": 25.9},
            risk_level="low", risk_score=22.0,
        ),
        BotPickView(
            ticker="GARAN", timeframe="mid", action="BUY", confidence=66.0,
            picked_at=now - timedelta(days=35),
            price_at_pick=Decimal("128.40"),
            target_price=Decimal("155.00"),
            current_price=Decimal("142.30"),
            return_pct=10.82,
            outcome="open",
            currency="TRY",
            summary="Orta vade AL — temettü kararı ve faiz beklentisi destekliyor.",
            contributions={"tech": 26.4, "sentiment": 16.5, "consensus": 23.1},
            risk_level="medium", risk_score=42.0,
        ),
        BotPickView(
            ticker="EREGL", timeframe="mid", action="BUY", confidence=58.0,
            picked_at=now - timedelta(days=72),
            price_at_pick=Decimal("48.20"),
            target_price=Decimal("56.00"),
            current_price=Decimal("56.40"),
            return_pct=17.01,
            outcome="hit_target",
            currency="TRY",
            summary="Orta vade AL — hedef 72 günde tutturuldu.",
            contributions={"tech": 23.2, "sentiment": 14.5, "consensus": 20.3},
            risk_level="medium", risk_score=44.0,
        ),
        # ---------------- UZUN VADE -------------------
        BotPickView(
            ticker="AAPL", timeframe="long", action="HOLD", confidence=58.0,
            picked_at=now - timedelta(days=90),
            price_at_pick=Decimal("182.50"),
            target_price=Decimal("240.00"),
            current_price=Decimal("218.34"),
            return_pct=19.64,
            outcome="open",
            currency="USD",
            summary=(
                "Uzun vade BEKLE — değerleme yüksek ama AI çipi haberi "
                "uzun vadeli marjı destekleyebilir."
            ),
            contributions={"tech": 17.4, "sentiment": 11.6, "consensus": 29.0},
            risk_level="low", risk_score=24.0,
        ),
        BotPickView(
            ticker="SAHOL", timeframe="long", action="BUY", confidence=72.0,
            picked_at=now - timedelta(days=120),
            price_at_pick=Decimal("65.00"),
            target_price=Decimal("90.00"),
            current_price=Decimal("82.30"),
            return_pct=26.62,
            outcome="open",
            currency="TRY",
            summary="Uzun vade AL — pay geri alım programı + holding iskontosu azalıyor.",
            contributions={"tech": 21.6, "sentiment": 14.4, "consensus": 36.0},
            risk_level="medium", risk_score=38.0,
        ),
        BotPickView(
            ticker="TUPRS", timeframe="long", action="HOLD", confidence=52.0,
            picked_at=now - timedelta(days=210),
            price_at_pick=Decimal("210.00"),
            target_price=Decimal("245.00"),
            current_price=Decimal("198.40"),
            return_pct=-5.52,
            outcome="expired",
            currency="TRY",
            summary="Uzun vade BEKLE — süre doldu, hedefe ulaşılamadı.",
            contributions={"tech": 15.6, "sentiment": 10.4, "consensus": 26.0},
            risk_level="medium", risk_score=48.0,
        ),
    ]


def _compute_stats(picks: list[BotPickView]) -> BotPicksStats:
    """Picks listesinden istatistik kartı modeli üretir."""
    if not picks:
        return BotPicksStats(0, 0, 0, 0.0, 0.0, 0.0)

    total = len(picks)
    open_count = sum(1 for p in picks if p.outcome == "open")
    closed = [p for p in picks if p.outcome != "open"]
    closed_count = len(closed)
    success = sum(1 for p in closed if p.outcome == "hit_target")
    success_rate = (success / closed_count * 100.0) if closed_count else 0.0
    avg_return = (
        sum(p.return_pct for p in closed) / closed_count if closed_count else 0.0
    )
    # Tutma günü hesabı: kapananlar için picked_at -> şimdi (kapanış tarihi
    # placeholder'da yok, Faz 3'te closed_at gelecek)
    now = datetime.now(tz=timezone.utc)
    avg_hold = (
        sum((now - p.picked_at).days for p in closed) / closed_count
        if closed_count
        else 0.0
    )
    return BotPicksStats(
        total=total,
        open_count=open_count,
        closed_count=closed_count,
        success_rate_pct=success_rate,
        avg_return_pct=avg_return,
        avg_hold_days=avg_hold,
    )


# ---------------------------------------------------------------------------
# Tek sekmenin içeriği: tablo + istatistik panel
# ---------------------------------------------------------------------------


class _TimeframeTabContent(QWidget):
    """Bir vade sekmesinin içeriği: solda tablo, sağda istatistik kartı."""

    COLUMNS = (
        "Ticker", "Aksiyon", "Güven", "Öneri Tarihi", "Öneri Fiyatı",
        "Hedef", "Şimdiki", "Getiri %", "Durum",
    )

    #: BotPicksWidget tarafından yakalanan — pozisyon mutasyonu sonrası
    #: MainWindow Portfolio/Budget/History refresh tetiklenecek.
    trade_applied = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._all_picks: list[BotPickView] = []
        self._open_only: bool = False
        self._build_ui()

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Tablo
        self._table = QTableWidget(0, len(self.COLUMNS), splitter)
        self._table.setHorizontalHeaderLabels(list(self.COLUMNS))
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.setSortingEnabled(True)
        self._table.itemDoubleClicked.connect(self._on_row_double_clicked)
        # Sağ tık menüsü — "İşlem öner ve aç" girdisi
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)

        header = self._table.horizontalHeader()
        # Akıllı kolon dağılımı: Ticker/Aksiyon/Durum kompakt (içeriğe göre),
        # diğer sayısal kolonlar Stretch ile genişliğe yayılsın.
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        for col in range(self._table.columnCount()):
            # Index 0=Ticker, 1=Aksiyon, 8=Durum → içeriğe göre dar
            if col in (0, 1, 8):
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
            else:
                header.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)

        splitter.addWidget(self._table)

        # İstatistik kartı
        self._stats_panel = self._build_stats_panel(splitter)
        splitter.addWidget(self._stats_panel)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([800, 280])

        root.addWidget(splitter, stretch=1)

    def _build_stats_panel(self, parent: QWidget) -> QWidget:
        box = QGroupBox("Vade İstatistikleri", parent)
        box.setMinimumWidth(240)
        lay = QVBoxLayout(box)
        lay.setSpacing(10)

        self._stat_rows: dict[str, QLabel] = {}
        labels = [
            ("total", "Toplam Pick"),
            ("open", "Açık"),
            ("closed", "Kapanan"),
            ("success", "Başarı Oranı"),
            ("avg_return", "Ortalama Getiri"),
            ("avg_hold", "Ortalama Tutma"),
        ]
        for key, label in labels:
            row = QHBoxLayout()
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("color: gray;")
            value_lbl = QLabel("—")
            f = QFont(value_lbl.font())
            f.setBold(True)
            f.setPointSize(f.pointSize() + 2)
            value_lbl.setFont(f)
            value_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._stat_rows[key] = value_lbl

            row.addWidget(name_lbl)
            row.addStretch(1)
            row.addWidget(value_lbl)
            lay.addLayout(row)

        lay.addStretch(1)
        return box

    # ------------------------------------------------------------------ data
    def load_picks(self, picks: list[BotPickView]) -> None:
        self._all_picks = list(picks)
        self._render()

    def set_open_only(self, value: bool) -> None:
        self._open_only = value
        self._render()

    # ------------------------------------------------------------------ render
    def _render(self) -> None:
        filtered = [
            p for p in self._all_picks
            if not self._open_only or p.outcome == "open"
        ]

        # Sıralama event'ini geçici kapat (insertRow sırasında bozulmasın)
        self._table.setSortingEnabled(False)
        self._table.setRowCount(0)

        for row, pick in enumerate(filtered):
            self._table.insertRow(row)

            ticker_item = QTableWidgetItem(pick.ticker)
            tf = QFont(ticker_item.font())
            tf.setBold(True)
            ticker_item.setFont(tf)
            ticker_item.setData(Qt.ItemDataRole.UserRole, pick)
            self._table.setItem(row, 0, ticker_item)

            # Aksiyon rozeti
            bg, fg, label = ACTION_PALETTE.get(pick.action, ACTION_PALETTE["HOLD"])
            action_item = QTableWidgetItem(label)
            action_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            action_item.setForeground(QBrush(QColor(fg)))
            action_item.setBackground(QBrush(QColor(bg)))
            af = QFont(action_item.font())
            af.setBold(True)
            action_item.setFont(af)
            self._table.setItem(row, 1, action_item)

            # Güven
            conf_item = QTableWidgetItem(fmt_pct(pick.confidence, decimals=0))
            conf_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 2, conf_item)

            # Öneri tarihi
            date_item = QTableWidgetItem(
                pick.picked_at.astimezone().strftime("%d.%m.%Y")
            )
            self._table.setItem(row, 3, date_item)

            # Fiyatlar
            self._table.setItem(
                row, 4,
                self._right_aligned(fmt_money(pick.price_at_pick, pick.currency))
            )
            target_str = (
                fmt_money(pick.target_price, pick.currency)
                if pick.target_price is not None else "—"
            )
            self._table.setItem(row, 5, self._right_aligned(target_str))
            self._table.setItem(
                row, 6,
                self._right_aligned(fmt_money(pick.current_price, pick.currency))
            )

            # Getiri %
            ret_item = QTableWidgetItem(fmt_pct(pick.return_pct, with_sign=True))
            ret_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            ret_color = self._pct_color(pick.return_pct)
            ret_item.setForeground(QBrush(QColor(ret_color)))
            rf = QFont(ret_item.font())
            rf.setBold(True)
            ret_item.setFont(rf)
            self._table.setItem(row, 7, ret_item)

            # Durum rozeti
            o_bg, o_fg, o_label = OUTCOME_PALETTE.get(
                pick.outcome, OUTCOME_PALETTE["open"]
            )
            outcome_item = QTableWidgetItem(o_label)
            outcome_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            outcome_item.setForeground(QBrush(QColor(o_fg)))
            outcome_item.setBackground(QBrush(QColor(o_bg)))
            of = QFont(outcome_item.font())
            of.setBold(True)
            outcome_item.setFont(of)
            self._table.setItem(row, 8, outcome_item)

        self._table.setSortingEnabled(True)
        self._table.resizeRowsToContents()

        # İstatistikler — TÜM picks üzerinden, "sadece açık" filtresi
        # istatistiği bozmamalı (özellikle başarı oranı kapanlara bakar).
        stats = _compute_stats(self._all_picks)
        self._update_stats_panel(stats)

    def _update_stats_panel(self, stats: BotPicksStats) -> None:
        self._stat_rows["total"].setText(str(stats.total))
        self._stat_rows["open"].setText(str(stats.open_count))
        self._stat_rows["closed"].setText(str(stats.closed_count))

        success_lbl = self._stat_rows["success"]
        success_lbl.setText(fmt_pct(stats.success_rate_pct, decimals=1))
        # Renk: >= 60 yeşil, 40-60 nötr, <40 kırmızı
        if stats.closed_count == 0:
            success_lbl.setStyleSheet("color: gray;")
        elif stats.success_rate_pct >= 60.0:
            success_lbl.setStyleSheet(f"color: {POSITIVE_COLOR}; font-weight: 700;")
        elif stats.success_rate_pct < 40.0:
            success_lbl.setStyleSheet(f"color: {NEGATIVE_COLOR}; font-weight: 700;")
        else:
            success_lbl.setStyleSheet(f"color: {NEUTRAL_COLOR}; font-weight: 700;")

        ret_lbl = self._stat_rows["avg_return"]
        ret_lbl.setText(fmt_pct(stats.avg_return_pct, with_sign=True))
        ret_lbl.setStyleSheet(
            f"color: {self._pct_color(stats.avg_return_pct)}; font-weight: 700;"
        )

        self._stat_rows["avg_hold"].setText(f"{stats.avg_hold_days:.1f} gün")

    # ------------------------------------------------------------------ slots
    @Slot(QTableWidgetItem)
    def _on_row_double_clicked(self, item: QTableWidgetItem) -> None:
        # 0. kolondaki ticker item'ında pick datası tutuluyor
        row = item.row()
        ticker_item = self._table.item(row, 0)
        if ticker_item is None:
            return
        pick: BotPickView = ticker_item.data(Qt.ItemDataRole.UserRole)
        if pick is None:
            return

        view = RecommendationView(
            ticker=pick.ticker,
            current_price=pick.current_price,
            currency=pick.currency,
            action=pick.action,
            confidence=pick.confidence,
            timeframe=pick.timeframe,
            risk_level=pick.risk_level,
            risk_score=pick.risk_score,
            target_price=pick.target_price,
            summary=pick.summary or "(Özet bulunamadı)",
            contributions=dict(pick.contributions),
        )
        # Bridge'i parent zincirinden bul (BotPicksWidget'a iletildi)
        bridge = None
        parent = self.parent()
        while parent is not None:
            br = getattr(parent, "_bridge", None)
            if br is not None:
                bridge = br
                break
            parent = parent.parent()
        dlg = RecommendationDialog(view, parent=self, bridge=bridge)
        dlg.exec()

    @Slot(QPoint)
    def _on_context_menu(self, pos: QPoint) -> None:
        """Sağ tık → 'İşlem öner ve aç' (TradeAdviceDialog)."""
        row = self._table.rowAt(pos.y())
        if row < 0:
            return
        ticker_item = self._table.item(row, 0)
        if ticker_item is None:
            return
        pick: BotPickView = ticker_item.data(Qt.ItemDataRole.UserRole)
        if pick is None:
            return

        menu = QMenu(self)
        trade_action = QAction("İşlem öner ve aç", menu)
        try:
            if QTAWESOME_AVAILABLE:
                trade_action.setIcon(qta.icon("fa5s.robot"))
        except Exception:  # noqa: BLE001
            pass

        def _open_advice() -> None:
            self._open_trade_advice(pick)

        trade_action.triggered.connect(_open_advice)
        menu.addAction(trade_action)
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _open_trade_advice(self, pick: "BotPickView") -> None:
        """Parent chain'den bridge bulup TradeAdviceDialog aç."""
        from app.ui._trade_dialogs import _TradeAdviceDialog

        # Bridge'i parent zincirinden bul
        bridge = None
        instrument_id: Optional[int] = None
        parent = self.parent()
        while parent is not None:
            br = getattr(parent, "_bridge", None)
            if br is not None:
                bridge = br
                break
            parent = parent.parent()

        if bridge is None or not getattr(bridge, "available", False):
            from app.ui.components import ToastManager

            ToastManager.instance().show(
                "Backend bağlantısı yok — işlem önerisi üretilemiyor.",
                level="warning",
            )
            return

        # instrument_id'yi ticker'dan çöz (BotPickView'da yok)
        try:
            from sqlalchemy import select  # noqa: WPS433

            from app.db.models import Instrument  # noqa: WPS433

            session = bridge.session_factory()
            try:
                row = session.execute(
                    select(Instrument.id, Instrument.name).where(
                        Instrument.ticker == pick.ticker.upper()
                    )
                ).first()
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
            instrument_id = int(row[0]) if row else None
            name = str(row[1]) if (row and row[1]) else pick.ticker
        except Exception as exc:  # noqa: BLE001
            logger.debug("BotPicks instrument lookup hata: {}", exc)
            name = pick.ticker

        dlg = _TradeAdviceDialog(
            ticker=pick.ticker,
            name=name,
            current_price=pick.current_price,
            instrument_id=instrument_id,
            bridge=bridge,
            parent=self,
        )
        dlg.position_changed.connect(self.trade_applied.emit)
        dlg.exec()

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _right_aligned(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        return item

    @staticmethod
    def _pct_color(pct: float) -> str:
        if pct > 0.01:
            return POSITIVE_COLOR
        if pct < -0.01:
            return NEGATIVE_COLOR
        return NEUTRAL_COLOR


# ---------------------------------------------------------------------------
# Ana widget — sekmeli görünüm
# ---------------------------------------------------------------------------


class BotPicksWidget(QWidget):
    """Bot Önerileri ekranı (Faz 2)."""

    #: Sağ tık → TradeAdviceDialog → BUY uygulandığında MainWindow refresh.
    position_changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._all_picks: list[BotPickView] = []
        self._tabs: dict[str, _TimeframeTabContent] = {}
        self._build_ui()
        # Backend yoksa placeholder göster.
        if bridge is None or not bridge.available:
            self.load_picks(_sample_picks())
        else:
            # Backend varsa async çekim
            self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------ backend
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        bridge = self._bridge
        only_open = self._open_only_check.isChecked()
        # Hepsini tek çağrıda çek; sekme filtrelemesi _all_picks üzerinden.
        bridge.run_async(
            lambda: bridge.bot_picks.list_picks(
                timeframe=None, only_open=only_open, limit=200
            ),
            on_success=self._on_picks_ready,
            on_error=self._on_refresh_error,
        )

    def _on_picks_ready(self, pick_views) -> None:
        try:
            picks: list[BotPickView] = []
            for p in (pick_views or []):
                # Backend BotPickView'in current_price'ı yok (closed picks
                # için price_at_close var). Pragmatik: açık ise pick fiyatı,
                # kapalı ise close fiyatı.
                current = (
                    p.price_at_close if (p.price_at_close is not None) else p.price_at_pick
                )
                ret_pct = float(p.return_pct or 0)
                picks.append(
                    BotPickView(
                        ticker=p.ticker,
                        timeframe=p.timeframe,
                        action=p.action,
                        confidence=p.confidence,
                        picked_at=p.picked_at,
                        price_at_pick=p.price_at_pick,
                        target_price=p.target_price,
                        current_price=current,
                        return_pct=ret_pct,
                        outcome=p.outcome or ("open" if p.is_open else "expired"),
                    )
                )
            self.load_picks(picks)
        except Exception as exc:  # noqa: BLE001
            logger.debug("BotPicksWidget._on_picks_ready hata: {}", exc)

    def _on_refresh_error(self, exc) -> None:
        logger.debug("BotPicksWidget refresh hata: {}", exc)

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(10)

        # Başlık
        title = QLabel("Bot Önerileri")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Net açıklama metni — SAT önerisinin ne demek olduğu
        info = QLabel(
            "<b>📊 Bu bot ÖNERİ listesidir, portföyünüz değil.</b> "
            "<i>SAT</i> önerisi = 'Bu hisseyi şu an alma' veya "
            "'Açık pozisyonun varsa düşün' demek. "
            "Sahip olduğun pozisyonları <b>Portföy</b> ekranında görürsün."
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setWordWrap(True)
        info.setStyleSheet(
            "color: #1976D2; background-color: rgba(25,118,210,18); "
            "border-left: 3px solid #1976D2; padding: 8px 10px; "
            "border-radius: 4px;"
        )
        root.addWidget(info)

        # Dismissible HelpBanner — bir kez okunsa kapatılabilir
        root.addWidget(
            HelpBanner(
                text=(
                    "Bot tüm piyasadan ürettiği önerileri burada listeler. "
                    "Sahip olduğun hisseleri burada görmeyebilirsin; bunlar bot "
                    "perspektifinden anlık fırsat / risk değerlendirmeleridir."
                ),
                key="bot_picks",
                parent=self,
            )
        )

        # Filtre paneli (Faz 4 batch 3) — client-side filtreleme, backend bozulmaz
        filter_box = self._build_filter_bar()
        root.addWidget(filter_box)

        # Sekmeli görünüm
        self._tab_widget = QTabWidget(self)
        for key, label in TIMEFRAME_TABS:
            content = _TimeframeTabContent(self._tab_widget)
            content.trade_applied.connect(self.position_changed.emit)
            self._tabs[key] = content
            self._tab_widget.addTab(content, label)
            if QTAWESOME_AVAILABLE:
                try:
                    icon_name = {
                        "short": "fa5s.bolt",
                        "mid": "fa5s.calendar-alt",
                        "long": "fa5s.mountain",
                        "all": "fa5s.layer-group",
                    }.get(key, "fa5s.list")
                    self._tab_widget.setTabIcon(
                        self._tab_widget.count() - 1, qta.icon(icon_name)
                    )
                except Exception:  # noqa: BLE001
                    pass

        root.addWidget(self._tab_widget, stretch=1)

    # ------------------------------------------------------------------
    def _build_filter_bar(self) -> QWidget:
        """Üst filtre çubuğu: vade / aksiyon / borsa / fiyat / min güven."""
        box = QGroupBox("Filtreler")
        lay = QGridLayout(box)
        lay.setHorizontalSpacing(10)
        lay.setVerticalSpacing(6)

        # Vade
        lay.addWidget(QLabel("Vade:"), 0, 0)
        self._filter_tf = QComboBox()
        for k, v in (("all", "Tümü"), ("short", "Kısa"), ("mid", "Orta"), ("long", "Uzun")):
            self._filter_tf.addItem(v, userData=k)
        lay.addWidget(self._filter_tf, 0, 1)

        # Aksiyon
        lay.addWidget(QLabel("Aksiyon:"), 0, 2)
        self._filter_action = QComboBox()
        for k, v in (("all", "Tümü"), ("BUY", "AL"), ("SELL", "SAT"), ("HOLD", "BEKLE")):
            self._filter_action.addItem(v, userData=k)
        lay.addWidget(self._filter_action, 0, 3)

        # Borsa
        lay.addWidget(QLabel("Borsa:"), 0, 4)
        self._filter_exchange = QComboBox()
        for k, v in (
            ("all", "Tümü"),
            ("BIST", "BIST"),
            ("NASDAQ", "NASDAQ"),
            ("NYSE", "NYSE"),
        ):
            self._filter_exchange.addItem(v, userData=k)
        lay.addWidget(self._filter_exchange, 0, 5)

        # Min güven
        lay.addWidget(QLabel("Min güven %:"), 1, 0)
        self._filter_min_conf = QSpinBox()
        self._filter_min_conf.setRange(0, 100)
        self._filter_min_conf.setValue(0)
        self._filter_min_conf.setSuffix(" %")
        lay.addWidget(self._filter_min_conf, 1, 1)

        # Fiyat min
        lay.addWidget(QLabel("Min fiyat:"), 1, 2)
        self._filter_min_price = QDoubleSpinBox()
        self._filter_min_price.setRange(0.0, 1_000_000.0)
        self._filter_min_price.setDecimals(2)
        self._filter_min_price.setSpecialValueText("Yok")
        lay.addWidget(self._filter_min_price, 1, 3)

        # Fiyat max
        lay.addWidget(QLabel("Maks fiyat:"), 1, 4)
        self._filter_max_price = QDoubleSpinBox()
        self._filter_max_price.setRange(0.0, 1_000_000.0)
        self._filter_max_price.setDecimals(2)
        self._filter_max_price.setSpecialValueText("Yok")
        lay.addWidget(self._filter_max_price, 1, 5)

        # Butonlar — Şimdi Yenile + Sıfırla
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)

        self._open_only_check = QCheckBox("Sadece açık olanlar")
        self._open_only_check.toggled.connect(self._on_open_only_toggled)
        btn_row.addWidget(self._open_only_check)

        btn_row.addStretch(1)

        self._btn_apply_filters = QPushButton("Filtreleri Uygula")
        if QTAWESOME_AVAILABLE:
            try:
                self._btn_apply_filters.setIcon(qta.icon("fa5s.filter"))
            except Exception:  # noqa: BLE001
                pass
        self._btn_apply_filters.clicked.connect(self._apply_filters)
        btn_row.addWidget(self._btn_apply_filters)

        self._btn_reset_filters = QPushButton("Sıfırla")
        if QTAWESOME_AVAILABLE:
            try:
                self._btn_reset_filters.setIcon(qta.icon("fa5s.undo"))
            except Exception:  # noqa: BLE001
                pass
        self._btn_reset_filters.clicked.connect(self._reset_filters)
        btn_row.addWidget(self._btn_reset_filters)

        self._btn_refresh = QPushButton("Şimdi Yenile")
        if QTAWESOME_AVAILABLE:
            try:
                self._btn_refresh.setIcon(qta.icon("fa5s.sync"))
            except Exception:  # noqa: BLE001
                pass
        self._btn_refresh.clicked.connect(self.refresh)
        btn_row.addWidget(self._btn_refresh)

        # Combobox/spinbox değişimlerini de filtre uygulasına bağla
        for w in (
            self._filter_tf, self._filter_action, self._filter_exchange,
        ):
            w.currentIndexChanged.connect(self._apply_filters)
        for w in (
            self._filter_min_conf, self._filter_min_price, self._filter_max_price,
        ):
            w.valueChanged.connect(self._apply_filters)

        lay.addLayout(btn_row, 2, 0, 1, 6)
        return box

    # ------------------------------------------------------------------ filters
    def _reset_filters(self) -> None:
        self._filter_tf.setCurrentIndex(0)
        self._filter_action.setCurrentIndex(0)
        self._filter_exchange.setCurrentIndex(0)
        self._filter_min_conf.setValue(0)
        self._filter_min_price.setValue(0)
        self._filter_max_price.setValue(0)
        self._apply_filters()

    def _apply_filters(self) -> None:
        """Aktif filtrelere göre tüm sekmeleri client-side filtrele + render."""
        tf_key = self._filter_tf.currentData() or "all"
        action = self._filter_action.currentData() or "all"
        exchange = self._filter_exchange.currentData() or "all"
        min_conf = float(self._filter_min_conf.value() or 0)
        min_price = float(self._filter_min_price.value() or 0)
        max_price = float(self._filter_max_price.value() or 0)

        def _match(p: BotPickView) -> bool:
            if action != "all" and p.action != action:
                return False
            if min_conf > 0 and p.confidence < min_conf:
                return False
            price = float(p.current_price or 0)
            if min_price > 0 and price < min_price:
                return False
            if max_price > 0 and price > max_price:
                return False
            if exchange != "all":
                # Currency üzerinden basit eşleme — backend exchange alanı
                # picks objesinde yok; TRY → BIST, USD → NASDAQ/NYSE varsayımı.
                if exchange == "BIST" and p.currency != "TRY":
                    return False
                if exchange in ("NASDAQ", "NYSE") and p.currency != "USD":
                    return False
            return True

        filtered_all = [p for p in self._all_picks if _match(p)]

        for key, content in self._tabs.items():
            if tf_key != "all" and key != tf_key and key != "all":
                # Vade filtresi seçildi ve bu sekme uymuyor → boş listele
                content.load_picks([])
            elif key == "all":
                content.load_picks(filtered_all)
            else:
                content.load_picks(
                    [p for p in filtered_all if p.timeframe == key]
                )
            content.set_open_only(self._open_only_check.isChecked())

    # ------------------------------------------------------------------ data
    def load_picks(self, picks: list[BotPickView]) -> None:
        """Tüm sekmeleri yeni veri ile doldurur (sonra filtre uygulanır)."""
        self._all_picks = list(picks)
        # Filtre paneli hazırsa onun üzerinden render — aksi halde direkt dağıt.
        if hasattr(self, "_filter_tf"):
            self._apply_filters()
            return
        for key, content in self._tabs.items():
            if key == "all":
                content.load_picks(self._all_picks)
            else:
                content.load_picks(
                    [p for p in self._all_picks if p.timeframe == key]
                )
            content.set_open_only(self._open_only_check.isChecked())

    # ------------------------------------------------------------------ slots
    @Slot(bool)
    def _on_open_only_toggled(self, checked: bool) -> None:
        for content in self._tabs.values():
            content.set_open_only(checked)
        # Backend yenilemeyi de tetikle (yalnız_açık server-side filtre)
        if self._bridge and self._bridge.available:
            self.refresh()


__all__ = [
    "BotPicksWidget",
    "BotPickView",
    "BotPicksStats",
    "OUTCOME_PALETTE",
    "TIMEFRAME_TABS",
]
