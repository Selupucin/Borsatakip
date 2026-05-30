"""Haber Akışı ekranı — Faz 2 ilk versiyon (placeholder veri ile).

Doküman §8.3 "Haberler" satırı, §5.6 (Şeffaflık ve Açıklanabilirlik) ve
``app/data/sources/rss_source.py`` ``NewsItem`` dataclass'ı referans alınmıştır.

Yapı:
- Üstte filtre çubuğu: dil combo (Hepsi/TR/EN), kaynak combo, hisse arama input.
- Sol: haber listesi (``QListWidget``). Her satırda başlık + tarih (sağ üst),
  alt satırda kaynak rozeti + sentiment göstergesi (renkli daire + skor).
- Sağ: seçili haberin detay paneli — başlık, kaynak, tarih, sentiment detayı,
  özet/tam metin, "Tarayıcıda aç" butonu.

Faz 2'de placeholder veri kullanılır (sample 5-10 dummy haber). Faz 3'te gerçek
async fetch:
    # Faz 3 TODO:
    # - RSSSource.fetch_news() çağrısı QThreadPool worker'ında yapılacak
    # - SentimentAnalyzer.analyze() ile her habere skor üretilecek
    # - NewsAggregator + DB persist akışı UI'a sinyal/slot ile bağlanacak
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Optional

from loguru import logger
from PySide6.QtCore import QSize, QUrl, Qt, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QCompleter,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
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
# Sabitler ve renk paleti
# ---------------------------------------------------------------------------

#: Sentiment etiketi -> (hex renk, Türkçe etiket)
SENTIMENT_PALETTE: dict[str, tuple[str, str]] = {
    "positive": ("#2E7D32", "Pozitif"),   # yeşil
    "neutral": ("#9E9E9E", "Nötr"),       # gri
    "negative": ("#C62828", "Negatif"),   # kırmızı
}

#: Tüm filtre seçenekleri (ID: insan-okur etiket).
LANGUAGE_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "Hepsi"),
    ("tr", "TR"),
    ("en", "EN"),
)

#: ``rss_source.DEFAULT_FEEDS`` ile aynı anahtarlar — sıralı dropdown için.
SOURCE_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "Tüm Kaynaklar"),
    ("reuters_finance", "Reuters Finance"),
    ("bloomberg_tr", "Bloomberg TR"),
    ("mynet_finans", "Mynet Finans"),
    ("dunya_gazetesi", "Dünya Gazetesi"),
    ("kap_disclosures", "KAP Bildirimleri"),
)

#: Borsa filtresi (instrument.exchange ile karşılaştırılır).
EXCHANGE_FILTERS: tuple[tuple[str, str], ...] = (
    ("", "Hepsi"),
    ("BIST", "BIST"),
    ("NASDAQ", "NASDAQ"),
    ("NYSE", "NYSE"),
)

#: Tarih aralığı filtresi — (key, label, lookback_days). ``None`` = sınırsız.
DATE_FILTERS: tuple[tuple[str, str, Optional[int]], ...] = (
    ("today", "Bugün", 1),
    ("7d", "Son 7 gün", 7),
    ("30d", "Son 30 gün", 30),
    ("all", "Tümü", None),
)


# ---------------------------------------------------------------------------
# UI view-model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewsItemView:
    """UI tarafının gösterim için kullandığı haber kaydı.

    Faz 3'te ``rss_source.NewsItem`` + ``SentimentResult`` adaptör ile bu
    yapıya dönüştürülecek. Şimdilik placeholder veri doğrudan üretilir.
    """

    title: str
    url: str
    published_at: datetime
    source: str         # feed key (örn. 'reuters_finance')
    source_label: str   # insan-okur ad (örn. 'Reuters Finance')
    language: str       # 'tr' | 'en'
    sentiment: str      # 'positive' | 'neutral' | 'negative'
    sentiment_score: float  # [-1, +1]
    summary: Optional[str] = None
    tickers: tuple[str, ...] = ()  # ilgili hisseler (filtre için)
    exchange: str = ""  # ilgili instrument'ın borsası (BIST/NASDAQ/NYSE) — DB'den


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _colored_dot(color_hex: str, diameter: int = 12) -> QPixmap:
    """Sentiment göstergesi için içi dolu renkli daire ``QPixmap``'i üretir."""
    pm = QPixmap(diameter, diameter)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(color_hex))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, diameter, diameter)
    finally:
        painter.end()
    return pm


def _source_label(source_key: str) -> str:
    for key, label in SOURCE_FILTERS:
        if key == source_key:
            return label
    return source_key.replace("_", " ").title()


def _sample_news() -> list[NewsItemView]:
    """Placeholder veri — Faz 3'te ``RSSSource.fetch_news()`` ile değişecek."""
    now = datetime.now(tz=timezone.utc)
    return [
        NewsItemView(
            title="THYAO 3. çeyrek bilançosunda beklentilerin üzerinde net kâr açıkladı",
            url="https://www.kap.org.tr/tr/Bildirim/1234567",
            published_at=now - timedelta(minutes=18),
            source="kap_disclosures",
            source_label="KAP Bildirimleri",
            language="tr",
            sentiment="positive",
            sentiment_score=0.82,
            summary=(
                "Türk Hava Yolları (THYAO), 3. çeyrekte 12,4 milyar TL net kâr "
                "açıkladı. Analist beklentisi 10,1 milyar TL idi. Yolcu doluluğu "
                "%84,3 ile rekor seviyede. Yönetim 4. çeyrek için temkinli iyimser."
            ),
            tickers=("THYAO",),
        ),
        NewsItemView(
            title="Fed faiz kararı sonrası ABD endeksleri sert geri çekildi",
            url="https://www.reuters.com/markets/us/fed-hike-shock",
            published_at=now - timedelta(hours=1, minutes=12),
            source="reuters_finance",
            source_label="Reuters Finance",
            language="en",
            sentiment="negative",
            sentiment_score=-0.64,
            summary=(
                "Fed faiz kararının ardından S&P 500 %1,8, Nasdaq %2,3 düşüş "
                "yaşadı. Powell'ın 'enflasyon dirençli' açıklaması piyasada "
                "satış baskısı yarattı."
            ),
            tickers=("SPY", "AAPL", "MSFT"),
        ),
        NewsItemView(
            title="ASELS yeni savunma sanayii ihalesini kazandı",
            url="https://www.bloomberght.com/asels-yeni-ihale",
            published_at=now - timedelta(hours=2, minutes=40),
            source="bloomberg_tr",
            source_label="Bloomberg TR",
            language="tr",
            sentiment="positive",
            sentiment_score=0.71,
            summary=(
                "ASELSAN (ASELS), 240 milyon USD değerinde radar sistemleri "
                "ihalesini kazandı. Sözleşme bedeli 2027 sonuna kadar gelir "
                "olarak yansıyacak."
            ),
            tickers=("ASELS",),
        ),
        NewsItemView(
            title="BIST 100 açılışta yatay seyrediyor, bankacılık endeksi pozitif",
            url="https://www.dunya.com/bist-100-yatay-seyrediyor",
            published_at=now - timedelta(hours=3),
            source="dunya_gazetesi",
            source_label="Dünya Gazetesi",
            language="tr",
            sentiment="neutral",
            sentiment_score=0.05,
            summary=(
                "BIST 100 endeksi 9.842 puandan açıldı. Bankacılık endeksi "
                "%0,4 yükselirken sanayi endeksi %0,1 geriledi."
            ),
            tickers=("XU100", "XBANK"),
        ),
        NewsItemView(
            title="Apple AI çipini kendi üretmeye başlıyor, tedarikçi hisseleri düştü",
            url="https://www.reuters.com/technology/apple-ai-chip-in-house",
            published_at=now - timedelta(hours=4, minutes=20),
            source="reuters_finance",
            source_label="Reuters Finance",
            language="en",
            sentiment="negative",
            sentiment_score=-0.42,
            summary=(
                "Apple, kendi AI çiplerini üretmeye başladığını duyurdu. "
                "Haber sonrası NVDA %3, AVGO %5 değer kaybetti. AAPL ise "
                "yatay seyrediyor."
            ),
            tickers=("AAPL", "NVDA", "AVGO"),
        ),
        NewsItemView(
            title="Garanti Bankası temettü dağıtım kararı aldı",
            url="https://www.mynet.com/finans/garanti-temettu",
            published_at=now - timedelta(hours=5, minutes=5),
            source="mynet_finans",
            source_label="Mynet Finans",
            language="tr",
            sentiment="positive",
            sentiment_score=0.58,
            summary=(
                "Garanti BBVA (GARAN) yönetim kurulu, hisse başı 1,85 TL "
                "brüt temettü dağıtımı önerisini onayladı. Genel kurul "
                "tarihi henüz açıklanmadı."
            ),
            tickers=("GARAN",),
        ),
        NewsItemView(
            title="Tesla üretim hedefini düşürdü, sosyal medyada tartışma sürüyor",
            url="https://www.reuters.com/business/autos/tesla-production-cut",
            published_at=now - timedelta(hours=7),
            source="reuters_finance",
            source_label="Reuters Finance",
            language="en",
            sentiment="negative",
            sentiment_score=-0.55,
            summary=(
                "Tesla 4. çeyrek üretim hedefini 510.000'den 470.000'e düşürdü. "
                "Hisseler ön piyasada %4,2 değer kaybetti."
            ),
            tickers=("TSLA",),
        ),
        NewsItemView(
            title="KAP: Sabancı Holding pay geri alım programını uzattı",
            url="https://www.kap.org.tr/tr/Bildirim/7654321",
            published_at=now - timedelta(hours=8, minutes=30),
            source="kap_disclosures",
            source_label="KAP Bildirimleri",
            language="tr",
            sentiment="positive",
            sentiment_score=0.46,
            summary=(
                "Sabancı Holding (SAHOL) pay geri alım programının süresinin "
                "31 Aralık 2026'ya uzatıldığını ve azami tutarın 2 milyar TL'ye "
                "çıkarıldığını bildirdi."
            ),
            tickers=("SAHOL",),
        ),
    ]


# ---------------------------------------------------------------------------
# Haber satırı widget'ı
# ---------------------------------------------------------------------------


class _NewsRowWidget(QWidget):
    """Liste satırı: üstte başlık + tarih, altta kaynak + sentiment göstergesi."""

    def __init__(self, item: NewsItemView, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._item = item
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        # Üst satır: başlık + tarih
        top = QHBoxLayout()
        top.setSpacing(8)

        title = QLabel(self._item.title)
        title_font = title.font()
        title_font.setBold(True)
        title.setFont(title_font)
        title.setWordWrap(True)
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        date_lbl = QLabel(self._format_date(self._item.published_at))
        date_font = date_lbl.font()
        date_font.setPointSizeF(max(7.0, date_font.pointSizeF() - 1.0))
        date_lbl.setFont(date_font)
        date_lbl.setStyleSheet("color: gray;")
        date_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        date_lbl.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

        top.addWidget(title, stretch=1)
        top.addWidget(date_lbl, stretch=0)
        layout.addLayout(top)

        # Alt satır: kaynak rozeti + sentiment göstergesi
        bottom = QHBoxLayout()
        bottom.setSpacing(8)

        source_badge = QLabel(self._item.source_label)
        source_badge.setStyleSheet(
            "background-color: rgba(120,120,120,40); "
            "border-radius: 8px; padding: 2px 8px;"
        )
        small_font = QFont(source_badge.font())
        small_font.setPointSizeF(max(7.5, small_font.pointSizeF() - 1.0))
        source_badge.setFont(small_font)

        lang_badge = QLabel(self._item.language.upper())
        lang_badge.setStyleSheet(
            "background-color: rgba(70,130,180,80); "
            "color: white; border-radius: 6px; padding: 1px 6px;"
        )
        lang_badge.setFont(small_font)

        # Sentiment göstergesi (yuvarlak nokta + Türkçe etiket + skor)
        sentiment_color, sentiment_label = SENTIMENT_PALETTE.get(
            self._item.sentiment, SENTIMENT_PALETTE["neutral"]
        )
        dot_lbl = QLabel()
        dot_lbl.setPixmap(_colored_dot(sentiment_color))
        dot_lbl.setFixedSize(QSize(14, 14))

        score_lbl = QLabel(f"{sentiment_label} ({self._item.sentiment_score:+.1f})")
        score_lbl.setStyleSheet(f"color: {sentiment_color}; font-weight: 600;")
        score_lbl.setFont(small_font)

        bottom.addWidget(source_badge)
        bottom.addWidget(lang_badge)
        bottom.addSpacing(8)
        bottom.addWidget(dot_lbl)
        bottom.addWidget(score_lbl)
        bottom.addStretch(1)

        layout.addLayout(bottom)

    @staticmethod
    def _format_date(dt: datetime) -> str:
        """Bağıl tarih: '12 dk önce', '3 sa önce', sonra tam tarih."""
        now = datetime.now(tz=dt.tzinfo or timezone.utc)
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return "az önce"
        if seconds < 3600:
            return f"{seconds // 60} dk önce"
        if seconds < 86_400:
            return f"{seconds // 3600} sa önce"
        if seconds < 7 * 86_400:
            return f"{seconds // 86_400} gün önce"
        return dt.strftime("%d.%m.%Y %H:%M")


# ---------------------------------------------------------------------------
# Ana widget
# ---------------------------------------------------------------------------


class NewsWidget(QWidget):
    """Haber akışı ekranı (Faz 2)."""

    #: UI'ya dışarıdan haber yüklenmek istendiğinde tetiklenecek sinyal
    #: (Faz 3'te gerçek RSS worker'ı ile bağlanacak).
    news_loaded = Signal(int)  # eklenen kayıt sayısı

    def __init__(
        self,
        parent: QWidget | None = None,
        bridge: "BackendBridge | None" = None,
    ) -> None:
        super().__init__(parent)
        self._bridge = bridge
        self._all_items: list[NewsItemView] = []
        self._selected_item: Optional[NewsItemView] = None
        self._build_ui()
        # Backend yoksa placeholder göster.
        if bridge is None or not bridge.available:
            self.load_items(_sample_news())
        else:
            self.refresh()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh()

    # ------------------------------------------------------------------ backend
    def refresh(self) -> None:
        if self._bridge is None or not self._bridge.available:
            return
        session_factory = self._bridge.session_factory

        async def _fetch():
            return _fetch_news_from_db(session_factory)

        self._bridge.run_async(
            _fetch, on_success=self._on_news_ready, on_error=self._on_refresh_error
        )

    def _on_news_ready(self, items) -> None:
        try:
            if not items:
                # Boş tabloda placeholder göster (RSS henüz çekilmemiş olabilir)
                self.load_items([])
                return
            self.load_items(items)
        except Exception as exc:  # noqa: BLE001
            logger.debug("NewsWidget._on_news_ready hata: {}", exc)

    def _on_refresh_error(self, exc) -> None:
        logger.debug("NewsWidget refresh hata: {}", exc)

    # ------------------------------------------------------------------ build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(12)

        # Başlık
        title = QLabel("Haberler")
        f = title.font()
        f.setPointSize(f.pointSize() + 6)
        f.setBold(True)
        title.setFont(f)
        root.addWidget(title)

        # Filtre çubuğu — Faz 4 cilası: serbest metin arama yerine yapılandırılmış
        # dropdown'lar. Borsa, Hisse (instruments tablosundan), Dil, Tarih.
        filter_bar = QHBoxLayout()
        filter_bar.setSpacing(8)

        # Borsa
        self._exchange_combo = QComboBox()
        for code, label in EXCHANGE_FILTERS:
            self._exchange_combo.addItem(label, userData=code)
        self._exchange_combo.setToolTip("Borsaya göre filtrele")
        self._exchange_combo.currentIndexChanged.connect(self._on_exchange_changed)

        # Hisse — "Hepsi" + instruments
        self._ticker_combo = QComboBox()
        self._ticker_combo.setEditable(True)
        self._ticker_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._ticker_combo.setMinimumWidth(180)
        self._ticker_combo.setToolTip(
            "Hisseye göre filtrele — yazmaya başlayarak arayın"
        )
        self._ticker_combo.addItem("Hepsi", userData="")
        self._populate_ticker_combo()
        self._ticker_combo.currentIndexChanged.connect(self._apply_filters)
        # Editable text de değiştiğinde filtre uygulansın (combo seçimine
        # eşdeğer — kullanıcı yazıp Enter'a basmasa bile listede o ticker varsa
        # seçim güncellenir).
        line = self._ticker_combo.lineEdit()
        if line is not None:
            line.editingFinished.connect(self._on_ticker_text_committed)

        # Dil
        self._lang_combo = QComboBox()
        for code, label in LANGUAGE_FILTERS:
            self._lang_combo.addItem(label, userData=code)
        self._lang_combo.setToolTip("Dile göre filtrele")
        self._lang_combo.currentIndexChanged.connect(self._apply_filters)

        # Tarih aralığı
        self._date_combo = QComboBox()
        for key, label, _days in DATE_FILTERS:
            self._date_combo.addItem(label, userData=key)
        self._date_combo.setCurrentIndex(2)  # "Son 30 gün" varsayılan
        self._date_combo.setToolTip("Yayın tarihine göre filtrele")
        self._date_combo.currentIndexChanged.connect(self._apply_filters)

        clear_btn = QPushButton("Filtreleri Temizle")
        if QTAWESOME_AVAILABLE:
            try:
                clear_btn.setIcon(qta.icon("fa5s.times"))
            except Exception:  # noqa: BLE001
                pass
        clear_btn.clicked.connect(self._clear_filters)

        filter_bar.addWidget(QLabel("Borsa:"))
        filter_bar.addWidget(self._exchange_combo)
        filter_bar.addWidget(QLabel("Hisse:"))
        filter_bar.addWidget(self._ticker_combo)
        filter_bar.addWidget(QLabel("Dil:"))
        filter_bar.addWidget(self._lang_combo)
        filter_bar.addWidget(QLabel("Tarih:"))
        filter_bar.addWidget(self._date_combo)
        filter_bar.addStretch(1)
        filter_bar.addWidget(clear_btn)
        root.addLayout(filter_bar)

        # Splitter: sol liste | sağ detay
        splitter = QSplitter(Qt.Orientation.Horizontal, self)

        # Sol panel: haber listesi
        self._news_list = QListWidget(splitter)
        self._news_list.setSpacing(2)
        self._news_list.setAlternatingRowColors(True)
        self._news_list.itemSelectionChanged.connect(self._on_selection_changed)
        self._news_list.itemDoubleClicked.connect(self._on_item_double_clicked)
        splitter.addWidget(self._news_list)

        # Sağ panel: haber detayı
        self._detail_panel = self._build_detail_panel(splitter)
        splitter.addWidget(self._detail_panel)

        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([700, 400])

        root.addWidget(splitter, stretch=1)

    def _build_detail_panel(self, parent: QWidget) -> QWidget:
        panel = QFrame(parent)
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._detail_title = QLabel("Bir haber seçiniz")
        dt_font = self._detail_title.font()
        dt_font.setPointSize(dt_font.pointSize() + 2)
        dt_font.setBold(True)
        self._detail_title.setFont(dt_font)
        self._detail_title.setWordWrap(True)

        self._detail_meta = QLabel("—")
        self._detail_meta.setStyleSheet("color: gray;")
        self._detail_meta.setWordWrap(True)

        # Sentiment detayı satırı
        sentiment_row = QHBoxLayout()
        self._detail_sentiment_dot = QLabel()
        self._detail_sentiment_dot.setFixedSize(QSize(16, 16))
        self._detail_sentiment_label = QLabel("Sentiment: —")
        self._detail_sentiment_label.setStyleSheet("font-weight: 600;")
        sentiment_row.addWidget(self._detail_sentiment_dot)
        sentiment_row.addWidget(self._detail_sentiment_label)
        sentiment_row.addStretch(1)

        # Tam metin / özet
        self._detail_body = QTextBrowser()
        self._detail_body.setOpenExternalLinks(True)
        self._detail_body.setPlaceholderText(
            "Bir haber seçtiğinizde özet burada görünecek."
        )

        # Aksiyon butonları
        button_row = QHBoxLayout()
        self._open_browser_btn = QPushButton("Tarayıcıda Aç")
        if QTAWESOME_AVAILABLE:
            try:
                self._open_browser_btn.setIcon(qta.icon("fa5s.external-link-alt"))
            except Exception:  # noqa: BLE001
                pass
        self._open_browser_btn.setEnabled(False)
        self._open_browser_btn.clicked.connect(self._open_selected_in_browser)
        button_row.addStretch(1)
        button_row.addWidget(self._open_browser_btn)

        layout.addWidget(self._detail_title)
        layout.addWidget(self._detail_meta)
        layout.addLayout(sentiment_row)
        layout.addWidget(self._detail_body, stretch=1)
        layout.addLayout(button_row)
        return panel

    # ------------------------------------------------------------------ data API
    def load_items(self, items: list[NewsItemView]) -> None:
        """Yeni veri seti yükler.

        Faz 3'te ``RSSSource.fetch_news()`` -> ``SentimentAnalyzer.analyze()``
        -> bu yapıya dönüşüm bir QThreadPool worker'ında yapılacak ve sinyalle
        bu metoda iletilecek.
        """
        self._all_items = list(items)
        self._apply_filters()
        self.news_loaded.emit(len(self._all_items))

    # ------------------------------------------------------------------ slots
    @Slot()
    def _apply_filters(self) -> None:
        """Aktif filtrelere göre listeyi yeniden çizer.

        Liste her zaman ``published_at DESC`` (en yeni üstte). Backend
        sorgusu zaten bu sırada döndürür; UI tarafı sadece görünür
        filtreleri uygular.
        """
        lang = self._lang_combo.currentData() or ""
        exchange = self._exchange_combo.currentData() or ""
        ticker_filter = (self._ticker_combo.currentData() or "").upper()
        # Editable combo: kullanıcı serbest yazdıysa userData null kalabilir;
        # o zaman görünen metni ticker olarak değerlendir.
        if not ticker_filter:
            text = (self._ticker_combo.currentText() or "").strip().upper()
            if text and text != "HEPSI":
                ticker_filter = text

        date_key = self._date_combo.currentData() or "all"
        cutoff: Optional[datetime] = None
        for key, _label, days in DATE_FILTERS:
            if key == date_key and days is not None:
                cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
                break

        # En yeni üstte garanti: kaynak listesi sıralı varsayılsa da burada
        # tekrar sıralıyoruz (placeholder veri sırasız gelebilir).
        items = sorted(
            self._all_items,
            key=lambda it: it.published_at or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

        self._news_list.clear()

        for item in items:
            if lang and item.language != lang:
                continue
            if exchange and (item.exchange or "").upper() != exchange.upper():
                continue
            if ticker_filter:
                # Hisse filtresi: instrument.exchange/ticker match veya
                # haber tickerları arasında geçiyorsa kabul et.
                hay = {t.upper() for t in item.tickers}
                if ticker_filter not in hay:
                    continue
            if cutoff is not None and item.published_at is not None:
                # tz-naive olabilir; aware'a çevirip karşılaştır
                pub = item.published_at
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    continue

            list_item = QListWidgetItem(self._news_list)
            row_widget = _NewsRowWidget(item)
            list_item.setSizeHint(row_widget.sizeHint())
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self._news_list.addItem(list_item)
            self._news_list.setItemWidget(list_item, row_widget)

        if self._news_list.count() == 0:
            if not self._all_items:
                msg = (
                    "Henüz haber yok — RSS scheduler eklenince burada güncel "
                    "haberler listelenecek."
                )
            else:
                msg = "Seçili filtrelere uyan haber bulunamadı."
            empty = QListWidgetItem(msg)
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            empty.setForeground(QColor("#888888"))
            self._news_list.addItem(empty)

    @Slot()
    def _clear_filters(self) -> None:
        self._lang_combo.setCurrentIndex(0)
        self._exchange_combo.setCurrentIndex(0)
        self._date_combo.setCurrentIndex(2)  # default = Son 30 gün
        self._ticker_combo.setCurrentIndex(0)
        self._apply_filters()

    # ------------------------------------------------------------------ helpers
    def _populate_ticker_combo(self, exchange_filter: str = "") -> None:
        """Ticker combobox'unu DB cache'inden doldur.

        Combo zaten "Hepsi" satırı içeriyor olabilir — onu koruyup geri
        kalanı yeniden ekler.
        """
        # İlk satır (Hepsi) hariç temizle
        self._ticker_combo.blockSignals(True)
        while self._ticker_combo.count() > 1:
            self._ticker_combo.removeItem(1)

        available: list[tuple[str, str, str]] = []
        if self._bridge is not None:
            try:
                available = self._bridge.cached_instruments()
            except Exception as exc:  # noqa: BLE001
                logger.debug("news ticker_combo: cached_instruments hata: {}", exc)

        tickers_for_completer: list[str] = []
        for ticker, name, exchange in available:
            if exchange_filter and exchange.upper() != exchange_filter.upper():
                continue
            display = f"{ticker}  —  {name}" if name else ticker
            self._ticker_combo.addItem(display, userData=ticker)
            tickers_for_completer.append(ticker)

        # Tek seferlik QCompleter
        completer = QCompleter(tickers_for_completer + ["Hepsi"], self._ticker_combo)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self._ticker_combo.setCompleter(completer)
        self._ticker_combo.blockSignals(False)

    @Slot()
    def _on_exchange_changed(self) -> None:
        """Borsa değişince ticker combosu da o borsaya kısılsın."""
        exchange = self._exchange_combo.currentData() or ""
        self._populate_ticker_combo(exchange_filter=exchange)
        self._apply_filters()

    @Slot()
    def _on_ticker_text_committed(self) -> None:
        """Editable combo'da kullanıcı text girip Enter'a basınca."""
        # Combo zaten currentText() kullanır; _apply_filters yeniden uygula
        self._apply_filters()

    @Slot()
    def _on_selection_changed(self) -> None:
        items = self._news_list.selectedItems()
        if not items:
            self._selected_item = None
            self._open_browser_btn.setEnabled(False)
            return
        data = items[0].data(Qt.ItemDataRole.UserRole)
        if not isinstance(data, NewsItemView):
            return
        self._selected_item = data
        self._render_detail(data)
        self._open_browser_btn.setEnabled(True)

    @Slot(QListWidgetItem)
    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(data, NewsItemView):
            QDesktopServices.openUrl(QUrl(data.url))

    @Slot()
    def _open_selected_in_browser(self) -> None:
        if self._selected_item is None:
            return
        QDesktopServices.openUrl(QUrl(self._selected_item.url))

    # ------------------------------------------------------------------ render
    def _render_detail(self, item: NewsItemView) -> None:
        self._detail_title.setText(item.title)
        date_str = item.published_at.astimezone().strftime("%d.%m.%Y %H:%M")
        meta = (
            f"{item.source_label}  •  {item.language.upper()}  •  {date_str}"
        )
        if item.tickers:
            meta += f"  •  {', '.join(item.tickers)}"
        self._detail_meta.setText(meta)

        color, label = SENTIMENT_PALETTE.get(
            item.sentiment, SENTIMENT_PALETTE["neutral"]
        )
        self._detail_sentiment_dot.setPixmap(_colored_dot(color, diameter=16))
        self._detail_sentiment_label.setText(
            f"Sentiment: {label} (skor {item.sentiment_score:+.2f})"
        )
        self._detail_sentiment_label.setStyleSheet(
            f"color: {color}; font-weight: 600;"
        )

        body = item.summary or "(Özet bulunamadı)"
        # Tıklanabilir URL
        html = (
            f"<p>{body}</p>"
            f"<p><a href='{item.url}'>{item.url}</a></p>"
        )
        self._detail_body.setHtml(html)


def _fetch_news_from_db(session_factory, limit: int = 200) -> list[NewsItemView]:
    """``news_feed`` tablosundan son haberleri çek — sync sorgu.

    Instrument bilgisini outer-join ile getiririz; haberin doğrudan bir
    hisseyle eşleşmemiş olması durumunda ``exchange``/``tickers`` boş kalır.
    Sıralama: ``published_at DESC`` (en yeni üstte).
    """

    from sqlalchemy import select  # noqa: WPS433
    from app.db.models import Instrument, NewsFeed  # noqa: WPS433

    session = session_factory()
    try:
        stmt = (
            select(NewsFeed, Instrument)
            .join(Instrument, Instrument.id == NewsFeed.instrument_id, isouter=True)
            .order_by(NewsFeed.published_at.desc().nulls_last())
            .limit(limit)
        )
        rows = list(session.execute(stmt).all())
    finally:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    result: list[NewsItemView] = []
    for row in rows:
        news = row[0]
        instr = row[1] if len(row) > 1 else None
        source_key = (news.source or "").strip()
        ticker = str(instr.ticker) if (instr is not None and instr.ticker) else ""
        exchange = str(instr.exchange) if (instr is not None and instr.exchange) else ""
        result.append(
            NewsItemView(
                title=str(news.title or "(başlık yok)"),
                url=str(news.url or ""),
                published_at=news.published_at or datetime.now(tz=timezone.utc),
                source=source_key,
                source_label=_source_label(source_key),
                language=str(news.language or "tr"),
                sentiment=str(news.sentiment or "neutral"),
                sentiment_score=float(news.sentiment_score or 0),
                summary=None,
                tickers=((ticker,) if ticker else ()),
                exchange=exchange,
            )
        )
    return result


__all__ = [
    "NewsWidget",
    "NewsItemView",
    "SENTIMENT_PALETTE",
    "LANGUAGE_FILTERS",
    "SOURCE_FILTERS",
    "EXCHANGE_FILTERS",
    "DATE_FILTERS",
]
