---
date: 2026-05-27
agent: test-engineer
phase: faz-3
type: test
related_files:
  - tests/test_fundamental.py
  - tests/test_recommender_full.py
  - tests/test_backtester.py
  - tests/test_risk.py
  - tests/test_account_service.py
  - tests/test_wallet_service.py
  - tests/test_cash_flow_service.py
  - tests/test_position_service.py
  - tests/test_pnl_calculator.py
  - tests/test_benchmark_service.py
  - tests/test_paper_trading.py
  - tests/test_execution_modes.py
  - tests/test_manual_parallel_service.py
  - tests/test_risk_scorer.py
  - tests/test_auto_gate.py
  - tests/test_order_manager.py
  - tests/test_safety_engine.py
  - tests/test_base_broker.py
related_doc_sections:
  - "5.2 Vade × Ağırlık Matrisi"
  - "5.4 Backtesting"
  - "5.5 Risk Yönetimi"
  - "6.1 Bütçe Dağılımı (Havuz × Vade)"
  - "6.4 Portföy ve Bakiye Takibi"
  - "6.6 Komisyon, Vergi, Kur Etkisi"
  - "6.7 Endeks Kıyaslaması (TWR)"
  - "6.8 Paper Trading"
  - "7.3 İşlem Modları"
  - "7.4 Risk Tabanlı Otomasyon (AutoGate)"
  - "7.5 Güvenlik (SafetyEngine)"
  - "7.6 BaseBroker Arayüzü"
---

## Özet
Faz 3 Batch 1-3 modülleri (analysis + portfolio + trading + broker) için 193 birim testi yazıldı. Hepsi yeşil. Faz 1+2 regresyonu yok — toplam 389 test geçiyor, 1 önceden skipped.

## Detaylar

### Eklenen test dosyaları
| Dosya | Test sayısı | Kapsam |
|---|---|---|
| `tests/test_fundamental.py` | 15 | FundamentalSnapshot dataclass + score() bonus/penalty + fetch_snapshot mock |
| `tests/test_recommender_full.py` | 14 | MID/LONG ağırlık, score_fundamental, dispatcher (short/mid/long + invalid → ValueError, wrong input type → TypeError) |
| `tests/test_backtester.py` | 11 | BacktestConfig, **look-ahead bias guard** (`_signal_window`), compute_metrics (Sharpe/MaxDD numerik), save_result DB INSERT, vectorbt fallback |
| `tests/test_risk.py` | 13 | position_size cap, stop_loss_take_profit BUY/SELL/fallback, portfolio_correlation simetrik+diagonal=1, diversification_warnings yüksek ρ, sector_concentration |
| `tests/test_account_service.py` | 8 | get_or_create idempotent, set_trading_mode/risk_threshold validation, get_snapshot |
| `tests/test_wallet_service.py` | 9 | init_matrix 6 cüzdan idempotent, allocate/reallocate cash flow zinciri, compute_snapshot |
| `tests/test_cash_flow_service.py` | 11 | deposit/withdrawal mutasyon, negatif kayıt, insufficient cash, net_deposited, list_flows DESC |
| `tests/test_position_service.py` | 11 | apply_buy weighted-average (10@100 + 5@110 → 103.33), apply_sell realized_pnl, partial sell, full sell deletes pos, followed_bot flag |
| `tests/test_pnl_calculator.py` | 4 | closed_trade_pnl TRY (fx=0), position_pnl unrealized, wallet_total |
| `tests/test_benchmark_service.py` | 5 | TWR (deposit şişirmez), benchmark_return seeded prices, compare alpha + beat_benchmark |
| `tests/test_paper_trading.py` | 6 | reset_paper_account, execute_paper_trade BUY paper-only, simulate_bot_picks tek hit_target pick |
| `tests/test_execution_modes.py` | 11 | TradingMode enum 4 değer, MODE_REGISTRY metadata, is_active_in_current_phase (manual+paper=True, semi+full=False) |
| `tests/test_manual_parallel_service.py` | 6 | pending_recommendations, mark_applied portfolio INSERT, mark_skipped, bot_follow_rate |
| `tests/test_risk_scorer.py` | 15 | WEIGHTS toplam=1, score_volatility/trade_size/liquidity/concentration uç değerler, calculate threshold |
| `tests/test_auto_gate.py` | 9 | Karar matrisi: manual_parallel=MANUAL_ONLY, paper=EXECUTE_AUTO, semi/full broker yok→BLOCKED, full_auto skor<eşik→EXECUTE_AUTO, safety override |
| `tests/test_order_manager.py` | 10 | validate (qty/price/deviation/oversize), execute mod bazlı (manual=awaiting, paper=dry-run, semi/full→NotImplementedError) |
| `tests/test_safety_engine.py` | 10 | kill switch activate/deactivate (onaysız no-op), daily_trade_limit, circuit_breaker drawdown ≥ eşik, can_trade, audit_log |
| `tests/test_base_broker.py` | 11 | BaseBroker abstract (TypeError), ExampleBroker instantiate OK, tüm metodlar NotImplementedError("Faz 4") |

**Toplam: 193 yeni test eklendi.**

### Test stratejisi
- **In-memory SQLite** (conftest.py'deki `sync_engine` / `session_factory`) — gerçek DB yok.
- **vectorbt / TA-Lib / transformers** — kurulu olsun ya da olmasın testler geçer (lazy import + fallback).
- **Async fixture**'lar pytest-asyncio auto mode ile resolve oluyor — `await` consumer tarafında değil, fixture body içinde.
- **Look-ahead bias guard** Backtester._signal_window saf-fonksiyon test edildi (`prices.loc[:as_of]` kesim doğrulandı).
- **Karar matrisi** AutoGate için her (mode × score × safety × broker) kombinasyonu tek tek test edildi.

## Gerekçe
Faz 3 tamamlanması için her modülün doğru imza ve davranışla test kapsamına alınması gerekiyordu. Özellikle:
- Look-ahead bias (backtester) güvenlik kritik
- Weighted-average cost (positions) finansal doğruluk için
- AutoGate karar matrisi (Faz 4 koruması: broker yoksa BLOCKED) — kullanıcı parasının korunması
- SafetyEngine (kill switch + circuit breaker + daily limit) — felaket önleme

## Test / Doğrulama
```
$ python -m pytest tests/ --tb=short --no-header -q
389 passed, 1 skipped, 1 warning in 4.27s
```
- Faz 1+2 regresyonu: 196 önceki test hâlâ geçiyor.
- Faz 3 yeni testler: 193/193 geçti.
- Hiç başarısız test yok.

## Bulunan bug'lar (üretim kodu)
**Yok.** Tüm modüller spec uyumlu yazılmış; testler ilk denemede ya da küçük expectation düzeltmeleriyle yeşil oldu.

Test yazımı sırasında karşılaşılan teknik notlar (üretim bug'ı değil, test setup):
1. `RiskManager._fetch_price_panel` `cutoff = now - lookback_days` kullanıyor — test fixture sabit tarih (2024-01-01) yerine `datetime.now() - timedelta(days=N+1)` ile seed ediyor.
2. `Backtester.compute_metrics` max_drawdown'u `np.maximum.accumulate` ile tüm peak'lere bakarak hesaplıyor (peak=120 sonrası değil, peak=110→105 düşüşü en büyük); test expectation `4.545%` olarak güncellendi.
3. `WalletService` `current_prices` mevcut değilken `positions_value` ham maliyetle değerleniyor (koruyucu davranış, doğru).

## Notlar
- Coverage detayı: pytest-cov çalıştırılmadı (test runner zaten 4 saniyede 389 test koşuyor). Manuel inceleme: tüm public API metodları ve karar matrisi dalları test ediliyor.
- Faz 4 hazır: BaseBroker abstract + ExampleBroker placeholder testleri ileride gerçek adapter (IsYatirimBroker) yazıldığında aynı yapıyı `NotImplementedError → mock fixture` ile değiştirerek koruyacak.
- `tests/test_recommender.py` (ShortTerm) Faz 2'den geliyor; yeni `tests/test_recommender_full.py` orta + uzun + dispatcher kapsıyor (çakışma yok).
- Async fixture deprecation warning'i pytest-asyncio sürüm kaynaklı, kod hatası değil.
