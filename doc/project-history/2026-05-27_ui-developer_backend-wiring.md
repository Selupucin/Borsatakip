# UI Developer — Backend Wiring (Faz 3 → Faz 4 hazırlık)

Tarih: 2026-05-27

## Özet

Tüm UI widget'larındaki `# TODO: <Service>'i çağır` notları gerçek async
backend servis çağrılarına dönüştürüldü. UI thread bloklanmadan
SQLAlchemy session'ları açıp veri çekmek için `QThreadPool` üzerinde
`asyncio.run()` ile koşan bir bridge eklendi.

## Yeni Modül

### `app/ui/_backend_bridge.py` (YENİ)

- `BackendBridge` QObject: tüm portföy, trading, broker, analysis
  servislerini lazy property olarak ürütür; `SessionLocal` callable'ını
  `session_factory` sözleşmesi gereği iletir.
- `_AsyncWorker(QRunnable)` + `_AsyncWorkerSignals(QObject)`:
  - `coro_factory` parametresi alır; thread'de `asyncio.run` ile koşturur.
  - `finished(object)` ve `error(object)` Qt sinyalleri Qt'nin otomatik
    cross-thread mekanizması sayesinde UI thread'de slot'lara teslim olur.
- `BackendBridge.run_async(coro_factory, on_success, on_error)`: bir
  satırda async çağrı + UI callback bağlama.
- `BackendBridge.ensure_account()`: `UserAccount` ve 6 cüzdan matrisini
  idempotent garanti eder; `account_id` cache'lenir.
- `get_bridge()` singleton; test izolasyonu için `reset_bridge()`.

**Defansif:** `app.db.session.SessionLocal` import edilemezse bridge
`available=False` ile yaşar; widget'lar bu durumda placeholder/empty
state'e düşer (toast hatası yerine sessiz devam).

## Widget → Servis Eşleme

| Widget | Çağrılan servis(ler) | Mutation |
|---|---|---|
| `dashboard.py` | `AccountService.get_snapshot`, `WalletService.list_wallets`, `PositionService.list_all_open_positions`, `BenchmarkService.compare`, `ManualParallelService.pending_recommendations` | — |
| `portfolio_widget.py` | `AccountService.get_snapshot`, `PositionService.list_all_open_positions`, `BenchmarkService.compare` | — |
| `budget_widget.py` | `AccountService.get_snapshot`, `WalletService.list_wallets`, `CashFlowService.list_flows` | `WalletService.allocate`, `WalletService.reallocate`, `CashFlowService.deposit`, `CashFlowService.withdrawal`, `PaperTradingService.reset_paper_account` |
| `trading_widget.py` | `AccountService.get_snapshot`, `ManualParallelService.pending_recommendations`, `ManualParallelService.bot_follow_rate`, `SafetyEngine.get_state` | `AccountService.set_trading_mode`, `AccountService.set_risk_threshold`, `ManualParallelService.mark_applied`, `ManualParallelService.mark_skipped`, `SafetyEngine.activate_kill_switch` |
| `history_widget.py` | `Portfolio JOIN wallets JOIN instruments` (raw sync select; `_fetch_transactions` helper) | CSV export (csv stdlib), Excel export (pandas opsiyonel) |
| `watchlist_widget.py` | `WatchlistService.list_all`, `WatchlistService.list_items(with_quotes=True)` | `WatchlistService.create`, `WatchlistService.delete`, `WatchlistService.add_item` |
| `bot_picks_widget.py` | `BotPicksService.list_picks(only_open=…)` | — (detay dialog'undan `add_pick`) |
| `news_widget.py` | `news_feed` tablosundan async select (`_fetch_news_from_db`) | — |
| `recommendation_widget.py` | `backtest_results` select, `RiskManager.diversification_warnings + sector_concentration` | `BotPicksService.add_pick` ("Bot Pick'lere Ekle" butonu) |
| `alerts_widget.py` | `Alert JOIN instruments` async select | `Alert` tablosuna INSERT (yeni alarm formu) |
| `settings_widget.py` | `AccountService.get_snapshot` (mode + threshold senkronizasyonu) | `AccountService.set_trading_mode`, `AccountService.set_risk_threshold`, `SafetyEngine.activate/deactivate_kill_switch` |
| `main_window.py` | `BackendBridge.ensure_account` (açılışta), `SafetyEngine.activate_kill_switch` (toolbar Kill Switch) | — |

## QThreadPool Worker Stratejisi

- Her widget yüklendiğinde (`showEvent`) ve gerektiğinde (mutation
  sonrası, filtre değişimi, vb.) `refresh()` çağırır.
- `refresh()` içinde `bridge.run_async(lambda: bridge.service.method(...))`
  ile worker üretilir; UI thread bloklanmaz.
- Async sonuçlar Qt sinyal/slot ile UI thread'e taşındığından `Q*Widget`
  güncellemeleri thread-güvenli.
- `_AsyncWorker.run()` `asyncio.run` kullanır — yani her çağrı için
  yeni event loop; servislerin async metodları içindeki sync SQLAlchemy
  session işlemleri sorunsuz çalışır.

## Hata Handling

- `BackendBridge.available=False` (DB yoksa): widget'lar placeholder
  veriyle (varsa) veya empty state ile çalışır; toast/uyarı çıkmaz.
- Mutation hatalarında (`allocate`, `deposit`, `set_trading_mode`, vb.)
  `on_error` callback'i `ToastManager.instance().show(..., level="error")`
  ile kullanıcıya bildirir.
- Read hatalarında (`refresh`) genelde sessiz `logger.debug` — toast
  spam'i olmasın diye.
- Crashlar `try/except` ile callback'lerde yakalanır.

## Empty State

- `PortfolioWidget`: "Henüz açık pozisyon yok." — tablonun ilk satırı.
- `HistoryWidget`: "Henüz işlem geçmişi yok."
- `NewsWidget`: "Henüz haber yok — NewsAggregator henüz çalışmamış olabilir."
- `DashboardWidget`: "Henüz cüzdan tanımlı değil." (init_matrix sonrası
  hızla 6 cüzdana dönüşür).
- `WatchlistWidget`: bridge varsa boş liste döner; bridge yoksa
  placeholder watchlist'ler.

## Bilinen Sınırlamalar (Faz 4'e Devredilen)

1. **Gerçek-zamanlı fiyat akışı yok.** `PositionService.list_all_open_positions`
   `current_prices=None` ile çağrılıyor — market_value/pnl_unrealized
   eksik. Data-collector tick stream'i Faz 4'te bağlanacak.
2. **`PositionView` cüzdan meta'sı yok.** Portfolio widget'ta her
   pozisyon `pool="bot"`, `timeframe="short"` olarak gösteriliyor —
   gerçek wallet metadata'sı için PositionService genişlemesi gerek.
3. **`WatchlistItemView` instrument_id yok.** Listeden ticker silmek
   için `remove_item(wl_id, instrument_id)` çağırmak gerekiyor;
   şu an UI satır kaldırma + toast info ile maskeleniyor.
4. **NewsFeed boş tabloda.** `news_widget` `news_feed` tablosundan
   okuyor ama tablo NewsAggregator çalışmadan boş kalır — empty state
   mesajı kullanıcıyı bilgilendirir.
5. **Backtest tablosu placeholder.** RecommendationWidget tüm
   `backtest_results` satırlarını ticker bağımsız çekiyor (tablo
   şu an boş; Faz 4'te Backtester run'larından sonra dolar).
6. **Kill Switch process-yerel.** `SafetyEngine._kill_switch_active`
   in-memory; çok-süreçli çalıştırmada paylaşılmaz. Faz 4'te
   `trade_safety_state` tablosu önerilir.
7. **`ManualParallelService.mark_applied`** için varsayılan cüzdan
   `bot/mid` seçildi; gerçek UI'da kullanıcı cüzdan seçmeli (Faz 4
   manuel-eşli paneline cüzdan combo eklenecek).
8. **`AutoGate` UI'da kullanılmadı.** Trading widget'taki "Onayla"
   butonu manuel-eşli akışta kullanıcıyı yönlendirir; semi/full auto
   broker entegrasyonu Faz 4'te yapılacak.

## Klavye Kısayolları (değişmedi)

- Mevcut Ctrl+1..9, Ctrl+M, Ctrl+G, Ctrl+T, Ctrl+H, Ctrl+Shift+K
  kısayolları korundu.

## Test

- `tests/test_ui_smoke.py` (5 test): hepsi geçti ✓
- Sanity import: `from app.ui.main_window import MainWindow;
  from app.ui._backend_bridge import get_bridge` → OK

## Backend API İhtiyaçları (Diğer Agent'ları Haberdar Et)

- **portfolio-manager**: `PositionView`'a `wallet_pool` ve
  `wallet_timeframe` alanları eklenirse portfolio_widget'taki rozet
  doğru gösterilir.
- **portfolio-manager**: `WatchlistItemView`'a `instrument_id` eklenirse
  watchlist'ten ticker silmek kalıcı olur.
- **portfolio-manager**: `WalletService.withdraw(wallet_id, amount)`
  metodu eklenirse "Cüzdandan Çek" butonu gerçekten çalışır.
- **trading-executor**: `ManualParallelService.mark_applied` çağrısında
  cüzdan seçimi UI'dan iletilecek; varsayılan kaldırılmalı.
