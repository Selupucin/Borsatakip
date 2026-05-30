"""Bütçe ekranı — Faz 3 ilk versiyon (placeholder veri).

Doküman §6.1 (Bütçe Dağılımı — Havuz × Vade Matrisi) referans alınmıştır.

Yapı:
- Üst: toplam bakiye + serbest nakit (``MetricCard``).
- 2 havuz × 3 vade = 6 cüzdan hücresi grid'i. Her hücre: pool/timeframe
  rozeti + tahsis + nakit + pozisyon değeri + getiri %.
- Hücreye tıklayınca sağ panelde düzenleme formu açılır:
  tutar input + "Tahsis et" / "Yeniden dağıt" / "Çek" butonları.
- Yan panel: "Para Ekle" (deposit) / "Para Çek" (withdrawal) ana butonları.
- Alt: son ``cash_flows`` tablosu.
- Sağ üstte: "Tüm cüzdanları sıfırla" (paper mod için).

Faz 3 TODO (backend bağlantısı):
    # - AccountService.get_account() → currency, cash_balance
    # - WalletService.list_wallets(account_id) + compute_snapshot → 6 hücre
    # - WalletService.allocate(wallet_id, amount, notes)
    # - WalletService.reallocate(from_wallet_id, to_wallet_id, amount)
    # - CashFlowService.deposit(account_id, amount, notes)
    # - CashFlowService.withdrawal(account_id, amount, notes)
    # - CashFlowService.list_recent(account_id, limit=20)
    # - PaperTradingService.reset_paper_account(account_id) → paper modda
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import apply_tr_locale, fmt_money, fmt_pct
from app.ui.components import HelpBanner, MetricCard, ToastManager
from app.ui.portfolio_widget import (
    POOL_LABEL_TR,
    TIMEFRAME_LABEL_TR,
    WALLET_BADGE_COLORS,
)

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


# ---------------------------------------------------------------------------
# Placeholder view-model
# ---------------------------------------------------------------------------


@dataclass
class WalletCellData:
    """UI hücre verisi — backend ``WalletSnapshot`` ile birebir eşlenecek."""

    pool: str
    timeframe: str
    allocated: Decimal
    cash_balance: Decimal
    positions_value: Decimal
    return_pct: float


@dataclass(frozen=True)
class CashFlowRow:
    occurred_at: datetime
    flow_type: str   # 'deposit' | 'withdrawal' | 'allocate' | 'reallocate'
    amount: Decimal
    wallet_label: str
    notes: str


FLOW_TYPE_LABEL_TR: dict[str, str] = {
    "deposit": "Para Ekleme",
    "withdrawal": "Para Çekme",
    "allocate": "Tahsis",
    "reallocate": "Yeniden Dağıtım",
    "trade_buy": "Alış",
    "trade_sell": "Satış",
}


def _sample_wallets() -> list[WalletCellData]:
    return [
        WalletCellData("bot", "short", Decimal("4000"), Decimal("1200"), Decimal("2900"), 2.5),
        WalletCellData("bot", "mid", Decimal("3000"), Decimal("400"), Decimal("2750"), 5.0),
        WalletCellData("bot", "long", Decimal("3000"), Decimal("200"), Decimal("3120"), 10.7),
        WalletCellData("user", "short", Decimal("2000"), Decimal("450"), Decimal("1610"), -3.0),
        WalletCellData("user", "mid", Decimal("1500"), Decimal("100"), Decimal("1520"), 1.3),
        WalletCellData("user", "long", Decimal("1500"), Decimal("0"), Decimal("1690"), 12.7),
    ]


def _sample_cash_flows() -> list[CashFlowRow]:
    now = datetime.utcnow()
    return [
        CashFlowRow(now, "deposit", Decimal("2000"), "—", "Maaş eklemesi"),
        CashFlowRow(now, "allocate", Decimal("1000"), "Bot · Orta", ""),
        CashFlowRow(now, "reallocate", Decimal("500"), "Bot · Kısa → Kendim · Uzun", ""),
        CashFlowRow(now, "withdrawal", Decimal("300"), "—", ""),
    ]


# ---------------------------------------------------------------------------
# Yardımcı widget: tek cüzdan hücresi
# ---------------------------------------------------------------------------


class _WalletCell(QFrame):
    """6 hücreli grid'in tek bir kutusu — tıklanınca seçim sinyali."""

    clicked = Signal(str, str)  # pool, timeframe
    deposit_clicked = Signal(str, str)  # pool, timeframe — "Cüzdana Yatır"
    allocate_clicked = Signal(str, str)  # pool, timeframe — "Tahsis Et"

    def __init__(self, data: WalletCellData, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._data = data
        self.setObjectName("walletCell")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setStyleSheet(
            "#walletCell { border: 1px solid rgba(120,120,120,90); "
            "border-radius: 8px; }"
            "#walletCell:hover { border: 1px solid #1976D2; }"
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(4)

        # Üst: rozet
        top = QHBoxLayout()
        color = WALLET_BADGE_COLORS.get((data.pool, data.timeframe), "#757575")
        label = f"{POOL_LABEL_TR.get(data.pool, data.pool)} · {TIMEFRAME_LABEL_TR.get(data.timeframe, data.timeframe)}"
        badge = QLabel(label)
        badge.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 6px; padding: 2px 10px; font-weight: 600;"
        )
        top.addWidget(badge)
        top.addStretch(1)
        ret_color = "#2E7D32" if data.return_pct >= 0 else "#C62828"
        ret_lbl = QLabel(fmt_pct(data.return_pct, with_sign=True))
        ret_lbl.setStyleSheet(
            f"color: {ret_color}; font-weight: 700;"
        )
        top.addWidget(ret_lbl)
        root.addLayout(top)

        # Tahsis (büyük)
        alloc_lbl = QLabel(fmt_money(data.allocated, "TRY"))
        af = QFont(alloc_lbl.font())
        af.setPointSize(af.pointSize() + 5)
        af.setBold(True)
        alloc_lbl.setFont(af)
        root.addWidget(alloc_lbl)
        root.addWidget(self._small("Tahsis edilen toplam"))

        # Alt iki satır: nakit + pozisyon değeri
        details = QGridLayout()
        details.setHorizontalSpacing(12)
        details.setVerticalSpacing(2)
        details.addWidget(self._small("Nakit:"), 0, 0)
        details.addWidget(self._mono(fmt_money(data.cash_balance, "TRY")), 0, 1)
        details.addWidget(self._small("Pozisyon:"), 1, 0)
        details.addWidget(self._mono(fmt_money(data.positions_value, "TRY")), 1, 1)
        details.setColumnStretch(1, 1)
        root.addLayout(details)

        # Hızlı işlem butonları: "Cüzdana Yatır" (deposit_and_allocate) + "Tahsis Et"
        btn_row = QHBoxLayout()
        btn_row.setSpacing(6)
        self._deposit_btn = QPushButton("Cüzdana Yatır")
        self._deposit_btn.setToolTip(
            "Hesabınıza para yatırma + cüzdana tahsis tek adımda"
        )
        self._deposit_btn.setStyleSheet(
            "QPushButton { background-color: #2E7D32; color: white; "
            "font-weight: 600; border-radius: 6px; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #1B5E20; }"
        )
        self._deposit_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._deposit_btn.clicked.connect(
            lambda: self.deposit_clicked.emit(self._data.pool, self._data.timeframe)
        )

        self._alloc_btn = QPushButton("Tahsis Et")
        self._alloc_btn.setToolTip(
            "Hesabınızdaki serbest nakitten cüzdana tahsis edin"
        )
        self._alloc_btn.setStyleSheet(
            "QPushButton { background-color: #1976D2; color: white; "
            "font-weight: 600; border-radius: 6px; padding: 4px 8px; }"
            "QPushButton:hover { background-color: #1565C0; }"
        )
        self._alloc_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._alloc_btn.clicked.connect(
            lambda: self.allocate_clicked.emit(self._data.pool, self._data.timeframe)
        )

        btn_row.addWidget(self._deposit_btn)
        btn_row.addWidget(self._alloc_btn)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------
    def _small(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("color: gray;")
        f = QFont(lbl.font())
        f.setPointSize(max(8, f.pointSize() - 1))
        lbl.setFont(f)
        return lbl

    def _mono(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setStyleSheet("font-weight: 600;")
        return lbl

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._data.pool, self._data.timeframe)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class BudgetWidget(QWidget):
    """Bütçe dağılımı ekranı — backend bağlantılı (Faz 3)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._wallets: list[WalletCellData] = []
        self._wallet_id_map: dict[tuple[str, str], int] = {}
        self._flows: list[CashFlowRow] = []
        self._selected: Optional[tuple[str, str]] = None
        self._build_ui()
        self._refresh_flows_table()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        # Üst kabuk: başlık sabit + scroll alanı
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 8)
        outer.setSpacing(8)

        # Başlık + sıfırla butonu (sabit toolbar)
        header = QHBoxLayout()
        title = QLabel("Bütçe Dağılımı")
        tf = QFont(title.font())
        tf.setPointSize(tf.pointSize() + 6)
        tf.setBold(True)
        title.setFont(tf)
        header.addWidget(title)
        header.addStretch(1)
        reset_btn = QPushButton("Tüm Cüzdanları Sıfırla (Paper)")
        reset_btn.setToolTip(
            "Yalnızca Paper modunda etkindir. TODO: PaperTradingService.reset_paper_account"
        )
        reset_btn.clicked.connect(self._on_reset_paper)
        header.addWidget(reset_btn)
        outer.addLayout(header)

        outer.addWidget(
            HelpBanner(
                text=(
                    "6 cüzdan: Bot × 3 vade (Kısa/Orta/Uzun) + Kendim × 3 vade. "
                    "Önce 'Para Ekle' ile bakiye yatır, sonra hücreye tıklayıp "
                    "'Tahsis Et' ile dağıt. Cüzdanlar arasında para taşımak için "
                    "'Yeniden Dağıt' kullan."
                ),
                key="budget",
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

        # KPI: toplam bakiye + serbest nakit
        kpi_row = QHBoxLayout()
        kpi_row.setSpacing(10)
        # TODO: AccountService.get_snapshot → total_value, cash_balance
        self._kpi_total = MetricCard(
            "Toplam Bakiye", "—", "6 cüzdan + serbest nakit", trend="neutral"
        )
        self._kpi_free = MetricCard(
            "Serbest Nakit", "—", "Henüz hiçbir cüzdana tahsis edilmedi", trend="neutral"
        )
        kpi_row.addWidget(self._kpi_total)
        kpi_row.addWidget(self._kpi_free)
        # Para ekle/çek butonları
        deposit_btn = QPushButton("Para Ekle")
        deposit_btn.setMinimumHeight(48)
        deposit_btn.setStyleSheet(
            "background-color: #2E7D32; color: white; font-weight: 700; "
            "border-radius: 8px; padding: 0 18px;"
        )
        deposit_btn.clicked.connect(self._on_deposit)

        withdraw_btn = QPushButton("Para Çek")
        withdraw_btn.setMinimumHeight(48)
        withdraw_btn.setStyleSheet(
            "background-color: #C62828; color: white; font-weight: 700; "
            "border-radius: 8px; padding: 0 18px;"
        )
        withdraw_btn.clicked.connect(self._on_withdraw)

        kpi_row.addWidget(deposit_btn)
        kpi_row.addWidget(withdraw_btn)
        root.addLayout(kpi_row)

        # 2x3 grid + sağ düzenleme paneli
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        grid_box = QGroupBox("Cüzdan Matrisi (Havuz × Vade)")
        grid_layout = QGridLayout(grid_box)
        grid_layout.setSpacing(10)

        # Satır başlıkları
        for col_idx, tf in enumerate(("short", "mid", "long")):
            hdr = QLabel(TIMEFRAME_LABEL_TR.get(tf, tf))
            hf = QFont(hdr.font())
            hf.setBold(True)
            hdr.setFont(hf)
            hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid_layout.addWidget(hdr, 0, col_idx + 1)

        for row_idx, pool in enumerate(("bot", "user")):
            pool_hdr = QLabel(POOL_LABEL_TR.get(pool, pool))
            pf = QFont(pool_hdr.font())
            pf.setBold(True)
            pool_hdr.setFont(pf)
            grid_layout.addWidget(pool_hdr, row_idx + 1, 0)

            for col_idx, tf in enumerate(("short", "mid", "long")):
                data = next(
                    (w for w in self._wallets if w.pool == pool and w.timeframe == tf),
                    WalletCellData(pool, tf, Decimal("0"), Decimal("0"), Decimal("0"), 0.0),
                )
                cell = _WalletCell(data, grid_box)
                cell.clicked.connect(self._on_cell_selected)
                cell.deposit_clicked.connect(self._on_cell_deposit)
                cell.allocate_clicked.connect(self._on_cell_allocate_quick)
                grid_layout.addWidget(cell, row_idx + 1, col_idx + 1)

        for c in range(1, 4):
            grid_layout.setColumnStretch(c, 1)
        splitter.addWidget(grid_box)

        # Sağ düzenleme paneli
        edit_box = QGroupBox("Cüzdan Düzenleme")
        edit_layout = QVBoxLayout(edit_box)
        self._selected_label = QLabel("Bir cüzdan seç…")
        self._selected_label.setStyleSheet("font-weight: 700; color: gray;")
        edit_layout.addWidget(self._selected_label)

        form = QFormLayout()
        self._amount_input = QDoubleSpinBox()
        self._amount_input.setRange(0.0, 1_000_000_000.0)
        self._amount_input.setDecimals(2)
        self._amount_input.setSingleStep(100.0)
        self._amount_input.setPrefix("₺ ")
        apply_tr_locale(self._amount_input)
        form.addRow("Tutar:", self._amount_input)
        edit_layout.addLayout(form)

        btn_row = QHBoxLayout()
        self._btn_allocate = QPushButton("Tahsis Et")
        self._btn_allocate.clicked.connect(self._on_allocate)
        self._btn_reallocate = QPushButton("Yeniden Dağıt")
        self._btn_reallocate.clicked.connect(self._on_reallocate)
        self._btn_withdraw_wallet = QPushButton("Cüzdandan Çek")
        self._btn_withdraw_wallet.clicked.connect(self._on_withdraw_wallet)
        for btn in (self._btn_allocate, self._btn_reallocate, self._btn_withdraw_wallet):
            btn.setEnabled(False)
            btn_row.addWidget(btn)
        edit_layout.addLayout(btn_row)
        edit_layout.addStretch(1)

        splitter.addWidget(edit_box)
        splitter.setSizes([700, 320])
        root.addWidget(splitter, stretch=1)

        # Cash flow tablosu
        flows_box = QGroupBox("Son Para Hareketleri")
        flows_layout = QVBoxLayout(flows_box)
        self._flows_table = QTableWidget(0, 5, flows_box)
        self._flows_table.setHorizontalHeaderLabels(
            ["Tarih", "Tip", "Tutar", "Cüzdan", "Not"]
        )
        self._flows_table.verticalHeader().setVisible(False)
        self._flows_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._flows_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._flows_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        flows_layout.addWidget(self._flows_table)
        root.addWidget(flows_box, stretch=1)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

    # ------------------------------------------------------------------
    def _refresh_flows_table(self) -> None:
        self._flows_table.setRowCount(len(self._flows))
        for ridx, row in enumerate(self._flows):
            self._flows_table.setItem(
                ridx, 0, QTableWidgetItem(row.occurred_at.strftime("%Y-%m-%d %H:%M"))
            )
            self._flows_table.setItem(
                ridx, 1, QTableWidgetItem(FLOW_TYPE_LABEL_TR.get(row.flow_type, row.flow_type))
            )
            sign = "+" if row.flow_type in ("deposit",) else ("-" if row.flow_type == "withdrawal" else "")
            self._flows_table.setItem(
                ridx, 2, QTableWidgetItem(f"{sign}{fmt_money(row.amount, 'TRY')}")
            )
            self._flows_table.setItem(ridx, 3, QTableWidgetItem(row.wallet_label))
            self._flows_table.setItem(ridx, 4, QTableWidgetItem(row.notes))

    # ------------------------------------------------------------------
    # Slot'lar
    # ------------------------------------------------------------------
    @Slot(str, str)
    def _on_cell_selected(self, pool: str, timeframe: str) -> None:
        self._selected = (pool, timeframe)
        label = f"{POOL_LABEL_TR.get(pool, pool)} · {TIMEFRAME_LABEL_TR.get(timeframe, timeframe)}"
        self._selected_label.setText(f"Seçili: {label}")
        self._selected_label.setStyleSheet("font-weight: 700; color: #1976D2;")
        for btn in (self._btn_allocate, self._btn_reallocate, self._btn_withdraw_wallet):
            btn.setEnabled(True)

    @Slot(str, str)
    def _on_cell_deposit(self, pool: str, timeframe: str) -> None:
        """Hücredeki 'Cüzdana Yatır' tıklandı — deposit + allocate atomic.

        Yeni WalletService.deposit_and_allocate metodunu kullanır.
        """
        if (
            self._bridge is None
            or not self._bridge.available
            or self._bridge.account_id is None
        ):
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        wallet_id = self._wallet_id_map.get((pool, timeframe))
        if wallet_id is None:
            ToastManager.instance().show(
                "Cüzdan bulunamadı — sayfa tazelendiğinde tekrar deneyin.",
                level="error",
            )
            return
        label = (
            f"{POOL_LABEL_TR.get(pool, pool)} · "
            f"{TIMEFRAME_LABEL_TR.get(timeframe, timeframe)}"
        )
        amount, ok = QInputDialog.getDouble(
            self,
            f"Cüzdana Yatır — {label}",
            "Tutar (₺):\n(Hesabınıza yatırılır + hemen cüzdana tahsis edilir.)",
            1000.0,
            0.01,
            1e9,
            2,
        )
        if not ok or amount <= 0:
            return
        amt = Decimal(str(amount))
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.wallets.deposit_and_allocate(wallet_id, amt),
            on_success=lambda _r: self._after_mutation(
                f"{fmt_money(amt, 'TRY')} cüzdana yatırıldı ({label})."
            ),
            on_error=self._on_mutation_error,
        )

    @Slot(str, str)
    def _on_cell_allocate_quick(self, pool: str, timeframe: str) -> None:
        """Hücredeki 'Tahsis Et' butonu — hücreyi seç + tutar sor + allocate."""
        if (
            self._bridge is None
            or not self._bridge.available
            or self._bridge.account_id is None
        ):
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        wallet_id = self._wallet_id_map.get((pool, timeframe))
        if wallet_id is None:
            ToastManager.instance().show(
                "Cüzdan bulunamadı — sayfa tazelendiğinde tekrar deneyin.",
                level="error",
            )
            return
        label = (
            f"{POOL_LABEL_TR.get(pool, pool)} · "
            f"{TIMEFRAME_LABEL_TR.get(timeframe, timeframe)}"
        )
        amount, ok = QInputDialog.getDouble(
            self,
            f"Tahsis Et — {label}",
            "Tutar (₺):\n(Hesabınızdaki serbest nakitten ayrılır.)",
            1000.0,
            0.01,
            1e9,
            2,
        )
        if not ok or amount <= 0:
            return
        amt = Decimal(str(amount))
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.wallets.allocate(wallet_id, amt),
            on_success=lambda _r: self._after_mutation(
                f"Tahsis edildi: {fmt_money(amt, 'TRY')} → {label}."
            ),
            on_error=self._on_mutation_error,
        )

    # ------------------------------------------------------------------
    # Backend bağlantısı
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        if self._bridge.account_id is None:
            return
        account_id = int(self._bridge.account_id)
        bridge = self._bridge

        bridge.run_async(
            lambda: bridge.account.get_snapshot(account_id),
            on_success=self._on_account_snapshot,
            on_error=self._on_refresh_error,
        )
        bridge.run_async(
            lambda: bridge.wallets.list_wallets(account_id),
            on_success=self._on_wallets_ready,
            on_error=self._on_refresh_error,
        )
        bridge.run_async(
            lambda: bridge.cash_flows.list_flows(account_id, None, 30),
            on_success=self._on_flows_ready,
            on_error=self._on_refresh_error,
        )

    def _on_account_snapshot(self, snapshot) -> None:
        try:
            curr = snapshot.currency or "TRY"
            self._kpi_total.set_value(
                fmt_money(snapshot.total_value, curr),
                "Tüm cüzdanlar dahil",
                trend="neutral",
            )
            self._kpi_free.set_value(
                fmt_money(snapshot.cash_balance, curr),
                "Henüz tahsis edilmedi",
                trend="neutral",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("BudgetWidget._on_account_snapshot hata: {}", exc)

    def _on_wallets_ready(self, wallet_snapshots) -> None:
        try:
            self._wallets = [
                WalletCellData(
                    pool=w.pool,
                    timeframe=w.timeframe,
                    allocated=w.allocated,
                    cash_balance=w.cash_balance,
                    positions_value=w.positions_value,
                    return_pct=float(w.return_pct_twr) if w.return_pct_twr else 0.0,
                )
                for w in (wallet_snapshots or [])
            ]
            self._wallet_id_map = {
                (w.pool, w.timeframe): int(w.id) for w in (wallet_snapshots or [])
            }
            self._rebuild_grid()
        except Exception as exc:  # noqa: BLE001
            logger.debug("BudgetWidget._on_wallets_ready hata: {}", exc)

    def _on_flows_ready(self, flow_rows) -> None:
        try:
            self._flows = [
                CashFlowRow(
                    occurred_at=r["occurred_at"] or datetime.utcnow(),
                    flow_type=r["flow_type"] or "",
                    amount=Decimal(str(abs(r["amount"]))) if r["amount"] is not None else Decimal("0"),
                    wallet_label=f"#{r['wallet_id']}" if r.get("wallet_id") else "—",
                    notes=r.get("notes", "") or "",
                )
                for r in (flow_rows or [])
            ]
            self._refresh_flows_table()
        except Exception as exc:  # noqa: BLE001
            logger.debug("BudgetWidget._on_flows_ready hata: {}", exc)

    def _on_refresh_error(self, exc) -> None:
        logger.debug("BudgetWidget refresh hata: {}", exc)

    def _rebuild_grid(self) -> None:
        """Cüzdan ID'leri geldiğinde grid'i yeniden çiz (her _WalletCell yeni)."""
        # Mevcut grid layout'tan _WalletCell'leri bul ve verisini güncelle.
        # Basit yaklaşım: BudgetWidget._build_ui'da inşa edilen grid'i
        # tamamen yeniden inşa etmek için widget'ı tazeleyebilirdik;
        # ama refresh sık değil, sade tutalım: hücreleri label seviyesinde
        # update etmek yerine grid içeriğini sıfırla + yeniden çiz.
        # Mevcut grid'i bulmak için: tüm child grid_box'ları gez.
        for box in self.findChildren(QGroupBox):
            if box.title() != "Cüzdan Matrisi (Havuz × Vade)":
                continue
            layout = box.layout()
            if layout is None:
                continue
            # Mevcut tüm öğeleri temizle (başlıklar dahil).
            while layout.count():
                item = layout.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.deleteLater()
            # Yeniden çiz
            for col_idx, tf in enumerate(("short", "mid", "long")):
                hdr = QLabel(TIMEFRAME_LABEL_TR.get(tf, tf))
                hf = QFont(hdr.font())
                hf.setBold(True)
                hdr.setFont(hf)
                hdr.setAlignment(Qt.AlignmentFlag.AlignCenter)
                layout.addWidget(hdr, 0, col_idx + 1)
            for row_idx, pool in enumerate(("bot", "user")):
                pool_hdr = QLabel(POOL_LABEL_TR.get(pool, pool))
                pf = QFont(pool_hdr.font())
                pf.setBold(True)
                pool_hdr.setFont(pf)
                layout.addWidget(pool_hdr, row_idx + 1, 0)
                for col_idx, tf in enumerate(("short", "mid", "long")):
                    data = next(
                        (w for w in self._wallets if w.pool == pool and w.timeframe == tf),
                        WalletCellData(pool, tf, Decimal("0"), Decimal("0"), Decimal("0"), 0.0),
                    )
                    cell = _WalletCell(data, box)
                    cell.clicked.connect(self._on_cell_selected)
                    cell.deposit_clicked.connect(self._on_cell_deposit)
                    cell.allocate_clicked.connect(self._on_cell_allocate_quick)
                    layout.addWidget(cell, row_idx + 1, col_idx + 1)
            for c in range(1, 4):
                layout.setColumnStretch(c, 1)
            break

    # ------------------------------------------------------------------
    # Slot'lar
    # ------------------------------------------------------------------
    @Slot()
    def _on_allocate(self) -> None:
        amount = Decimal(str(self._amount_input.value()))
        if self._selected is None or amount <= 0:
            return
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show(
                "Backend hazır değil — tahsis yapılamadı.", level="warning"
            )
            return
        pool, tf = self._selected
        wallet_id = self._wallet_id_map.get((pool, tf))
        if wallet_id is None:
            ToastManager.instance().show(
                "Cüzdan bulunamadı — sayfa tazelendiğinde tekrar deneyin.",
                level="error",
            )
            return
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.wallets.allocate(wallet_id, amount),
            on_success=lambda _r: self._after_mutation(
                f"Tahsis edildi: {pool}/{tf} ← {fmt_money(amount, 'TRY')}"
            ),
            on_error=self._on_mutation_error,
        )

    @Slot()
    def _on_reallocate(self) -> None:
        amount = Decimal(str(self._amount_input.value()))
        if self._selected is None or amount <= 0:
            return
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        # Hedef cüzdanı kullanıcıya sor.
        options = []
        for (pool, tf), _wid in self._wallet_id_map.items():
            if (pool, tf) == self._selected:
                continue
            options.append(
                f"{POOL_LABEL_TR.get(pool, pool)} · {TIMEFRAME_LABEL_TR.get(tf, tf)}"
            )
        if not options:
            ToastManager.instance().show("Hedef cüzdan yok.", level="warning")
            return
        choice, ok = QInputDialog.getItem(
            self, "Yeniden Dağıt", "Hedef cüzdan:", options, 0, False
        )
        if not ok:
            return
        # Etiketten geri (pool, timeframe) çıkar.
        target_key = None
        for (pool, tf), _wid in self._wallet_id_map.items():
            label = (
                f"{POOL_LABEL_TR.get(pool, pool)} · {TIMEFRAME_LABEL_TR.get(tf, tf)}"
            )
            if label == choice and (pool, tf) != self._selected:
                target_key = (pool, tf)
                break
        if target_key is None:
            return
        from_id = self._wallet_id_map[self._selected]
        to_id = self._wallet_id_map[target_key]
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.wallets.reallocate(from_id, to_id, amount),
            on_success=lambda _r: self._after_mutation(
                f"Aktarıldı: {fmt_money(amount, 'TRY')} → {choice}"
            ),
            on_error=self._on_mutation_error,
        )

    @Slot()
    def _on_withdraw_wallet(self) -> None:
        amount = Decimal(str(self._amount_input.value()))
        if self._selected is None or amount <= 0:
            return
        # Cüzdandan çekmek = wallet.cash_balance -> account.cash_balance
        # (reallocate yerine: önce reallocate'in tersi gerekir; servis bunu
        # tek metodda sunmuyor. Pragmatik: kullanıcı manuel reallocate ile
        # başka cüzdana çekebilir; "cüzdandan çek" şu an no-op + bilgi).
        ToastManager.instance().show(
            "Cüzdandan doğrudan çekim Faz 4'te eklenecek; "
            "şu an 'Yeniden Dağıt' ile başka cüzdana taşıyabilirsiniz.",
            level="info",
            duration_ms=5000,
        )

    @Slot()
    def _on_deposit(self) -> None:
        if self._bridge is None or not self._bridge.available or self._bridge.account_id is None:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        amount, ok = QInputDialog.getDouble(
            self, "Para Ekle", "Eklenecek tutar (₺):", 1000.0, 0.01, 1e9, 2
        )
        if not ok or amount <= 0:
            return
        bridge = self._bridge
        amt = Decimal(str(amount))
        bridge.run_async(
            lambda: bridge.cash_flows.deposit(int(bridge.account_id), amt, "UI deposit"),
            on_success=lambda _r: self._after_mutation(
                f"Para eklendi: {fmt_money(amt, 'TRY')}"
            ),
            on_error=self._on_mutation_error,
        )

    @Slot()
    def _on_withdraw(self) -> None:
        if self._bridge is None or not self._bridge.available or self._bridge.account_id is None:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        amount, ok = QInputDialog.getDouble(
            self, "Para Çek", "Çekilecek tutar (₺):", 100.0, 0.01, 1e9, 2
        )
        if not ok or amount <= 0:
            return
        bridge = self._bridge
        amt = Decimal(str(amount))
        bridge.run_async(
            lambda: bridge.cash_flows.withdrawal(int(bridge.account_id), amt, "UI withdrawal"),
            on_success=lambda _r: self._after_mutation(
                f"Para çekildi: {fmt_money(amt, 'TRY')}"
            ),
            on_error=self._on_mutation_error,
        )

    @Slot()
    def _on_reset_paper(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Cüzdanları Sıfırla",
            "Tüm cüzdanların açık pozisyonlarını ve nakitini sıfırlamak istediğine emin misin?\n"
            "(Yalnızca Paper modunda etkilidir.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        if self._bridge is None or not self._bridge.available or self._bridge.account_id is None:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        bridge = self._bridge
        bridge.run_async(
            lambda: bridge.paper.reset_paper_account(int(bridge.account_id)),
            on_success=lambda _r: self._after_mutation(
                "Paper hesabı sıfırlandı."
            ),
            on_error=self._on_mutation_error,
        )

    # ------------------------------------------------------------------
    def _after_mutation(self, message: str) -> None:
        ToastManager.instance().show(message, level="success")
        self.refresh()

    def _on_mutation_error(self, exc) -> None:
        ToastManager.instance().show(f"Hata: {exc}", level="error", duration_ms=5000)


__all__ = ["BudgetWidget", "WalletCellData", "CashFlowRow"]
