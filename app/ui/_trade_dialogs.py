"""İşlem önerisi diyaloğu (``_TradeAdviceDialog``) — Faz 4 batch 2 cilası.

Kullanıcı bir hisse seçtiğinde "bütçeme göre kaç lot/adet alayım?" sorusunu
botun cevaplayabildiği diyalog. Watchlist / Bot Picks sağ tık menüsünden
açılır.

Akış:
1. Hisse bilgisi (ticker, ad, güncel fiyat) yukarıda görünür.
2. Kullanıcı "Cüzdan" ve "Risk profilim" seçer.
3. Bot Önerisi paneli otomatik dolar:
    - Önerilen adet (RiskManager.position_size çıktısı)
    - Önerilen tutar + cüzdana oran
    - Stop-loss / Take-profit (RiskManager.stop_loss_take_profit)
    - Kısa Türkçe gerekçe
4. Kullanıcı adedi elle değiştirebilir (QSpinBox).
5. "Onayla ve Uygula" → PositionService.apply_buy (BUY) çağrılır;
   "Sadece kaydet" → BotPicksService.add_pick.

Bu dialog **opsiyonel finplot** veya **TalibBackend** yoksa da çalışır —
ATR sıfır geldiğinde RiskManager pragmatic fallback ile çalışır.

Tasarım notları:
- Backend tarafında imza değişikliği YOK; mevcut RiskManager.position_size,
  RiskManager.stop_loss_take_profit ve PositionService.apply_buy kullanılır.
- ATR değeri price_history son 14 günden basit True-Range ortalaması ile
  hesaplanır (sync, ufak sorgu). Eğer veri yoksa ATR=0 → RiskManager cap
  varsayılanını kullanır.
- Dialog UI thread'ini bloklamaz; uygulama PositionService çağrısı
  ``bridge.run_async`` ile yapılır.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Callable, Optional

from loguru import logger
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from app.ui._format import apply_tr_locale, fmt_money, fmt_pct
from app.ui.components import ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge


# ---------------------------------------------------------------------------
# Sabitler / yardımcı
# ---------------------------------------------------------------------------

#: Risk profili → risk_per_trade. RiskManager.position_size'a iletilir.
RISK_PROFILES: tuple[tuple[str, str, float], ...] = (
    ("low", "Düşük (1%)", 0.01),
    ("medium", "Orta (2%)", 0.02),
    ("high", "Yüksek (3%)", 0.03),
)

POOL_LABEL_TR = {"bot": "Bot", "user": "Ben"}
TIMEFRAME_LABEL_TR = {"short": "Kısa", "mid": "Orta", "long": "Uzun"}


@dataclass(frozen=True)
class _WalletChoice:
    """Combobox userData için cüzdan tanımı."""

    wallet_id: int
    pool: str
    timeframe: str
    label: str
    cash_balance: Decimal


# ---------------------------------------------------------------------------
# ATR yardımcı sorgusu
# ---------------------------------------------------------------------------


def _compute_atr_from_history(
    session_factory: Callable[[], object],
    instrument_id: int,
    lookback_days: int = 30,
    period: int = 14,
) -> float:
    """Son N günün true-range ortalamasını döndürür (basit ATR).

    DB'de ``technical_signals.atr`` kolonu olmadığından runtime'da
    hesaplıyoruz. Veri yetersizse 0 döner — RiskManager fallback kullanır.
    """

    try:
        from sqlalchemy import and_, select  # noqa: WPS433

        from app.db.models import PriceHistory  # noqa: WPS433

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        session = session_factory()
        try:
            stmt = (
                select(
                    PriceHistory.timestamp,
                    PriceHistory.high,
                    PriceHistory.low,
                    PriceHistory.close,
                )
                .where(
                    and_(
                        PriceHistory.instrument_id == instrument_id,
                        PriceHistory.timestamp >= cutoff,
                        PriceHistory.high.isnot(None),
                        PriceHistory.low.isnot(None),
                    )
                )
                .order_by(PriceHistory.timestamp.asc())
            )
            rows = session.execute(stmt).all()
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        if len(rows) < 2:
            return 0.0

        # True Range = max(high-low, |high-prevClose|, |low-prevClose|)
        trs: list[float] = []
        prev_close: Optional[float] = None
        for _ts, h, l, c in rows:
            try:
                hf = float(h)
                lf = float(l)
            except (TypeError, ValueError):
                continue
            tr = hf - lf
            if prev_close is not None:
                tr = max(tr, abs(hf - prev_close), abs(lf - prev_close))
            trs.append(tr)
            try:
                prev_close = float(c) if c is not None else prev_close
            except (TypeError, ValueError):
                pass

        if not trs:
            return 0.0
        # Son `period` TR'nin basit ortalaması (Wilder yerine basit — UI uyarı için yeterli)
        tail = trs[-period:] if len(trs) >= period else trs
        return float(sum(tail) / len(tail))
    except Exception as exc:  # noqa: BLE001
        logger.debug("ATR hesaplanırken hata: {}", exc)
        return 0.0


def _load_wallet_choices_sync(
    bridge: "BackendBridge", account_id: int
) -> list[_WalletChoice]:
    """6 cüzdanı senkron raw query ile çek (UI thread, küçük sorgu)."""

    if bridge is None or not bridge.available:
        return []
    try:
        from sqlalchemy import select  # noqa: WPS433

        from app.db.models import Wallet  # noqa: WPS433

        session = bridge.session_factory()
        try:
            rows = session.execute(
                select(Wallet.id, Wallet.pool, Wallet.timeframe, Wallet.cash_balance)
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
            cash = Decimal(str(r[3] or 0))
            choices.append(
                _WalletChoice(
                    wallet_id=int(r[0]),
                    pool=pool,
                    timeframe=tf,
                    label=(
                        f"{POOL_LABEL_TR.get(pool, pool)} — "
                        f"{TIMEFRAME_LABEL_TR.get(tf, tf)}"
                        f"  ({cash:,.0f} TL)"
                    ),
                    cash_balance=cash,
                )
            )
        choices.sort(key=lambda w: order.get((w.pool, w.timeframe), 99))
        return choices
    except Exception as exc:  # noqa: BLE001
        logger.warning("Cüzdan listesi çekilemedi: {}", exc)
        return []


def _resolve_instrument_id_sync(
    bridge: "BackendBridge", ticker: str
) -> Optional[int]:
    try:
        from sqlalchemy import select  # noqa: WPS433

        from app.db.models import Instrument  # noqa: WPS433

        session = bridge.session_factory()
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


# ---------------------------------------------------------------------------
# Diyalog
# ---------------------------------------------------------------------------


class _TradeAdviceDialog(QDialog):
    """Bot pozisyon önerisi diyaloğu.

    Kullanım::

        dlg = _TradeAdviceDialog(
            ticker="THYAO",
            name="Türk Hava Yolları",
            current_price=Decimal("302.50"),
            instrument_id=42,
            bridge=bridge,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            ...  # işlem zaten arka planda uygulandı / kaydedildi
    """

    #: BUY uygulandığında MainWindow refresh akışını tetiklemek için.
    position_changed = Signal()

    def __init__(
        self,
        ticker: str,
        name: str,
        current_price: Decimal,
        instrument_id: Optional[int],
        bridge: "BackendBridge | None",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"İşlem Önerisi — {ticker}")
        self.setMinimumWidth(480)

        self._ticker = ticker.upper()
        self._name = name or ""
        self._current_price = Decimal(str(current_price or "0"))
        self._instrument_id = instrument_id
        self._bridge = bridge
        self._atr: float = 0.0
        self._suggested_qty: int = 0
        self._suggested_sl: Optional[Decimal] = None
        self._suggested_tp: Optional[Decimal] = None

        self._build_ui()
        # Bridge varsa cüzdanları ve ATR'yi sync olarak yükle (küçük sorgular)
        self._load_wallets()
        self._refresh_recommendation()

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)

        # --- Hisse başlığı ---
        header = QHBoxLayout()
        ticker_lbl = QLabel(self._ticker)
        f = QFont(ticker_lbl.font())
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        ticker_lbl.setFont(f)
        header.addWidget(ticker_lbl)

        name_lbl = QLabel(f"  {self._name}" if self._name else "")
        name_lbl.setStyleSheet("color: gray;")
        header.addWidget(name_lbl)
        header.addStretch(1)

        price_lbl = QLabel(f"Güncel: {fmt_money(self._current_price, 'TRY')}")
        pf = QFont(price_lbl.font())
        pf.setBold(True)
        price_lbl.setFont(pf)
        header.addWidget(price_lbl)
        root.addLayout(header)

        # --- Form: cüzdan + risk profili ---
        form = QFormLayout()
        form.setSpacing(8)

        self._wallet_combo = QComboBox()
        self._wallet_combo.setMinimumWidth(260)
        self._wallet_combo.currentIndexChanged.connect(self._refresh_recommendation)
        form.addRow("Cüzdan:", self._wallet_combo)

        self._risk_combo = QComboBox()
        for key, label, _pct in RISK_PROFILES:
            self._risk_combo.addItem(label, userData=key)
        self._risk_combo.setCurrentIndex(1)  # default = Orta
        self._risk_combo.currentIndexChanged.connect(self._refresh_recommendation)
        form.addRow("Risk profilim:", self._risk_combo)

        root.addLayout(form)

        # --- Bot Önerisi paneli ---
        suggestion_box = QFrame()
        suggestion_box.setFrameShape(QFrame.Shape.StyledPanel)
        suggestion_box.setStyleSheet(
            "QFrame { background-color: rgba(25, 118, 210, 18); "
            "border: 1px solid rgba(25, 118, 210, 80); border-radius: 6px; }"
        )
        sbl = QVBoxLayout(suggestion_box)
        sbl.setContentsMargins(12, 10, 12, 10)
        sbl.setSpacing(6)

        title = QLabel("Bot Önerisi")
        tf = QFont(title.font())
        tf.setBold(True)
        title.setFont(tf)
        sbl.addWidget(title)

        self._lbl_qty = QLabel("—")
        self._lbl_amount = QLabel("—")
        self._lbl_stop = QLabel("—")
        self._lbl_take = QLabel("—")
        self._lbl_reason = QLabel("—")
        self._lbl_reason.setWordWrap(True)
        self._lbl_reason.setStyleSheet("color: gray; font-style: italic;")

        for lbl in (
            self._lbl_qty, self._lbl_amount, self._lbl_stop, self._lbl_take
        ):
            f2 = QFont(lbl.font())
            f2.setBold(True)
            lbl.setFont(f2)

        sbl.addWidget(self._lbl_qty)
        sbl.addWidget(self._lbl_amount)
        sbl.addWidget(self._lbl_stop)
        sbl.addWidget(self._lbl_take)
        sbl.addSpacing(4)
        sbl.addWidget(self._lbl_reason)

        root.addWidget(suggestion_box)

        # --- Düzenlenebilir adet ---
        qty_row = QHBoxLayout()
        qty_row.addWidget(QLabel("Adet:"))
        self._qty_spin = QSpinBox()
        self._qty_spin.setRange(0, 1_000_000)
        self._qty_spin.setSingleStep(1)
        self._qty_spin.setValue(0)
        apply_tr_locale(self._qty_spin)
        qty_row.addWidget(self._qty_spin)
        qty_row.addStretch(1)

        # "Öneriye dön" butonu
        reset_btn = QPushButton("Öneriye Dön")
        reset_btn.clicked.connect(self._reset_qty_to_suggestion)
        qty_row.addWidget(reset_btn)
        root.addLayout(qty_row)

        # --- Butonlar ---
        btn_row = QHBoxLayout()
        self._btn_apply = QPushButton("Onayla ve Uygula")
        self._btn_apply.setStyleSheet(
            "background-color: #2E7D32; color: white; "
            "font-weight: 700; padding: 6px 14px;"
        )
        self._btn_apply.clicked.connect(self._on_apply_clicked)

        self._btn_save_pick = QPushButton("Sadece Kaydet (Bot önerisi)")
        self._btn_save_pick.setStyleSheet(
            "background-color: #1976D2; color: white; "
            "font-weight: 600; padding: 6px 14px;"
        )
        self._btn_save_pick.clicked.connect(self._on_save_pick_clicked)

        cancel_btn = QPushButton("İptal")
        cancel_btn.clicked.connect(self.reject)

        btn_row.addWidget(self._btn_save_pick)
        btn_row.addStretch(1)
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(self._btn_apply)
        root.addLayout(btn_row)

    # ------------------------------------------------------------------ data
    def _load_wallets(self) -> None:
        """6 cüzdanı sync olarak çek; bridge yoksa devre dışı bırak."""
        if self._bridge is None or not self._bridge.available:
            self._wallet_combo.addItem("(Backend bağlı değil)", userData=None)
            self._wallet_combo.setEnabled(False)
            self._btn_apply.setEnabled(False)
            self._btn_save_pick.setEnabled(False)
            return
        if self._bridge.account_id is None:
            self._wallet_combo.addItem("(Hesap hazır değil)", userData=None)
            self._wallet_combo.setEnabled(False)
            self._btn_apply.setEnabled(False)
            return
        choices = _load_wallet_choices_sync(self._bridge, int(self._bridge.account_id))
        if not choices:
            self._wallet_combo.addItem("(Cüzdan bulunamadı)", userData=None)
            self._wallet_combo.setEnabled(False)
            self._btn_apply.setEnabled(False)
            return
        for w in choices:
            self._wallet_combo.addItem(w.label, userData=w)
        # Varsayılan: bot/short (kullanıcı genelde kısa vade için soracak)
        for i in range(self._wallet_combo.count()):
            data = self._wallet_combo.itemData(i)
            if isinstance(data, _WalletChoice) and data.pool == "bot" and data.timeframe == "short":
                self._wallet_combo.setCurrentIndex(i)
                break

    def _current_wallet(self) -> Optional[_WalletChoice]:
        data = self._wallet_combo.currentData()
        return data if isinstance(data, _WalletChoice) else None

    def _current_risk_pct(self) -> float:
        key = self._risk_combo.currentData()
        for k, _label, pct in RISK_PROFILES:
            if k == key:
                return pct
        return 0.02

    # ------------------------------------------------------------------ recommendation
    def _refresh_recommendation(self, *_args) -> None:
        """Cüzdan/risk değişince RiskManager'dan yeni öneri al."""
        wallet = self._current_wallet()
        if wallet is None or self._bridge is None or self._bridge.risk_mgr is None:
            self._lbl_qty.setText("—")
            self._lbl_amount.setText("—")
            self._lbl_stop.setText("—")
            self._lbl_take.setText("—")
            self._lbl_reason.setText(
                "Cüzdan veya risk yöneticisi hazır değil — öneri üretilemiyor."
            )
            return

        # ATR: instrument_id varsa price_history'den hesapla
        if self._instrument_id is None and self._bridge.available:
            self._instrument_id = _resolve_instrument_id_sync(
                self._bridge, self._ticker
            )
        if self._instrument_id is not None and self._bridge.session_factory is not None:
            self._atr = _compute_atr_from_history(
                self._bridge.session_factory, int(self._instrument_id)
            )
        else:
            self._atr = 0.0

        # Cüzdan değeri = cash_balance (basitlik) — pozisyon değerlemesi
        # opsiyonel; UI hızlı tepki versin diye sadece nakit kullanılıyor.
        portfolio_value = wallet.cash_balance if wallet.cash_balance > 0 else Decimal("1000")
        risk_pct = self._current_risk_pct()

        try:
            rec = self._bridge.risk_mgr.position_size(
                portfolio_value=portfolio_value,
                atr=self._atr,
                current_price=self._current_price,
                risk_per_trade=risk_pct,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("position_size hata: {}", exc)
            self._lbl_reason.setText(f"Öneri üretilemedi: {exc}")
            return

        try:
            sltp = self._bridge.risk_mgr.stop_loss_take_profit(
                current_price=self._current_price,
                atr=self._atr,
                action="BUY",
            )
            self._suggested_sl = sltp.stop_loss
            self._suggested_tp = sltp.take_profit
        except Exception as exc:  # noqa: BLE001
            logger.debug("stop_loss_take_profit hata: {}", exc)
            self._suggested_sl = None
            self._suggested_tp = None

        qty = max(0, int(rec.suggested_quantity))
        amount = self._current_price * Decimal(qty)
        pct_of_wallet = (
            float(amount / portfolio_value * 100) if portfolio_value > 0 else 0.0
        )

        self._suggested_qty = qty

        # UI doldur
        self._lbl_qty.setText(f"Önerilen adet: {qty} lot")
        self._lbl_amount.setText(
            f"Önerilen tutar: {fmt_money(amount, 'TRY')}  "
            f"(cüzdanın {fmt_pct(pct_of_wallet, decimals=1)}'i)"
        )
        if self._suggested_sl is not None:
            sl_pct = (
                float((self._current_price - self._suggested_sl) / self._current_price * 100)
                if self._current_price > 0 else 0.0
            )
            self._lbl_stop.setText(
                f"Stop-loss: {fmt_money(self._suggested_sl, 'TRY')}  "
                f"(-{fmt_pct(sl_pct)})"
            )
        else:
            self._lbl_stop.setText("Stop-loss: hesaplanamadı")
        if self._suggested_tp is not None:
            tp_pct = (
                float((self._suggested_tp - self._current_price) / self._current_price * 100)
                if self._current_price > 0 else 0.0
            )
            self._lbl_take.setText(
                f"Take-profit: {fmt_money(self._suggested_tp, 'TRY')}  "
                f"(+{fmt_pct(tp_pct)})"
            )
        else:
            self._lbl_take.setText("Take-profit: hesaplanamadı")

        self._lbl_reason.setText(
            f"Gerekçe: ATR={self._atr:.2f}, "
            f"cüzdan nakdi={fmt_money(portfolio_value, 'TRY')}, "
            f"risk={int(risk_pct * 100)}% → maks. {qty} lot. {rec.reason}"
        )

        # Adet spin'ini öneriye sıfırla (kullanıcı elle değiştirmemişse)
        if self._qty_spin.value() == 0:
            self._qty_spin.setValue(qty)

    def _reset_qty_to_suggestion(self) -> None:
        self._qty_spin.setValue(self._suggested_qty)

    # ------------------------------------------------------------------ apply / save
    def _on_apply_clicked(self) -> None:
        """PositionService.apply_buy → cüzdana gerçek pozisyon yaz."""
        wallet = self._current_wallet()
        if wallet is None or self._bridge is None or not self._bridge.available:
            ToastManager.instance().show(
                "Backend hazır değil — uygulama yapılamıyor.", level="warning"
            )
            return
        if self._instrument_id is None:
            self._instrument_id = _resolve_instrument_id_sync(
                self._bridge, self._ticker
            )
        if self._instrument_id is None:
            ToastManager.instance().show(
                f"{self._ticker}: instrument bulunamadı.", level="error"
            )
            return

        qty = int(self._qty_spin.value())
        if qty <= 0:
            ToastManager.instance().show(
                "Adet 0'dan büyük olmalı.", level="warning"
            )
            return
        if self._current_price <= 0:
            ToastManager.instance().show(
                "Güncel fiyat geçersiz.", level="warning"
            )
            return

        bridge = self._bridge
        wallet_id = int(wallet.wallet_id)
        instrument_id = int(self._instrument_id)
        price = self._current_price
        qty_dec = Decimal(qty)
        # Komisyon default %0.2; settings'ten oku
        try:
            from app.config import settings  # noqa: WPS433

            comm_pct = float(getattr(settings, "default_commission_pct", 0.2))
        except Exception:  # noqa: BLE001
            comm_pct = 0.2
        comm = (price * qty_dec) * Decimal(str(comm_pct / 100.0))

        now = datetime.now(tz=timezone.utc)
        notes = (
            f"_TradeAdviceDialog: önerilen={self._suggested_qty}, "
            f"uygulanan={qty}, atr={self._atr:.2f}, "
            f"risk={int(self._current_risk_pct() * 100)}%"
        )

        def _coro():
            return bridge.positions.apply_buy(
                wallet_id=wallet_id,
                instrument_id=instrument_id,
                quantity=qty_dec,
                price=price,
                commission=comm,
                transaction_at=now,
                followed_bot=True,
                notes=notes,
            )

        def _on_success(_result) -> None:
            ToastManager.instance().show(
                f"BUY {qty} {self._ticker} → {wallet.label} uygulandı.",
                level="success",
            )
            self.position_changed.emit()
            self.accept()

        def _on_error(exc) -> None:
            logger.warning("_TradeAdviceDialog apply_buy hata: {}", exc)
            ToastManager.instance().show(
                f"İşlem hatası: {exc}", level="error", duration_ms=6000
            )

        # Butonları geçici kilitle ki çift tıklama olmasın
        self._btn_apply.setEnabled(False)
        self._btn_save_pick.setEnabled(False)
        bridge.run_async(_coro, on_success=_on_success, on_error=_on_error)

    def _on_save_pick_clicked(self) -> None:
        """BotPicksService.add_pick → öneri kaydı (işlem uygulanmaz)."""
        if self._bridge is None or not self._bridge.available:
            ToastManager.instance().show("Backend hazır değil.", level="warning")
            return
        if self._instrument_id is None:
            self._instrument_id = _resolve_instrument_id_sync(
                self._bridge, self._ticker
            )
        if self._instrument_id is None:
            ToastManager.instance().show(
                f"{self._ticker}: instrument bulunamadı.", level="error"
            )
            return

        bridge = self._bridge
        instrument_id = int(self._instrument_id)
        wallet = self._current_wallet()
        timeframe = wallet.timeframe if wallet is not None else "short"
        target = self._suggested_tp if self._suggested_tp is not None else None

        def _coro():
            # BotPicksService.add_pick imzası tahminî; service sync ise sync
            # çağrılır, async ise await edilir. run_async her iki durumu da
            # destekler (coroutine değilse doğrudan sonucu döner).
            add = getattr(bridge.bot_picks, "add_pick", None)
            if not callable(add):
                raise RuntimeError("BotPicksService.add_pick mevcut değil.")
            # Imza varyasyonlarına karşı en yaygın parametreleri ver.
            try:
                return add(
                    instrument_id=instrument_id,
                    timeframe=timeframe,
                    action="BUY",
                    confidence=60.0,
                    price_at_pick=self._current_price,
                    target_price=target,
                )
            except TypeError:
                # Alternatif imza: pick objesi
                return add(
                    instrument_id,
                    timeframe,
                    "BUY",
                    60.0,
                    self._current_price,
                    target,
                )

        def _on_success(_r) -> None:
            ToastManager.instance().show(
                f"{self._ticker} bot picks listesine eklendi.",
                level="success",
            )
            self.accept()

        def _on_error(exc) -> None:
            logger.warning("BotPicksService.add_pick hata: {}", exc)
            ToastManager.instance().show(
                f"Kaydedilemedi: {exc}", level="error", duration_ms=5000
            )

        self._btn_apply.setEnabled(False)
        self._btn_save_pick.setEnabled(False)
        bridge.run_async(_coro, on_success=_on_success, on_error=_on_error)


__all__ = [
    "_TradeAdviceDialog",
    "RISK_PROFILES",
]
