# UI Developer — Bug Fixes Batch 2 (2026-05-28)

## Özet
İlk launch sonrası raporlanan 7 UI/UX hatası giderildi. Yeni
`_TradeAdviceDialog` ile "bütçeme göre kaç lot alayım?" sorusu
RiskManager üzerinden cevaplanır hale getirildi. Backend servis imzaları
değişmedi; tüm düzeltmeler UI tarafında.

## Yapılan 7 Düzeltme

### 1. Grafik gösterilemiyor / placeholder mesajı (`chart_widget.py`)
- finplot yokken artık çağrı yapılmıyor, çekilen veri özeti
  (satır sayısı + son kapanış) placeholder olarak gösteriliyor.
- finplot varsa eski akış korundu; çizim hatası olursa kullanıcıya net
  Türkçe mesaj + toast ("Grafik gösterilemedi: …\n
  finplot opsiyoneldir. Geçici çözüm: pip install --upgrade finplot
  pyqtgraph").
- Veri çekim sırasında "X verisi çekiliyor… yfinance üzerinden OHLCV
  indiriliyor." placeholder eklendi — kullanıcı UI'ın donmadığını
  bilsin.
- finplot yokken `_show_placeholder` ile özet + info toast birlikte
  gösterilir (kullanıcı için belirsizlik kalmasın).

### 2. Bekleyen öneri tablosu tam genişlik (`trading_widget.py`)
- `_pending_table` header'ı:
  - Ticker / Aksiyon / Vade / Güven → `ResizeToContents`
  - Hedef + İşlem (Onayla/Reddet butonu) → `Stretch`, son kolon
    `setStretchLastSection(True)`.
- `resizeColumnsToContents()` çağrısı kaldırıldı (Stretch ile
  çakışıyordu); `resizeRowsToContents()` korundu.

### 3. Haberler ekranı yeniden tasarımı (`news_widget.py`)
- `QLineEdit` (ticker arama) **kaldırıldı**.
- Yeni filtre çubuğu:
  - **Borsa**: Hepsi / BIST / NASDAQ / NYSE (`EXCHANGE_FILTERS`)
  - **Hisse**: editable `QComboBox` + `QCompleter` — instruments
    tablosundan dinamik doldurulur, "Hepsi" satırı varsayılan.
  - **Dil**: Hepsi / TR / EN (`LANGUAGE_FILTERS` güncellendi).
  - **Tarih**: Bugün / Son 7 gün / Son 30 gün / Tümü (`DATE_FILTERS`).
- Borsa değişince ticker dropdown'u o borsaya kısılır (relasyonel
  filtre).
- Liste her zaman `published_at DESC` sıralı (en yeni üstte).
- `_fetch_news_from_db` artık `Instrument` ile outer-join — haber
  bağlı instrument'ın ticker + exchange'i `NewsItemView`'a iliştirilir.
- `NewsItemView.exchange: str = ""` alanı eklendi (backward-compat,
  default boş).
- Boş liste mesajı: "Henüz haber yok — RSS scheduler eklenince burada
  güncel haberler listelenecek."
- Filtreleri Temizle butonu Bugün/30 gün varsayılana geri döner.
- Sağdaki detay paneli (mevcut yapı) korundu — değişiklik yok.

### 4. Grafik picker hızlandırması (`_backend_bridge.py` + `chart_widget.py` + `watchlist_widget.py`)
- `BackendBridge.cached_instruments() -> list[(ticker, name, exchange)]`
  eklendi — 60 saniyelik in-memory cache, UI thread'den okunur.
- `BackendBridge.invalidate_instruments_cache()` — yeni instrument
  eklendiğinde çağrılabilir.
- `ChartWidget._on_pick_ticker` ve `WatchlistWidget._on_add_ticker`
  artık DB yerine cache'ten okuyor → dialog açılışı <100ms.

### 5 + 8. Pozisyon büyüklüğü önerisi (`_trade_dialogs.py` — YENİ)
- Yeni dosya `app/ui/_trade_dialogs.py` — `_TradeAdviceDialog` sınıfı.
- UI:
  - Üstte ticker + ad + güncel fiyat.
  - "Cüzdan" combo (6 cüzdan, varsayılan `bot/short`) — her satırda
    nakit bakiyesi de görünür.
  - "Risk profilim" combo: Düşük (1%) / Orta (2%) / Yüksek (3%).
  - "Bot Önerisi" paneli (mavi vurgu) — RiskManager çıktısı:
    - Önerilen adet (lot)
    - Önerilen tutar (₺) ve cüzdan oranı (%)
    - Stop-loss + yüzdesi
    - Take-profit + yüzdesi
    - Kısa Türkçe gerekçe (ATR, cüzdan, risk yüzdesi).
  - Düzenlenebilir `QSpinBox` adet + "Öneriye Dön" butonu.
  - "Onayla ve Uygula" → `PositionService.apply_buy(transaction_at=now,
    followed_bot=True)` async.
  - "Sadece Kaydet (Bot önerisi)" → `BotPicksService.add_pick(...)` —
    imza varyasyonlarına karşı try/except.
- ATR hesabı: `app/db/models.py::TechnicalSignal` tablosunda atr
  kolonu yok → `_compute_atr_from_history()` ile son 30 günden basit
  True-Range ortalaması hesaplanır (Wilder approximation yerine basit
  mean; UI uyarı amaçlı yeterli).
- Backend bridge yoksa diyalog devre dışı (uyarı toast'u).
- BUY uygulandığında `position_changed` sinyali yayılır.

#### Watchlist + Bot Picks sağ tık entegrasyonu
- `WatchlistWidget._on_context_menu` → "İşlem öner ve aç" QAction
  (`fa5s.robot` ikonlu) eklendi; `_open_trade_advice(item)` çağırır.
- `BotPicksWidget`: `_TimeframeTabContent` tablosuna
  `customContextMenuRequested` bağlandı; sağ tıkta "İşlem öner ve aç"
  menüsü → `_open_trade_advice(pick)` (parent zincirinden bridge'i
  bulur, instrument_id'yi ticker'dan çözer).

### 6. mark_applied sonrası crash + commission/transaction_at (`trading_widget.py`)
- `PositionService.apply_buy/sell` zaten `transaction_at` None
  varsayımı destekliyor; UI tarafında ek olarak `notes` parametresine
  iso timestamp eklendi (audit log).
- mark_applied çağrısı try/except içinde **bridge.run_async** ile
  zaten korumalı — hatayı yutmak yerine toast (`level="error",
  duration_ms=6000`) gösteriyor.
- Logger uyarısı (`logger.warning`) eklendi.

### 7. mark_applied sonrası aktif ekranlar refresh (`main_window.py`)
- `TradingWidget.position_changed = Signal()` eklendi; başarılı
  mark_applied sonrası emit edilir.
- `WatchlistWidget.position_changed = Signal()` eklendi (trade advice
  + add_to_wallet sonrası kullanılır).
- `BotPicksWidget.position_changed = Signal()` eklendi (sağ tık trade
  advice sonrası).
- `MainWindow._build_screen`'de TradingWidget, WatchlistWidget,
  BotPicksWidget oluşturulurken `position_changed` →
  `_on_position_changed` bağlandı.
- `MainWindow._on_position_changed` Portfolio / Budget / History /
  Dashboard widget'larının `refresh()` veya `refresh_data()`
  metodunu sırayla çağırır (defansif try/except).

## Değişen Dosyalar
- `app/ui/_backend_bridge.py` — `cached_instruments`, cache TTL field'ları
- `app/ui/chart_widget.py` — placeholder + cache kullanımı + finplot
  fallback mesajları
- `app/ui/news_widget.py` — filtre çubuğu yeniden tasarım, Instrument
  join, EXCHANGE_FILTERS + DATE_FILTERS
- `app/ui/trading_widget.py` — pending tablo stretch, mark_applied
  notes + position_changed sinyali
- `app/ui/watchlist_widget.py` — cached_instruments kullanımı,
  TradeAdvice menü girdisi, position_changed sinyali
- `app/ui/bot_picks_widget.py` — sağ tık menüsü, TradeAdvice entegrasyonu,
  position_changed sinyali
- `app/ui/main_window.py` — `_on_position_changed` handler, sinyal
  bağlantıları
- `app/ui/_trade_dialogs.py` — **yeni dosya**, `_TradeAdviceDialog` +
  ATR helper'ı

## Yeni API'ler
- `BackendBridge.cached_instruments(force_refresh=False)`
- `BackendBridge.invalidate_instruments_cache()`
- `TradingWidget.position_changed = Signal()`
- `WatchlistWidget.position_changed = Signal()`
- `BotPicksWidget.position_changed = Signal()`
- `_TradeAdviceDialog(ticker, name, current_price, instrument_id, bridge, parent)`

## Yeni Sabitler
- `news_widget.EXCHANGE_FILTERS` — borsa filtresi dropdown'u
- `news_widget.DATE_FILTERS` — tarih aralığı dropdown'u
- `_trade_dialogs.RISK_PROFILES` — Düşük/Orta/Yüksek risk seçenekleri

## Bilinen Sınırlamalar
- ATR hesabı `technical_signals` tablosundaki kolondan değil
  `price_history`'den runtime hesaplanır. Yeterli geçmiş veri yoksa
  ATR=0 → RiskManager fallback (cap/2). analysis-engine ileride
  `technical_signals.atr_14` kolonu eklerse hesaplama o kolondan
  çekilebilir.
- `BotPicksService.add_pick` imzası varyasyonları için diyalog
  try/except ile iki form deniyor (kwargs ve positional). Portfolio
  agent imzayı sabitlerse `_TradeAdviceDialog._on_save_pick_clicked`
  basitleştirilebilir.
- `cached_instruments` thread-safe değil — sadece UI thread'inden
  okunur. Background thread'den çağrılması beklenmiyor.
- `_TradeAdviceDialog` cüzdan değerlemesini yalnızca `cash_balance`
  üzerinden yapıyor; pozisyon değerlemesi dahil tam portföy
  değerlemesi `WalletService.compute_snapshot` ile mümkün ama o
  async ve fiyat haritası gerektirir — dialog hızlı tepki versin
  diye sadeleştirildi.
- BUY sonrası diyalog kapanır; sağ tıkla başka bir hisseye aynı anda
  öneri açıldıysa o diyaloğa otomatik refresh yok (her diyalog kendi
  ömrü içinde bağımsız).

## Ekran Görüntüsü Notları
Manuel test için aşağıdaki akışlar denenmeli:
1. Haberler ekranı → 4 dropdown filtre kombinasyonu.
2. Grafik → "Hisse Seç" hızlı açılışı (1 saniye altında olmalı).
3. Watchlist sağ tık → "İşlem öner ve aç" → BUY uygula → Portfolio
   ekranına geç (otomatik tazelenmiş olmalı).
4. Bot Picks satırına sağ tık → trade advice → "Sadece Kaydet".
5. Trading & Otomasyon → bekleyen öneri seç → Uyguladım → Portfolio
   ve History ekranlarına geç (refresh otomatik).
