# UI Developer — Tablo, Format ve Bütçe Düzeltmeleri (2026-05-28)

## Özet
5 UI bug fix ile tablo genişlik dağılımı, TR locale sayı formatı (binlik
nokta + ondalık virgül), yeni `WalletService.deposit_and_allocate` backend
metodunun UI bağlantısı ve dashboard piyasa özetine 2 yeni satır
(EUR/TRY + Altın) eklendi. Backend imza değişikliği YOK; tüm değişiklikler
UI katmanında.

## Yapılan 5 Düzeltme

### #4 — Bot Picks tablosu genişliğe uymuyor (`bot_picks_widget.py`)
- `_TimeframeTabContent._build_ui` header'ı yeniden yapılandırıldı:
  - Mod: `Interactive` (kullanıcı manuel boyutlandırabilir)
  - `setStretchLastSection(False)`, `setSectionsMovable(True)`
  - Ticker (0), Aksiyon (1), Durum (8) → `ResizeToContents` (kompakt)
  - Güven, Öneri Tarihi, Öneri Fiyatı, Hedef, Şimdiki, Getiri % →
    `Stretch` (tablo genişliğini doldurur)
- Sonuç: tablo widget tam genişliğe yayılıyor, sayısal kolonlar eşit
  dağılıyor, ticker/aksiyon dar kalıyor.

### #5 — Watchlist kolonları ayarlanabilir + min genişlik (`watchlist_widget.py`)
- `setSortingEnabled(True)` aktif: Ticker / Fiyat / Hacim / Günlük %
  kolonlarından sıralama yapılabilir.
- `_render_table` içinde geçici `setSortingEnabled(False)` ile insertRow
  sırasında sıralamanın bozulması engellendi (sort state korunur).
- Header:
  - `Interactive` mod — kullanıcı her kolonu serbest boyutlandırır.
  - `setSectionsMovable(True)` — sürükle-bırak ile kolon sırası
    değiştirilebilir.
  - `setStretchLastSection(True)` — son kolon (Sparkline) kalan alanı
    doldurur.
  - `setMinimumSectionSize(40)` — kullanıcı kolonu tamamen kaybedemez.
- Akıllı varsayılan kolon genişlikleri (px):
  Ticker 70, Ad 180, Borsa 70, Fiyat 100, Günlük % 85, Hacim 80,
  Kısa/Orta/Uzun 110, Sparkline 100.

### #8 — Para giriş alanlarında otomatik TR locale binlik ayracı (1.000.000,50)
- Yeni helper: `app/ui/_format.py::apply_tr_locale(spinbox)`
  - `QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey)` set eder.
  - `setGroupSeparatorShown(True)` ile binlik nokta + ondalık virgül
    Qt'nin built-in lokal sisteminden otomatik gelir.
  - PySide6 lazy import — modül Qt yoksa hata vermez (tek başına test
    edilebilir).
- Uygulanan spinbox'lar:
  - `budget_widget.py::BudgetWidget._amount_input` (Tutar)
  - `watchlist_widget.py::_AddToWalletDialog._qty_spin / _price_spin / _comm_spin`
  - `_trade_dialogs.py::_TradeAdviceDialog._qty_spin`
- Para Ekle / Para Çek dialog'ları `QInputDialog.getDouble` kullanıyor —
  bu sistem locale'i ile zaten TR formatında çalışır (uygulamanın açılış
  locale'i değiştiyse otomatik gelir).

### #7 ek — Bütçe widget'a "Cüzdana Yatır" butonu (`budget_widget.py`)
- `_WalletCell` artık 2 hızlı işlem butonu içeriyor:
  - **Cüzdana Yatır** (yeşil) — `WalletService.deposit_and_allocate`
    çağırır (hesaba yatırma + cüzdana tahsis tek adımda, atomic).
    Tooltip: "Hesabınıza para yatırma + cüzdana tahsis tek adımda"
  - **Tahsis Et** (mavi) — `WalletService.allocate` (mevcut serbest
    nakitten ayır). Tooltip: "Hesabınızdaki serbest nakitten cüzdana
    tahsis edin"
- Yeni `_WalletCell` sinyalleri: `deposit_clicked(pool, timeframe)`,
  `allocate_clicked(pool, timeframe)`.
- Yeni `BudgetWidget` slot'ları: `_on_cell_deposit` ve
  `_on_cell_allocate_quick` — `QInputDialog.getDouble` ile tutar al,
  `bridge.run_async` ile çağır, başarı/hata toast'u göster, sonra
  `refresh()`.
- Mevcut sağ paneldeki "Tahsis Et" / "Yeniden Dağıt" / "Cüzdandan Çek"
  butonları korundu (tutar input alanı ile birlikte) — hızlı butonlar
  bunlara alternatif/ek olarak çalışıyor.
- `_WalletCell.setMinimumHeight` 120 → 160 (buton sığsın).

### #3 ek — Dashboard piyasa özetine EUR/TRY + Altın eklendi (`dashboard.py`)
- `_MARKET_ROWS` 3 → 5 satır:
  - BIST 100 (XU100 instrument)
  - S&P 500 (^GSPC instrument)
  - USD/TRY (USDTRY fx_rates)
  - **EUR/TRY** (EURTRY fx_rates) — YENİ
  - **Altın (USD/oz)** (XAUUSD fx_rates) — YENİ
- `_market_table` satır sayısı `len(_MARKET_ROWS)` olarak dinamikleşti;
  placeholder rows liste comprehension ile üretiliyor.
- `_fetch_market_summary` fonksiyonu zaten `kind="fx"` durumunu
  destekliyor (`FxRate.pair == key` ile son 2 kaydı çekip değişim
  hesaplar) → yeni satırlar otomatik çalışır.
- Scheduler `FX_PAIRS = (USDTRY, EURTRY, XAUUSD)` ile bu 3 pair'i 1
  dakikada bir çekiyor → dashboard sadece SELECT ediyor.

## TR Locale Spinbox Uygulaması — Detay
`apply_tr_locale` helper:
```python
from PySide6.QtCore import QLocale
tr_locale = QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey)
spinbox.setLocale(tr_locale)
spinbox.setGroupSeparatorShown(True)
```
- Qt 6'nın native lokal sistemi kullanılıyor — custom validator yazılmadı.
- `1500000.5` → "1.500.000,50" otomatik dönüşür; kullanıcı "1.500.000,50"
  yazınca da kabul edilir.
- `QDoubleSpinBox` ve `QSpinBox` her ikisini de destekler.

## deposit_and_allocate UI Bağlantısı
- Backend: `WalletService.deposit_and_allocate(wallet_id, amount)` —
  hesap cash_balance += amount + cash_flows('deposit') + cüzdan
  allocated/cash_balance += amount + cash_flows('allocate') tek
  transaction'da yazar.
- UI çağrısı: `bridge.run_async(lambda: bridge.wallets.deposit_and_allocate(wallet_id, amt))`.
- 6 cüzdan hücresinin her birinde ayrı buton; her hücre `QInputDialog`
  ile tutarı sorar.
- Başarı: yeşil toast (örn. "1.000,00 ₺ cüzdana yatırıldı (Bot · Kısa).")
  + `refresh()` (hesap snapshot + wallet list + cash_flows tablosu).
- Hata: kırmızı toast 5sn ("Hata: ...").

## Dashboard Yeni Satırlar
- EUR/TRY ve Altın (XAUUSD) satırları otomatik renk kodlu:
  - Yeşil = pozitif değişim, kırmızı = negatif (mevcut palet).
- Değişim ve Değişim % kolonu için son 2 fx_rates kaydı kullanılır
  (önceki dakika vs şimdi).

## Backend API İhtiyacı / Bilgilendirme
- Hiçbir backend imzası değişmedi.
- `WalletService.deposit_and_allocate` zaten Faz 3 Batch 2 ile mevcut;
  UI sadece bağlandı.
- portfolio-agent: hiç dokunulmadı.
- data-engineer: scheduler'ın FX_PAIRS'a EURTRY + XAUUSD pair'lerini
  her 1 dakikada çekmesi gerekiyor (bu görev kapsamında değil — UI
  sadece bunları select ediyor).

## Klavye Kısayolları
Bu batch'te yeni kısayol eklenmedi. Watchlist sıralama header'a
tıklayarak çalışır (Qt default).

## Test
Smoke test geçti:
```python
from PySide6.QtWidgets import QApplication
app = QApplication([])
from app.ui.main_window import MainWindow
from app.ui.theme_manager import ThemeManager
tm = ThemeManager(app); tm.apply_saved_or_default()
mw = MainWindow(theme_manager=tm)  # OK
```

## Ekran Görüntüsü
Manuel test sonrası eklenecek:
- Bot Picks tablosu — kolonların eşit dağılımı
- Watchlist — kolon sürüklenebilirliği + sıralama
- Bütçe — her hücrede 2 yeni buton
- Dashboard — piyasa özetinde 5 satır
- Para Ekle dialog — "1.000.000,50" formatlı input
