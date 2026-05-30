"""Grafik widget — TradingView gömme (Faz 4 batch 3).

Kullanıcının net sorduğu performans / mimari noktası:

> "Bot her hisseyi TEK TEK yfinance ile çekmez — ``app/services/scheduler.py``
> her 60 saniyede aktif evrendeki TÜM hisseleri paralel olarak çeker
> (``asyncio.gather``). UI tarafında ise burada gösterilen grafik
> TradingView'in canlı web embed'idir; ayrı bir indirme / render yapılmaz."

Mimari özet:
- Mum/indikatör/zaman seçimi gibi tüm grafik etkileşimleri **TradingView web
  widget**'ı içinde çalışır; PySide6 yalnızca embed iframe'i barındırır.
- Bu sayede ``finplot`` / ``pyqtgraph`` bağımlılıklarına ve özel render
  kodlarına ihtiyaç kalmaz. (finplot kodu Faz 4 batch 3'te tamamen kaldırıldı.)
- ``PySide6.QtWebEngineWidgets`` opsiyonel — kurulu değilse graceful fallback:
  TradingView'a yönlendiren tıklanabilir bir link gösterilir.

Kurulum:
    pip install PySide6-Addons
    # veya:
    pip install PyQtWebEngine

Mevcut Public API korundu:
    * ``ChartWidget(parent=..., bridge=...)``
    * ``ChartWidget.load_ticker(ticker)``
    * ``ChartWidget.set_theme(is_dark=...)``
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional
from urllib.parse import quote

from loguru import logger
from PySide6.QtCore import Qt, QUrl
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.ui.components import ToastManager

if TYPE_CHECKING:
    from app.ui._backend_bridge import BackendBridge

try:
    import qtawesome as qta  # type: ignore[import-not-found]

    QTAWESOME_AVAILABLE = True
except ImportError:
    qta = None  # type: ignore[assignment]
    QTAWESOME_AVAILABLE = False

# QWebEngineView opsiyonel — kurulu değilse fallback'e geçilir.
try:
    from PySide6.QtWebEngineWidgets import QWebEngineView  # type: ignore[import-not-found]

    QWEBENGINE_AVAILABLE = True
except Exception as _exc:  # noqa: BLE001 — Qt6 derlemesinde yoksa ImportError veya RuntimeError
    QWebEngineView = None  # type: ignore[assignment,misc]
    QWEBENGINE_AVAILABLE = False
    logger.warning(
        "QWebEngineView yüklü değil (PySide6-Addons): TradingView gömme yerine "
        "tıklanabilir link fallback'i kullanılacak. ({})", _exc,
    )


# Üst toolbar zaman aralığı: TradingView 'interval' parametresi
# 1=1m, 5=5m, 15=15m, 60=1h, 240=4h, D=1d, W=1w, M=1mo
TIMEFRAMES: tuple[tuple[str, str], ...] = (
    ("1 Dakika", "1"),
    ("5 Dakika", "5"),
    ("15 Dakika", "15"),
    ("1 Saat", "60"),
    ("4 Saat", "240"),
    ("Günlük", "D"),
    ("Haftalık", "W"),
    ("Aylık", "M"),
)


# Borsa kodu -> TradingView exchange prefix
_TV_EXCHANGE_MAP: dict[str, str] = {
    "BIST": "BIST",
    "NASDAQ": "NASDAQ",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
    "LSE": "LSE",
    "XETR": "XETR",
}


#: Özel sembollere doğrudan eşleme — endeksler, emtia, döviz vb.
_TV_SPECIAL_MAP: dict[str, str] = {
    # BIST endeksleri
    "XU100": "BIST:XU100",
    "BIST100": "BIST:XU100",
    "XU030": "BIST:XU030",
    "XBANK": "BIST:XBANK",
    # ABD endeksleri (TradingView'de en yaygın prefix'ler)
    "^GSPC": "SP:SPX",
    "SPX": "SP:SPX",
    "SPY": "AMEX:SPY",
    "^DJI": "DJ:DJI",
    "^IXIC": "NASDAQ:IXIC",
    "QQQ": "NASDAQ:QQQ",
    "^RUT": "TVC:RUT",
    "^VIX": "TVC:VIX",
    # Döviz
    "TRY=X": "FX_IDC:USDTRY",
    "USDTRY": "FX_IDC:USDTRY",
    "EURTRY=X": "FX_IDC:EURTRY",
    "EURTRY": "FX_IDC:EURTRY",
    "EURUSD=X": "FX:EURUSD",
    # Emtia
    "GC=F": "COMEX:GC1!",
    "XAUUSD": "TVC:GOLD",
    "SI=F": "COMEX:SI1!",
    "CL=F": "NYMEX:CL1!",
    "BTC-USD": "BINANCE:BTCUSDT",
    "ETH-USD": "BINANCE:ETHUSDT",
}


def _tv_symbol(ticker: str, exchange: str = "") -> str:
    """Ticker + exchange → TradingView symbol stringi.

    Öncelik sırası (en güveniliriden en heuristik'e):
      1) Özel sembol haritası (endeks/emtia/döviz)
      2) Açık ``exchange`` parametresi (DB'den geliyorsa hep doluyu kullan)
      3) yfinance suffix'leri (``.IS`` → BIST, ``.L`` → LSE, vb.)
      4) Ticker uzunluk + alfa karakter heuristic'i (4–6 harf BIST varsayımı)
    """
    t_raw = (ticker or "").upper().strip()
    if not t_raw:
        return ""

    # 1) Özel sembol (endeks, emtia, döviz)
    if t_raw in _TV_SPECIAL_MAP:
        return _TV_SPECIAL_MAP[t_raw]

    # 2) Açık exchange parametresi en güvenilir kaynak (DB'den geliyor)
    ex = (exchange or "").upper().strip()
    t = t_raw

    # yfinance suffix'lerini temizle ki ex prefix ile çakışmasın
    if t.endswith(".IS"):
        t = t[:-3]
        if not ex:
            ex = "BIST"
    else:
        for sfx in (".L", ".DE", ".PA", ".AS", ".HK", ".SS", ".SZ", ".TO"):
            if t.endswith(sfx):
                t = t[: -len(sfx)]
                break

    if ex == "BIST":
        return f"BIST:{t}"
    if ex == "NYSE":
        return f"NYSE:{t}"
    if ex == "NASDAQ":
        return f"NASDAQ:{t}"
    if ex == "AMEX":
        return f"AMEX:{t}"
    if ex and ex in _TV_EXCHANGE_MAP:
        return f"{_TV_EXCHANGE_MAP[ex]}:{t}"

    # 3) Heuristic — exchange bilinmiyor:
    #    - 5–6 harfli pür alfabetik → BIST tahmin (THYAO/AKBNK/EREGL/KCHOL/HEKTS…)
    #      (ABD ticker'ları neredeyse hep 1–4 harf; 5+ harf nadir → ayırt edici)
    #    - 1–4 harf → prefix'siz; TradingView en uygun borsayı (US) bulur
    if 5 <= len(t) <= 6 and t.isalpha() and t.isascii():
        return f"BIST:{t}"
    return t


def _build_tv_embed_url(
    symbol: str, interval: str = "D", is_dark: bool = True
) -> str:
    """TradingView gömme widget URL'i.

    ``widgetembed`` standart iframe endpoint'idir; tema, locale, sembol ve
    zaman aralığı sorgu parametreleri olarak geçer. Indicators (studies)
    boş — kullanıcı widget içinden ekleyebilir.
    """
    theme = "dark" if is_dark else "light"
    toolbar_bg = "2A2E39" if is_dark else "F1F3F6"
    params = (
        f"symbol={quote(symbol)}"
        f"&interval={quote(interval)}"
        f"&theme={theme}"
        f"&style=1"
        f"&locale=tr"
        f"&toolbarbg={toolbar_bg}"
        f"&studies=%5B%5D"
        f"&hideideas=1"
        f"&withdateranges=1"
        f"&allow_symbol_change=1"
    )
    return f"https://s.tradingview.com/widgetembed/?{params}"


def _build_tv_landing_url(symbol: str) -> str:
    """TradingView sembol sayfasının insan-okunabilir URL'i (fallback için)."""
    # 'BIST:THYAO' → 'BIST-THYAO'
    slug = symbol.replace(":", "-")
    return f"https://www.tradingview.com/symbols/{quote(slug)}/"


class ChartWidget(QWidget):
    """TradingView canlı grafik gömmesi.

    UI hiçbir OHLCV indirimi yapmaz — TradingView iframe'i kendi besleme
    kanalları üzerinden mum/indikatör/zaman seçimini sunar. Bu yaklaşım
    kullanıcıya tanıdık bir grafik deneyimi verir ve Python tarafındaki
    finplot / yfinance bağımlılıklarını kaldırır.
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._current_ticker: str | None = None
        self._current_exchange: str = ""
        self._is_dark = True
        self._web_view: Optional["QWebEngineView"] = None
        self._fallback_link: Optional[QLabel] = None
        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # Üst toolbar: hisse seç + seçili ticker + zaman aralığı
        toolbar = QHBoxLayout()

        self._pick_button = QPushButton("Hisse Seç")
        if QTAWESOME_AVAILABLE:
            try:
                self._pick_button.setIcon(qta.icon("fa5s.search"))
            except Exception:  # noqa: BLE001
                pass
        self._pick_button.setToolTip("DB'deki hisseler içinden ara ve seç")
        self._pick_button.clicked.connect(self._on_pick_ticker)
        toolbar.addWidget(self._pick_button)

        self._ticker_label = QLabel("Seçili sembol yok")
        self._ticker_label.setStyleSheet(
            "padding: 4px 10px; border: 1px solid rgba(120,120,120,80);"
            "border-radius: 6px; font-weight: 700;"
        )
        self._ticker_label.setMinimumWidth(160)
        toolbar.addWidget(self._ticker_label)

        toolbar.addWidget(QLabel("Aralık:"))
        self._timeframe_combo = QComboBox()
        for label, interval in TIMEFRAMES:
            self._timeframe_combo.addItem(label, userData=interval)
        # Default: Günlük
        for i in range(self._timeframe_combo.count()):
            if self._timeframe_combo.itemData(i) == "D":
                self._timeframe_combo.setCurrentIndex(i)
                break
        self._timeframe_combo.currentIndexChanged.connect(self._on_timeframe_changed)
        toolbar.addWidget(self._timeframe_combo)

        self._reload_button = QPushButton("Yenile")
        if QTAWESOME_AVAILABLE:
            try:
                self._reload_button.setIcon(qta.icon("fa5s.sync"))
            except Exception:  # noqa: BLE001
                pass
        self._reload_button.setEnabled(False)
        self._reload_button.setToolTip("Grafiği yeniden yükle")
        self._reload_button.clicked.connect(self._on_reload_clicked)
        toolbar.addWidget(self._reload_button)

        # Grafik yüklenmese veya sembol haritalanamasa bile her durumda
        # kullanıcının TradingView'a tarayıcıdan ulaşabileceği bir buton.
        self._tv_open_button = QPushButton("🔗 TradingView")
        self._tv_open_button.setToolTip(
            "Seçili hisseyi TradingView'da tarayıcıda aç "
            "(gömülü grafik yüklenmediyse veya başka bir endpoint denemek için)"
        )
        self._tv_open_button.setEnabled(False)
        self._tv_open_button.clicked.connect(self._on_tv_open_clicked)
        toolbar.addWidget(self._tv_open_button)

        toolbar.addStretch(1)

        # Sağda küçük not — kullanıcıya mimariyi açıkla
        info_lbl = QLabel("ⓘ TradingView canlı")
        info_lbl.setStyleSheet("color: gray; font-style: italic;")
        info_lbl.setToolTip(
            "Bu grafik TradingView'in canlı web widget'ıdır. "
            "Bot tarafı veriyi paralel olarak Scheduler ile çeker; "
            "grafik bağımsız bir ayrı bağlantıdır."
        )
        toolbar.addWidget(info_lbl)

        root.addLayout(toolbar)

        # Grafik alanı
        self._chart_host = QWidget(self)
        self._chart_host.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self._chart_host_layout = QVBoxLayout(self._chart_host)
        self._chart_host_layout.setContentsMargins(0, 0, 0, 0)

        if QWEBENGINE_AVAILABLE:
            self._web_view = QWebEngineView(self._chart_host)
            self._web_view.setSizePolicy(
                QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
            )
            # Load fail durumunda kullanıcıya açık uyarı + tarayıcı linki ver
            self._web_view.loadFinished.connect(self._on_load_finished)
            self._chart_host_layout.addWidget(self._web_view)
            self._show_placeholder(
                "Sembol seçmek için yukarıdaki 'Hisse Seç' butonuna tıkla."
            )
        else:
            # Fallback — QWebEngineView yok
            self._show_fallback(
                "Gömülü grafik için PySide6-Addons gerekiyor.\n"
                "Kurulum: pip install PySide6-Addons\n\n"
                "Şimdilik sembol seçildiğinde TradingView linki gösterilecek."
            )

        root.addWidget(self._chart_host, stretch=1)

    # ------------------------------------------------------------------
    # Sembol seçimi
    # ------------------------------------------------------------------
    def _on_pick_ticker(self) -> None:
        """`_AddTickerDialog`'u reuse ederek hisse seçimi al."""
        from app.ui.watchlist_widget import _AddTickerDialog

        available: list[tuple[str, str, str]] = []
        if self._bridge is not None:
            try:
                available = self._bridge.cached_instruments()
            except Exception as exc:  # noqa: BLE001
                logger.debug("ChartWidget instrument cache hatası: {}", exc)

        if not available:
            ToastManager.instance().show(
                "DB'de henüz tanımlı hisse yok. Önce takip listesine ekleyin.",
                level="warning",
            )
            return

        dlg = _AddTickerDialog(self, available_instruments=available)
        dlg.setWindowTitle("Hisse Seç")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        result = dlg.result_data()
        if result is None:
            return
        ticker, exchange = result
        self._current_ticker = ticker
        self._current_exchange = exchange
        self.load_ticker(ticker, exchange)

    def _on_reload_clicked(self) -> None:
        if self._current_ticker:
            self.load_ticker(self._current_ticker, self._current_exchange)

    def _on_tv_open_clicked(self) -> None:
        """TradingView landing page'ini sistem tarayıcısında aç."""
        if not self._current_ticker:
            return
        from PySide6.QtGui import QDesktopServices

        symbol = _tv_symbol(self._current_ticker, self._current_exchange)
        url = _build_tv_landing_url(symbol)
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:  # noqa: BLE001
            logger.warning("TradingView URL açılamadı: {}", exc)
            ToastManager.instance().show(
                f"Tarayıcı açılamadı: {url}", level="warning"
            )

    def _on_timeframe_changed(self, _index: int) -> None:
        if self._current_ticker:
            self.load_ticker(self._current_ticker, self._current_exchange)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def load_ticker(self, ticker: str, exchange: str = "") -> None:
        """Verilen ticker için TradingView grafiğini yükle.

        Args:
            ticker: 'THYAO', 'AAPL', 'MSFT' vb.
            exchange: 'BIST' / 'NASDAQ' / 'NYSE' — boşsa heuristik kullanılır.
        """
        if not ticker:
            return
        self._current_ticker = ticker.upper()
        if exchange:
            self._current_exchange = exchange

        symbol = _tv_symbol(self._current_ticker, self._current_exchange)
        if self._current_exchange:
            self._ticker_label.setText(
                f"{self._current_ticker}  [{self._current_exchange}]"
            )
        else:
            self._ticker_label.setText(self._current_ticker)
        self._reload_button.setEnabled(True)
        self._tv_open_button.setEnabled(True)

        # Timeframe seçimi
        interval = self._timeframe_combo.currentData() or "D"

        if QWEBENGINE_AVAILABLE and self._web_view is not None:
            url = _build_tv_embed_url(symbol, interval=interval, is_dark=self._is_dark)
            logger.debug("ChartWidget yükleniyor: {}", url)
            try:
                self._web_view.setUrl(QUrl(url))
            except Exception as exc:  # noqa: BLE001
                logger.warning("QWebEngineView setUrl hatası: {}", exc)
                self._show_fallback(
                    f"TradingView gömülemedi: {exc}\n"
                    f"Tıkla: {_build_tv_landing_url(symbol)}",
                    landing_url=_build_tv_landing_url(symbol),
                )
        else:
            # Fallback: tıklanabilir link
            self._show_fallback(
                f"TradingView'da {symbol} grafiğini açmak için tıkla.",
                landing_url=_build_tv_landing_url(symbol),
            )

    def set_theme(self, is_dark: bool) -> None:
        """Tema değişikliğinde grafiği yeniden yükle (yeni tema parametresiyle)."""
        if self._is_dark == is_dark:
            return
        self._is_dark = is_dark
        if self._current_ticker:
            self.load_ticker(self._current_ticker, self._current_exchange)

    # ------------------------------------------------------------------
    # Yardımcılar
    # ------------------------------------------------------------------
    def _show_placeholder(self, text: str) -> None:
        """QWebEngineView varken about:blank html ile basit metin göster."""
        if not (QWEBENGINE_AVAILABLE and self._web_view is not None):
            return
        bg = "#1E1E1E" if self._is_dark else "#FAFAFA"
        fg = "#9E9E9E"
        html = (
            "<!doctype html><html><head>"
            "<meta charset='utf-8'>"
            "<style>"
            f"html,body{{margin:0;padding:0;background:{bg};color:{fg};"
            "font-family:Segoe UI,Arial,sans-serif;}}"
            ".center{height:100vh;display:flex;align-items:center;"
            "justify-content:center;text-align:center;padding:24px;}"
            "</style></head><body>"
            f"<div class='center'>{text}</div></body></html>"
        )
        try:
            self._web_view.setHtml(html)
        except Exception as exc:  # noqa: BLE001
            logger.debug("placeholder setHtml hatası: {}", exc)

    def _show_fallback(self, text: str, landing_url: Optional[str] = None) -> None:
        """QWebEngineView yokken — tıklanabilir link göster."""
        # Mevcut içeriği temizle
        while self._chart_host_layout.count():
            it = self._chart_host_layout.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()

        container = QWidget(self._chart_host)
        lay = QVBoxLayout(container)
        lay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.setSpacing(12)

        info = QLabel(text)
        info.setAlignment(Qt.AlignmentFlag.AlignCenter)
        info.setStyleSheet("color: gray; font-style: italic;")
        info.setWordWrap(True)
        lay.addWidget(info)

        if landing_url:
            link = QLabel(
                f'<a href="{landing_url}" '
                f'style="color:#1976D2; font-size:14pt; font-weight:700;">'
                f"TradingView'da Aç</a>"
            )
            link.setTextFormat(Qt.TextFormat.RichText)
            link.setOpenExternalLinks(True)
            link.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lay.addWidget(link)

        self._chart_host_layout.addWidget(container)
        self._fallback_link = info

    def _on_load_finished(self, ok: bool) -> None:
        """QWebEngineView load tamamlandı — başarısızsa kullanıcıya bildir."""
        if ok or not self._current_ticker:
            return
        symbol = _tv_symbol(self._current_ticker, self._current_exchange)
        logger.warning(
            "TradingView grafik yüklenemedi: symbol={} — fallback'e geçiliyor.",
            symbol,
        )
        ToastManager.instance().show(
            f"Grafik yüklenmedi ({symbol}). "
            f"Üst toolbar'daki '🔗 TradingView' butonu ile tarayıcıda aç.",
            level="warning",
        )

    # Geriye uyumluluk — eski API metodları (no-op).
    def add_indicator(self, name: str, series) -> None:  # noqa: ANN001
        """TradingView içi indikatör eklemesi widget içinden yapılır.

        Bu metod eski API ile geriye uyumluluk için tutuldu; gömülü
        TradingView'da kullanıcı doğrudan widget toolbar'ından
        indikatör ekler.
        """
        logger.debug("ChartWidget.add_indicator no-op (TradingView UI içinden): {}", name)


__all__ = ["ChartWidget", "TIMEFRAMES"]
