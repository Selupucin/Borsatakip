---
date: 2026-05-27
agent: ui-developer
phase: faz-2
type: feature
related_files:
  - app/ui/news_widget.py
  - app/ui/watchlist_widget.py
  - app/ui/bot_picks_widget.py
  - app/ui/recommendation_widget.py
  - app/ui/main_window.py
  - app/ui/dashboard.py
related_doc_sections:
  - "5.3 Öneri Çıktısı"
  - "5.6 Şeffaflık ve Açıklanabilirlik"
  - "6.2 Kullanıcı Takip Listesi (Watchlist)"
  - "6.3 Bot Öneri Listesi (Bot Picks)"
  - "8.1 Tasarım İlkeleri"
  - "8.3 Ekran Yapısı"
  - "8.4 Modern UI Bileşenleri"
  - "11. Faz 2 — Haber & Sentiment + Scraping + Listeler"
---

## Özet

Faz 2 kapsamında dört yeni UI ekranı yazıldı: `news_widget.py`,
`watchlist_widget.py`, `bot_picks_widget.py`, `recommendation_widget.py`.
`main_window.py`'nin sidebar'ında daha önce "Yakında" placeholder gösteren
Takip Listesi / Bot Önerileri / Haberler slot'ları aktif edildi ve gerçek
widget'larla bağlandı. `dashboard.py`'nin "Son Haberler" placeholder
listesi sentiment renkli ilk 5 haberle dolduruldu ve yeni "Bot Öne
Çıkanlar" kartı en yüksek güvenli 3 kısa vade pick'i mini kart olarak
gösteriyor. Backend asenkron servisleri (`WatchlistService`,
`BotPicksService`, `RSSSource.fetch_news`) henüz aktif olmadığı için tüm
ekranlar **placeholder veri** ile çalışıyor; Faz 3'te gerçek bağlama
yapılacak.

## Detaylar

### 1. `app/ui/news_widget.py` (YENİ)

- **Filtre çubuğu** (üst satır):
  - `QComboBox` — Dil: Tüm Diller / Türkçe / İngilizce (`rss_source` feed
    dil etiketlerine eşlenir).
  - `QComboBox` — Kaynak: Reuters / Bloomberg TR / Mynet / Dünya / KAP
    (anahtarlar `rss_source.DEFAULT_FEEDS` ile birebir).
  - `QLineEdit` — hisse arama (ticker; haber `tickers` tuple'ı +
    başlıkta substring eşlemesi).
  - "Filtreleri Temizle" butonu.
- **Ana içerik:** `QSplitter` ile ikiye bölünmüş.
  - **Sol** `QListWidget` — her satır özel `_NewsRowWidget`:
    - Üst satır: başlık (bold, wrap) + tarih (sağ üst, ince yazı,
      "az önce / N dk önce / N sa önce / N gün önce" göreceli format).
    - Alt satır: kaynak rozeti + dil rozeti + sentiment göstergesi
      (içi dolu renkli daire + "Pozitif/Nötr/Negatif (+0.82)").
  - **Sağ** `QFrame` detay paneli — seçili haberin başlığı, kaynak/dil/tarih
    metası, sentiment etiketi (büyük renkli daire), özet metni
    (`QTextBrowser`), "Tarayıcıda Aç" butonu (`QDesktopServices.openUrl`).
- **Davranışlar:**
  - Tek tık → sağ panelde detay.
  - Çift tık → URL tarayıcıda açılır.
- **Sentiment renk paleti** (`SENTIMENT_PALETTE`):
  - `positive` → `#2E7D32` (yeşil)
  - `neutral`  → `#9E9E9E` (gri)
  - `negative` → `#C62828` (kırmızı)
- **Public API:** `load_items(list[NewsItemView])` — Faz 3'te `RSSSource`
  + `SentimentAnalyzer` worker'ı bu metodu çağıracak. `NewsItemView`
  dataclass'ı UI view-model (rss_source.NewsItem + sentiment ekstrası).
- **Placeholder veri:** `_sample_news()` 8 örnek haber (KAP, Reuters,
  Bloomberg TR, Mynet, Dünya — pozitif / nötr / negatif sentimentlerin
  hepsini kapsar; TR + EN karışık).

### 2. `app/ui/watchlist_widget.py` (YENİ)

- **Üst kontrol barı:**
  - `QComboBox` aktif liste seçici ("Uzun Vade", "Temettü", "Gözlem"
    placeholder listeleri).
  - "Yeni Liste" — `QInputDialog` ile ad alır, yeni `_WatchlistDef`
    ekler.
  - "Yeniden Adlandır" — `QInputDialog`.
  - "Sil" — `QMessageBox.question` onay.
  - "Hisse Ekle" — özel `_AddTickerDialog` (ticker + borsa seçimi). Faz
    3'te `instruments` tablosundan arama eklenecek.
- **Ana tablo** — `QTableWidget`, 8 kolon:
  | Ticker | Ad | Borsa | Fiyat | Günlük % | Hacim | Bot Sinyali | Sparkline |
  - Renk kodu: pozitif % yeşil (`#2E7D32`), negatif kırmızı (`#C62828`),
    sıfıra yakın gri.
  - **Bot Sinyali** kolonu özel widget: BUY/HOLD/SELL renkli rozet
    (`SIGNAL_PALETTE`) + güven yüzdesi (`%72`).
  - **Sparkline** kolonu Faz 2'de Unicode mini glyph (`▁▂▃▅▆▇`); Faz 3'te
    pyqtgraph mini grafik ile değişecek (tooltip uyarısı yazıldı).
  - Hacim auto-format: B/M/K kısaltmaları.
- **Sürükle-bırak sıralama:** `QAbstractItemView.DragDropMode.InternalMove`
  + `Qt.DropAction.MoveAction` (Doküman §6.2 gereği).
- **Sağ tık menüsü:** "Sil" (aktif), "Grafik aç (Faz 3)" pasif,
  "Cüzdana ekle (Faz 3)" pasif — Faz 3'te `add_to_wallet_requested(str)`
  ve `open_chart_requested(str)` sinyalleri trading/chart widget'lara
  bağlanacak.
- **Placeholder veri:** `_sample_watchlists()` 3 isimli liste, 11 ticker
  toplam (BIST: THYAO/ASELS/AKBNK/GARAN/TUPRS/PETKM; ABD:
  AAPL/MSFT/KO/TSLA/NVDA — karışık).

### 3. `app/ui/bot_picks_widget.py` (YENİ)

- **Üstte global filtre:** `QCheckBox` "Sadece açık olanlar" — tüm
  sekmelerdeki tablolara uygulanır (ama istatistik kartı kapanlara da
  bakar — değişmez).
- **`QTabWidget` vade sekmeleri** (`TIMEFRAME_TABS`):
  - "Kısa Vade" (`short`)
  - "Orta Vade" (`mid`)
  - "Uzun Vade" (`long`)
  - "Tümü" (`all`)
  - Sekme ikonları: bolt / calendar-alt / mountain / layer-group
    (qtawesome).
- **Her sekmenin içeriği** (`_TimeframeTabContent`): `QSplitter` ile
  solda tablo, sağda istatistik paneli.
- **Tablo kolonları:**
  | Ticker | Aksiyon | Güven | Öneri Tarihi | Öneri Fiyatı | Hedef | Şimdiki | Getiri % | Durum |
  - **Aksiyon** rozeti (`ACTION_PALETTE`'den ortak renkler) — `AL`/`BEKLE`/`SAT`.
  - **Durum** rozeti (`OUTCOME_PALETTE`):
    - `open` → mavi `#1976D2` "Açık"
    - `hit_target` → yeşil `#2E7D32` "Hedefe Ulaştı"
    - `stopped` → kırmızı `#C62828` "Stop"
    - `expired` → gri `#757575` "Süresi Doldu"
  - **Getiri %** yeşil/kırmızı renkli.
  - Sıralama aktif (`setSortingEnabled(True)`).
- **İstatistik kartı** (`_build_stats_panel`):
  - Toplam Pick, Açık, Kapanan, Başarı Oranı %, Ortalama Getiri %,
    Ortalama Tutma (gün).
  - Başarı oranı renk: ≥60 yeşil, 40-60 gri, <40 kırmızı.
  - `BotPicksStats` dataclass'ı UI'a hazır — Faz 3'te
    `BotPicksService.stats(timeframe)` async çıktısı ile değişecek.
  - `_compute_stats()` placeholder veri için yerel hesaplar; kapananların
    tutma günü `picked_at - now` (Faz 3'te `closed_at` kolonu gelince
    doğru hesap).
- **Detay modal:** Tablo satırına çift tıklayınca `RecommendationDialog`
  açılır (içinde `RecommendationWidget` + Bot Pick'ten türetilmiş
  `RecommendationView`). Pick özet metni, contributions ve risk skoru
  modal'da gösterilir.
- **Placeholder veri:** `_sample_picks()` 11 örnek pick (5 kısa, 3 orta,
  3 uzun vade — açık/kapanmış karışık, AL/BEKLE/SAT karışık, başarılı
  ve başarısız örnekler dahil).

### 4. `app/ui/recommendation_widget.py` (YENİ — Faz 2 erken versiyon)

`app.analysis.recommender.RecommendationOutput`'a UI tarafından beslenen
açıklanabilirlik kartı:

- **Üst:** BÜYÜK aksiyon rozeti (`AL`/`BEKLE`/`SAT` — 24pt font, 12px
  border-radius, 12x28 padding) + "Güven %78" etiketi.
- **Başlık satırı:** Ticker (büyük bold) + vade rozeti (Kısa Vade / Orta
  Vade / Uzun Vade).
- **Fiyat paneli** (`QFrame` styled panel): Güncel Fiyat (büyük bold) +
  Hedef Fiyat (yeşil bold; `None` ise gri "—").
- **Açıklanabilirlik** (`_ContributionBar` × 3):
  - Etiket (90px min): "Teknik" / "Sentiment" / "Mutabakat" (Türkçe).
  - `QProgressBar` (16px sabit yükseklik, 8px border-radius, renkli chunk):
    - Teknik → mavi `#1976D2`
    - Sentiment → mor `#7B1FA2`
    - Mutabakat → cyan `#00838F`
  - Yüzde etiketi sağda (`%46.8`).
  - Bar maksimum referansı 100 (composite max = `sum(weight*100) = 100`).
  - Doküman §5.6 (şeffaflık ve açıklanabilirlik) gereği her sinyal
    grubunun ağırlıklı katkısı görsel olarak temsil edilir.
- **Türkçe özet metin:** `QTextBrowser` (max 140px yükseklik) — doğrudan
  `RecommendationOutput.summary` (örn. "Kısa vade AL önerisi (güven %78).
  Teknik analiz pozitif…").
- **Alt:**
  - Risk rozeti (`RISK_PALETTE`): low yeşil `#388E3C`, medium turuncu
    `#F57C00`, high kırmızı `#D32F2F` — "Risk: Düşük (22/100)".
  - "Bot Pick'lere Ekle" butonu (qta `fa5s.robot`) — `setEnabled(False)`
    + tooltip "Faz 3'te aktif" + `add_to_picks_requested(str)` sinyali
    (Faz 3 için hazır).
- **Public API:**
  - `RecommendationWidget(view: RecommendationView | None, parent=None)`
  - `set_view(view: RecommendationView)` — re-render.
  - `RecommendationDialog(view, parent)` — modal sarmalayıcı,
    `BotPicksWidget`'tan çağrılıyor.
- **View model:** `RecommendationView` dataclass — `RecommendationOutput`
  alanlarının UI yansıması (ticker + current_price + currency ek alanları
  ile). Faz 3'te adaptör fonksiyon `RecommendationOutput → RecommendationView`
  yazılacak.

### 5. `app/ui/main_window.py` (GÜNCELLENDİ)

- `NAV_ITEMS`: `watchlist`, `bot_picks`, `news` artık `enabled=True`.
- `_build_screen` switch'i yeni 3 ekran için `WatchlistWidget`,
  `BotPicksWidget`, `NewsWidget` döndürüyor.
- `_install_shortcuts`'a Faz 2 hızlı erişim kısayolları eklendi:
  - `Ctrl+N` → Haberler
  - `Ctrl+W` → Takip Listesi
  - `Ctrl+P` → Bot Önerileri (`Ctrl+B` sidebar toggle ile çakışmasın
    diye P; "Picks" baş harfi olarak da hatırlanabilir).
- `_goto_screen(key: str)` yardımcı metodu eklendi — verilen nav key'ine
  göre sidebar'da o satırı seçer; bilinmeyen key debug log'a düşer.

### 6. `app/ui/dashboard.py` (GÜNCELLENDİ)

- Faz 1'deki "Son Haberler" placeholder `QListWidgetItem("Henüz haber
  yok…")` artık `_sample_news()[:5]` ile dolu:
  - Her satırda sentiment renkli daire ikonu (12px).
  - Tooltip: "kaynak • etiket (skor)".
- **YENİ alt blok:** "Bot Öne Çıkanlar" — `_sample_picks()`'ten kısa
  vade pick'ler güvene göre sıralanır, top 3 mini kart:
  - Ticker (bold) + aksiyon rozeti + güven % + güncel fiyat.
  - Solda haberler (stretch=3), sağda öneriler (stretch=2) yan yana.
- Eski `News + Markets + Metrics` dikey akışı korundu; haberler ve
  pick'ler en alt satırda yan yana QHBoxLayout ile yerleşiyor.

## Klavye Kısayolları (Faz 2 eklemeleri)

| Kısayol | İşlev |
|---|---|
| `Ctrl+N` | Haberler ekranına git |
| `Ctrl+W` | Takip Listesi ekranına git |
| `Ctrl+P` | Bot Önerileri ekranına git (`Ctrl+B` çakışmaması için P) |

Mevcut Faz 1 kısayolları (`Ctrl+1..9`, `Ctrl+B`, `Ctrl+T`, `Ctrl+,`,
`Ctrl+Q`) korundu.

## Placeholder Veri Stratejisi

Faz 2'de hiçbir widget gerçek backend çağrısı yapmaz; tüm görsel
prototipleme sahte veriyle çalışır. Bu sayede:

1. **UI bileşenleri test-engineer tarafından bağımsız** sınanabilir
   (`pytest-qt` ile veri yükleme + filtre + click davranışları).
2. **Backend hazır olmadan UX iterasyonu** yapılabilir (renkler,
   yerleşim, klavye kısayolları).
3. **Faz 3 entegrasyon** sırasında widget API'leri stabil kalır —
   yalnızca placeholder fonksiyonların gerçek async servislere swap
   edilmesi gerekir.

Placeholder kaynaklar:
- `news_widget._sample_news()` — 8 haber.
- `watchlist_widget._sample_watchlists()` — 3 liste × 3-5 ticker.
- `bot_picks_widget._sample_picks()` — 11 pick (5 kısa, 3 orta, 3 uzun).
- `dashboard.py` `_populate_news()` ve `_populate_bot_highlights()` aynı
  iki kaynaktan veri çekiyor — Faz 3'te bu çift kaynak ortak servise
  bağlandığında tutarlılık otomatik sağlanacak.

## Renk Kodlaması (tutarlılık)

| Anlam | Hex | Kullanım |
|---|---|---|
| Pozitif / Al / Kazanç | `#2E7D32` | Sentiment positive, BUY badge, pozitif % |
| Negatif / Sat / Kayıp | `#C62828` | Sentiment negative, SELL badge, negatif % |
| Nötr / Bekle | `#9E9E9E` | Sentiment neutral, HOLD badge, ≈0 % |
| Bilgi / Açık | `#1976D2` | Bot pick `open` rozeti, contribution bar (teknik) |
| Uyarı / Süresi dolmuş | `#757575` | `expired` outcome |
| Mor (sentiment katkı) | `#7B1FA2` | RecommendationWidget contribution bar |
| Cyan (mutabakat katkı) | `#00838F` | RecommendationWidget contribution bar |
| Risk: Düşük | `#388E3C` | RISK_PALETTE.low |
| Risk: Orta | `#F57C00` | RISK_PALETTE.medium |
| Risk: Yüksek | `#D32F2F` | RISK_PALETTE.high |

## Gerekçe

Doküman §11 Faz 2 maddeleri:
- "Kullanıcı takip listeleri (watchlist) — oluştur, düzenle, sırala" ✓
- "Bot öneri listesi (bot picks) ve öneri takip mekanizması" (UI tarafı) ✓
- "UI'ya haber paneli, sentiment göstergesi, watchlist ve bot picks
  ekranları" ✓
- "Öneri motorunun ilk versiyonu (kısa vade ağırlıklı)" — UI eşliği
  (RecommendationWidget) ✓

Doküman §5.6 (Şeffaflık ve Açıklanabilirlik) gereği `RecommendationWidget`
her sinyal grubunun (teknik / sentiment / mutabakat) ağırlıklı katkısını
çubuk grafik olarak gösterir; agent kuralları (`ui-developer.md` →
"Açıklanabilirlik UI'sı") bu davranışı zorunlu kılıyor.

`Ctrl+P` seçimi: dokümanda Ctrl+B sidebar toggle olarak Faz 1'de
sabitlenmiş — Bot Picks için en uygun anlamlı harf "P" (Picks
kısaltması).

## Test / Doğrulama

- Sözdizimi seviyesinde yazıldı; runtime testi yapılmadı (test-engineer
  ayrı çağrı ile çalıştırılacak).
- pytest-qt ile önerilen manuel kontrol listesi:
  - `Ctrl+N`, `Ctrl+W`, `Ctrl+P` kısayolları doğru ekranı açmalı.
  - Sidebar'da Watchlist / Bot Önerileri / Haberler artık tıklanabilir
    olmalı (`enabled=True`).
  - `NewsWidget`: filtreler doğru hisseleri/dilleri/kaynakları
    filtrelemeli; çift tık tarayıcı açmalı (sandbox'ta test edilemez,
    görsel doğrulama).
  - `WatchlistWidget`: liste oluştur/sil/yeniden adlandır akışı,
    "Hisse Ekle" diyaloğu, sürükle-bırak satır taşıma.
  - `BotPicksWidget`: sekmeler doğru veriyi göstermeli; "Sadece açık
    olanlar" filtresi tablo değiştirmeli ama istatistik panelini değil
    (başarı oranı kapananlara bakar); çift tık modal açmalı.
  - `RecommendationWidget`: aksiyon rozeti rengi BUY=yeşil, contribution
    bar yüzdeleri toplam confidence ile tutarlı, hedef fiyat yoksa "—".
  - `Dashboard`: alt satırda 5 haber + 3 mini pick kartı görünmeli.

## Notlar

### Faz 3 için TODO

1. **Gerçek backend bağlantısı:**
   - `RSSSource.fetch_news()` → `QThreadPool` worker → `SentimentAnalyzer.analyze()`
     → `NewsWidget.load_items()` (Signal/Slot ile). Periyodik refresh
     (60s) timer eklenecek.
   - `WatchlistService.list_items()` async → `WatchlistWidget` tabloya
     basacak; `DataCollector.fetch_quote()` ile real-time fiyat
     güncellemesi.
   - `BotPicksService.list_picks(timeframe)` + `stats(timeframe)` →
     `BotPicksWidget`. `BotPicksStats` dataclass parametreleri zaten
     hazır.
   - `ShortTermRecommender.recommend()` çıktısı → adaptör fonksiyon →
     `RecommendationView` → `RecommendationWidget.set_view()`.

2. **Reusable component'lar** (henüz yok — Faz 3):
   - `app/ui/components/toast.py` — sağ üst köşede beliren bildirim
     balonu (AlertsWidget'taki `QMessageBox` placeholder + Faz 3'te
     RSS hata bildirimleri).
   - `app/ui/components/sparkline.py` — pyqtgraph mini trend grafiği
     (Watchlist tablosundaki Unicode glyph yerine).
   - `app/ui/components/skeleton.py` — yükleme iskeleti animasyonu (RSS
     fetch sırasında, Watchlist real-time refresh sırasında).
   - `app/ui/components/metric_card.py` — sparkline + delta ikon entegre
     metrik kartı (Dashboard'daki minimal `_build_metric_card` yerine).

3. **Portföy, bütçe, trading, history widget'ları** — Faz 3 (bu
   görevde değil).

4. **Auto-complete:** WatchlistWidget'taki `_AddTickerDialog` Faz 3'te
   `instruments` tablosundan QCompleter ile ticker arama yapacak.

5. **`closed_at` doğru tutma günü:** `BotPicksWidget._compute_stats` şu
   an `(now - picked_at).days` ile yaklaşık hesap; Faz 3'te `bot_picks`
   tablosundan `closed_at` geldiğinde doğru hesaplanacak.

### Backend API ihtiyaçları (Faz 3'te ilgili agent'lar bağlayacak)

- **data-collector:** `RSSSource.fetch_news(since)` → `list[NewsItem]`
  + `SentimentAnalyzer` çıktısı `NewsItemView` adaptör fonksiyonu.
- **portfolio-manager:**
  - `WatchlistService` async API: `list_watchlists()`,
    `create_watchlist(name)`, `rename_watchlist(id, name)`,
    `delete_watchlist(id)`, `list_items(watchlist_id)`,
    `add_item(watchlist_id, ticker)`, `remove_item(item_id)`,
    `reorder(watchlist_id, item_ids)`.
  - `BotPicksService` async API: `list_picks(timeframe=None,
    open_only=False)`, `stats(timeframe)` → `BotPicksStats`,
    `add_from_recommendation(ticker, recommendation_id)`.
- **analysis-engine:** `ShortTermRecommender.recommend(inputs)` zaten
  hazır; adaptör fonksiyon (`output → RecommendationView`) UI
  developer Faz 3'te yazacak.

### Bilinen sınırlamalar

- Placeholder veri tarihleri `datetime.now() - timedelta(...)` ile
  hesaplanır; testler `freezegun` veya monkeypatch ile sabit tarih
  kullanmalı.
- `WatchlistWidget`'ın sürükle-bırak sıralaması yalnızca **görsel**
  düzeyde çalışır (`InternalMove`); kalıcı sıra Faz 3'te
  `WatchlistService.reorder()` çağrısı ile DB'ye yazılacak.
- `BotPicksWidget` istatistik kartı sekme bazlı **toplam** üzerinden
  hesaplar; "sadece açık olanlar" filtresi yalnızca tabloyu etkiler.
- `RecommendationDialog` modal — büyük öneri kartlarının olduğu modal
  küçük ekranda taşabilir; `setMinimumSize(520, 480)` ile makul minimum
  belirlendi.
- Hiçbir Faz 2 widget'ı henüz gerçek async iş yapmıyor → `QThreadPool` /
  `QtAsyncio` örüntüsü yorum satırlarında belirtildi, Faz 3'te
  uygulanacak.

### Bağımlılık değişikliği

YOK — `pyproject.toml`'a yeni paket eklenmedi. Tüm widget'lar mevcut
PySide6 + qtawesome (opsiyonel) stack'ini kullanır. `RecommendationOutput`
import edilmiyor (UI tarafı `RecommendationView` adaptör dataclass'ı
kullanıyor) — backend ↔ UI bağımlılığı tek yönlü tutuldu.
