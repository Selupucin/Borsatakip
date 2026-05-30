# UI Developer — UX Redesign Batch (7 Fix)

**Tarih:** 2026-05-28
**Agent:** ui-developer (sub-agent)
**Kapsam:** Kullanıcı geri bildirimine dayalı 7 UI/UX düzeltmesi.

## Yapılan İşler

### 1 + 6 — QScrollArea Sarmalaması (uzun ekranlar)
- `settings_widget.py`, `trading_widget.py`, `portfolio_widget.py`,
  `budget_widget.py` artık içeriklerini `QScrollArea` içinde tutuyor.
- Başlık + toolbar sabit kaldı; sadece içerik dikey scroll edilebilir.
- Yatay scroll bar tüm ekranlarda kapalı (`ScrollBarAlwaysOff`).
- Küçük ekranlarda dipteki API anahtarları / hardcoded form alanları
  artık görünür.

### 2 — Onboarding Bilgi Kartları (HelpBanner)
- Yeni reusable bileşen: `app/ui/components/help_banner.py` →
  `HelpBanner(text, key, parent, icon, dismissable)`.
- Sağ üstte `×` butonu ile kalıcı olarak gizlenir; state `QSettings`
  altında `help_dismissed/<key>` olarak saklanır.
- Eklendi: `watchlist`, `bot_picks`, `portfolio`, `budget`, `trading`
  ekranlarına özel Türkçe ipuçları.
- `HelpBanner.reset_all()` debug helper'ı da var.

### 2.2 — Portföy'de "Sat" Akışı
- `PortfolioWidget._positions_table` artık sağ tık menüsü dinliyor:
  - **Sat** → `_AddToWalletDialog` (SELL kilitli, qty = mevcut adet,
    fiyat = son piyasa fiyatı). Başarılı `apply_sell` → toast +
    `position_changed` signal.
  - **Grafik aç** → MainWindow `_goto_chart_for(ticker)` çağırır.
- Yeni sinyaller: `PortfolioWidget.position_changed`,
  `PortfolioWidget.chart_requested`.
- `main_window.py` portfolio ekranını oluştururken bu sinyalleri bağladı.

### 2.3 — Watchlist Rozet Metni
- `_make_mini_signal_widget` artık sadece ikon değil, **etiket + güven %**
  gösteriyor: `▲ AL %72`, `▼ SAT %65`, `● BEKLE %50`.
- Veri yoksa "Yetersiz veri" (gri italik).
- Hücre tooltip: "Kısa vade önerisi: **AL** — güven %72" formatında.

### 2.4 — Bot Picks Açıklama Bandı
- `BotPicksWidget` başlığı altına net bir açıklama metni:
  > 📊 Bu bot ÖNERİ listesidir, portföyünüz değil.
  > SAT önerisi = 'Bu hisseyi şu an alma' veya
  > 'Açık pozisyonun varsa düşün' demek.

### 3 — Grafik: TradingView Embed
- `chart_widget.py` baştan aşağı yeniden yazıldı; eski `finplot` kodu
  tamamen kaldırıldı.
- `QWebEngineView` (PySide6-Addons) opsiyonel; varsa TradingView'in
  `widgetembed` URL'ini iframe gibi gömüyor.
- Yoksa fallback: tıklanabilir "TradingView'da Aç" linki.
- URL şablonu: `https://s.tradingview.com/widgetembed/?symbol=BIST:THYAO&interval=D&theme=dark&style=1&locale=tr&...`
- Borsa heuristiği: BIST için `.IS` yerine `BIST:THYAO`; ABD için
  `NASDAQ:AAPL` / `NYSE:JPM`. 5 harfli alfabetik fallback → BIST.
- Zaman aralığı combobox'ı (1m / 5m / 15m / 1h / 4h / D / W / M).
- Tema değişikliğinde otomatik reload.
- Modül docstring'inde kullanıcının sorduğu performans soru-cevabı:
  > "Bot her hisseyi TEK TEK yfinance ile çekmez — scheduler her 60sn'de
  > TÜM aktif evreni paralel olarak çeker; UI tarafındaki grafik
  > TradingView'in canlı web embed'i, ayrı bir indirme yapılmaz."

### 4 — Trading Widget UI Sadeleştirmesi
- "Bekleyen Öneri Onayları" başlığı yanına `(?)` info butonu + tooltip +
  tıklayınca QMessageBox açıklaması.
- "Şimdi Yenile" butonu eklendi → `refresh()` çağırır.
- Otomatik refresh timer 30 saniyeye düşürüldü (QTimer).
- HelpBanner ile genel akış açıklaması eklendi.

### 5 — Türkçe Locale Uygulaması
- `app/ui/_format.py` (zaten mevcut) tüm widget'larda kullanıma alındı.
- Yeni helper: `fmt_qty(value, max_decimals=4)` — tam sayıysa ondalık
  göstermez; trailing zeroları kırpar.
- `_us_to_tr` placeholder bug'ı düzeltildi (`""` → `\x01`).
- Değişen dosyalar:
  - `dashboard.py` — MetricCard değerleri, market özet, endeks rozeti,
    cüzdan mini-grid.
  - `portfolio_widget.py` — fiyat, K/Z, ağırlık %, KPI'lar.
  - `budget_widget.py` — cüzdan tutarları, cash flow tablosu, KPI'lar,
    mutation toast'ları.
  - `history_widget.py` — fiyat, toplam, komisyon, K/Z, özet.
  - `watchlist_widget.py` — fiyat, günlük %, hacim, AddToWallet diyaloğu.
  - `bot_picks_widget.py` — fiyat, hedef, getiri %, istatistik kartı.
  - `trading_widget.py` — bekleyen öneri fiyatları, KPI değerleri.
  - `recommendation_widget.py` — fiyat, güven %, contribution barları.
  - `_trade_dialogs.py` — tüm öneri/risk etiketleri.
- `dashboard.py` ve `portfolio_widget.py`'da hardcoded placeholder değer
  ("₺68.149,60" gibi) "—" ile değiştirildi — kullanıcıyı yanlış
  beklentiye düşürmesin.

### 7 — Bot Picks Filtre Paneli + Manuel Refresh
- `BotPicksWidget._build_filter_bar` eklendi:
  - **Vade** combobox (Tümü / Kısa / Orta / Uzun)
  - **Aksiyon** combobox (Tümü / AL / SAT / BEKLE)
  - **Borsa** combobox (Tümü / BIST / NASDAQ / NYSE)
  - **Min güven %** QSpinBox 0–100
  - **Min / Maks fiyat** QDoubleSpinBox (special value = "Yok")
  - **Sadece açık olanlar** checkbox (eskiden global'di, filtre çubuğuna
    taşındı)
- Butonlar: "Filtreleri Uygula", "Sıfırla", "🔄 Şimdi Yenile".
- Tüm filtre değişimleri client-side; backend imzası değişmedi.

## Yeni Component'lar
- `app/ui/components/help_banner.py` — `HelpBanner` (dismissible card)
- `app/ui/_format.py::fmt_qty` — adet formatlama

## Kaldırılan / Eskimiş
- `app/ui/chart_widget.py`'daki tüm `finplot` import + render kodu
  silindi. (`finplot` opsiyonel olduğu için bağımlılık zaten gevşekti.)

## Klavye Kısayolları
- Yeni kısayol eklenmedi; mevcut Ctrl+1..9 / Ctrl+T / Ctrl+P / Ctrl+M
  sözleşmesi korundu.

## Backend Etkisi
- **Yok.** Tüm değişiklik UI katmanında. Servis imzaları, signal
  sözleşmeleri ve `bridge.run_async` çağrı şekli değişmedi.
- Sadece `PortfolioWidget`'ta `apply_sell` çağrısı yeni bir akıştan
  tetikleniyor (zaten mevcut `PositionService.apply_sell` kullanılıyor).

## Bilinen Sınırlamalar
- **QWebEngineView opsiyonel:** `PySide6-Addons` yüklü değilse grafik
  ekranı yalnızca TradingView'a yönlendiren tıklanabilir link gösterir.
  Kurulum:
  ```
  pip install PySide6-Addons
  # veya alternatif:
  pip install PyQtWebEngine
  ```
- TradingView iframe'i internet bağlantısı gerektirir. Offline modda
  grafik boş kalır (TradingView kendi placeholder'ını gösterir).
- Bot Picks filtresinde "Borsa" eşleştirmesi `currency` üzerinden
  heuristik yapılıyor (TRY ↔ BIST, USD ↔ NASDAQ/NYSE) çünkü
  `BotPickView` exchange alanı tutmuyor. Faz 4'te `BotPicksService`
  exchange'i de döndürebilir.
- Watchlist "Sil" işlemi backend tarafında hala "Faz 4'te DB sil"
  notuyla UI-only — bu PR'da değişmedi.

## Manuel Kurulum Gerekli Paketler
```bash
pip install PySide6-Addons    # QWebEngineView için (TradingView embed)
```

## Manuel Test Önerisi
1. `HelpBanner.reset_all()` ile QSettings'i temizle, her ekranı sırayla
   aç → bilgi kartları görünmeli; `×` ile kapatınca bir daha gelmemeli.
2. Settings ekranını küçük pencere boyutunda aç → API anahtarları artık
   scroll ile erişilebilir.
3. Portfolio ekranında bir pozisyona sağ tıkla → "Sat" akışı çalışmalı;
   sonra Budget ve History ekranı otomatik refresh olmalı.
4. Chart ekranı: bir BIST hissesi seç → TradingView gömme açılmalı
   (QWebEngineView varsa). Tema değiştir → grafik yeniden yüklenmeli.
5. Bot Picks: filtre çubuğundan AL + min güven %60 → sadece eşleşen
   satırlar kalmalı.

## Değişen Dosyalar (özet)
- `app/ui/_format.py` — `fmt_qty` eklendi, `_us_to_tr` bug düzeltildi
- `app/ui/components/help_banner.py` — **YENİ**
- `app/ui/components/__init__.py` — HelpBanner export
- `app/ui/chart_widget.py` — TradingView embed (yeniden yazıldı)
- `app/ui/settings_widget.py` — QScrollArea
- `app/ui/trading_widget.py` — QScrollArea + (?) + Şimdi Yenile +
  HelpBanner + 30sn timer + locale
- `app/ui/portfolio_widget.py` — QScrollArea + HelpBanner + Sat akışı +
  Grafik aç + locale + signal'lar
- `app/ui/budget_widget.py` — QScrollArea + HelpBanner + locale
- `app/ui/dashboard.py` — locale
- `app/ui/watchlist_widget.py` — HelpBanner + locale + yeni rozet metni
- `app/ui/bot_picks_widget.py` — HelpBanner + açıklama bandı + filtre
  paneli + locale
- `app/ui/history_widget.py` — locale
- `app/ui/recommendation_widget.py` — locale
- `app/ui/_trade_dialogs.py` — locale
- `app/ui/main_window.py` — PortfolioWidget signal bağlantıları
