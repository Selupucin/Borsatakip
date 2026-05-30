# UI Developer — Bug Fixes Batch (2026-05-28)

## Özet
Watchlist, Chart, Portfolio, History ve Dashboard ekranlarındaki 6 görünüm /
etkileşim hatası giderildi. Faz 4 (broker entegrasyonu) korunarak yalnızca UI
ve UI yardımcı sorguları (raw SQL) değiştirildi. Backend servis imzaları
değişmedi.

## Yapılan 6 Düzeltme

### 1. Grafik bölümünde "Hisse Seç" diyaloğu (`app/ui/chart_widget.py`)
- `QLineEdit` ile manuel ticker girişi kaldırıldı.
- "Hisse Seç" butonu (`qta.icon('fa5s.search')`) eklendi; tıklayınca
  `_AddTickerDialog` reuse edilerek liste-arama diyaloğu açılır.
- Seçilen ticker yan tarafta etiket olarak görünür (`[exchange]` ile).
- `load_ticker(ticker: str)` artık yfinance üzerinden async OHLCV çekiyor
  (`bridge.run_async`); UI thread bloklanmıyor.
- BIST sembolleri için `.IS` suffix'i otomatik eklenir.
- finplot yüklü değilse "X satır veri çekildi" placeholder mesajı gösterilir.
- Hata durumunda toast (`ToastManager`) + placeholder ile bilgi verilir.
- `ChartWidget(__init__, bridge=...)` parametresi eklendi; `main_window.py`
  bridge'i artık iletiyor.

### 2. Portfolio + History tablo kolonları responsive (`portfolio_widget.py`, `history_widget.py`)
- `QHeaderView` resize moduna geçildi:
  - Metin/rozet kolonları (`Ticker`, `Cüzdan`, `Adet`, `Ağırlık %`, `Tarih`,
    `Tip`, `Bot Uyumlu`) `ResizeToContents`.
  - Sayısal kolonlar (`Maliyet`, `Güncel`, `K/Z TL`, `K/Z %`, `Fiyat`,
    `Toplam`, `Komisyon`, vb.) `Stretch`.
- `resizeColumnsToContents()` çağrıları kaldırıldı (Stretch ile çakışıyordu).
- Pencere boyutu değişince kolonlar düzgün uyum sağlıyor.

### 5. Sağ tık "Grafik aç" çalışıyor (`watchlist_widget.py` + `main_window.py`)
- Yeni Qt sinyal: `chart_requested = Signal(str)` (ticker yayınlar).
  - Eski `open_chart_requested` sinyali geriye uyumluluk için korundu —
    yeni `_emit_chart_request` her iki sinyali de yayar.
- Menü etiketinden "(Faz 3)" kaldırıldı; action aktif edildi.
- `main_window.py`:
  - `WatchlistWidget.chart_requested → MainWindow._goto_chart_for(ticker)`.
  - `_goto_chart_for`: grafik ekranına geçer ve `ChartWidget.load_ticker`
    çağırır.
- `BotPicksWidget`'ta sağ tık menüsü yok (sadece çift tık → detay modal),
  o yüzden değişiklik yapılmadı.

### 6. Sağ tık "Cüzdana ekle" implement edildi (`watchlist_widget.py`)
- Yeni diyalog: `_AddToWalletDialog` (modul-level).
  - Hisse: kilitli label (seçili ticker).
  - Cüzdan: QComboBox — 6 satır (Bot/Ben × Kısa/Orta/Uzun) `WalletService`
    snapshot'ından sync raw query ile çekilir.
  - İşlem: BUY/SELL.
  - Adet: `QDoubleSpinBox` (decimals=4, min=0.0001, max=1_000_000).
  - Fiyat: default = son piyasa fiyatı.
  - Komisyon: otomatik = `gross * (settings.default_commission_pct / 100)`;
    kullanıcı elle değiştirirse otomatik hesap devre dışı kalır.
  - Bot uyumlu checkbox → `followed_bot` flag'ine map'lenir.
- Onay sonrası `bridge.run_async(positions.apply_buy | apply_sell, …)`.
- Başarı/hata toast'ı (`ToastManager.show(level=success|error)`).
- Menü etiketinden "(Faz 3)" kaldırıldı.

### 7. Bot sinyali vade etiketi — 3 ayrı kolon (`watchlist_widget.py`)
- Tek "Bot Sinyali" kolonu yerine artık 3 kolon: **Kısa**, **Orta**, **Uzun**.
- Yeni kompakt rozet (`_make_mini_signal_widget`):
  - BUY → yeşil `▲` + %güven
  - SELL → kırmızı `▼` + %güven
  - HOLD → gri `●` + %güven
  - Veri yoksa "—"
- `WatchlistItemView` dataclass'a vade alanları eklendi:
  `bot_action_short / mid / long` + `bot_confidence_short / mid / long`,
  ayrıca `instrument_id`.
- Backend hizmeti **değişmedi**. Bunun yerine UI tarafında ek async sorgu:
  - `_fetch_timeframe_signals` — `recommendations` tablosundan
    `(instrument_id, timeframe)` bazında en yeni satırı çeker (tek IN sorgu,
    N+1 yok).
  - Sonuç `_apply_timeframe_signals` ile sadece aktif liste için yansıtılır
    (liste değişti ise atılır).
- Mevcut `WatchlistService.list_items(with_quotes=True)` çıktısındaki tek
  `bot_action` alanı geriye uyumluluk için "kısa" kolonunda fallback olarak
  gösterilir.

### 8. Dashboard "Piyasa Özeti" backend bağlandı (`dashboard.py`)
- 3 satır: BIST 100 (`XU100`), S&P 500 (`^GSPC`), USD/TRY (`USDTRY`).
- Yeni async helper `_fetch_market_summary(session_factory)`:
  - Endeksler için `instruments` + `price_history` (son 2 satır).
  - USD/TRY için `fx_rates` (son 2 satır).
  - Değişim ve değişim % önceki kayda göre hesaplanır.
  - `verified_close` varsa `close`'a tercih edilir.
- Renk: değişim ≥ 0 → yeşil (`#2E7D32`), < 0 → kırmızı (`#C62828`).
- Yenileme: `refresh_data` (showEvent ile her tab geçişinde) + 30 sn
  `QTimer` ile periyodik.
- Veri yoksa "—" gösterilir (toast spam etmez).

## Yeni / Değişen Dosyalar

| Dosya | Değişiklik |
|---|---|
| `app/ui/chart_widget.py` | Tamamen yeniden yazıldı — sembol seçimi + async OHLCV fetch. |
| `app/ui/watchlist_widget.py` | `_AddToWalletDialog`, `_WalletChoice`, `_make_mini_signal_widget`, `chart_requested` sinyali, 3 vade kolonu, vade sinyali fetcher, sağ tık aksiyonları. |
| `app/ui/dashboard.py` | Piyasa özeti refresh + `_fetch_market_summary` yardımcısı + 30 sn `QTimer`. |
| `app/ui/portfolio_widget.py` | Açık pozisyonlar tablosu kolon resize modu (Stretch + ResizeToContents). |
| `app/ui/history_widget.py` | İşlem geçmişi tablosu kolon resize modu. |
| `app/ui/main_window.py` | `ChartWidget`'a `bridge` iletildi; `WatchlistWidget.chart_requested` → `_goto_chart_for(ticker)`. |

## Yeni Sinyal / Diyalog
- **Sinyal:** `WatchlistWidget.chart_requested = Signal(str)` — grafik ekran
  isteği. `MainWindow._goto_chart_for` slot'u dinler.
- **Diyalog:** `_AddToWalletDialog` (modul-level, reuse edilebilir; gelecekte
  bot_picks_widget'tan da çağrılabilir).

## Backend Etkileşim Noktaları (yalnızca okuma + servis çağrısı)
| Etkileşim | Modül / Servis |
|---|---|
| Hisse listesi (chart + watchlist) | `Instrument` (raw select) |
| Cüzdan listesi (Cüzdana Ekle dialog) | `Wallet` (raw select) |
| Ticker → instrument_id | `Instrument` (raw select) |
| BUY/SELL uygulama | `PositionService.apply_buy / apply_sell` |
| Vade bazlı bot sinyalleri | `Recommendation` (raw select, IN + max(generated_at)) |
| Piyasa özeti | `Instrument`, `PriceHistory`, `FxRate` (raw select) |

Backend servis imzaları **değişmedi**. UI'da raw SQLAlchemy 2.0 `select`
çağrıları yapıldı; bu standartlar `_backend_bridge.session_factory` üzerinden
geçer.

## Bilinen Sınırlamalar
- **finplot grafik render** — `fplt.create_plot()` `QWidget` host'a doğrudan
  attach etmek için finplot'un host policy'sine bağlıdır. Bazı finplot
  versiyonlarında widget ayrı pencere açabilir (tek başına host'a embed
  garantisi yok). Faz 4'te finplot host integration revize edilmeli.
- **yfinance** sembol uyumsuzluğu: BIST için `.IS` ekleniyor; ancak özel
  semboller (örn. tahvil) için ek adaptasyon gerekebilir.
- **WatchlistService.list_items**, hâlâ vadeden bağımsız "en güncel"
  recommendation dönüyor. UI bunu "kısa" fallback olarak kullanıyor; ileride
  servis API'sini genişletmek temizlik için tercih edilir
  (`bot_action_short/mid/long` field'ları `WatchlistItemView` dataclass'a).
- **Cüzdan listesi sync olarak yükleniyor** (cüzdana ekle dialog'unu açmadan
  önce). 6 satırlık çok küçük bir sorgu — UI thread'i bloklamasını önemsemek
  yerine modal aç-kapa basitliği tercih edildi.
- **Piyasa özeti** sembolleri `instruments` tablosunda yoksa "—" gösterilir.
  data-collector seed'i bu tickerları eklemeli (Faz 3 sonrası).

## Klavye Kısayolları
Değişmedi. Mevcut tüm shortcut'lar (Ctrl+1..9, Ctrl+W, Ctrl+M, Ctrl+G, vs.)
geçerliliğini koruyor.

## Test Notu
Manuel test yapılmadı (test ortamı yok). Tüm dosyalar `py -m py_compile` ile
başarıyla derlendi.
