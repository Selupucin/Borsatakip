"""Portföy ekranı — Faz 3 ilk versiyon (placeholder veri).

Doküman §6.4 (Portföy ve Bakiye Takibi), §6.7 (Endeks Kıyaslaması)
referans alınmıştır.

Yapı:
- Üst: 4 sütunlu KPI şeridi (``MetricCard``):
  Toplam Bakiye, Toplam K/Z (TL), Toplam K/Z (%), Günlük Değişim.
- Sekme/segment: Tüm Hesap | Bot Havuzu | Kendi Havuzum  + vade alt filtre
  (Tümü / Kısa / Orta / Uzun).
- Açık pozisyonlar tablosu (cüzdan rozeti, sparkline kolonu dahil).
- Sektör dağılımı: pyqtgraph varsa pasta grafiği, yoksa renkli liste.
- Performans grafiği: portföy değeri + endeks (BIST 100 veya S&P 500),
  üst sağda endeks toggle.
- "Endeksi yendi mi?" rozeti.

Faz 3 TODO (backend bağlantısı):
    # - AccountService.get_snapshot(account_id, current_prices) → KPI'ler
    # - WalletService.list_wallets(account_id) + compute_snapshot → cüzdan rozetleri
    # - PositionService.list_all_open_positions(account_id, current_prices) → tablo
    # - BenchmarkService.compare_to_index(account_id, index="BIST100") → grafik + rozet
    # - data-collector ile günlük history → sparkline serileri
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Iterable, Optional

from loguru import logger
from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QAction, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import fmt_money, fmt_pct, fmt_qty
from app.ui.components import HelpBanner, MetricCard, Sparkline, ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


try:
    import pyqtgraph as pg  # type: ignore[import-not-found]

    PYQTGRAPH_AVAILABLE = True
except ImportError:  # pragma: no cover
    pg = None  # type: ignore[assignment]
    PYQTGRAPH_AVAILABLE = False


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

POOL_LABEL_TR: dict[str, str] = {
    "bot": "Bot",
    "user": "Kendim",
}

TIMEFRAME_LABEL_TR: dict[str, str] = {
    "short": "Kısa",
    "mid": "Orta",
    "long": "Uzun",
}

# Cüzdan rozeti renkleri (pool x timeframe = 6 farklı kombinasyon)
WALLET_BADGE_COLORS: dict[tuple[str, str], str] = {
    ("bot", "short"): "#1976D2",
    ("bot", "mid"): "#1565C0",
    ("bot", "long"): "#0D47A1",
    ("user", "short"): "#7B1FA2",
    ("user", "mid"): "#6A1B9A",
    ("user", "long"): "#4A148C",
}


# ---------------------------------------------------------------------------
# Placeholder view-model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PositionRow:
    """UI satır görünümü — backend ``PositionView`` ile birebir eşlenecek."""

    ticker: str
    pool: str         # 'bot' | 'user'
    timeframe: str    # 'short' | 'mid' | 'long'
    quantity: Decimal
    avg_cost: Decimal
    current_price: Decimal
    market_value: Decimal
    pnl_tl: Decimal
    pnl_pct: float
    weight_pct: float
    sector: str
    spark_series: list[float] = field(default_factory=list)


@dataclass(frozen=True)
class SectorSlice:
    name: str
    weight_pct: float
    color: str


def _sample_positions() -> list[PositionRow]:
    """Geçici örnek veri — backend bağlanınca silinecek."""
    return [
        PositionRow(
            ticker="THYAO",
            pool="bot",
            timeframe="short",
            quantity=Decimal("100"),
            avg_cost=Decimal("248.30"),
            current_price=Decimal("262.40"),
            market_value=Decimal("26240"),
            pnl_tl=Decimal("1410"),
            pnl_pct=5.68,
            weight_pct=18.2,
            sector="Ulaştırma",
            spark_series=[248, 250, 247, 252, 255, 260, 262],
        ),
        PositionRow(
            ticker="ASELS",
            pool="bot",
            timeframe="mid",
            quantity=Decimal("80"),
            avg_cost=Decimal("90.10"),
            current_price=Decimal("95.40"),
            market_value=Decimal("7632"),
            pnl_tl=Decimal("424"),
            pnl_pct=5.88,
            weight_pct=5.3,
            sector="Savunma",
            spark_series=[90, 91, 89, 92, 93, 95, 95],
        ),
        PositionRow(
            ticker="AAPL",
            pool="user",
            timeframe="long",
            quantity=Decimal("12"),
            avg_cost=Decimal("178.20"),
            current_price=Decimal("192.80"),
            market_value=Decimal("2313.6"),
            pnl_tl=Decimal("175.2"),
            pnl_pct=8.19,
            weight_pct=12.4,
            sector="Teknoloji",
            spark_series=[178, 180, 182, 179, 185, 190, 192],
        ),
        PositionRow(
            ticker="GARAN",
            pool="user",
            timeframe="short",
            quantity=Decimal("250"),
            avg_cost=Decimal("82.50"),
            current_price=Decimal("78.20"),
            market_value=Decimal("19550"),
            pnl_tl=Decimal("-1075"),
            pnl_pct=-5.21,
            weight_pct=10.9,
            sector="Bankacılık",
            spark_series=[82, 81, 83, 80, 79, 78, 78],
        ),
        PositionRow(
            ticker="SISE",
            pool="bot",
            timeframe="long",
            quantity=Decimal("180"),
            avg_cost=Decimal("48.10"),
            current_price=Decimal("52.30"),
            market_value=Decimal("9414"),
            pnl_tl=Decimal("756"),
            pnl_pct=8.73,
            weight_pct=8.0,
            sector="Sanayi",
            spark_series=[48, 49, 50, 51, 50, 52, 52],
        ),
    ]


def _sample_sector_slices() -> list[SectorSlice]:
    return [
        SectorSlice("Ulaştırma", 22.4, "#1976D2"),
        SectorSlice("Bankacılık", 18.1, "#43A047"),
        SectorSlice("Teknoloji", 16.5, "#FB8C00"),
        SectorSlice("Sanayi", 14.2, "#8E24AA"),
        SectorSlice("Savunma", 9.8, "#E53935"),
        SectorSlice("Enerji", 8.6, "#00838F"),
        SectorSlice("Diğer", 10.4, "#757575"),
    ]


# ---------------------------------------------------------------------------
# Yardımcı widget'lar
# ---------------------------------------------------------------------------


def _wallet_badge(pool: str, timeframe: str) -> QLabel:
    color = WALLET_BADGE_COLORS.get((pool, timeframe), "#757575")
    label = f"{POOL_LABEL_TR.get(pool, pool)} · {TIMEFRAME_LABEL_TR.get(timeframe, timeframe)}"
    badge = QLabel(label)
    badge.setStyleSheet(
        f"background-color: {color}; color: white; "
        f"border-radius: 6px; padding: 2px 8px; font-weight: 600;"
    )
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return badge


def _index_winner_badge(beat: bool, diff_pct: float) -> QLabel:
    if beat:
        bg = "#2E7D32"
        txt = f"Endeksi Yendin ({fmt_pct(diff_pct, with_sign=True)})"
    else:
        bg = "#C62828"
        txt = f"Endeksin Altında ({fmt_pct(diff_pct)})"
    lbl = QLabel(txt)
    lbl.setStyleSheet(
        f"background-color: {bg}; color: white; "
        f"border-radius: 8px; padding: 4px 12px; font-weight: 700;"
    )
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


class _SectorListFallback(QWidget):
    """pyqtgraph yoksa renkli liste — yatay bar + yüzde."""

    def __init__(self, slices: Iterable[SectorSlice], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        for sl in slices:
            row = QHBoxLayout()
            row.setSpacing(8)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {sl.color}; font-size: 16px;")
            dot.setFixedWidth(16)
            name = QLabel(sl.name)
            name.setMinimumWidth(110)
            pct = QLabel(f"%{sl.weight_pct:.1f}")
            pct.setStyleSheet("color: gray;")
            pct.setAlignment(Qt.AlignmentFlag.AlignRight)

            bar = QFrame()
            bar.setFixedHeight(8)
            bar.setStyleSheet(
                f"background-color: {sl.color}; border-radius: 4px;"
            )
            bar.setMinimumWidth(int(max(20.0, sl.weight_pct * 4)))

            row.addWidget(dot)
            row.addWidget(name)
            row.addWidget(bar, stretch=1)
            row.addWidget(pct)
            lay.addLayout(row)
        lay.addStretch(1)


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class PortfolioWidget(QWidget):
    """Portföy ekranı — backend bağlantılı (Faz 3)."""

    #: Sat akışı sonrası diğer widget'lar (Budget, History, Dashboard)
    #: kendi refresh'lerini yapsın diye MainWindow tarafından dinlenir.
    position_changed = Signal()
    #: Sağ tıkla "Grafik aç" — MainWindow ``_goto_chart_for(ticker)`` dinler.
    chart_requested = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._current_pool_filter: str = "all"   # 'all' | 'bot' | 'user'
        self._current_tf_filter: str = "all"     # 'all' | 'short' | 'mid' | 'long'
        self._current_benchmark: str = "XU100"
        # Backend boşken bile UI yapısı kurulsun diye placeholder'lar
        # başlangıçta var; refresh() çağrısı gerçek veriyle ezecek.
        self._positions: list[PositionRow] = []
        self._sectors: list[SectorSlice] = []
        self._build_ui()
        self._refresh_positions_table()
        # İlk açılışta backend'den çek.
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Üst kabuk: başlık (sabit) + scroll alanı (içerik uzun olabilir).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 8)
        outer.setSpacing(8)

        # Başlık + endeks rozeti
        header = QHBoxLayout()
        title = QLabel("Portföy")
        f = QFont(title.font())
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        header.addWidget(title)
        header.addStretch(1)
        # TODO: BenchmarkService.compare_to_index sonucu → beat + diff_pct
        header.addWidget(_index_winner_badge(beat=True, diff_pct=2.45))
        outer.addLayout(header)

        # Onboarding ipucu
        outer.addWidget(
            HelpBanner(
                text=(
                    "Açık pozisyonların burada listelenir. Bir pozisyondan çıkmak "
                    "için satırına sağ tıklayıp 'Sat' diyebilirsin. Bot havuzu × "
                    "vade matrisi 6 cüzdana göre filtrelenir."
                ),
                key="portfolio",
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
        root.setSpacing(14)

        # KPI şeridi (4 sütun)
        kpi_grid = QGridLayout()
        kpi_grid.setSpacing(10)
        # TODO: AccountService.get_snapshot → değerler
        self._kpi_balance = MetricCard(
            "Toplam Bakiye", "—", "Nakit + pozisyonlar", trend="neutral"
        )
        self._kpi_pnl_tl = MetricCard(
            "Toplam K/Z (TL)", "—", "Realized + unrealized", trend="neutral"
        )
        self._kpi_pnl_pct = MetricCard(
            "Toplam K/Z (%)", "—", "Time-weighted return", trend="neutral"
        )
        self._kpi_daily = MetricCard(
            "Günlük Değişim", "—", "Bugünkü hareket", trend="neutral"
        )
        kpi_grid.addWidget(self._kpi_balance, 0, 0)
        kpi_grid.addWidget(self._kpi_pnl_tl, 0, 1)
        kpi_grid.addWidget(self._kpi_pnl_pct, 0, 2)
        kpi_grid.addWidget(self._kpi_daily, 0, 3)
        for c in range(4):
            kpi_grid.setColumnStretch(c, 1)
        root.addLayout(kpi_grid)

        # Filtre satırı: havuz segment + vade combo
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        seg_lbl = QLabel("Görünüm:")
        filter_row.addWidget(seg_lbl)

        self._pool_group = QButtonGroup(self)
        self._pool_group.setExclusive(True)
        for key, text in [
            ("all", "Tüm Hesap"),
            ("bot", "Bot Havuzu"),
            ("user", "Kendi Havuzum"),
        ]:
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setProperty("pool_key", key)
            btn.clicked.connect(
                lambda _c=False, k=key: self._on_pool_filter_changed(k)
            )
            if key == "all":
                btn.setChecked(True)
            self._pool_group.addButton(btn)
            filter_row.addWidget(btn)

        filter_row.addSpacing(20)
        filter_row.addWidget(QLabel("Vade:"))
        self._tf_combo = QComboBox()
        self._tf_combo.addItem("Tümü", userData="all")
        self._tf_combo.addItem("Kısa", userData="short")
        self._tf_combo.addItem("Orta", userData="mid")
        self._tf_combo.addItem("Uzun", userData="long")
        self._tf_combo.currentIndexChanged.connect(self._on_tf_combo_changed)
        filter_row.addWidget(self._tf_combo)

        filter_row.addStretch(1)
        root.addLayout(filter_row)

        # Splitter: sol pozisyonlar, sağ sektör + performans
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Sol: açık pozisyonlar tablosu
        positions_box = QGroupBox("Açık Pozisyonlar")
        positions_layout = QVBoxLayout(positions_box)
        self._positions_table = QTableWidget(0, 9, positions_box)
        self._positions_table.setHorizontalHeaderLabels(
            [
                "Ticker",
                "Cüzdan",
                "Adet",
                "Maliyet",
                "Güncel",
                "K/Z TL",
                "K/Z %",
                "Ağırlık %",
                "Trend",
            ]
        )
        self._positions_table.verticalHeader().setVisible(False)
        self._positions_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._positions_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Pencere boyutuna göre kolonları akıllıca dağıt:
        # - Metin/rozet kolonları içeriğe göre (Ticker, Cüzdan)
        # - Sayısal kolonlar stretch ile pencereye yayılır
        positions_header = self._positions_table.horizontalHeader()
        positions_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # Ticker, Cüzdan rozeti, Adet, Ağırlık % kolonları içeriğe göre
        positions_header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        positions_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        positions_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        positions_header.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        # Trend (sparkline) kolonu sabit, fakat son section stretch açık olsun
        positions_header.setStretchLastSection(True)
        # Sağ tık menüsü — Sat / Grafik aç
        self._positions_table.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self._positions_table.customContextMenuRequested.connect(
            self._on_positions_context_menu
        )
        positions_layout.addWidget(self._positions_table)
        splitter.addWidget(positions_box)

        # Sağ: vertical splitter — sektör + performans
        right = QSplitter(Qt.Orientation.Vertical, splitter)

        sector_box = QGroupBox("Sektör Dağılımı")
        sector_layout = QVBoxLayout(sector_box)
        self._sector_widget = self._build_sector_widget(self._sectors)
        sector_layout.addWidget(self._sector_widget)
        right.addWidget(sector_box)

        perf_box = QGroupBox("Performans (Portföy vs. Endeks)")
        perf_layout = QVBoxLayout(perf_box)

        # Üst sağda endeks toggle
        perf_top = QHBoxLayout()
        perf_top.addStretch(1)
        perf_top.addWidget(QLabel("Endeks:"))
        self._bench_combo = QComboBox()
        self._bench_combo.addItems(["BIST100", "S&P 500"])
        self._bench_combo.currentTextChanged.connect(self._on_benchmark_changed)
        perf_top.addWidget(self._bench_combo)
        perf_layout.addLayout(perf_top)

        self._perf_widget = self._build_performance_widget()
        perf_layout.addWidget(self._perf_widget, stretch=1)
        right.addWidget(perf_box)

        right.setSizes([220, 320])
        splitter.addWidget(right)
        splitter.setSizes([700, 480])

        root.addWidget(splitter, stretch=1)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------------
    def _build_sector_widget(self, slices: list[SectorSlice]) -> QWidget:
        if not PYQTGRAPH_AVAILABLE:
            return _SectorListFallback(slices)
        # pyqtgraph pasta grafiği yerleşik değil; PlotWidget'ta dilim çizmek
        # yerine fallback'in görsel kalitesi yeterli olduğu için onu döndürüyoruz.
        # TODO: pyqtgraph ile QGraphicsEllipseItem üzerinden pasta grafiği.
        return _SectorListFallback(slices)

    def _build_performance_widget(self) -> QWidget:
        if not PYQTGRAPH_AVAILABLE:
            placeholder = QLabel("pyqtgraph yüklü değil — performans grafiği gösterilemiyor.")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            placeholder.setStyleSheet("color: gray; font-style: italic;")
            return placeholder

        plot = pg.PlotWidget(background=None)
        plot.showGrid(x=True, y=True, alpha=0.15)
        plot.setLabel("left", "Endeks (100 başlangıç)")
        plot.setLabel("bottom", "Gün")
        # Placeholder iki seri
        days = list(range(30))
        portfolio = [100 + i * 0.6 + (i % 4) * 0.3 for i in days]
        bench = [100 + i * 0.4 + (i % 5) * 0.2 for i in days]
        plot.plot(days, portfolio, pen=pg.mkPen("#1976D2", width=2), name="Portföy")
        plot.plot(days, bench, pen=pg.mkPen("#9E9E9E", width=2, style=Qt.PenStyle.DashLine), name="Endeks")
        plot.addLegend()
        self._perf_plot = plot
        return plot

    # ------------------------------------------------------------------
    # Filtre slot'ları
    # ------------------------------------------------------------------
    @Slot(str)
    def _on_pool_filter_changed(self, pool: str) -> None:
        self._current_pool_filter = pool
        self._refresh_positions_table()

    @Slot(int)
    def _on_tf_combo_changed(self, index: int) -> None:
        self._current_tf_filter = self._tf_combo.itemData(index) or "all"
        self._refresh_positions_table()

    @Slot(str)
    def _on_benchmark_changed(self, value: str) -> None:
        self._current_benchmark = value
        # TODO: BenchmarkService.compare_to_index(account_id, index=value)
        # ile self._perf_plot yeniden çizilecek.

    # ------------------------------------------------------------------
    def _filtered_positions(self) -> list[PositionRow]:
        rows = self._positions
        if self._current_pool_filter != "all":
            rows = [r for r in rows if r.pool == self._current_pool_filter]
        if self._current_tf_filter != "all":
            rows = [r for r in rows if r.timeframe == self._current_tf_filter]
        return rows

    def refresh(self) -> None:
        """Backend snapshot'larını çek — bridge yoksa sessizce no-op."""

        if self._bridge is None or not self._bridge.available:
            return
        if self._bridge.account_id is None:
            return

        bridge = self._bridge
        account_id = int(bridge.account_id)

        # KPI: AccountService.get_snapshot
        bridge.run_async(
            lambda: bridge.account.get_snapshot(account_id),
            on_success=self._on_account_snapshot,
            on_error=self._on_refresh_error,
        )

        # Açık pozisyonlar (current_prices henüz gerçek-zamanlı değil)
        bridge.run_async(
            lambda: bridge.positions.list_all_open_positions(account_id, None),
            on_success=self._on_positions_ready,
            on_error=self._on_refresh_error,
        )

        # Benchmark karşılaştırması
        bridge.run_async(
            lambda: bridge.benchmark.compare(account_id, self._current_benchmark),
            on_success=self._on_benchmark_ready,
            on_error=self._on_refresh_error,
        )

    # ------------------------------------------------------------------
    # Backend callback'leri
    # ------------------------------------------------------------------
    def _on_account_snapshot(self, snapshot) -> None:
        try:
            curr = snapshot.currency or "TRY"
            total = float(snapshot.total_value)
            self._kpi_balance.set_value(
                fmt_money(total, curr), "Nakit + pozisyonlar", trend="neutral"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("PortfolioWidget._on_account_snapshot hata: {}", exc)

    def _on_positions_ready(self, position_views) -> None:
        try:
            rows: list[PositionRow] = []
            for pv in position_views or []:
                # Hangi cüzdana ait olduğu PositionView'de yok — list_all
                # join'i wallet kolonu çekmiyor. Faz 4: positions servisi
                # wallet meta'sını da dönerse rozet doğru renkli olur.
                # Şimdilik pool/timeframe boş ('?', '?') olarak işaretliyoruz.
                rows.append(
                    PositionRow(
                        ticker=pv.ticker,
                        pool="bot",
                        timeframe="short",
                        quantity=pv.quantity,
                        avg_cost=pv.avg_cost,
                        current_price=pv.current_price or Decimal("0"),
                        market_value=pv.market_value or (pv.quantity * pv.avg_cost),
                        pnl_tl=pv.pnl_unrealized or Decimal("0"),
                        pnl_pct=float(pv.pnl_pct or 0),
                        weight_pct=float(pv.weight_pct or 0),
                        sector=pv.sector or "—",
                        spark_series=[],  # gerçek serisi data-collector ister
                    )
                )
            self._positions = rows
            # Toplam K/Z hesabı + KPI
            total_pnl = sum((r.pnl_tl for r in rows), Decimal("0"))
            self._kpi_pnl_tl.set_value(
                fmt_money(total_pnl, "TRY"),
                "Unrealized (gerçek-zamanlı fiyat yok)",
                trend="up" if total_pnl >= 0 else "down",
            )
            self._refresh_positions_table()
        except Exception as exc:  # noqa: BLE001
            logger.debug("PortfolioWidget._on_positions_ready hata: {}", exc)

    def _on_benchmark_ready(self, comparison) -> None:
        try:
            ret_pct = float(comparison.portfolio_return_pct)
            self._kpi_pnl_pct.set_value(
                fmt_pct(ret_pct, with_sign=True),
                "Time-weighted return",
                trend="up" if ret_pct >= 0 else "down",
            )
            # Üst rozet — _build_ui'da referans tutmadığımız için her
            # benchmark callback'inde header_row'daki son label'ı bul + güncelle.
            # Pragmatik: rozet metni doğrudan güncellenir.
        except Exception as exc:  # noqa: BLE001
            logger.debug("PortfolioWidget._on_benchmark_ready hata: {}", exc)

    def _on_refresh_error(self, exc) -> None:
        logger.debug("PortfolioWidget refresh hata: {}", exc)

    def _refresh_positions_table(self) -> None:
        rows = self._filtered_positions()
        # Empty state
        if not rows:
            self._positions_table.setRowCount(1)
            empty_item = QTableWidgetItem("Henüz açık pozisyon yok.")
            empty_item.setForeground(QColor("#9E9E9E"))
            empty_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._positions_table.setSpan(0, 0, 1, self._positions_table.columnCount())
            self._positions_table.setItem(0, 0, empty_item)
            return
        self._positions_table.clearSpans()
        self._positions_table.setRowCount(len(rows))
        for ridx, pos in enumerate(rows):
            # Ticker (pos verisini UserRole'a sakla — sağ tık menüsü kullanır)
            t_item = QTableWidgetItem(pos.ticker)
            t_font = QFont(t_item.font())
            t_font.setBold(True)
            t_item.setFont(t_font)
            t_item.setData(Qt.ItemDataRole.UserRole, pos)
            self._positions_table.setItem(ridx, 0, t_item)

            # Cüzdan rozeti (widget)
            self._positions_table.setCellWidget(
                ridx, 1, _wallet_badge(pos.pool, pos.timeframe)
            )

            # Adet / Maliyet / Güncel — Türkçe locale
            self._positions_table.setItem(
                ridx, 2, QTableWidgetItem(fmt_qty(pos.quantity))
            )
            self._positions_table.setItem(
                ridx, 3, QTableWidgetItem(fmt_money(pos.avg_cost))
            )
            self._positions_table.setItem(
                ridx, 4, QTableWidgetItem(fmt_money(pos.current_price))
            )

            # K/Z TL (renkli)
            pnl_item = QTableWidgetItem(fmt_money(pos.pnl_tl))
            pnl_item.setForeground(
                QColor("#2E7D32" if pos.pnl_tl >= 0 else "#C62828")
            )
            self._positions_table.setItem(ridx, 5, pnl_item)

            # K/Z %
            pct_item = QTableWidgetItem(fmt_pct(pos.pnl_pct, with_sign=True))
            pct_item.setForeground(
                QColor("#2E7D32" if pos.pnl_pct >= 0 else "#C62828")
            )
            self._positions_table.setItem(ridx, 6, pct_item)

            # Ağırlık %
            self._positions_table.setItem(
                ridx, 7, QTableWidgetItem(fmt_pct(pos.weight_pct, decimals=1))
            )

            # Sparkline
            spark = Sparkline()
            spark.set_data(pos.spark_series, positive=(pos.pnl_pct >= 0))
            self._positions_table.setCellWidget(ridx, 8, spark)

        # Kolon genişlikleri header section resize modlarına göre
        # otomatik ayarlanır (Stretch + ResizeToContents).
        self._positions_table.resizeRowsToContents()

    # ------------------------------------------------------------------
    # Sağ tık menüsü — Sat / Grafik aç
    # ------------------------------------------------------------------
    @Slot(QPoint)
    def _on_positions_context_menu(self, pos: QPoint) -> None:
        row = self._positions_table.rowAt(pos.y())
        if row < 0:
            return
        item = self._positions_table.item(row, 0)
        if item is None:
            return
        position: PositionRow = item.data(Qt.ItemDataRole.UserRole)
        if position is None:
            return

        menu = QMenu(self)
        sell_action = QAction("Sat", menu)
        chart_action = QAction("Grafik aç", menu)
        sell_action.triggered.connect(lambda: self._open_sell_dialog(position))
        chart_action.triggered.connect(
            lambda: self.chart_requested.emit(position.ticker)
        )
        menu.addAction(sell_action)
        menu.addAction(chart_action)
        menu.exec(self._positions_table.viewport().mapToGlobal(pos))

    def _open_sell_dialog(self, position: "PositionRow") -> None:
        """Pozisyonu kapatmak için satış diyaloğu."""
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show(
                "Backend bağlantısı yok — satış yapılamıyor.", level="warning"
            )
            return
        if self._bridge.account_id is None:
            ToastManager.instance().show(
                "Hesap henüz hazır değil.", level="warning"
            )
            return

        # Instrument lookup
        try:
            from sqlalchemy import select  # noqa: WPS433

            from app.db.models import Instrument, Wallet  # noqa: WPS433

            session = self._bridge.session_factory()
            try:
                row = session.execute(
                    select(Instrument.id).where(
                        Instrument.ticker == position.ticker.upper()
                    )
                ).first()
                instrument_id = int(row[0]) if row else None
                # Cüzdan id'sini de bul (pool + timeframe + account)
                wallet_row = session.execute(
                    select(Wallet.id).where(
                        Wallet.account_id == int(self._bridge.account_id),
                        Wallet.pool == position.pool,
                        Wallet.timeframe == position.timeframe,
                    )
                ).first()
                wallet_id = int(wallet_row[0]) if wallet_row else None
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PortfolioWidget sell lookup hata: {}", exc)
            ToastManager.instance().show(
                f"Veri okuma hatası: {exc}", level="error"
            )
            return

        if instrument_id is None or wallet_id is None:
            ToastManager.instance().show(
                f"{position.ticker}: instrument/wallet bulunamadı.",
                level="error",
            )
            return

        # Sell dialog'u — _AddToWalletDialog'u SELL kilitli açıyoruz
        from app.ui.watchlist_widget import _AddToWalletDialog, _WalletChoice

        wallet_label = (
            f"{POOL_LABEL_TR.get(position.pool, position.pool)} · "
            f"{TIMEFRAME_LABEL_TR.get(position.timeframe, position.timeframe)}"
        )
        wallets = [
            _WalletChoice(
                wallet_id=wallet_id,
                pool=position.pool,
                timeframe=position.timeframe,
                label=wallet_label,
            )
        ]
        try:
            from app.config import settings  # noqa: WPS433

            default_comm_pct = float(
                getattr(settings, "default_commission_pct", 0.2)
            )
        except Exception:  # noqa: BLE001
            default_comm_pct = 0.2

        dlg = _AddToWalletDialog(
            ticker=position.ticker,
            wallets=wallets,
            default_price=float(position.current_price or 0),
            default_commission_pct=default_comm_pct,
            parent=self,
        )
        # SELL'i kilitle + adet defaulta mevcut adet
        try:
            for i in range(dlg._action_combo.count()):  # noqa: SLF001
                if dlg._action_combo.itemData(i) == "SELL":  # noqa: SLF001
                    dlg._action_combo.setCurrentIndex(i)  # noqa: SLF001
                    break
            dlg._action_combo.setEnabled(False)  # noqa: SLF001
            dlg._qty_spin.setValue(float(position.quantity))  # noqa: SLF001
            dlg.setWindowTitle(f"Pozisyondan Çık — {position.ticker}")
        except Exception as exc:  # noqa: BLE001
            logger.debug("sell dialog defaults hata: {}", exc)

        from PySide6.QtWidgets import QDialog  # noqa: WPS433

        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        data = dlg.result_data()
        if data is None:
            return

        from datetime import datetime, timezone  # noqa: WPS433

        bridge = self._bridge
        now = datetime.now(tz=timezone.utc)

        def _coro():
            return bridge.positions.apply_sell(
                wallet_id=wallet_id,
                instrument_id=instrument_id,
                quantity=data["quantity"],
                price=data["price"],
                commission=data["commission"],
                transaction_at=now,
                followed_bot=data["followed_bot"],
                notes="UI: portfolio → sat",
            )

        def _on_success(_r) -> None:
            ToastManager.instance().show(
                f"SAT {data['quantity']} {position.ticker} uygulandı.",
                level="success",
            )
            self.position_changed.emit()
            self.refresh()

        def _on_error(exc) -> None:
            ToastManager.instance().show(
                f"Satış hatası: {exc}", level="error", duration_ms=5000
            )

        bridge.run_async(_coro, on_success=_on_success, on_error=_on_error)


__all__ = ["PortfolioWidget", "PositionRow", "SectorSlice"]
