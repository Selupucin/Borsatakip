"""Alarm yönetimi ekranı — Faz 1 iskelet.

Doküman §8.3 "Alarmlar" satırı ve §9.1 ``alerts`` tablosu referans alınmıştır.
Faz 1'de yalnızca form + tablo iskeleti hazırlanır; Faz 3'te
``app.alerts.engine`` ile bağlanır, ``notifier.alert_triggered`` sinyalini
dinler ve ``components/toast.py`` ile sağ üst köşede bildirim gösterir.

Faz 1'de toast component'i henüz yok — şimdilik ``QMessageBox`` placeholder.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger
from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.ui.components import ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


# Doküman §9.1 alerts.alert_type değerleri.
ALERT_TYPES: tuple[tuple[str, str], ...] = (
    ("Fiyat üstüne çıkarsa", "price_above"),
    ("Fiyat altına düşerse", "price_below"),
    ("Yüzde değişim", "pct_change"),
    ("Sinyal değişimi", "signal"),
)


class AlertsWidget(QWidget):
    """Alarm kuralları yönetimi ekranı (Faz 1 iskelet)."""

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._build_ui()
        self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        title = QLabel("Alarmlar")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Yeni alarm formu
        form_box = QGroupBox("Yeni Alarm")
        form_layout = QFormLayout(form_box)

        self._ticker_combo = QComboBox()
        self._ticker_combo.setEditable(True)
        self._ticker_combo.addItems(["", "THYAO", "AKBNK", "ASELS", "AAPL", "MSFT", "TSLA"])
        self._ticker_combo.setToolTip("Faz 2'de instruments tablosundan dinamik dolacak")
        form_layout.addRow("Sembol:", self._ticker_combo)

        self._type_combo = QComboBox()
        for label, code in ALERT_TYPES:
            self._type_combo.addItem(label, userData=code)
        form_layout.addRow("Alarm Türü:", self._type_combo)

        self._threshold_spin = QDoubleSpinBox()
        self._threshold_spin.setRange(-1_000_000.0, 1_000_000.0)
        self._threshold_spin.setDecimals(4)
        self._threshold_spin.setSingleStep(0.5)
        form_layout.addRow("Eşik Değeri:", self._threshold_spin)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self._add_button = QPushButton("Alarm Ekle")
        self._add_button.clicked.connect(self._on_add_alert)
        button_row.addWidget(self._add_button)
        form_layout.addRow(button_row)

        root.addWidget(form_box)

        # Alarm listesi tablosu
        list_box = QGroupBox("Tanımlı Alarmlar")
        list_layout = QVBoxLayout(list_box)
        self._alerts_table = QTableWidget(0, 5, list_box)
        self._alerts_table.setHorizontalHeaderLabels(
            ["Sembol", "Tür", "Eşik", "Durum", "Oluşturulma"]
        )
        self._alerts_table.verticalHeader().setVisible(False)
        self._alerts_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._alerts_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._alerts_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        list_layout.addWidget(self._alerts_table)

        empty_hint = QLabel(
            "Henüz alarm yok — Faz 3'te ``alerts`` tablosundan otomatik doldurulacak."
        )
        empty_hint.setStyleSheet("color: gray; font-style: italic;")
        list_layout.addWidget(empty_hint)

        root.addWidget(list_box, stretch=1)

    # ------------------------------------------------------------------
    # Slot'lar
    # ------------------------------------------------------------------
    @Slot()
    def _on_add_alert(self) -> None:
        """Yeni alarm ekleme — backend varsa Alert tablosuna INSERT, yoksa UI."""
        ticker = self._ticker_combo.currentText().strip().upper()
        if not ticker:
            QMessageBox.warning(self, "Eksik Bilgi", "Lütfen bir sembol giriniz.")
            return

        type_label = self._type_combo.currentText()
        alert_type = self._type_combo.currentData() or "price_above"
        threshold = self._threshold_spin.value()

        # Backend varsa kalıcı INSERT
        if self._bridge and self._bridge.available:
            session_factory = self._bridge.session_factory

            async def _insert_alert():
                from sqlalchemy import select  # noqa: WPS433
                from app.db.models import Alert, Instrument  # noqa: WPS433

                session = session_factory()
                try:
                    instr = session.execute(
                        select(Instrument).where(Instrument.ticker == ticker)
                    ).scalar_one_or_none()
                    if instr is None:
                        raise LookupError(
                            f"Instrument bulunamadı: {ticker}. "
                            "Önce data-collector ile seed edin."
                        )
                    row = Alert(
                        instrument_id=int(instr.id),
                        alert_type=str(alert_type),
                        threshold=float(threshold),
                        is_active=True,
                    )
                    session.add(row)
                    commit = getattr(session, "commit", None)
                    if callable(commit):
                        commit()
                    return int(row.id) if hasattr(row, "id") else 0
                finally:
                    close = getattr(session, "close", None)
                    if callable(close):
                        close()

            self._bridge.run_async(
                _insert_alert,
                on_success=lambda _r: (
                    ToastManager.instance().show(
                        f"{ticker} alarmı eklendi.", level="success"
                    ),
                    self.refresh(),
                ),
                on_error=lambda exc: ToastManager.instance().show(
                    f"Alarm eklenemedi: {exc}", level="error"
                ),
            )
            return

        # Backend yok — sadece UI satırı
        row = self._alerts_table.rowCount()
        self._alerts_table.insertRow(row)
        for col, val in enumerate(
            [ticker, type_label, f"{threshold:.4f}", "Aktif", "—"]
        ):
            item = QTableWidgetItem(val)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._alerts_table.setItem(row, col, item)

    # ------------------------------------------------------------------
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        session_factory = self._bridge.session_factory

        async def _fetch():
            from sqlalchemy import select  # noqa: WPS433
            from app.db.models import Alert, Instrument  # noqa: WPS433

            session = session_factory()
            try:
                stmt = (
                    select(
                        Instrument.ticker,
                        Alert.alert_type,
                        Alert.threshold,
                        Alert.is_active,
                        Alert.created_at,
                    )
                    .join(Instrument, Instrument.id == Alert.instrument_id)
                    .order_by(Alert.created_at.desc())
                )
                return list(session.execute(stmt).all())
            finally:
                close = getattr(session, "close", None)
                if callable(close):
                    close()

        self._bridge.run_async(_fetch, on_success=self._on_alerts_ready)

    def _on_alerts_ready(self, rows) -> None:
        try:
            self._alerts_table.setRowCount(len(rows or []))
            for ridx, r in enumerate(rows or []):
                ticker = str(r[0] or "—")
                a_type = str(r[1] or "")
                threshold = float(r[2] or 0)
                active = "Aktif" if r[3] else "Pasif"
                created = r[4].strftime("%Y-%m-%d %H:%M") if r[4] else "—"
                for col, val in enumerate(
                    [ticker, a_type, f"{threshold:.4f}", active, created]
                ):
                    item = QTableWidgetItem(val)
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    self._alerts_table.setItem(ridx, col, item)
        except Exception as exc:  # noqa: BLE001
            logger.debug("AlertsWidget._on_alerts_ready hata: {}", exc)

    @Slot(dict)
    def on_alert_triggered(self, payload: dict) -> None:
        """``Notifier.alert_triggered(dict)`` Qt sinyalinin slot karşılığı.

        Toast bildirimi gösterir + alarm listesini tazeler.
        """
        message = str(payload.get("message", "Alarm tetiklendi"))
        ToastManager.instance().show(message, level="warning", duration_ms=5000)
        self.refresh()
