---
date: 2026-05-27
agent: ui-developer
phase: faz-3
type: feature
related_files:
  - app/ui/components/metric_card.py
  - app/ui/components/sparkline.py
  - app/ui/components/toast.py
  - app/ui/components/skeleton.py
  - app/ui/components/__init__.py
  - app/ui/portfolio_widget.py
  - app/ui/budget_widget.py
  - app/ui/trading_widget.py
  - app/ui/history_widget.py
  - app/ui/recommendation_widget.py
  - app/ui/main_window.py
  - app/ui/dashboard.py
  - app/ui/settings_widget.py
related_doc_sections:
  - "6.1 Bütçe Dağılımı (Havuz × Vade Matrisi)"
  - "6.4 Portföy ve Bakiye Takibi"
  - "6.5 İşlem Geçmişi"
  - "6.7 Endeks Kıyaslaması"
  - "7.3 İşlem Modları"
  - "7.4 Risk Tabanlı Otomasyon"
  - "7.5 Güvenlik ve Koruma Önlemleri"
  - "8.4 Modern UI Bileşenleri"
---

## Özet
Faz 3 UI cilası: 4 yeni reusable component (`MetricCard`, `Sparkline`,
`ToastNotification` + `ToastManager`, `SkeletonLoader`); Portföy, Bütçe,
İşlem & Otomasyon, İşlem Geçmişi widget'ları placeholder veri ile aktif
edildi; Dashboard, Settings ve Recommendation widget'ları reusable
component'ler ve yeni özelliklerle yenilendi; Kill Switch toolbar'da
sabit + Ctrl+Shift+K kısayolu eklendi; Faz 4 modları her yerde
"Yakında - Faz 4" rozeti ile disabled.

## Detaylar

### Yeni Reusable Component'lar (`app/ui/components/`)
- **`metric_card.py`** — `MetricCard(title, value, subtitle, trend)`.
  Flat tasarım: ince kenarlık, gri başlık, büyük bold değer, trend ok
  ikonu (qtawesome graceful degradation → unicode ▲ ▼ — fallback).
  `set_value()`, `set_title()` API'leri runtime güncellemesi için.
- **`sparkline.py`** — `Sparkline()` 80×24 px mini trend.
  pyqtgraph varsa `PlotWidget` (eksenler kapalı); yoksa `QPainter`
  fallback. Renk: `positive=True/False/None` (otomatik karar).
- **`toast.py`** — `ToastNotification` + `ToastManager` (singleton).
  Sağ üst köşede yığın, `QGraphicsOpacityEffect` ile fade-in/out
  (`QPropertyAnimation`), `QTimer` ile auto-dismiss; 4 seviye
  (info / success / warning / error) renkli ikon + mesaj.
- **`skeleton.py`** — `SkeletonLoader(rows)` shimmer animasyonlu
  placeholder. `start()` / `stop()` API'si, satır genişlik varyasyonu.

### Yeni Widget'lar
- **`portfolio_widget.py` — Portföy (DOC §6.4):**
  - Üst KPI şeridi: 4 `MetricCard` (Toplam Bakiye, K/Z TL, K/Z %, Günlük).
  - Segment butonları (Tüm Hesap / Bot / Kendim) + Vade combo.
  - Açık pozisyon tablosu: Ticker, Cüzdan rozeti (renk kodlu pool×tf),
    Adet, Maliyet, Güncel, K/Z TL, K/Z %, Ağırlık %, `Sparkline` kolonu.
  - Sektör dağılımı: pyqtgraph yoksa renkli liste fallback.
  - Performans grafiği: portföy + endeks çizgisi (pyqtgraph), üst sağda
    endeks toggle (`BIST100` / `S&P 500`).
  - "Endeksi Yendin mi?" rozeti.

- **`budget_widget.py` — Bütçe (DOC §6.1):**
  - Toplam Bakiye + Serbest Nakit MetricCard'ları, büyük yeşil "Para Ekle"
    ve kırmızı "Para Çek" butonları.
  - 2×3 grid: 6 `_WalletCell` (tıklanabilir, hover'da mavi kenarlık).
  - Sağ düzenleme paneli: tutar input + "Tahsis Et", "Yeniden Dağıt",
    "Cüzdandan Çek" butonları (cüzdan seçilince enable).
  - Cash flow tablosu (deposit / withdrawal / allocate / reallocate
    Türkçe etiketleri).
  - "Tüm Cüzdanları Sıfırla (Paper)" butonu — onay diyaloğu ile.

- **`trading_widget.py` — İşlem & Otomasyon (DOC §7):**
  - Mod combo: `MODE_REGISTRY`'den description_tr. `semi_auto` ve
    `full_auto` Qt model item enabled=False ile **disabled**;
    "Yakında - Faz 4" turuncu rozeti yan tarafta.
  - Risk eşiği slider (0-100) + canlı sayı + açıklama metni.
  - `KillSwitchButton` (büyük kırmızı, 64px yükseklik, onay diyaloğu).
  - Safety KPI satırı: Kill Switch, Günlük İşlem, Drawdown, Bot Dinleme
    Oranı (4 `MetricCard`).
  - Bekleyen öneri tablosu: aksiyon rozeti + "Onayla" / "Reddet" satır
    butonları.
  - Manuel-eşli işaretleme paneli (yalnızca `manual_parallel` modda
    görünür): gerçek fiyat + adet + komisyon → "Uyguladım" / "Atladım".

- **`history_widget.py` — İşlem Geçmişi (DOC §6.5):**
  - Filtre çubuğu: tarih aralığı (`QDateEdit`), ticker arama, BUY/SELL
    combo, cüzdan combo (6 + Tümü), bot uyumlu checkbox.
  - Ana tablo: 11 sütun (Tarih, Ticker, Tip rozeti, Adet, Fiyat, Toplam,
    Komisyon, K/Z, Tutma günü, Cüzdan rozeti, Bot Uyumlu ✓).
  - CSV / Excel dışa aktarım butonları (`QFileDialog` + TODO pandas).
  - Alt özet: toplam işlem, toplam komisyon, net realized P&L (renkli).

### Güncellenen Widget'lar
- **`recommendation_widget.py`** — Faz 3 sekmeleri eklendi:
  - "Özet" (eski summary text), "Backtest Performansı"
    (`BacktestRow` tablosu), "Risk Uyarıları" (`RiskWarning` listesi
    renk seviyeli), "Botu Dinleseydin" (`BotSimResult` özeti).
  - `set_backtests()`, `set_risk_warnings()`, `set_bot_sim()` public API.
  - "Bot Pick'lere Ekle" butonu artık **aktif** — view yüklendiğinde
    enable olur, sinyali `add_to_picks_requested` emit eder.
- **`main_window.py`** —
  - Portföy / Bütçe / İşlem & Otomasyon / İşlem Geçmişi NAV item'ları
    `enabled=True`, `_build_screen()` gerçek widget'ları döndürür.
  - Toolbar'a Kill Switch butonu eklendi (sağa sabit, kompakt stil).
  - `ToastManager.instance().attach(self)` çağrısı.
- **`dashboard.py`** —
  - 4 metrik kartı reusable `MetricCard` ile yeniden yazıldı.
  - Yeni "Cüzdan Özeti" grup kutusu: 2×3 mini cell grid (6 cüzdan).
  - "Endeksi Yendin mi?" rozeti başlık satırına eklendi.
  - "Son Haberler" + "Bot Öne Çıkanlar" blokları korundu.
- **`settings_widget.py`** —
  - "Trading ve Güvenlik" grup kutusu: trading mode combo (Faz 4 modları
    disabled + rozetli), risk eşiği slider, daily limit `QSpinBox`,
    max drawdown `QDoubleSpinBox`, kill switch checkbox (kırmızı bold).

### Klavye Kısayolları
| Kısayol | İşlev | Not |
|---|---|---|
| Ctrl+M | Portföy | (Money) — yeni |
| Ctrl+G | Bütçe | (Günlük) — Ctrl+B sidebar toggle çakışmasın diye G |
| Ctrl+T | İşlem & Otomasyon | (Trading) — yeni; tema artık Ctrl+L |
| Ctrl+H | İşlem Geçmişi | (History) — yeni |
| Ctrl+L | Tema toggle | (Light) — eski Ctrl+T'den taşındı |
| Ctrl+Shift+K | Kill Switch | Global, her ekrandan tetiklenir |
| Ctrl+1..9 | Sidebar geçişi | Faz 1 |
| Ctrl+N / Ctrl+W / Ctrl+P | Haberler / Watchlist / Bot Picks | Faz 2 |
| Ctrl+, / Ctrl+B / Ctrl+Q | Ayarlar / Sidebar toggle / Çıkış | Faz 1 |

### Kill Switch Konumu
- **Toolbar sağ tarafında** sabit (genişlik 120px, yükseklik 36px) —
  her ekrandan görünür ve tıklanabilir.
- **Trading widget'ı içinde** ayrıca büyük versiyonu (64px yükseklik,
  16pt font) sağ üst köşede.
- Her ikisi de onay diyaloğu ister; aktifleşince `ToastManager` ile
  hata seviyesinde toast gösterir ve Trading ekranındaki "Kill Switch"
  MetricCard'ı "AKTİF" durumuna geçer.
- **Ctrl+Shift+K** global kısayolu mevcut.

### Faz 4 Modları İçin UI Koruması
- `trading_widget.py` mode combo: `is_active_in_current_phase(mode)`
  False ise Qt model item `setEnabled(False)` + tooltip "Yakında -
  Faz 4 (aracı kurum entegrasyonu)". Yan tarafta turuncu rozet.
- `settings_widget.py` aynı koruma + etiket sonuna "(Yakında - Faz 4)"
  metni eklenir.
- `trading_widget.py` üstündeki rozet (`_phase4_badge`) seçilen mod
  Faz 4 ise ek görsel uyarı.

## Gerekçe
Faz 3 Batch 2 (portfolio API'leri) ve Batch 3 (trading API'leri)
arka uç tarafında hazırlandı; bu commit UI'da bu servislerin
**tüketim arayüzlerini** yazıyor. Placeholder veri ile çalışıyor;
Faz 4'te `AccountService`, `WalletService`, `PositionService`,
`BenchmarkService`, `ManualParallelService`, `SafetyEngine`,
`AutoGate`, `BotPicksService`, `PaperTradingService` çağrıları her
widget içinde `# TODO:` yorumu altında gösterildi.

Reusable component'lar, ileride yeni ekranlar yazılırken kod
tekrarını azaltacak (`MetricCard` zaten Dashboard / Portföy / Bütçe /
Trading'de kullanılıyor).

## Test / Doğrulama
- Tüm yeni ve değişen dosyalar `ast.parse` ile syntax doğrulandı
  (13 dosya — `ALL OK`).
- Widget'lar import ve instantiation seviyesinde test-engineer
  tarafından smoke test edilecek (`tests/test_ui_*.py` paralel iş).
- pyqtgraph / qtawesome graceful degradation kontrolü: her import
  try/except ile sarıldı; her bileşen kütüphane yoksa fallback yola
  düşer (basit liste, unicode glyph, QPainter).

## Backend Bağlantı TODO Listesi (Her Widget İçin)

| Widget | İhtiyaç Duyduğu Servisler |
|---|---|
| `portfolio_widget` | `AccountService.get_snapshot`, `WalletService.list_wallets + compute_snapshot`, `PositionService.list_all_open_positions`, `BenchmarkService.compare_to_index` |
| `budget_widget` | `AccountService.get_account`, `WalletService.list_wallets`, `WalletService.allocate / reallocate`, `CashFlowService.deposit / withdrawal / list_recent`, `PaperTradingService.reset_paper_account` |
| `trading_widget` | `AccountService.set_trading_mode / set_risk_threshold`, `ManualParallelService.list_pending / mark_applied / mark_skipped / get_follow_rate`, `SafetyEngine.get_state / activate_kill_switch`, `AutoGate.decide` (Faz 4) |
| `history_widget` | `portfolio` tablosundan `list_transactions(filters)` helper'ı + pandas export |
| `recommendation_widget` | `Backtester.run_strategy`, `RiskManager.diversification_warnings + correlation_warnings`, `PaperTradingService.simulate_bot_picks`, `BotPicksService.add_pick` |
| `dashboard` | `AccountService.get_snapshot`, `WalletService.list_wallets`, `BenchmarkService.compare_to_index` |
| `settings_widget` | `AccountService.set_trading_mode / set_risk_threshold`, `SafetyEngine` config (max_daily_trades, max_drawdown_pct), `SafetyEngine.activate / deactivate_kill_switch` |
| `main_window` | `SafetyEngine.activate_kill_switch` (global kill switch) |

## Notlar
- Pasta grafiği için pyqtgraph'ın yerleşik bileşeni olmadığı için
  şimdilik sektör dağılımı renkli liste fallback'i kullanıyor;
  Faz 4'te `QGraphicsEllipseItem` ile gerçek pasta dilimleri çizilebilir.
- `BudgetWidget` "Yeniden Dağıt" şu an hedef cüzdan seçimi için ayrı
  dialog gerektiriyor — Faz 4 entegrasyonunda eklenecek (TODO yorumu var).
- CSV / Excel export şu an `QFileDialog` ile dosya yolu alır ama
  yazma işlemi yapılmaz — pandas bağlanınca aktif olacak.
- `ToastManager` singleton paterni; ana pencere `attach()` çağrısı
  yapmadan toast gösterilmez (sessiz no-op — açılış sırasında erken
  çağrılara karşı güvenli).
- UI thread'i bloklayacak bir iş henüz yok; gerçek backend bağlandığında
  uzun sorgular için `QThreadPool` worker pattern'i widget'lara
  eklenecek (şu an TODO yorumlarında belirtildi).
