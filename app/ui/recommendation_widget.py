"""Öneri kartı + açıklanabilirlik bileşeni — Faz 2 erken + Faz 3 cilası.

Doküman §5.3 (Öneri Çıktısı), §5.4 (Backtesting), §5.5 (Risk Yönetimi)
ve §5.6 (Şeffaflık ve Açıklanabilirlik) referans alınmıştır.

Yapı:
- Üst: BÜYÜK aksiyon rozeti (AL/BEKLE/SAT) + güven yüzdesi.
- Orta: ticker + güncel fiyat + hedef fiyat + vade etiketi.
- Açıklanabilirlik: 3 yatay bar (Teknik / Sentiment / Mutabakat).
- Türkçe açıklama metni.
- Alt: Risk seviyesi rozeti + sayısal risk skoru.
- **Faz 3 eklemesi — Sekmeler:**
    - "Backtest Performansı": strateji × vade × Sharpe + max DD + win rate.
    - "Risk Uyarıları": korelasyon + sektör yoğunlaşması uyarıları.
    - "Botu Dinleseydin": paper_trading simülasyonu özet sonucu.
- Buton: "Bot Pick'lere Ekle" — Faz 3'te AKTİF (BotPicksService.add_pick).

Faz 3 TODO (backend bağlantısı):
    # - Backtester.run_strategy(...) → BacktestResult listesi
    # - RiskManager.diversification_warnings(portfolio_id)
    # - RiskManager.correlation_warnings(portfolio_id, ticker)
    # - PaperTradingService.simulate_bot_picks(...) → SimResult
    # - BotPicksService.add_pick(ticker, timeframe, action, confidence, ...)
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import fmt_money, fmt_pct

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge

try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False


# ---------------------------------------------------------------------------
# Sabitler — renkler
# ---------------------------------------------------------------------------

ACTION_PALETTE: dict[str, tuple[str, str, str]] = {
    # action -> (bg color, fg color, Türkçe etiket)
    "BUY": ("#2E7D32", "#FFFFFF", "AL"),
    "HOLD": ("#9E9E9E", "#FFFFFF", "BEKLE"),
    "SELL": ("#C62828", "#FFFFFF", "SAT"),
}

RISK_PALETTE: dict[str, tuple[str, str, str]] = {
    "low": ("#388E3C", "#FFFFFF", "Düşük"),
    "medium": ("#F57C00", "#FFFFFF", "Orta"),
    "high": ("#D32F2F", "#FFFFFF", "Yüksek"),
}

TIMEFRAME_TR: dict[str, str] = {
    "short": "Kısa Vade",
    "mid": "Orta Vade",
    "long": "Uzun Vade",
}

# Açıklanabilirlik bar renkleri (her sinyal grubu için)
CONTRIBUTION_COLORS: dict[str, str] = {
    "tech": "#1976D2",       # mavi — teknik
    "sentiment": "#7B1FA2",  # mor  — sentiment
    "consensus": "#00838F",  # cyan — mutabakat
}

CONTRIBUTION_LABELS_TR: dict[str, str] = {
    "tech": "Teknik",
    "sentiment": "Sentiment",
    "consensus": "Mutabakat",
}


# ---------------------------------------------------------------------------
# View model (UI dışındaki tüketicilerin doğrudan RecommendationOutput
# gönderebilmesi için adaptör; ancak bağımlılık tek yönlü olsun diye burada
# basit bir snapshot dataclass tanımlıyoruz)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecommendationView:
    """``RecommendationOutput`` + ticker/fiyat ekstrası UI snapshot'ı."""

    ticker: str
    current_price: Decimal
    currency: str
    action: str            # 'BUY' | 'HOLD' | 'SELL'
    confidence: float      # 0..100
    timeframe: str         # 'short' | 'mid' | 'long'
    risk_level: str        # 'low' | 'medium' | 'high'
    risk_score: float      # 0..100
    target_price: Optional[Decimal]
    summary: str           # Türkçe açıklama (RecommendationOutput.summary)
    contributions: dict[str, float]  # {'tech': ağırlıklı_katkı, ...}


# ---------------------------------------------------------------------------
# Faz 3 ek view-model'leri
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BacktestRow:
    """Backtest sonuç satırı — ``backtest_results`` tablosundan."""

    strategy_name: str
    timeframe: str
    total_return: float    # %
    sharpe_ratio: float
    max_drawdown: float    # %
    win_rate: float        # %
    avg_hold_days: float


@dataclass(frozen=True, slots=True)
class RiskWarning:
    """Risk uyarısı — ``RiskManager.diversification_warnings``'tan."""

    level: str       # 'info' | 'warning' | 'critical'
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class BotSimResult:
    """Botu dinleseydin simülasyon sonucu — PaperTradingService'ten."""

    period_days: int
    simulated_return_pct: float
    bot_picks_used: int
    actual_user_return_pct: float
    difference_pct: float


# ---------------------------------------------------------------------------
# Yardımcı widget'lar
# ---------------------------------------------------------------------------


def _make_action_badge(action: str, confidence: float, parent: QWidget) -> QWidget:
    """BÜYÜK aksiyon rozeti (AL/BEKLE/SAT) + güven yüzdesi."""
    bg, fg, label = ACTION_PALETTE.get(action, ACTION_PALETTE["HOLD"])
    container = QWidget(parent)
    lay = QHBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(12)

    badge = QLabel(label)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(
        f"background-color: {bg}; color: {fg}; "
        f"border-radius: 12px; padding: 12px 28px; "
        f"font-weight: 800; font-size: 24pt;"
    )

    conf = QLabel(f"Güven {fmt_pct(confidence, decimals=0)}")
    conf_font = QFont(conf.font())
    conf_font.setPointSize(conf_font.pointSize() + 4)
    conf_font.setBold(True)
    conf.setFont(conf_font)
    conf.setStyleSheet("color: gray;")

    lay.addWidget(badge)
    lay.addWidget(conf)
    lay.addStretch(1)
    return container


def _make_risk_badge(risk_level: str, risk_score: float, parent: QWidget) -> QWidget:
    bg, fg, label = RISK_PALETTE.get(risk_level, RISK_PALETTE["medium"])
    container = QWidget(parent)
    lay = QHBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)

    lbl = QLabel(f"Risk: {label}")
    lbl.setStyleSheet(
        f"background-color: {bg}; color: {fg}; "
        f"border-radius: 8px; padding: 4px 14px; font-weight: 700;"
    )

    score_lbl = QLabel(f"({risk_score:.0f}/100)")
    score_lbl.setStyleSheet("color: gray;")

    lay.addWidget(lbl)
    lay.addWidget(score_lbl)
    lay.addStretch(1)
    return container


class _ContributionBar(QWidget):
    """Tek bir sinyal grubu için: ad — yatay bar — yüzde."""

    def __init__(
        self,
        name_key: str,
        contribution: float,
        max_contribution: float,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        label_tr = CONTRIBUTION_LABELS_TR.get(name_key, name_key)
        color = CONTRIBUTION_COLORS.get(name_key, "#777777")

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(8)

        name_lbl = QLabel(label_tr)
        name_lbl.setMinimumWidth(90)
        f = QFont(name_lbl.font())
        f.setBold(True)
        name_lbl.setFont(f)

        bar = QProgressBar()
        bar.setRange(0, max(1, int(max_contribution * 100)))
        bar.setValue(int(contribution * 100))
        bar.setTextVisible(False)
        bar.setFixedHeight(16)
        bar.setStyleSheet(
            f"QProgressBar {{ background-color: rgba(120,120,120,40); "
            f"border-radius: 8px; }} "
            f"QProgressBar::chunk {{ background-color: {color}; "
            f"border-radius: 8px; }}"
        )

        # Yüzde etiketi (0..100 üzerinden katkının paydası
        # composite_max_possible = sum(weight*100) = 100 olduğu için doğrudan %)
        pct_lbl = QLabel(fmt_pct(contribution, decimals=1))
        pct_lbl.setMinimumWidth(54)
        pct_lbl.setAlignment(Qt.AlignmentFlag.AlignRight)
        pct_lbl.setStyleSheet("color: gray;")

        lay.addWidget(name_lbl)
        lay.addWidget(bar, stretch=1)
        lay.addWidget(pct_lbl)


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class RecommendationWidget(QWidget):
    """Bot önerisini açıklayan kart bileşeni (Faz 2 erken versiyon).

    Pop-up (``QDialog`` içine) veya gömülü kullanılabilir; içinde tüm öneri
    detayları + açıklanabilirlik çubukları + Türkçe metin gösterilir.
    """

    #: "Bot Pick'lere Ekle" tıklandığında — Faz 3'te ``BotPicksService`` dinler.
    add_to_picks_requested = Signal(str)  # ticker

    def __init__(
        self,
        view: Optional[RecommendationView] = None,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._view: Optional[RecommendationView] = None
        # Faz 3 yan veriler — set_backtests / set_risk_warnings / set_bot_sim ile yüklenir
        self._backtests: list[BacktestRow] = []
        self._risks: list[RiskWarning] = []
        self._bot_sim: Optional[BotSimResult] = None
        self._build_ui()
        if view is not None:
            self.set_view(view)
        self.setMinimumSize(580, 620)
        # Bridge varsa otomatik backtests + risk warnings çek
        if view is not None and bridge and bridge.available:
            self._load_extras_from_backend()

    def set_bridge(self, bridge: "BackendBridge") -> None:
        self._bridge = bridge
        if self._view is not None and bridge.available:
            self._load_extras_from_backend()

    def _load_extras_from_backend(self) -> None:
        """Backtest performans, risk uyarıları ve bot simülasyonunu çek."""

        if self._bridge is None or self._view is None or not self._bridge.available:
            return
        bridge = self._bridge
        ticker = self._view.ticker

        # 1) Backtests — backtest_results tablosundan ticker bazlı çek
        session_factory = bridge.session_factory

        async def _fetch_backtests():
            from sqlalchemy import select  # noqa: WPS433
            from app.db.models import BacktestResult  # noqa: WPS433

            session = session_factory()
            try:
                stmt = (
                    select(BacktestResult)
                    .order_by(BacktestResult.run_at.desc())
                    .limit(20)
                )
                return list(session.execute(stmt).scalars().all())
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()

        def _on_backtests(rows):
            try:
                bts = [
                    BacktestRow(
                        strategy_name=str(r.strategy_name or "—"),
                        timeframe=str(r.timeframe or "short"),
                        total_return=float(r.total_return or 0),
                        sharpe_ratio=float(r.sharpe_ratio or 0),
                        max_drawdown=float(r.max_drawdown or 0),
                        win_rate=float(r.win_rate or 0),
                        avg_hold_days=float(r.avg_hold_days or 0),
                    )
                    for r in (rows or [])
                ]
                self.set_backtests(bts)
            except Exception as exc:  # noqa: BLE001
                logger.debug("recommendation backtests hata: {}", exc)

        bridge.run_async(_fetch_backtests, on_success=_on_backtests)

        # 2) Risk warnings — bot/mid wallet için diversification + sector
        if bridge.account_id is not None:
            account_id = int(bridge.account_id)

            async def _fetch_risks():
                from app.portfolio.wallets import WalletService  # noqa: WPS433

                wallet_id = await bridge.wallets.get_wallet(account_id, "bot", "mid")
                warnings = await bridge.risk_mgr.diversification_warnings(wallet_id)
                sectors = await bridge.risk_mgr.sector_concentration(wallet_id)
                return warnings, sectors

            def _on_risks(payload):
                try:
                    warnings, sectors = payload
                    risk_views: list[RiskWarning] = []
                    for w in (warnings or []):
                        risk_views.append(
                            RiskWarning(
                                level=getattr(w, "level", "warning"),
                                title=getattr(w, "title", str(w)),
                                detail=getattr(w, "detail", ""),
                            )
                        )
                    for sector, pct in (sectors or {}).items():
                        if pct >= 30:
                            risk_views.append(
                                RiskWarning(
                                    level="warning",
                                    title=f"Sektör yoğunlaşması: {sector}",
                                    detail=f"%{pct:.1f}",
                                )
                            )
                    self.set_risk_warnings(risk_views)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("recommendation risks hata: {}", exc)

            bridge.run_async(_fetch_risks, on_success=_on_risks)

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        # ----- başlık satırı: ticker + vade
        self._header_grid = QGridLayout()
        self._header_grid.setHorizontalSpacing(16)

        self._ticker_label = QLabel("—")
        ticker_font = self._ticker_label.font()
        ticker_font.setPointSize(ticker_font.pointSize() + 8)
        ticker_font.setBold(True)
        self._ticker_label.setFont(ticker_font)

        self._timeframe_label = QLabel("—")
        self._timeframe_label.setStyleSheet(
            "background-color: rgba(120,120,120,40); border-radius: 8px; "
            "padding: 4px 10px;"
        )

        self._header_grid.addWidget(self._ticker_label, 0, 0)
        self._header_grid.addWidget(
            self._timeframe_label, 0, 1, alignment=Qt.AlignmentFlag.AlignLeft
        )
        self._header_grid.setColumnStretch(2, 1)
        root.addLayout(self._header_grid)

        # ----- aksiyon rozeti
        self._action_holder = QWidget(self)
        self._action_holder_layout = QVBoxLayout(self._action_holder)
        self._action_holder_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self._action_holder)

        # ----- fiyat satırı (mevcut / hedef)
        price_frame = QFrame(self)
        price_frame.setFrameShape(QFrame.Shape.StyledPanel)
        price_layout = QGridLayout(price_frame)
        price_layout.setContentsMargins(12, 8, 12, 8)
        price_layout.setHorizontalSpacing(20)

        price_layout.addWidget(QLabel("Güncel Fiyat"), 0, 0)
        self._current_price_lbl = QLabel("—")
        cur_font = QFont(self._current_price_lbl.font())
        cur_font.setBold(True)
        cur_font.setPointSize(cur_font.pointSize() + 2)
        self._current_price_lbl.setFont(cur_font)
        price_layout.addWidget(self._current_price_lbl, 1, 0)

        price_layout.addWidget(QLabel("Hedef Fiyat"), 0, 1)
        self._target_price_lbl = QLabel("—")
        self._target_price_lbl.setFont(cur_font)
        self._target_price_lbl.setStyleSheet("color: #2E7D32;")  # yeşil hedef
        price_layout.addWidget(self._target_price_lbl, 1, 1)

        root.addWidget(price_frame)

        # ----- açıklanabilirlik (yatay çubuklar)
        self._explain_frame = QFrame(self)
        self._explain_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._explain_layout = QVBoxLayout(self._explain_frame)
        self._explain_layout.setContentsMargins(12, 8, 12, 8)
        self._explain_layout.setSpacing(6)

        self._explain_title = QLabel("Sinyal Katkıları")
        et_font = QFont(self._explain_title.font())
        et_font.setBold(True)
        self._explain_title.setFont(et_font)
        self._explain_layout.addWidget(self._explain_title)

        root.addWidget(self._explain_frame)

        # ----- Sekmeler: Özet / Backtest / Risk / Bot Simülasyonu (Faz 3)
        self._tabs = QTabWidget(self)

        # Tab 1 — Özet
        self._summary_browser = QTextBrowser(self)
        self._summary_browser.setPlaceholderText(
            "Bir öneri yüklendiğinde özet burada görünür."
        )
        self._tabs.addTab(self._summary_browser, "Özet")

        # Tab 2 — Backtest performansı (TODO: Backtester.run_strategy)
        self._backtest_table = QTableWidget(0, 6)
        self._backtest_table.setHorizontalHeaderLabels(
            ["Strateji", "Vade", "Getiri %", "Sharpe", "Max DD %", "Win Rate %"]
        )
        self._backtest_table.verticalHeader().setVisible(False)
        self._backtest_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._backtest_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._tabs.addTab(self._backtest_table, "Backtest Performansı")

        # Tab 3 — Risk uyarıları (TODO: RiskManager.diversification_warnings)
        self._risk_list = QListWidget()
        self._risk_list.setAlternatingRowColors(True)
        self._tabs.addTab(self._risk_list, "Risk Uyarıları")

        # Tab 4 — Botu Dinleseydin (TODO: PaperTradingService.simulate_bot_picks)
        self._bot_sim_widget = QFrame()
        self._bot_sim_layout = QVBoxLayout(self._bot_sim_widget)
        self._bot_sim_layout.setContentsMargins(12, 12, 12, 12)
        self._bot_sim_layout.setSpacing(8)
        sim_placeholder = QLabel("Henüz simülasyon verisi yok.")
        sim_placeholder.setStyleSheet("color: gray; font-style: italic;")
        self._bot_sim_layout.addWidget(sim_placeholder)
        self._bot_sim_layout.addStretch(1)
        self._tabs.addTab(self._bot_sim_widget, "Botu Dinleseydin")

        root.addWidget(self._tabs, stretch=1)

        # ----- risk rozeti + aksiyon butonu
        bottom = QHBoxLayout()
        self._risk_holder = QWidget(self)
        self._risk_holder_layout = QVBoxLayout(self._risk_holder)
        self._risk_holder_layout.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self._risk_holder)
        bottom.addStretch(1)

        self._add_to_picks_btn = QPushButton("Bot Pick'lere Ekle")
        self._add_to_picks_btn.setToolTip(
            "Bu öneriyi Bot Picks listesine ekle."
        )
        # Faz 3: BUTON AKTİF — view yüklendiğinde sinyal emit eder.
        # TODO: BotPicksService.add_pick bağlandığında dinleyen taraf
        #       set_view sonrası butonu otomatik enable yapar.
        self._add_to_picks_btn.setEnabled(False)
        if QTAWESOME_AVAILABLE:
            try:
                self._add_to_picks_btn.setIcon(qta.icon("fa5s.robot"))
            except Exception:  # noqa: BLE001
                pass
        self._add_to_picks_btn.clicked.connect(self._on_add_to_picks)
        bottom.addWidget(self._add_to_picks_btn)

        root.addLayout(bottom)

    # ------------------------------------------------------------------ public
    def set_view(self, view: RecommendationView) -> None:
        """Widget içeriğini yeni bir öneri view-model'i ile günceller."""
        self._view = view
        self._ticker_label.setText(view.ticker)
        self._timeframe_label.setText(TIMEFRAME_TR.get(view.timeframe, view.timeframe))

        # Aksiyon rozetini yeniden inşa et
        self._clear_holder(self._action_holder_layout)
        self._action_holder_layout.addWidget(
            _make_action_badge(view.action, view.confidence, self._action_holder)
        )

        # Fiyat etiketleri
        self._current_price_lbl.setText(fmt_money(view.current_price, view.currency))
        if view.target_price is not None:
            self._target_price_lbl.setText(
                fmt_money(view.target_price, view.currency)
            )
            self._target_price_lbl.setStyleSheet("color: #2E7D32; font-weight: 700;")
        else:
            self._target_price_lbl.setText("—")
            self._target_price_lbl.setStyleSheet("color: gray;")

        # Açıklanabilirlik çubuklarını yeniden inşa et
        self._rebuild_contribution_bars(view.contributions)

        # Türkçe açıklama
        self._summary_browser.setPlainText(view.summary)

        # Risk rozeti
        self._clear_holder(self._risk_holder_layout)
        self._risk_holder_layout.addWidget(
            _make_risk_badge(view.risk_level, view.risk_score, self._risk_holder)
        )

        # Faz 3: view yüklenince "Bot Pick'lere Ekle" butonu aktifleşir.
        self._add_to_picks_btn.setEnabled(True)

    # ------------------------------------------------------------------
    # Faz 3 ek setter'lar
    # ------------------------------------------------------------------
    def set_backtests(self, rows: list[BacktestRow]) -> None:
        """Backtest performans tab'ını doldur."""
        self._backtests = rows
        self._backtest_table.setRowCount(len(rows))
        for ridx, r in enumerate(rows):
            self._backtest_table.setItem(ridx, 0, QTableWidgetItem(r.strategy_name))
            self._backtest_table.setItem(
                ridx, 1, QTableWidgetItem(TIMEFRAME_TR.get(r.timeframe, r.timeframe))
            )
            ret_item = QTableWidgetItem(fmt_pct(r.total_return, with_sign=True))
            ret_item.setForeground(
                QColor("#2E7D32" if r.total_return >= 0 else "#C62828")
            )
            self._backtest_table.setItem(ridx, 2, ret_item)
            # Sharpe — locale formatla (fmt_money'nin currency=None biçimi)
            self._backtest_table.setItem(
                ridx, 3, QTableWidgetItem(fmt_money(r.sharpe_ratio))
            )
            dd_item = QTableWidgetItem(fmt_pct(r.max_drawdown))
            dd_item.setForeground(QColor("#C62828"))
            self._backtest_table.setItem(ridx, 4, dd_item)
            self._backtest_table.setItem(
                ridx, 5, QTableWidgetItem(fmt_pct(r.win_rate, decimals=1))
            )

    def set_risk_warnings(self, warnings: list[RiskWarning]) -> None:
        """Risk uyarıları tab'ını doldur."""
        self._risks = warnings
        self._risk_list.clear()
        palette = {
            "info": "#1976D2",
            "warning": "#F57C00",
            "critical": "#C62828",
        }
        for w in warnings:
            item = QListWidgetItem(f"[{w.level.upper()}] {w.title} — {w.detail}")
            item.setForeground(QColor(palette.get(w.level, "#9E9E9E")))
            self._risk_list.addItem(item)
        if not warnings:
            empty = QListWidgetItem("Şu an kayda değer risk uyarısı yok.")
            empty.setForeground(QColor("#9E9E9E"))
            self._risk_list.addItem(empty)

    def set_bot_sim(self, sim: BotSimResult) -> None:
        """Botu Dinleseydin sekmesini doldur."""
        self._bot_sim = sim
        # Eskiyi temizle
        while self._bot_sim_layout.count():
            it = self._bot_sim_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        intro = QLabel(
            f"Son {sim.period_days} günde botun {sim.bot_picks_used} önerisi"
            " uygulansaydı:"
        )
        intro.setStyleSheet("color: gray;")
        self._bot_sim_layout.addWidget(intro)

        sim_color = "#2E7D32" if sim.simulated_return_pct >= 0 else "#C62828"
        sim_lbl = QLabel(
            f"Bot Simülasyonu: <b style='color:{sim_color}'>"
            f"{fmt_pct(sim.simulated_return_pct, with_sign=True)}</b>"
        )
        f = QFont(sim_lbl.font())
        f.setPointSize(f.pointSize() + 4)
        sim_lbl.setFont(f)
        self._bot_sim_layout.addWidget(sim_lbl)

        usr_color = "#2E7D32" if sim.actual_user_return_pct >= 0 else "#C62828"
        usr_lbl = QLabel(
            f"Gerçek Getirin: <span style='color:{usr_color}'>"
            f"{fmt_pct(sim.actual_user_return_pct, with_sign=True)}</span>"
        )
        self._bot_sim_layout.addWidget(usr_lbl)

        diff_color = "#2E7D32" if sim.difference_pct >= 0 else "#C62828"
        diff_lbl = QLabel(
            f"<b style='color:{diff_color}'>"
            f"Fark: {fmt_pct(sim.difference_pct, with_sign=True)}</b>"
        )
        diff_f = QFont(diff_lbl.font())
        diff_f.setBold(True)
        diff_lbl.setFont(diff_f)
        self._bot_sim_layout.addWidget(diff_lbl)

        self._bot_sim_layout.addStretch(1)

    # ------------------------------------------------------------------ internals
    def _rebuild_contribution_bars(self, contributions: dict[str, float]) -> None:
        # Mevcut bar'ları temizle ama başlığı koru
        while self._explain_layout.count() > 1:
            item = self._explain_layout.takeAt(1)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        # Maksimum referans: 100 (composite max = sum weights * 100 = 100)
        max_ref = 100.0
        # Sıralı göster: tech, sentiment, consensus
        for key in ("tech", "sentiment", "consensus"):
            value = float(contributions.get(key, 0.0))
            self._explain_layout.addWidget(
                _ContributionBar(key, value, max_ref, parent=self._explain_frame)
            )

    @staticmethod
    def _clear_holder(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    # ------------------------------------------------------------------ slots
    @Slot()
    def _on_add_to_picks(self) -> None:
        if self._view is None:
            return
        self.add_to_picks_requested.emit(self._view.ticker)
        # Backend varsa BotPicksService.add_pick — instrument_id'yi
        # önce instruments tablosundan ticker ile bul.
        if self._bridge and self._bridge.available:
            bridge = self._bridge
            view = self._view

            async def _add_async():
                from sqlalchemy import select  # noqa: WPS433
                from app.db.models import Instrument  # noqa: WPS433

                session_factory = bridge.session_factory
                session = session_factory()
                try:
                    instr = session.execute(
                        select(Instrument).where(Instrument.ticker == view.ticker)
                    ).scalar_one_or_none()
                finally:
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()
                if instr is None:
                    raise LookupError(
                        f"Instrument bulunamadı: {view.ticker}. "
                        "Önce data-collector ile seed edilmiş olmalı."
                    )
                return await bridge.bot_picks.add_pick(
                    instrument_id=int(instr.id),
                    timeframe=view.timeframe,
                    action=view.action,
                    confidence=view.confidence,
                    current_price=view.current_price,
                    target_price=view.target_price,
                )

            from app.ui.components import ToastManager  # noqa: WPS433

            bridge.run_async(
                _add_async,
                on_success=lambda _r: ToastManager.instance().show(
                    f"{view.ticker} bot picks'e eklendi.", level="success"
                ),
                on_error=lambda exc: ToastManager.instance().show(
                    f"Eklenemedi: {exc}", level="error", duration_ms=5000
                ),
            )


# ---------------------------------------------------------------------------
# Dialog sarmalayıcı (modal pop-up)
# ---------------------------------------------------------------------------


class RecommendationDialog(QDialog):
    """``RecommendationWidget``'ı modal dialog olarak gösterir."""

    def __init__(
        self,
        view: RecommendationView,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Bot Önerisi — {view.ticker}")
        self.setModal(True)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._widget = RecommendationWidget(view, parent=self, bridge=bridge)
        self._widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        lay.addWidget(self._widget)

        close_btn = QPushButton("Kapat")
        close_btn.clicked.connect(self.accept)
        button_row = QHBoxLayout()
        button_row.setContentsMargins(20, 0, 20, 16)
        button_row.addStretch(1)
        button_row.addWidget(close_btn)
        lay.addLayout(button_row)

    def widget(self) -> RecommendationWidget:
        return self._widget


__all__ = [
    "RecommendationWidget",
    "RecommendationDialog",
    "RecommendationView",
    "BacktestRow",
    "RiskWarning",
    "BotSimResult",
    "ACTION_PALETTE",
    "RISK_PALETTE",
    "TIMEFRAME_TR",
    "CONTRIBUTION_COLORS",
    "CONTRIBUTION_LABELS_TR",
]
