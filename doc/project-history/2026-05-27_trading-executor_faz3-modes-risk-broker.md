---
date: 2026-05-27
agent: trading-executor
phase: faz-3
type: feature
related_files:
  - app/trading/__init__.py
  - app/trading/execution_modes.py
  - app/trading/manual_parallel.py
  - app/trading/risk_scorer.py
  - app/trading/auto_gate.py
  - app/trading/order_manager.py
  - app/broker/__init__.py
  - app/broker/base_broker.py
  - app/broker/adapters/__init__.py
  - app/broker/adapters/example_broker.py
  - app/broker/safety.py
related_doc_sections:
  - "7.1 Yasal Çerçeve"
  - "7.3 İşlem Modları"
  - "7.4 Risk Tabanlı Otomasyon"
  - "7.5 Güvenlik ve Koruma Önlemleri"
  - "7.6 Firma Bağlantısı — Hazır Bekletilen Modül"
---

## Özet

Faz 3 Batch 3 kapsamında `app/trading/` ve `app/broker/` katmanlarının
iskeleti hayata geçirildi: dört işlem modu, manuel-eşli akış,
0–100 risk skoru, mod + skor + safety birleştiren onay kapısı (AutoGate),
emir doğrulama/hazırlama servisi, soyut BaseBroker arayüzü, Faz 4
placeholder adapter (ExampleBroker) ve kill switch / günlük limit /
circuit breaker / audit log içeren SafetyEngine eklendi.

## Detaylar

### Yeni dosyalar (9 adet)

1. **`app/trading/execution_modes.py`**
   - `TradingMode(str, Enum)` — DB CHECK constraint (`user_account.trading_mode`)
     ile birebir eşleşen 4 değer: `manual_parallel | semi_auto | full_auto | paper`.
   - `ModeDescription` dataclass'ı + `MODE_REGISTRY` (her mod için broker
     gereği, otomatik gönderim, kullanıcı onayı, TR açıklama).
   - `is_active_in_current_phase(mode)` — Faz 4 aktif edilene kadar
     `MANUAL_PARALLEL` ve `PAPER` dışındaki modlar False döner.
   - `requires_broker(mode)` — UI uyarısı için kısa yol.

2. **`app/trading/manual_parallel.py`** — ŞU ANKİ BAŞLANGIÇ MODU
   - `ManualTradeMarker` (kullanıcı işareti DTO'su) +
     `PendingRecommendationView` + `BotFollowRate` + `FollowVsSkipPnL`.
   - `ManualParallelService`:
     - `pending_recommendations(account_id, since=None)` —
       `recommendations` ile `portfolio` LEFT-JOIN benzeri NOT EXISTS
       sorgusuyla henüz uygulanmamış öneriler.
     - `mark_applied(wallet_id, recommendation_id, actual_price,
       actual_quantity, commission=None, notes="")` — `portfolio`
       satırı INSERT (`followed_bot=True`) + opsiyonel
       `position_service.apply_buy/sell` çağrısı.
     - `mark_skipped(recommendation_id, notes)` — audit log.
     - `bot_follow_rate(account_id)` — uygulandı / atlandı / değiştirildi.
     - `follow_vs_skip_pnl(account_id)` — `bot_picks.return_pct` ile
       teorik karşılaştırma.

3. **`app/trading/risk_scorer.py`**
   - `TradeRiskScore` dataclass (`score`, `components`, `risk_level`,
     `reasons`, `requires_confirmation`).
   - `TradeRiskScorer` — bileşen ağırlıkları:
     - `volatility` 0.30 (ATR / price oranı; %0.5–%5 lineer eşleme)
     - `trade_size` 0.30 (işlem tutarı / portföy değeri; %1–%20 lineer)
     - `liquidity` 0.20 (ortalama hacim 10k–5M arası ters lineer)
     - `concentration` 0.20 (sektör ağırlığı %10–%40 lineer)
   - `risk_level_from_score(score)` — `low` (<25), `medium` (<60),
     `high` (≥60).
   - `calculate(...)` tek girişten tüm bileşenleri + ağırlıklı toplamı
     üretir; `threshold` parametresine göre `requires_confirmation`
     bayrağını set eder.

4. **`app/trading/auto_gate.py`**
   - `TradeDecision` enum: `EXECUTE_AUTO`, `REQUEST_CONFIRMATION`,
     `MANUAL_ONLY`, `BLOCKED`.
   - `GateResult` dataclass (decision + risk_score + reasons + mode +
     is_phase_active + broker_attached + safety_ok).
   - `AutoGate.evaluate(mode, risk_score, threshold, account_id,
     instrument_id, trade_value)` karar matrisi (aşağıda detay).

5. **`app/trading/order_manager.py`**
   - `OrderRequest`, `OrderValidation` dataclass'ları.
   - `OrderManager.validate(...)`:
     - `quantity > 0`, `price > 0`
     - `abs(price - last_price) / last_price < 0.20` (eşik aşıldığında
       hata)
     - `price * quantity / portfolio_value < 0.30`
   - `OrderManager.prepare(order)` → audit log + metadata dict.
   - `OrderManager.execute(order, mode)`:
     - `manual_parallel` → `awaiting_manual` durumu, hiç emir yok.
     - `paper` → `paper_service` varsa delege; yoksa dry-run.
     - `semi_auto` / `full_auto` → `broker is None` veya
       `place_order` köprüsü hazır değilse
       **`NotImplementedError("Faz 4 aktif değil ...")`**.

6. **`app/broker/base_broker.py`**
   - DTO'lar: `BrokerOrder`, `BrokerOrderResult`, `BrokerPosition`,
     `BrokerBalance`.
   - `BaseBroker(ABC)` — yedi abstract async metod: `authenticate`,
     `place_order`, `cancel_order`, `get_order`, `get_positions`,
     `get_balance`, `is_market_open`.

7. **`app/broker/adapters/example_broker.py`** — Faz 4 PLACEHOLDER
   - `ExampleBroker(BaseBroker)` — tüm metodlar
     `NotImplementedError("Faz 4 aktif değil. Gerçek aracı kurum
     entegrasyonu yapılmadı. ...")` atar.
   - Faz 4 başlatıldığında bu dosya bir **SPK lisanslı kurum** için
     yeniden yazılacak.

8. **`app/broker/safety.py`**
   - `SafetyState` dataclass.
   - `SafetyEngine(session_factory, max_daily_trades=10,
     max_drawdown_pct=15)`:
     - `activate_kill_switch(reason)` / `deactivate_kill_switch(user_confirmation)`
     - `check_daily_trade_limit(account_id)` — bugün açılan
       `portfolio` satır sayısı vs. limit.
     - `check_circuit_breaker(account_id, current_value=None)` —
       in-memory zirve takibi; drawdown ≥ %15 ise tetiklenir.
     - `can_trade(account_id)` — tüm safety kontrollerini koşar,
       `(bool, reasons)` döner.
     - `audit_log(account_id, action, details)` — yapılandırılmış
       loguru `audit=True` bind ile.
     - `get_state(account_id)` — UI safety paneli için özet.

9. **`app/trading/__init__.py` + `app/broker/__init__.py` +
   `app/broker/adapters/__init__.py`** export güncellemeleri.

### Karar matrisi (AutoGate)

| Mod              | Koşul                                          | Çıktı                  |
|------------------|------------------------------------------------|------------------------|
| `manual_parallel`| (her zaman)                                    | `MANUAL_ONLY`          |
| `paper`          | safety OK                                      | `EXECUTE_AUTO`         |
| `semi_auto`      | broker yok                                     | `BLOCKED` (Faz 4)      |
| `semi_auto`      | broker var + safety OK                         | `REQUEST_CONFIRMATION` |
| `full_auto`      | broker yok                                     | `BLOCKED` (Faz 4)      |
| `full_auto`      | broker var + skor < threshold + safety OK      | `EXECUTE_AUTO`         |
| `full_auto`      | broker var + skor ≥ threshold + safety OK      | `REQUEST_CONFIRMATION` |
| herhangi         | kill switch / daily limit / circuit breaker    | `BLOCKED`              |

### Safety katmanı özeti

- **Kill switch** process-yerel; ileride DB tablosuyla multi-process
  paylaşımı önerilir (Faz 4'te `trade_safety_state`).
- **Günlük limit** `portfolio.transaction_at` UTC bugünün satır sayısı
  üzerinden ölçülür.
- **Circuit breaker** in-memory zirve; `current_portfolio_value`
  verilmezse yalnızca nakit toplamı baz alınır.
- **Audit log** loguru `bind(audit=True)`; Faz 4'te `audit_log`
  tablosu eklenecek.

## Gerekçe

Doküman §7 (tüm işlem ve otomasyon bölümü) bu katmanın iskeletini
zorunlu kılıyor. Manuel-eşli mod (§7.3) projenin **şu anki
başlangıç modu**; risk skoru (§7.4) ve safety önlemleri (§7.5) ileride
yarı/tam otomatik moda geçilebilmesi için en başta hazır olmalı.
Soyut `BaseBroker` (§7.6) hazırda bekletilen modül olarak yazılıyor —
gerçek emir gönderimi Faz 4'tedir.

## Faz 4 Sınırı — UYARI YOK / İHLAL YOK

Bu batch'te Faz 4 sınırını **ihlal eden hiçbir şey eklenmedi**.
Aksine, sınır üç noktada kod düzeyinde kilitlendi:

1. `is_active_in_current_phase()` UI ve AutoGate'e `semi_auto` /
   `full_auto`'nun aktif olmadığını söyler.
2. `AutoGate.evaluate()` broker bağlı değilse bu modları `BLOCKED`
   eder.
3. `OrderManager.execute()` aynı modlarda gerçek emir noktasında
   `NotImplementedError("Faz 4 aktif değil...")` atar.
4. `ExampleBroker` tüm metodlarda aynı mesajla `NotImplementedError`
   atar — yanlışlıkla bağlansa bile zarar veremez.

Hiçbir gerçek aracı kurum API'sine bağlantı kurulmadı, hiçbir SPK
lisanslı kurum adapter'ı eklenmedi.

## Test / Doğrulama

Bu batch'te test yazılmadı (görev kapsamı: "sadece dosya yaz"). Aşağıda
test-engineer için **kapsamlı test ipucu** verilmiştir.

## Notlar

### Bağımlılıklar
- `loguru` (mevcut), `pydantic` (mevcut — kullanılmadı, dataclass
  yeterli oldu), `keyring` (Faz 4'te ExampleBroker yerine gelen gerçek
  adapter için zorunlu olacak).

### DB Şema Değişikliği
**YOK.** `UserAccount.trading_mode` CHECK constraint zaten 4 modu
içeriyor (`models.py` satır 301-305). `AccountService.set_trading_mode`
zaten CHECK uyumlu çalışıyor (`app/portfolio/account.py:227`).

### Diğer agent'lar için notlar

- **portfolio-manager:** `AccountService.set_trading_mode` mevcut hâli
  CHECK constraint ile uyumludur; ek bir değişiklik gerekmez.
  `PositionService` yazıldığında `ManualParallelService` `__init__`
  parametresi olarak verilebilir (opsiyoneldir, `apply_buy/sell`
  metodları aranır).
- **ui-developer:** Faz 3 Batch 4'te yazılacak `trading_widget` bu
  modüllerin API'lerini kullanacak:
  - Mod seçici → `MODE_REGISTRY` + `is_active_in_current_phase()`
    (pasif mod için "Yakında — Faz 4" rozeti).
  - Bekleyen öneri tablosu → `ManualParallelService.pending_recommendations`.
  - "Uyguladım" butonu → `ManualParallelService.mark_applied`.
  - "Atladım" butonu → `ManualParallelService.mark_skipped`.
  - Bot dinleme oranı paneli → `bot_follow_rate` + `follow_vs_skip_pnl`.
  - Risk eşiği slider → `AccountService.set_risk_threshold` (mevcut).
  - Kill switch butonu → `SafetyEngine.activate_kill_switch`.
  - Safety paneli → `SafetyEngine.get_state`.
- **test-engineer:** Önerilen test dosyaları ve kapsam:
  - `tests/test_execution_modes.py`
    - `is_active_in_current_phase` her mod için doğru bayrak.
    - `MODE_REGISTRY` tüm modları içeriyor.
  - `tests/test_manual_parallel.py`
    - `pending_recommendations` — uygulanmış öneri liste dışı.
    - `mark_applied` BUY ve SELL — portfolio satırı + `followed_bot=True`.
    - `mark_applied` HOLD — portfolio yazmaz.
    - `bot_follow_rate` — applied/skipped/modified sayımı.
  - `tests/test_risk_scorer.py`
    - Her bileşen skoru sınır değerlerde (0, 100, ortada).
    - `calculate` çıktısının ağırlıklı toplamı doğru.
    - `requires_confirmation` threshold ile uyumlu.
  - `tests/test_auto_gate.py` — **karar matrisi her mod × her skor**:
    - `MANUAL_PARALLEL` her zaman `MANUAL_ONLY`.
    - `PAPER` safety OK → `EXECUTE_AUTO`.
    - `SEMI_AUTO` broker yok → `BLOCKED`.
    - `SEMI_AUTO` broker var → `REQUEST_CONFIRMATION` (skor önemsiz).
    - `FULL_AUTO` broker yok → `BLOCKED`.
    - `FULL_AUTO` broker var + skor < threshold → `EXECUTE_AUTO`.
    - `FULL_AUTO` broker var + skor ≥ threshold → `REQUEST_CONFIRMATION`.
    - Her modda kill switch / daily limit / circuit breaker → `BLOCKED`.
  - `tests/test_order_manager.py`
    - `validate` — anormal fiyat sapması, anormal tutar, negatif değer.
    - `execute` `manual_parallel` → `awaiting_manual`.
    - `execute` `paper` (servis yok) → dry-run.
    - `execute` `semi_auto` broker=None → `NotImplementedError`.
    - `execute` `full_auto` broker=None → `NotImplementedError`.
  - `tests/test_broker.py`
    - `BaseBroker` abstract → instantiate edilemez.
    - `ExampleBroker.authenticate()` → `NotImplementedError`.
    - Tüm metodlar aynı şekilde.
  - `tests/test_safety.py`
    - `activate_kill_switch` → `can_trade` False.
    - `deactivate_kill_switch(False)` no-op.
    - `check_daily_trade_limit` boundary (limit-1, limit, limit+1).
    - `check_circuit_breaker` zirve takibi + tetiklenme.
    - `get_state` doğru özet.
