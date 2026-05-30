---
date: 2026-05-27
agent: ui-developer
phase: faz-1
type: feature
related_files:
  - app/main.py
  - app/ui/main_window.py
  - app/ui/theme_manager.py
  - app/ui/dashboard.py
  - app/ui/chart_widget.py
  - app/ui/alerts_widget.py
  - app/ui/settings_widget.py
  - app/ui/components/__init__.py
related_doc_sections:
  - "3.2 Arayüz (UI)"
  - "8.1 Tasarım İlkeleri"
  - "8.2 Dark / Light Mod"
  - "8.3 Ekran Yapısı"
  - "8.4 Modern UI Bileşenleri"
  - "11. Faz 1 — Temel Altyapı"
---

## Özet

Faz 1 PySide6 arayüz iskeleti tamamlandı: uygulama giriş noktası (`app/main.py`),
daraltılabilir sidebar'lı ana pencere, runtime dark/light/auto tema yöneticisi
(qdarktheme + Fusion fallback), iskelet Dashboard / Grafik / Alarmlar / Ayarlar
ekranları yazıldı. Faz 2/3'te eklenecek ekranlar için sidebar slot'ları "Yakında"
placeholder ile hazır bekliyor. Hiçbir backend çağrısı yapılmaz; tüm değerler
placeholder (`—`) olarak gösterilir.

## Detaylar

### Yazılan dosyalar

- **`app/main.py`** — `QApplication` kurulumu (`setApplicationName="Borsa Bot"`,
  `setOrganizationName="BorsaBot"` → `QSettings` için kalıcı tercih konumu),
  `ThemeManager.apply_saved_or_default()`, `MainWindow.show()` ve `app.exec()`.
  Loguru ile başlangıç log'u.

- **`app/ui/theme_manager.py`** — `QObject` tabanlı `ThemeManager`.
  - Sinyaller: `theme_changed(str)`, `accent_changed(str)`.
  - Tema seçenekleri: `("dark", "light", "auto")`, varsayılan `auto`.
  - Accent renkleri: 5 ton — `#1E88E5` (mavi/varsayılan), `#43A047` (yeşil),
    `#8E24AA` (mor), `#FB8C00` (turuncu), `#E53935` (kırmızı).
  - `qdarktheme.setup_theme(name, custom_colors={"primary": accent})` ile runtime
    geçiş, restart YOK.
  - Tercihler `QSettings("BorsaBot", "Borsa Bot")` üzerinde `appearance/theme` ve
    `appearance/accent` anahtarlarıyla saklanır.
  - **Graceful degradation:** `qdarktheme` `ImportError` durumunda
    `_apply_fusion_fallback()` devreye girer — `QApplication.setStyle("Fusion")` +
    manuel `QPalette` (koyu/açık).
  - `current_effective_theme()` 'auto' modda bile çözümlenmiş 'dark' veya 'light'
    döndürür (palette window-color lightness analizi).
  - `toggle_theme()` Ctrl+T kısayolu için sağlanır.

- **`app/ui/main_window.py`** — `QMainWindow` subclass.
  - **Sidebar:** `QListWidget`, daraltılabilir (200px ↔ 56px), `qtawesome`
    Font Awesome ikonları. qtawesome yoksa metin-only fallback.
  - **Stack:** `QStackedWidget` — ekranlar arası geçiş.
  - **NAV_ITEMS sırası (doküman §8.3):** Dashboard, Takip Listesi, Bot Önerileri,
    Grafik, Portföy, Bütçe, İşlem & Otomasyon, İşlem Geçmişi, Haberler, Alarmlar,
    Ayarlar. Faz 1'de yalnızca Dashboard / Grafik / Alarmlar / Ayarlar aktif;
    diğerleri `_ComingSoonWidget` placeholder gösterir.
  - **Toolbar:** sidebar daralt/genişlet, tema toggle (sun/moon ikon), ayarlar.
  - **Status bar:** bağlantı durumu, son güncelleme zamanı, sürüm (`v0.1.0`).
  - **QSettings kalıcılık:** `main_window/geometry`, `main_window/state`,
    `main_window/sidebar_collapsed` — açılışta restore, kapanışta save (`closeEvent`).
  - **`theme_manager.theme_changed` slot:** tema değiştiğinde ChartWidget'a
    `set_theme(is_dark)` çağrısı + toolbar tema ikonu güncelleme.

- **`app/ui/dashboard.py`** — `DashboardWidget` (`QWidget`). Üstte 4 sütun metrik
  kartı (Toplam Bakiye, Günlük Değişim, Aktif Pozisyon, Bot Sinyalleri), orta
  "Piyasa Özeti" `QTableWidget` (BIST 100, S&P 500, USD/TRY), alt "Son Haberler"
  `QListWidget`. Tüm değerler `—` placeholder. Faz 3'te `account.py`,
  `recommender.py`, Faz 2'de `rss_source.py` bağlanacak.

- **`app/ui/chart_widget.py`** — `ChartWidget` (`QWidget`). Üst toolbar: sembol
  arama (`QLineEdit`), zaman aralığı combo (1D / 1W / 1M / 3M / 1Y), "Yükle"
  butonu (Faz 1'de pasif, tooltip "Veri kaynağı Faz 2'de bağlanacak").
  - **Graceful degradation:** `finplot` `ImportError` → `QLabel("Grafik
    kütüphanesi yüklenmedi…")` placeholder gösterir, asla crash etmez.
  - Public API stub'ları: `load_ticker(ticker, df)`, `add_indicator(name, series)`,
    `set_theme(is_dark)` — Faz 2/3'te tamamlanacak.

- **`app/ui/alerts_widget.py`** — `AlertsWidget` (`QWidget`).
  - Üst form: sembol combo (editable), alarm türü combo (doküman §9.1
    `alert_type` değerleri: `price_above`, `price_below`, `pct_change`, `signal`),
    eşik `QDoubleSpinBox`, "Alarm Ekle" butonu.
  - Alt `QTableWidget` 5 kolon: Sembol, Tür, Eşik, Durum, Oluşturulma.
  - `on_alert_triggered(payload: dict)` slot — `app.alerts.notifier.Notifier.alert_triggered`
    (`Signal(dict)`) Qt sinyaline bağlanacak; payload `alert_id`,
    `instrument_id`, `alert_type`, `message` vb. alanları içerir.
    Faz 1'de `QMessageBox` placeholder, Faz 3'te `components/toast.py` ile
    değiştirilecek.

- **`app/ui/settings_widget.py`** — `SettingsWidget` (`QWidget`).
  - **Görünüm:** tema `QComboBox` ("Otomatik (sistem)", "Karanlık", "Aydınlık")
    → `ThemeManager.set_theme()`; accent için 5 renk butonu (mavi/yeşil/mor/turuncu/kırmızı)
    → `ThemeManager.set_accent()`. Checked durumu beyaz border ile vurgulanır.
  - **Dil:** Türkçe (aktif), English (pasif "yakında").
  - **Veri Kaynakları:** `QListWidget` checkable item'lar — yfinance, Stooq,
    Alpha Vantage, İş Yatırım, KAP, TradingView, Investing.com, Finviz,
    TCMB (USD/TRY), Reddit. Faz 2'de `DataCollector` ile bağlanacak.
  - **Bildirimler:** "Alarm bildirimlerini göster", "Sistem bildirimlerini kullan"
    checkbox'ları.
  - **API Anahtarları:** Alpha Vantage, Reddit Client ID, Reddit Client Secret —
    `QLineEdit(EchoMode.Password)`. Faz 3'te `keyring` ile OS güvenli deposunda
    şifreli saklanacak (şu an form görüntülenir, kaydetme bağlı değil).

- **`app/ui/components/__init__.py`** — Mevcut dosya (devops-engineer tarafından
  oluşturulmuş) korundu; tek satır docstring. Reusable component'lar Faz 3'te
  eklenecek.

### Klavye Kısayolları

| Kısayol | İşlev |
|---|---|
| `Ctrl+1` … `Ctrl+9` | Sidebar item'larına geçiş (ilk 9) |
| `Ctrl+B` | Yan menüyü daralt / genişlet |
| `Ctrl+T` | Karanlık / aydınlık tema toggle |
| `Ctrl+,` | Ayarlar ekranına git |
| `Ctrl+Q` | Uygulamadan çık |

### Tema Sistemi

- **Birincil:** `qdarktheme.setup_theme(name, custom_colors={"primary": accent_hex})`.
  Runtime'da anında uygulanır, uygulama restart edilmez.
- **Accent renk:** 5 önceden tanımlı ton. `custom_colors` ile qdarktheme palette'i
  üzerine bindirilir.
- **Auto modu:** qdarktheme sistemin renk şemasını okuyarak otomatik dark/light
  seçer. `current_effective_theme()` palette analizi ile çözümlenmiş temayı verir.
- **Fallback:** qdarktheme `ImportError` → `QApplication.setStyle("Fusion")` +
  manuel `QPalette` (koyu zemin RGB(30,30,30); aydınlık standart Fusion).
- **Kalıcılık:** `QSettings("BorsaBot", "Borsa Bot")` `appearance/theme` ve
  `appearance/accent` anahtarları.

### Graceful Degradation Stratejisi (Eksik Bağımlılıklar)

| Paket | Eksikse | Davranış |
|---|---|---|
| `pyqtdarktheme` (qdarktheme) | Tema yöneticisi Fusion fallback'e geçer | Uygulama çalışır, sade görünüm |
| `finplot` | ChartWidget placeholder QLabel gösterir | Uygulama çalışır, grafik gösteremez |
| `qtawesome` | Sidebar/toolbar metin-only, ikon yok | Uygulama çalışır, görsel daha sade |

`try/except ImportError` ile her bağımlılık modül seviyesinde sarmalanmıştır;
modülün geri kalanı modül-seviyesi bayrak (`QDARKTHEME_AVAILABLE` vb.) üzerinden
davranır. Loguru ile uyarı log'u atılır.

## Gerekçe

Doküman §11 Faz 1 maddesi "PySide6 ana pencere + sidebar navigasyon iskeleti",
"Dark/light tema altyapısı (theme_manager.py, qdarktheme)", "Grafik bileşeni
(finplot mum grafikleri, temaya duyarlı)" ve "Alarm sistemi" çıktılarını
karşılar. Faz 1'in görsel temeli kurularak Faz 2/3'teki ekran geliştirmeleri
için sağlam bir iskelet sağlandı.

PySide6 (LGPL) bilinçli olarak PyQt6 (GPL) yerine seçildi — doküman §3.2 ve
ui-developer agent kuralı gereği.

## Test / Doğrulama

- Sözdizimi seviyesinde yazıldı; bağımlılıklar henüz `poetry install`
  edilmediği için runtime testi YAPILMADI. Bu faz testleri DevOps `poetry install`
  ardından çalıştırılacak: `python -m app.main`.
- pytest UI testleri ileride `pytest-qt` ile eklenecek (Faz 3 UI cilası).
- Manuel test kontrol listesi:
  - Tema toggle (Ctrl+T) anında çalışmalı, restart gerektirmemeli.
  - Accent renk butonuna tıklayınca primary renk hemen değişmeli.
  - Sidebar daraltma (Ctrl+B) sonrası yalnızca ikon gözükmeli.
  - Pencere boyutu/konumu kapatıp açınca korunmalı.
  - Ctrl+1..9 ile ekranlar arası geçiş çalışmalı.
  - Pasif ekranlara (Watchlist, Portföy, vb.) tıklayınca "Yakında" mesajı
    gösterilmeli.

## Notlar

### Faz 2 — Eklenecek widget'lar (henüz yok)

- `app/ui/watchlist_widget.py` — Kullanıcı takip listeleri (sürükle-bırak sıralama).
- `app/ui/bot_picks_widget.py` — Bot öneri listesi + başarı oranı.
- `app/ui/news_widget.py` — Haber akışı + sentiment göstergesi.
- ChartWidget'a gerçek `finplot.create_plot()` + `candlestick_ochl()` entegrasyonu.
- DataCollector entegrasyonu (sembol seçildiğinde OHLCV çekme).

### Faz 3 — Eklenecek widget'lar

- `app/ui/portfolio_widget.py` — Bakiye, açık pozisyon, P&L, sektör pastası.
- `app/ui/budget_widget.py` — Havuz × vade matrisi, para ekleme/çekme.
- `app/ui/trading_widget.py` — İşlem modu, risk eşiği, **kırmızı büyük
  kill switch butonu** (doküman §7.5 — onay diyaloğu ile).
- `app/ui/history_widget.py` — İşlem geçmişi + filtreleme + CSV/Excel export.
- `app/ui/recommendation_widget.py` — Öneri kartı + açıklanabilirlik
  çubuk grafiği (hangi sinyal % katkı yaptı).
- **Reusable component'lar** (`app/ui/components/`):
  - `metric_card.py` — sparkline + delta ikon entegre kart.
  - `sparkline.py` — pyqtgraph mini trend grafiği.
  - `toast.py` — sağ üst köşede beliren bildirim balonu (AlertsWidget'taki
    QMessageBox placeholder'ı bununla değiştirilecek).
  - `skeleton.py` — yükleme iskeleti animasyonu.
- Settings'te API anahtarları için `keyring` entegrasyonu (şifreli kaydetme).
- Dashboard değerlerini `app.portfolio.account` + `app.analysis.recommender`
  servislerine bağlama.

### Backend API ihtiyaçları (ilgili agent'ları haberdar etmek için)

- `data-collector`: Ticker → OHLCV DataFrame async API'si (ChartWidget için).
- `alerts-engine`: `Notifier.alert_triggered = Signal(dict)` zaten mevcut
  (`app/alerts/notifier.py`); AlertsWidget'taki `on_alert_triggered(dict)`
  slot'una doğrudan `notifier.alert_triggered.connect(widget.on_alert_triggered)`
  ile bağlanabilir. Faz 3'te ana pencerede bu bağlama yapılacak.
- `portfolio-manager`: Dashboard kart değerleri için snapshot API
  (toplam bakiye, günlük değişim, açık pozisyon sayısı).
- `recommender`: Aktif/bekleyen sinyal sayısı + öneri listesi.

### Bilinen sınırlamalar

- `qdarktheme.setup_theme()` bazı 2.x sürümlerinde `custom_colors` argümanını
  desteklemeyebilir — exception yakalanıp Fusion fallback'e düşülüyor, accent
  renk o durumda yalnızca highlight'a yansır.
- finplot widget'ı ana stack'e dinamik eklenecek; Faz 2'de `chart_host`
  layout'una `fplt.create_plot()` çıktısı yerleştirilecek.
- Ekran görüntüsü için bağımlılıkların kurulması ve `python -m app.main`
  çalıştırılması gerekir — Faz 1 testi DevOps adımı sonrası.
