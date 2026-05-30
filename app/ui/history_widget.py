"""İşlem Geçmişi ekranı — Faz 3 ilk versiyon (placeholder veri).

Doküman §6.5 (İşlem Geçmişi) referans alınmıştır.

Yapı:
- Üst filtre çubuğu: tarih aralığı (başlangıç-bitiş), hisse arama,
  işlem tipi (Tümü/BUY/SELL), cüzdan filtresi (havuz × vade), "Bot uyumlu"
  checkbox.
- Ana tablo: Tarih, Ticker, Tip, Adet, Fiyat, Toplam, Komisyon, K/Z (kapanan),
  Tutma Günü, Cüzdan, Bot uyumlu.
- Alt sağda özet KPI: toplam işlem sayısı, toplam komisyon, net realized P&L.
- Sağ üstte: CSV / Excel dışa aktarım butonu.

Faz 3 TODO (backend bağlantısı):
    # - Portfolio tablosundan select (positions modülü içine
    #   list_transactions(account_id, filters) helper'ı eklenebilir).
    # - pandas.DataFrame(rows).to_csv(path) / to_excel(path)
    # - Filtre değişiminde DB sorgusu (QThreadPool ile async).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import QDate, Qt, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import fmt_money, fmt_qty
from app.ui.components import ToastManager
from app.ui.portfolio_widget import (
    POOL_LABEL_TR,
    TIMEFRAME_LABEL_TR,
    WALLET_BADGE_COLORS,
)

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:  # pragma: no cover
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False


# ---------------------------------------------------------------------------
# Placeholder view-model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransactionRow:
    transaction_at: datetime
    ticker: str
    action: str           # 'BUY' | 'SELL'
    quantity: Decimal
    price: Decimal
    total: Decimal
    commission: Decimal
    realized_pnl: Optional[Decimal]  # SELL'de değer, BUY'da None
    hold_days: Optional[int]
    pool: str
    timeframe: str
    followed_bot: bool


def _sample_transactions() -> list[TransactionRow]:
    now = datetime.utcnow()
    return [
        TransactionRow(
            now - timedelta(days=2), "THYAO", "BUY",
            Decimal("100"), Decimal("248.30"), Decimal("24830"),
            Decimal("12.50"), None, None, "bot", "short", True,
        ),
        TransactionRow(
            now - timedelta(days=5), "GARAN", "SELL",
            Decimal("50"), Decimal("82.10"), Decimal("4105"),
            Decimal("4.10"), Decimal("210.50"), 14, "user", "short", False,
        ),
        TransactionRow(
            now - timedelta(days=12), "AAPL", "BUY",
            Decimal("12"), Decimal("178.20"), Decimal("2138.40"),
            Decimal("3.00"), None, None, "user", "long", True,
        ),
        TransactionRow(
            now - timedelta(days=20), "ASELS", "BUY",
            Decimal("80"), Decimal("90.10"), Decimal("7208"),
            Decimal("7.20"), None, None, "bot", "mid", True,
        ),
        TransactionRow(
            now - timedelta(days=25), "SISE", "SELL",
            Decimal("100"), Decimal("47.30"), Decimal("4730"),
            Decimal("4.70"), Decimal("-105.20"), 8, "bot", "long", False,
        ),
    ]


def _action_badge(action: str) -> QLabel:
    palette = {"BUY": ("#2E7D32", "AL"), "SELL": ("#C62828", "SAT")}
    bg, txt = palette.get(action, ("#9E9E9E", action))
    lbl = QLabel(txt)
    lbl.setStyleSheet(
        f"background-color: {bg}; color: white; border-radius: 6px; "
        f"padding: 1px 8px; font-weight: 700;"
    )
    return lbl


def _wallet_badge(pool: str, timeframe: str) -> QLabel:
    color = WALLET_BADGE_COLORS.get((pool, timeframe), "#757575")
    label = f"{POOL_LABEL_TR.get(pool, pool)} · {TIMEFRAME_LABEL_TR.get(timeframe, timeframe)}"
    lbl = QLabel(label)
    lbl.setStyleSheet(
        f"background-color: {color}; color: white; border-radius: 6px; "
        f"padding: 1px 8px; font-weight: 600;"
    )
    return lbl


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class HistoryWidget(QWidget):
    """İşlem geçmişi ekranı — backend bağlantılı (Faz 3)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._all_transactions: list[TransactionRow] = []
        self._build_ui()
        self._apply_filters()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Başlık + export butonları
        header = QHBoxLayout()
        title = QLabel("İşlem Geçmişi")
        tf = QFont(title.font())
        tf.setPointSize(tf.pointSize() + 6)
        tf.setBold(True)
        title.setFont(tf)
        header.addWidget(title)
        header.addStretch(1)

        self._csv_btn = QPushButton("CSV Dışa Aktar")
        self._csv_btn.clicked.connect(self._on_export_csv)
        if QTAWESOME_AVAILABLE:
            try:
                self._csv_btn.setIcon(qta.icon("fa5s.file-csv"))
            except Exception:  # noqa: BLE001
                pass
        header.addWidget(self._csv_btn)

        self._xlsx_btn = QPushButton("Excel Dışa Aktar")
        self._xlsx_btn.clicked.connect(self._on_export_xlsx)
        if QTAWESOME_AVAILABLE:
            try:
                self._xlsx_btn.setIcon(qta.icon("fa5s.file-excel"))
            except Exception:  # noqa: BLE001
                pass
        header.addWidget(self._xlsx_btn)

        root.addLayout(header)

        # Filtre çubuğu
        filter_box = QGroupBox("Filtreler")
        filter_layout = QGridLayout(filter_box)
        filter_layout.setHorizontalSpacing(12)
        filter_layout.setVerticalSpacing(8)

        # Tarih aralığı
        filter_layout.addWidget(QLabel("Başlangıç:"), 0, 0)
        self._date_from = QDateEdit()
        self._date_from.setCalendarPopup(True)
        self._date_from.setDate(QDate.currentDate().addDays(-30))
        self._date_from.dateChanged.connect(lambda _d: self._apply_filters())
        filter_layout.addWidget(self._date_from, 0, 1)

        filter_layout.addWidget(QLabel("Bitiş:"), 0, 2)
        self._date_to = QDateEdit()
        self._date_to.setCalendarPopup(True)
        self._date_to.setDate(QDate.currentDate())
        self._date_to.dateChanged.connect(lambda _d: self._apply_filters())
        filter_layout.addWidget(self._date_to, 0, 3)

        # Ticker arama
        filter_layout.addWidget(QLabel("Hisse:"), 0, 4)
        self._ticker_input = QLineEdit()
        self._ticker_input.setPlaceholderText("THYAO, AAPL…")
        self._ticker_input.textChanged.connect(lambda _t: self._apply_filters())
        filter_layout.addWidget(self._ticker_input, 0, 5)

        # İşlem tipi
        filter_layout.addWidget(QLabel("Tip:"), 1, 0)
        self._action_combo = QComboBox()
        self._action_combo.addItem("Tümü", userData="all")
        self._action_combo.addItem("AL", userData="BUY")
        self._action_combo.addItem("SAT", userData="SELL")
        self._action_combo.currentIndexChanged.connect(lambda _i: self._apply_filters())
        filter_layout.addWidget(self._action_combo, 1, 1)

        # Cüzdan filtresi
        filter_layout.addWidget(QLabel("Cüzdan:"), 1, 2)
        self._wallet_combo = QComboBox()
        self._wallet_combo.addItem("Tümü", userData=("all", "all"))
        for pool in ("bot", "user"):
            for tf in ("short", "mid", "long"):
                label = f"{POOL_LABEL_TR[pool]} · {TIMEFRAME_LABEL_TR[tf]}"
                self._wallet_combo.addItem(label, userData=(pool, tf))
        self._wallet_combo.currentIndexChanged.connect(lambda _i: self._apply_filters())
        filter_layout.addWidget(self._wallet_combo, 1, 3)

        # Bot uyumlu checkbox
        self._followed_check = QCheckBox("Sadece bot uyumlu işlemler")
        self._followed_check.stateChanged.connect(lambda _s: self._apply_filters())
        filter_layout.addWidget(self._followed_check, 1, 4, 1, 2)

        root.addWidget(filter_box)

        # Ana tablo
        self._table = QTableWidget(0, 11, self)
        self._table.setHorizontalHeaderLabels(
            [
                "Tarih",
                "Ticker",
                "Tip",
                "Adet",
                "Fiyat",
                "Toplam",
                "Komisyon",
                "K/Z",
                "Tutma (gün)",
                "Cüzdan",
                "Bot Uyumlu",
            ]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Pencere boyutuna duyarlı kolon dağılımı:
        # - Metin/rozet kolonları içeriğe göre (Tarih, Ticker, Tip, Cüzdan, Bot Uyumlu)
        # - Sayısal kolonlar (Adet, Fiyat, Toplam, Komisyon, K/Z, Tutma) stretch
        hist_header = self._table.horizontalHeader()
        hist_header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for col in (0, 1, 2, 9, 10):
            hist_header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        hist_header.setStretchLastSection(False)
        self._table.setSortingEnabled(True)
        root.addWidget(self._table, stretch=1)

        # Alt özet
        summary_box = QGroupBox("Özet")
        summary_layout = QHBoxLayout(summary_box)
        self._summary_count = QLabel("Toplam İşlem: 0")
        self._summary_commission = QLabel("Toplam Komisyon: ₺0,00")
        self._summary_pnl = QLabel("Net Realized P&L: ₺0,00")
        for lbl in (self._summary_count, self._summary_commission, self._summary_pnl):
            f = QFont(lbl.font())
            f.setBold(True)
            lbl.setFont(f)
            summary_layout.addWidget(lbl)
        summary_layout.addStretch(1)
        root.addWidget(summary_box)

    # ------------------------------------------------------------------
    def _filtered_rows(self) -> list[TransactionRow]:
        rows = list(self._all_transactions)
        # Tarih
        df = self._date_from.date().toPython()
        dt = self._date_to.date().toPython()
        rows = [
            r for r in rows
            if df <= r.transaction_at.date() <= dt
        ]
        # Ticker
        ticker = self._ticker_input.text().strip().upper()
        if ticker:
            rows = [r for r in rows if ticker in r.ticker.upper()]
        # Action
        action = self._action_combo.currentData()
        if action and action != "all":
            rows = [r for r in rows if r.action == action]
        # Wallet
        pool, tf = self._wallet_combo.currentData() or ("all", "all")
        if pool != "all":
            rows = [r for r in rows if r.pool == pool and r.timeframe == tf]
        # Followed
        if self._followed_check.isChecked():
            rows = [r for r in rows if r.followed_bot]
        return rows

    def _apply_filters(self) -> None:
        rows = self._filtered_rows()
        self._table.setSortingEnabled(False)
        self._table.clearSpans()
        if not rows:
            self._table.setRowCount(1)
            empty = QTableWidgetItem("Henüz işlem geçmişi yok.")
            empty.setForeground(QColor("#9E9E9E"))
            empty.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setSpan(0, 0, 1, self._table.columnCount())
            self._table.setItem(0, 0, empty)
            self._update_summary(rows)
            return
        self._table.setRowCount(len(rows))
        for ridx, row in enumerate(rows):
            self._table.setItem(
                ridx, 0, QTableWidgetItem(row.transaction_at.strftime("%Y-%m-%d %H:%M"))
            )

            ticker_item = QTableWidgetItem(row.ticker)
            tf = QFont(ticker_item.font())
            tf.setBold(True)
            ticker_item.setFont(tf)
            self._table.setItem(ridx, 1, ticker_item)

            self._table.setCellWidget(ridx, 2, _action_badge(row.action))

            self._table.setItem(ridx, 3, QTableWidgetItem(fmt_qty(row.quantity)))
            self._table.setItem(ridx, 4, QTableWidgetItem(fmt_money(row.price, "TRY")))
            self._table.setItem(ridx, 5, QTableWidgetItem(fmt_money(row.total, "TRY")))
            self._table.setItem(
                ridx, 6, QTableWidgetItem(fmt_money(row.commission, "TRY"))
            )

            if row.realized_pnl is not None:
                pnl_item = QTableWidgetItem(fmt_money(row.realized_pnl, "TRY"))
                pnl_item.setForeground(
                    QColor("#2E7D32" if row.realized_pnl >= 0 else "#C62828")
                )
                self._table.setItem(ridx, 7, pnl_item)
            else:
                self._table.setItem(ridx, 7, QTableWidgetItem("—"))

            self._table.setItem(
                ridx, 8, QTableWidgetItem(str(row.hold_days) if row.hold_days is not None else "—")
            )

            self._table.setCellWidget(ridx, 9, _wallet_badge(row.pool, row.timeframe))

            followed_item = QTableWidgetItem("✓" if row.followed_bot else "—")
            followed_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if row.followed_bot:
                followed_item.setForeground(QColor("#2E7D32"))
            self._table.setItem(ridx, 10, followed_item)

        self._table.setSortingEnabled(True)
        # Kolon genişlikleri header section resize modlarına göre otomatik.
        self._update_summary(rows)

    def _update_summary(self, rows: list[TransactionRow]) -> None:
        total_commission = sum((r.commission for r in rows), Decimal("0"))
        net_pnl = sum(
            (r.realized_pnl for r in rows if r.realized_pnl is not None),
            Decimal("0"),
        )
        self._summary_count.setText(f"Toplam İşlem: {len(rows)}")
        self._summary_commission.setText(
            f"Toplam Komisyon: {fmt_money(total_commission, 'TRY')}"
        )
        net_color = "#2E7D32" if net_pnl >= 0 else "#C62828"
        self._summary_pnl.setText(
            f"Net Realized P&L: {fmt_money(net_pnl, 'TRY')}"
        )
        self._summary_pnl.setStyleSheet(f"color: {net_color};")

    # ------------------------------------------------------------------
    # Backend refresh — portfolio JOIN wallets JOIN instruments
    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        if self._bridge.account_id is None:
            return
        account_id = int(self._bridge.account_id)
        # Sync session ile basit raw query — async wrap içinde çalışacak.
        session_factory = self._bridge.session_factory

        async def _fetch_history():
            return _fetch_transactions(session_factory, account_id)

        self._bridge.run_async(
            _fetch_history,
            on_success=self._on_history_ready,
            on_error=self._on_history_error,
        )

    def _on_history_ready(self, rows) -> None:
        self._all_transactions = list(rows or [])
        self._apply_filters()

    def _on_history_error(self, exc) -> None:
        logger.debug("HistoryWidget refresh hata: {}", exc)

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    @Slot()
    def _on_export_csv(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "CSV Dışa Aktar", "islem_gecmisi.csv", "CSV (*.csv)"
        )
        if not path:
            return
        rows = self._filtered_rows()
        try:
            self._write_csv(path, rows)
            ToastManager.instance().show(
                f"{len(rows)} satır {path} dosyasına yazıldı.", level="success"
            )
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().show(
                f"CSV yazımı başarısız: {exc}", level="error"
            )

    @Slot()
    def _on_export_xlsx(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Excel Dışa Aktar", "islem_gecmisi.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        rows = self._filtered_rows()
        try:
            import pandas as pd  # type: ignore[import-not-found]

            df = pd.DataFrame(
                [
                    {
                        "Tarih": r.transaction_at.strftime("%Y-%m-%d %H:%M"),
                        "Ticker": r.ticker,
                        "Tip": r.action,
                        "Adet": float(r.quantity),
                        "Fiyat": float(r.price),
                        "Toplam": float(r.total),
                        "Komisyon": float(r.commission),
                        "K/Z": float(r.realized_pnl) if r.realized_pnl is not None else None,
                        "Tutma (gün)": r.hold_days,
                        "Cüzdan": f"{r.pool}/{r.timeframe}",
                        "Bot Uyumlu": "✓" if r.followed_bot else "",
                    }
                    for r in rows
                ]
            )
            df.to_excel(path, index=False)
            ToastManager.instance().show(
                f"{len(rows)} satır {path} dosyasına yazıldı.", level="success"
            )
        except ImportError:
            ToastManager.instance().show(
                "Excel için pandas + openpyxl gerekli. CSV dışa aktarımı kullanın.",
                level="warning",
                duration_ms=5000,
            )
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().show(
                f"Excel yazımı başarısız: {exc}", level="error"
            )

    @staticmethod
    def _write_csv(path: str, rows: list[TransactionRow]) -> None:
        import csv

        with open(path, "w", newline="", encoding="utf-8-sig") as fp:
            writer = csv.writer(fp, delimiter=";")
            writer.writerow(
                [
                    "Tarih",
                    "Ticker",
                    "Tip",
                    "Adet",
                    "Fiyat",
                    "Toplam",
                    "Komisyon",
                    "K/Z",
                    "Tutma (gün)",
                    "Cüzdan",
                    "Bot Uyumlu",
                ]
            )
            for r in rows:
                writer.writerow(
                    [
                        r.transaction_at.strftime("%Y-%m-%d %H:%M"),
                        r.ticker,
                        r.action,
                        float(r.quantity),
                        float(r.price),
                        float(r.total),
                        float(r.commission),
                        float(r.realized_pnl) if r.realized_pnl is not None else "",
                        r.hold_days if r.hold_days is not None else "",
                        f"{r.pool}/{r.timeframe}",
                        "1" if r.followed_bot else "",
                    ]
                )


def _fetch_transactions(session_factory, account_id: int) -> list[TransactionRow]:
    """Portfolio JOIN wallets JOIN instruments — sync sorgu."""

    from sqlalchemy import select  # noqa: WPS433
    from app.db.models import Instrument, Portfolio, Wallet  # noqa: WPS433

    session = session_factory()
    try:
        stmt = (
            select(
                Portfolio.transaction_at,
                Instrument.ticker,
                Portfolio.action,
                Portfolio.quantity,
                Portfolio.price,
                Portfolio.total,
                Portfolio.commission,
                Portfolio.realized_pnl,
                Portfolio.hold_days,
                Wallet.pool,
                Wallet.timeframe,
                Portfolio.followed_bot,
            )
            .join(Wallet, Wallet.id == Portfolio.wallet_id)
            .join(Instrument, Instrument.id == Portfolio.instrument_id)
            .where(Wallet.account_id == account_id)
            .order_by(Portfolio.transaction_at.desc())
        )
        rows = session.execute(stmt).all()
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    result: list[TransactionRow] = []
    for r in rows:
        result.append(
            TransactionRow(
                transaction_at=r[0] or datetime.utcnow(),
                ticker=str(r[1] or ""),
                action=str(r[2] or ""),
                quantity=Decimal(str(r[3])) if r[3] is not None else Decimal("0"),
                price=Decimal(str(r[4])) if r[4] is not None else Decimal("0"),
                total=Decimal(str(r[5])) if r[5] is not None else Decimal("0"),
                commission=Decimal(str(r[6])) if r[6] is not None else Decimal("0"),
                realized_pnl=Decimal(str(r[7])) if r[7] is not None else None,
                hold_days=int(r[8]) if r[8] is not None else None,
                pool=str(r[9] or "bot"),
                timeframe=str(r[10] or "short"),
                followed_bot=bool(r[11]),
            )
        )
    return result


__all__ = ["HistoryWidget", "TransactionRow"]
