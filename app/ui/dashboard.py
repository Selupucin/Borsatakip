"""Dashboard (Ana Ekran) — Faz 3 backend wiring.

Doküman §8.3 "Dashboard" satırı: portföy özeti, bot öne çıkanları, piyasa
özeti ve önemli haberler tek bakışta görünür.

Backend bağlantısı (Faz 3 sonrası):
- ``AccountService.get_snapshot`` → "Toplam Bakiye" + serbest nakit.
- ``WalletService.list_wallets`` → 6 cüzdan mini grid.
- ``BenchmarkService.compare`` → "Endeksi Yendin mi?" rozeti.
- "Son Haberler" + "Bot Öne Çıkanlar" şimdilik placeholder'da kalır
  (NewsWidget / BotPicksWidget kendi içlerinde gerçek veriyi çekiyor).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import fmt_money, fmt_pct
from app.ui.bot_picks_widget import _sample_picks
from app.ui.components import MetricCard, ToastManager
from app.ui.news_widget import SENTIMENT_PALETTE, _colored_dot, _sample_news
from app.ui.portfolio_widget import (
    POOL_LABEL_TR,
    TIMEFRAME_LABEL_TR,
    WALLET_BADGE_COLORS,
)
from app.ui.recommendation_widget import ACTION_PALETTE

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


# ----- Faz 3: yardımcı widget'lar -------------------------------------------
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
    return lbl


def _wallet_mini_cell(pool: str, timeframe: str, value: str, ret_pct: float) -> QFrame:
    """Dashboard "Cüzdan Özeti" için 6 mini-grid kutusundan biri."""
    cell = QFrame()
    cell.setObjectName("dashWalletCell")
    cell.setFrameShape(QFrame.Shape.StyledPanel)
    cell.setStyleSheet(
        "#dashWalletCell { border: 1px solid rgba(120,120,120,80); "
        "border-radius: 6px; padding: 4px; }"
    )
    lay = QVBoxLayout(cell)
    lay.setContentsMargins(8, 6, 8, 6)
    lay.setSpacing(2)

    color = WALLET_BADGE_COLORS.get((pool, timeframe), "#757575")
    label = f"{POOL_LABEL_TR.get(pool, pool)} · {TIMEFRAME_LABEL_TR.get(timeframe, timeframe)}"
    name = QLabel(label)
    name.setStyleSheet(
        f"color: white; background-color: {color}; border-radius: 4px;"
        f" padding: 1px 6px; font-size: 9pt; font-weight: 600;"
    )
    lay.addWidget(name)

    val = QLabel(value)
    vf = QFont(val.font())
    vf.setBold(True)
    val.setFont(vf)
    lay.addWidget(val)

    ret_color = "#2E7D32" if ret_pct >= 0 else "#C62828"
    ret = QLabel(fmt_pct(ret_pct, decimals=1, with_sign=True))
    ret.setStyleSheet(f"color: {ret_color}; font-weight: 600; font-size: 9pt;")
    lay.addWidget(ret)
    return cell


class DashboardWidget(QWidget):
    """Ana ekran — backend bağlantılı (Faz 3)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._build_ui()
        # Haberler / bot öne çıkanlar şimdilik paylaşılan örneklerden
        # geliyor — bunlar NewsWidget / BotPicksWidget ekranlarında zaten
        # gerçek backend ile çalışacak; dashboard mini kartlar Faz 4'te
        # NewsAggregator akışına bağlanır.
        self._populate_news()
        self._populate_bot_highlights()
        # İlk açılışta backend snapshot'ını çek.
        self.refresh_data()
        # Periyodik piyasa özeti yenileme (30sn) — UI thread'ini meşgul etmez.
        self._market_timer = QTimer(self)
        self._market_timer.setInterval(30_000)
        self._market_timer.timeout.connect(self._refresh_market_summary)
        self._market_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802 — Qt API
        super().showEvent(event)
        # Tab değiştirildiğinde her seferinde tazele (hızlı çağrı; cache yok)
        self.refresh_data()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        # Başlık satırı: başlık + endeks rozeti
        title_row = QHBoxLayout()
        title = QLabel("Genel Bakış")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 6)
        title_font.setBold(True)
        title.setFont(title_font)
        title_row.addWidget(title)
        title_row.addStretch(1)
        # BenchmarkService.compare → refresh_data sırasında güncellenir.
        self._index_badge = _index_winner_badge(beat=True, diff_pct=0.0)
        self._index_badge.setText("Endeks: —")
        self._index_badge.setStyleSheet(
            "background-color: #757575; color: white; "
            "border-radius: 8px; padding: 4px 12px; font-weight: 700;"
        )
        title_row.addWidget(self._index_badge)
        root.addLayout(title_row)

        # KPI şeridi — AccountService.get_snapshot ile refresh edilir.
        cards = QGridLayout()
        cards.setSpacing(12)
        self._card_balance = MetricCard(
            "Toplam Bakiye", "—", "Tüm cüzdanlar dahil", trend="neutral"
        )
        self._card_cash = MetricCard(
            "Serbest Nakit", "—", "Henüz tahsis edilmedi", trend="neutral"
        )
        self._card_positions = MetricCard(
            "Aktif Pozisyon", "—", "Açık pozisyon sayısı", trend="neutral"
        )
        self._card_signals = MetricCard(
            "Bot Sinyalleri", "—", "Bekleyen öneri sayısı", trend="neutral"
        )
        cards.addWidget(self._card_balance, 0, 0)
        cards.addWidget(self._card_cash, 0, 1)
        cards.addWidget(self._card_positions, 0, 2)
        cards.addWidget(self._card_signals, 0, 3)
        for col in range(4):
            cards.setColumnStretch(col, 1)
        root.addLayout(cards)

        # Cüzdan Özeti mini grid (havuz × vade = 6 hücre)
        wallet_box = QGroupBox("Cüzdan Özeti")
        self._wallet_grid = QGridLayout(wallet_box)
        self._wallet_grid.setSpacing(6)
        for c in range(3):
            self._wallet_grid.setColumnStretch(c, 1)
        # İlk açılışta boş state — refresh_data dolduracak.
        self._render_wallet_grid([])
        root.addWidget(wallet_box)

        # Piyasa özeti tablosu
        market_box = QGroupBox("Piyasa Özeti")
        market_layout = QVBoxLayout(market_box)
        self._market_table = QTableWidget(len(_MARKET_ROWS), 4, market_box)
        self._market_table.setHorizontalHeaderLabels(["Endeks", "Son", "Değişim", "Değişim %"])
        self._market_table.verticalHeader().setVisible(False)
        self._market_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._market_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._market_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )

        placeholder_rows = [(label, "—", "—", "—") for label, _, _ in _MARKET_ROWS]
        for row_idx, row in enumerate(placeholder_rows):
            for col_idx, val in enumerate(row):
                item = QTableWidgetItem(val)
                if col_idx == 0:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled)
                self._market_table.setItem(row_idx, col_idx, item)

        market_layout.addWidget(self._market_table)
        root.addWidget(market_box)

        # Hisselerim — açık pozisyon + bot durumu + K/Z (kullanıcı odaklı)
        positions_box = QGroupBox("Hisselerim")
        positions_layout = QVBoxLayout(positions_box)
        self._positions_table = QTableWidget(0, 7, positions_box)
        self._positions_table.setHorizontalHeaderLabels(
            ["Hisse", "Adet", "Maliyet", "Güncel", "K/Z (TL)", "K/Z %", "Bot"]
        )
        self._positions_table.verticalHeader().setVisible(False)
        self._positions_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._positions_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._positions_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._positions_table.horizontalHeader().setStretchLastSection(True)
        self._positions_empty_label = QLabel(
            "Henüz açık pozisyon yok — Watchlist'ten hisseye sağ tıklayıp 'İşlem öner ve aç' diyerek başlayabilirsin."
        )
        self._positions_empty_label.setStyleSheet("color: gray; font-style: italic; padding: 8px;")
        self._positions_empty_label.setWordWrap(True)
        positions_layout.addWidget(self._positions_table)
        positions_layout.addWidget(self._positions_empty_label)
        root.addWidget(positions_box)

        # Alt blok: sol haberler, sağ bot öne çıkanlar
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(12)

        # Sol — Son Haberler
        news_box = QGroupBox("Son Haberler")
        news_layout = QVBoxLayout(news_box)
        self._news_list = QListWidget(news_box)
        self._news_list.setAlternatingRowColors(True)
        news_layout.addWidget(self._news_list)
        bottom_row.addWidget(news_box, stretch=3)

        # Sağ — Bot Öne Çıkanlar
        picks_box = QGroupBox("Bot Öne Çıkanlar")
        picks_layout = QVBoxLayout(picks_box)
        picks_layout.setSpacing(8)
        self._picks_container = QWidget(picks_box)
        self._picks_container_layout = QVBoxLayout(self._picks_container)
        self._picks_container_layout.setContentsMargins(0, 0, 0, 0)
        self._picks_container_layout.setSpacing(6)
        picks_layout.addWidget(self._picks_container)
        picks_layout.addStretch(1)
        bottom_row.addWidget(picks_box, stretch=2)

        root.addLayout(bottom_row, stretch=1)

    # ------------------------------------------------------------------
    def _populate_news(self) -> None:
        """``NewsWidget`` ile aynı placeholder kaynağından ilk 5 haber."""
        items = _sample_news()[:5]
        self._news_list.clear()
        for item in items:
            color, label = SENTIMENT_PALETTE.get(
                item.sentiment, SENTIMENT_PALETTE["neutral"]
            )
            list_item = QListWidgetItem(item.title)
            list_item.setIcon(self._dot_icon(color))
            list_item.setToolTip(
                f"{item.source_label} • {label} ({item.sentiment_score:+.2f})"
            )
            self._news_list.addItem(list_item)

    def _populate_bot_highlights(self) -> None:
        """En yüksek güvenli 3 kısa vade pick'i mini kart olarak göster."""
        picks = [p for p in _sample_picks() if p.timeframe == "short"]
        picks.sort(key=lambda p: p.confidence, reverse=True)
        top3 = picks[:3]

        # Önce varsa eski kartları temizle (refresh için)
        while self._picks_container_layout.count():
            it = self._picks_container_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        if not top3:
            empty = QLabel("Henüz bot önerisi yok.")
            empty.setStyleSheet("color: gray; font-style: italic;")
            self._picks_container_layout.addWidget(empty)
            return

        for pick in top3:
            self._picks_container_layout.addWidget(self._build_pick_card(pick))

    def _build_pick_card(self, pick) -> QFrame:  # type: ignore[no-untyped-def]
        frame = QFrame(self._picks_container)
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(10)

        # Ticker bold
        ticker_lbl = QLabel(pick.ticker)
        tf = QFont(ticker_lbl.font())
        tf.setBold(True)
        tf.setPointSize(tf.pointSize() + 1)
        ticker_lbl.setFont(tf)
        ticker_lbl.setMinimumWidth(60)

        # Aksiyon rozeti
        bg, fg, label = ACTION_PALETTE.get(pick.action, ACTION_PALETTE["HOLD"])
        action_lbl = QLabel(label)
        action_lbl.setStyleSheet(
            f"background-color: {bg}; color: {fg}; "
            f"border-radius: 6px; padding: 2px 10px; font-weight: 700;"
        )

        conf_lbl = QLabel(f"%{pick.confidence:.0f}")
        conf_lbl.setStyleSheet("color: gray;")
        conf_lbl.setMinimumWidth(40)

        price_lbl = QLabel(fmt_money(pick.current_price, pick.currency))

        lay.addWidget(ticker_lbl)
        lay.addWidget(action_lbl)
        lay.addWidget(conf_lbl)
        lay.addStretch(1)
        lay.addWidget(price_lbl)
        return frame

    # ------------------------------------------------------------------
    @staticmethod
    def _dot_icon(color_hex: str):
        from PySide6.QtGui import QIcon

        pm = _colored_dot(color_hex, diameter=12)
        return QIcon(pm)

    # ------------------------------------------------------------------
    # Backend refresh akışı
    # ------------------------------------------------------------------
    def refresh_data(self) -> None:
        """Backend snapshot'larını async olarak çek ve KPI'ları doldur."""

        if self._bridge is None or not self._bridge.available:
            return
        if self._bridge.account_id is None:
            # Bridge daha hesabı yaratmamış — ensure_account_async sonrası
            # MainWindow yeniden refresh_data çağıracak.
            return

        bridge = self._bridge
        account_id = int(bridge.account_id)

        # 1) AccountService.get_snapshot
        bridge.run_async(
            lambda: bridge.account.get_snapshot(account_id),
            on_success=self._on_snapshot_ready,
            on_error=self._on_refresh_error,
        )

        # 2) WalletService.list_wallets
        bridge.run_async(
            lambda: bridge.wallets.list_wallets(account_id),
            on_success=self._on_wallets_ready,
            on_error=self._on_refresh_error,
        )

        # 3) Açık pozisyon sayısı — list_all_open_positions (current_prices yok)
        bridge.run_async(
            lambda: bridge.positions.list_all_open_positions(account_id, None),
            on_success=self._on_positions_ready,
            on_error=self._on_refresh_error,
        )

        # 4) BenchmarkService.compare — BIST 100 ile karşılaştır
        bridge.run_async(
            lambda: bridge.benchmark.compare(account_id, "XU100"),
            on_success=self._on_benchmark_ready,
            on_error=self._on_refresh_error,
        )

        # 5) Bekleyen öneri sayısı (ManualParallelService.pending_recommendations)
        bridge.run_async(
            lambda: bridge.manual.pending_recommendations(account_id),
            on_success=self._on_pending_ready,
            on_error=self._on_refresh_error,
        )

        # 6) Piyasa özeti
        self._refresh_market_summary()

    # ------------------------------------------------------------------
    # Piyasa özeti — instruments + price_history + fx_rates
    # ------------------------------------------------------------------
    def _refresh_market_summary(self) -> None:
        """BIST 100, S&P 500, USD/TRY satırlarını DB'den çek + tabloya yansıt."""
        if self._bridge is None or not self._bridge.available:
            return
        session_factory = self._bridge.session_factory

        def _fetch():
            return _fetch_market_summary(session_factory)

        self._bridge.run_async(
            _fetch,
            on_success=self._on_market_ready,
            on_error=self._on_refresh_error,
        )

    def _on_market_ready(self, rows: list[tuple[str, Optional[float], Optional[float], Optional[float]]]) -> None:
        """rows: list of (label, last, change, change_pct) — None'lar '—' olarak gösterilir."""
        try:
            for r_idx, row in enumerate(rows):
                if r_idx >= self._market_table.rowCount():
                    break
                label, last, change, change_pct = row
                # Endeks adı
                name_item = QTableWidgetItem(label)
                self._market_table.setItem(r_idx, 0, name_item)
                # Son
                last_str = fmt_money(last) if last is not None else "—"
                last_item = QTableWidgetItem(last_str)
                last_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                self._market_table.setItem(r_idx, 1, last_item)
                # Değişim
                if change is None:
                    change_str = "—"
                else:
                    sign = "+" if change >= 0 else ""
                    change_str = f"{sign}{fmt_money(change)}"
                change_item = QTableWidgetItem(change_str)
                change_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if change is not None:
                    color = "#2E7D32" if change >= 0 else "#C62828"
                    change_item.setForeground(QBrush(QColor(color)))
                self._market_table.setItem(r_idx, 2, change_item)
                # Değişim %
                if change_pct is None:
                    pct_str = "—"
                else:
                    pct_str = fmt_pct(change_pct, with_sign=True)
                pct_item = QTableWidgetItem(pct_str)
                pct_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
                if change_pct is not None:
                    color = "#2E7D32" if change_pct >= 0 else "#C62828"
                    pct_item.setForeground(QBrush(QColor(color)))
                    pf = QFont(pct_item.font())
                    pf.setBold(True)
                    pct_item.setFont(pf)
                self._market_table.setItem(r_idx, 3, pct_item)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Dashboard._on_market_ready hata: {}", exc)

    def _on_snapshot_ready(self, snapshot) -> None:
        try:
            total = float(snapshot.total_value)
            cash = float(snapshot.cash_balance)
            curr = snapshot.currency or "TRY"
            self._card_balance.set_value(
                fmt_money(total, curr), "Nakit + cüzdanlar", trend="neutral"
            )
            self._card_cash.set_value(
                fmt_money(cash, curr), "Henüz tahsis edilmedi", trend="neutral"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Dashboard._on_snapshot_ready hata: {}", exc)

    def _on_wallets_ready(self, wallets) -> None:
        try:
            self._render_wallet_grid(list(wallets or []))
        except Exception as exc:  # noqa: BLE001
            logger.debug("Dashboard._on_wallets_ready hata: {}", exc)

    def _on_positions_ready(self, positions) -> None:
        try:
            positions = positions or []
            count = len(positions)
            self._card_positions.set_value(
                str(count),
                "Açık pozisyon" if count else "Henüz pozisyon yok",
                trend="neutral",
            )
            self._render_positions_table(positions)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Dashboard._on_positions_ready hata: {}", exc)

    def _render_positions_table(self, positions) -> None:
        """Hisselerim tablosunu açık pozisyonlardan doldur."""
        from PySide6.QtGui import QColor
        from app.ui._format import fmt_money, fmt_pct, fmt_qty

        # Empty state
        if not positions:
            self._positions_table.setRowCount(0)
            self._positions_empty_label.setVisible(True)
            self._positions_table.setVisible(False)
            return
        self._positions_empty_label.setVisible(False)
        self._positions_table.setVisible(True)

        self._positions_table.setRowCount(len(positions))
        self._positions_row_to_instrument: dict[int, int] = {}
        for row_idx, pos in enumerate(positions):
            ticker = getattr(pos, "ticker", "—")
            quantity = getattr(pos, "quantity", None)
            avg_cost = getattr(pos, "avg_cost", None)
            current = getattr(pos, "current_price", None)
            pnl = getattr(pos, "pnl_unrealized", None)
            pnl_pct = getattr(pos, "pnl_pct", None)
            inst_id = getattr(pos, "instrument_id", None)
            if inst_id is not None:
                self._positions_row_to_instrument[row_idx] = int(inst_id)

            currency = "TRY"

            cells = [
                str(ticker),
                fmt_qty(quantity),
                fmt_money(avg_cost, currency) if avg_cost is not None else "—",
                fmt_money(current, currency) if current is not None else "—",
                fmt_money(pnl, currency) if pnl is not None else "—",
                fmt_pct(pnl_pct, with_sign=True) if pnl_pct is not None else "—",
                "…",  # Bot — async olarak doldurulur
            ]
            for col_idx, val in enumerate(cells):
                item = QTableWidgetItem(val)
                if col_idx in (4, 5) and pnl is not None:
                    try:
                        v = float(pnl) if col_idx == 4 else float(pnl_pct or 0)
                        item.setForeground(QColor("#2E7D32" if v >= 0 else "#C62828"))
                    except (TypeError, ValueError):
                        pass
                self._positions_table.setItem(row_idx, col_idx, item)

        # Bot kolonu — async olarak her instrument için son recommendation
        if self._bridge is not None and self._bridge.available:
            inst_ids = list(self._positions_row_to_instrument.values())
            if inst_ids:
                self._bridge.run_async(
                    lambda ids=tuple(inst_ids), sf=self._bridge.session_factory: _fetch_latest_actions(
                        sf, ids
                    ),
                    on_success=self._on_position_signals_ready,
                    on_error=lambda exc: logger.debug("position_signals hata: {}", exc),
                )

    def _on_position_signals_ready(self, action_map: dict) -> None:
        """{instrument_id: (action, timeframe)} → Bot kolonunu güncelle."""
        from PySide6.QtGui import QColor

        ACTION_COLOR = {
            "BUY": ("#2E7D32", "AL"),
            "SELL": ("#C62828", "SAT"),
            "HOLD": ("#9E9E9E", "TUT"),
        }
        for row_idx, inst_id in (self._positions_row_to_instrument or {}).items():
            sig = action_map.get(inst_id)
            cell = self._positions_table.item(row_idx, 6)
            if cell is None:
                continue
            if sig is None:
                cell.setText("—")
                continue
            action, timeframe = sig
            color_hex, label = ACTION_COLOR.get(action, ("#9E9E9E", action))
            tf_short = {"short": "Kısa", "mid": "Orta", "long": "Uzun"}.get(timeframe, timeframe)
            cell.setText(f"{label} ({tf_short})")
            cell.setForeground(QColor(color_hex))

    def _on_benchmark_ready(self, comparison) -> None:
        try:
            diff = float(comparison.alpha_pct)
            beat = bool(comparison.beat_benchmark)
            if beat:
                bg = "#2E7D32"
                txt = f"Endeksi Yendin ({fmt_pct(diff, with_sign=True)})"
            else:
                bg = "#C62828"
                txt = f"Endeksin Altında ({fmt_pct(diff)})"
            self._index_badge.setText(txt)
            self._index_badge.setStyleSheet(
                f"background-color: {bg}; color: white; "
                f"border-radius: 8px; padding: 4px 12px; font-weight: 700;"
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Dashboard._on_benchmark_ready hata: {}", exc)

    def _on_pending_ready(self, pending) -> None:
        try:
            count = len(pending or [])
            self._card_signals.set_value(
                str(count),
                "Bekleyen öneri" if count else "Bekleyen öneri yok",
                trend="up" if count > 0 else "neutral",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Dashboard._on_pending_ready hata: {}", exc)

    def _on_refresh_error(self, exc) -> None:
        logger.debug("Dashboard refresh hata: {}", exc)
        # Açılış sırasında DB henüz yokken sessiz kal — toast spam'i olmasın.

    def _render_wallet_grid(self, wallets: list) -> None:
        # Mevcut hücreleri temizle
        while self._wallet_grid.count():
            item = self._wallet_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        for c in range(3):
            self._wallet_grid.setColumnStretch(c, 1)

        if not wallets:
            empty = QLabel("Henüz cüzdan tanımlı değil.")
            empty.setStyleSheet("color: gray; font-style: italic;")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._wallet_grid.addWidget(empty, 0, 0, 1, 3)
            return

        # Servis çıktısı sırasız olabilir — pool×timeframe sıralaması garantili
        order = {
            ("bot", "short"): 0, ("bot", "mid"): 1, ("bot", "long"): 2,
            ("user", "short"): 3, ("user", "mid"): 4, ("user", "long"): 5,
        }
        sorted_w = sorted(wallets, key=lambda w: order.get((w.pool, w.timeframe), 99))
        for w in sorted_w:
            idx = order.get((w.pool, w.timeframe), 0)
            r, c = divmod(idx, 3)
            val_str = fmt_money(w.total_value, "TRY")
            ret_pct = (
                float(w.return_pct_twr) if getattr(w, "return_pct_twr", None) else 0.0
            )
            self._wallet_grid.addWidget(
                _wallet_mini_cell(w.pool, w.timeframe, val_str, ret_pct), r, c
            )


# ---------------------------------------------------------------------------
# Piyasa özeti yardımcısı — async worker'da çalışır
# ---------------------------------------------------------------------------

#: Piyasa özeti tablosu sıralı — (etiket, instrument ticker veya FX pair, kind)
_MARKET_ROWS: tuple[tuple[str, str, str], ...] = (
    ("BIST 100", "XU100", "instrument"),
    ("S&P 500", "^GSPC", "instrument"),
    ("USD/TRY", "USDTRY", "fx"),
    ("EUR/TRY", "EURTRY", "fx"),
    ("Altın (USD/oz)", "XAUUSD", "fx"),
)


def _fetch_latest_actions(session_factory, instrument_ids: tuple[int, ...]) -> dict[int, tuple[str, str]]:
    """Verilen instrument'lar için en yeni recommendation (action, timeframe).

    Her vade için ayrı; sadece KÜÇÜK vade (kısa > orta > uzun) öncelikli olanı döner —
    Pozisyon takibinde en hızlı reaksiyon gereken sinyal görünür.
    """
    if not instrument_ids:
        return {}
    from sqlalchemy import select
    from app.db.models import Recommendation

    out: dict[int, tuple[str, str]] = {}
    tf_priority = {"short": 0, "mid": 1, "long": 2}
    with session_factory() as session:
        stmt = (
            select(
                Recommendation.instrument_id,
                Recommendation.action,
                Recommendation.timeframe,
                Recommendation.generated_at,
            )
            .where(Recommendation.instrument_id.in_(instrument_ids))
            .order_by(Recommendation.generated_at.desc())
        )
        rows = session.execute(stmt).all()

    # En yeni + en kısa vadeyi seç
    seen_by_inst: dict[int, tuple[int, str, str]] = {}
    for inst_id, action, timeframe, _gen in rows:
        prio = tf_priority.get(str(timeframe), 99)
        if int(inst_id) not in seen_by_inst:
            seen_by_inst[int(inst_id)] = (prio, str(action), str(timeframe))

    for inst_id, (_p, action, tf) in seen_by_inst.items():
        out[inst_id] = (action, tf)
    return out


def _fetch_market_summary(session_factory) -> list[tuple[str, Optional[float], Optional[float], Optional[float]]]:
    """3 piyasa özeti satırını DB'den oku.

    Her satır için son 2 kaydı çekip değişimi hesaplar; tek kayıt varsa
    değişim ``None`` döner.
    """
    from sqlalchemy import select  # noqa: WPS433

    from app.db.models import FxRate, Instrument, PriceHistory  # noqa: WPS433

    out: list[tuple[str, Optional[float], Optional[float], Optional[float]]] = []
    session = session_factory()
    try:
        for label, key, kind in _MARKET_ROWS:
            last, change, change_pct = None, None, None
            try:
                if kind == "instrument":
                    instr = session.execute(
                        select(Instrument.id).where(Instrument.ticker == key)
                    ).first()
                    if instr:
                        rows = session.execute(
                            select(PriceHistory.close, PriceHistory.verified_close)
                            .where(PriceHistory.instrument_id == int(instr[0]))
                            .order_by(PriceHistory.timestamp.desc())
                            .limit(2)
                        ).all()
                        if rows:
                            chosen = rows[0][1] if rows[0][1] is not None else rows[0][0]
                            last = float(chosen) if chosen is not None else None
                            if len(rows) > 1 and last is not None:
                                prev = rows[1][1] if rows[1][1] is not None else rows[1][0]
                                if prev is not None and float(prev) != 0:
                                    prev_f = float(prev)
                                    change = last - prev_f
                                    change_pct = (change / prev_f) * 100.0
                elif kind == "fx":
                    rows = session.execute(
                        select(FxRate.rate)
                        .where(FxRate.pair == key)
                        .order_by(FxRate.timestamp.desc())
                        .limit(2)
                    ).all()
                    if rows:
                        last = float(rows[0][0]) if rows[0][0] is not None else None
                        if len(rows) > 1 and last is not None:
                            prev = rows[1][0]
                            if prev is not None and float(prev) != 0:
                                prev_f = float(prev)
                                change = last - prev_f
                                change_pct = (change / prev_f) * 100.0
            except Exception as exc:  # noqa: BLE001
                logger.debug("Piyasa satırı '{}' alınamadı: {}", label, exc)
            out.append((label, last, change, change_pct))
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()
    return out
