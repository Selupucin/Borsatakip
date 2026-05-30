---
date: 2026-05-27
agent: portfolio-manager
phase: faz-3
type: feature
related_files:
  - app/portfolio/__init__.py
  - app/portfolio/account.py
  - app/portfolio/wallets.py
  - app/portfolio/cash_flows.py
  - app/portfolio/positions.py
  - app/portfolio/pnl.py
  - app/portfolio/benchmark.py
  - app/portfolio/paper_trading.py
related_doc_sections:
  - "6.1 Bütçe Dağılımı (Havuz × Vade Matrisi)"
  - "6.4 Portföy ve Bakiye Takibi"
  - "6.5 İşlem Geçmişi (Transaction History)"
  - "6.6 Komisyon, Vergi ve Kur Etkisi"
  - "6.7 Endeks Kıyaslaması (Benchmark)"
  - "6.8 Sanal Portföy (Paper Trading) Modu"
  - "9. Veritabanı Şeması (user_account, wallets, cash_flows, portfolio, open_positions, fx_rates)"
---

## Özet
Faz 3 Batch 2 kapsamında **7 yeni modül** `app/portfolio/` altına eklendi:
`account`, `wallets`, `cash_flows`, `positions`, `pnl`, `benchmark`,
`paper_trading`. Bu paket havuz×vade cüzdan matrisini (6 cüzdan),
para giriş/çıkış audit'ini, weighted-average cost ile BUY/SELL akışını,
komisyon/vergi/kur ayrıştırmalı P&L'i, TWR + endeks kıyaslamasını ve
"botu dinleseydin" sanal portföy modunu kapsar. **DB şema değişikliği
yok** — tüm tablolar Faz 1 `0001_initial_schema.py` üzerinde mevcut.

## Detaylar

### 1. `app/portfolio/account.py` — AccountService
Tek-kullanıcı modeli: `get_or_create` ilk `user_account` satırını
idempotent yaratır (initial_balance=15000 TL default).

| Metod | Davranış |
|---|---|
| `get_or_create(initial_balance, currency)` | İlk kayıt varsa onun id'sini döndürür; yoksa `cash_balance=initial_balance` ile yeni kayıt. |
| `get_snapshot(account_id)` | `AccountSnapshot` — `total_value = account.cash + Σ wallet.cash`. Pozisyon değerleri **dahil değil**; caller `WalletService.compute_snapshot` ile ekler (döngüsel import önlendi). |
| `set_trading_mode(account_id, mode)` | CHECK constraint kümesi: `{manual_parallel, semi_auto, full_auto, paper}`. Geçersizse `ValueError`. |
| `set_risk_threshold(account_id, threshold)` | 0..100 aralığı doğrulaması. |

Sabitler: `VALID_TRADING_MODES`, `MIN_RISK_THRESHOLD`, `MAX_RISK_THRESHOLD`.

### 2. `app/portfolio/wallets.py` — WalletService (**6 cüzdan matrisi**)

`POOLS = ('bot', 'user')` × `TIMEFRAMES = ('short', 'mid', 'long')` =
6 bağımsız cüzdan. Her cüzdan ayrı `allocated` + `cash_balance`
+ pozisyonlar tutar.

| Metod | Davranış |
|---|---|
| `init_matrix(account_id)` | 6 hücreyi **idempotent** yarat (mevcut hücreler dokunulmaz, eksikler eklenir). Döner: `{(pool, tf): wallet_id}`. |
| `list_wallets(account_id)` | `WalletSnapshot` listesi — `positions_value=0` sabit (hızlı meta görünüm). |
| `get_wallet(account_id, pool, tf)` | UNIQUE `(account_id, pool, timeframe)` üzerinden lookup; yoksa `LookupError` ("önce init_matrix çağırın"). |
| `allocate(wallet_id, amount)` | `account.cash -= amount`; `wallet.allocated += amount`; `wallet.cash_balance += amount`. `CashFlow(flow_type='allocate', wallet_id=hedef)` INSERT. |
| `reallocate(from_wid, to_wid, amount)` | Atomik tek-transaction: kaynak `allocated`/`cash` -=; hedef +=. İki `CashFlow` satırı: kaynak için `amount=-X`, hedef için `amount=+X`, `flow_type='reallocate'`. Cross-account reddedilir. |
| `compute_snapshot(wallet_id, current_prices)` | `WalletSnapshot` — `positions_value`, `pnl_unrealized` (current_price × qty − qty × avg_cost), `pnl_realized` (Σ `portfolio.realized_pnl SELL`), `pnl_total`. `return_pct_twr=0` (BenchmarkService delege). |

**WalletSnapshot:**
```
id, pool, timeframe, allocated, cash_balance, positions_value,
total_value (= cash + positions_value), pnl_realized, pnl_unrealized,
pnl_total, return_pct_twr
```

### 3. `app/portfolio/cash_flows.py` — CashFlowService

Audit zorunluluğu: kayıtlar **silinmez** (model cascade yok).

| Metod | Davranış |
|---|---|
| `deposit(account_id, amount, notes)` | `account.cash += amount`; `CashFlow(flow_type='deposit', amount=+, wallet_id=NULL)`. |
| `withdrawal(account_id, amount, notes)` | `cash >= amount` zorunlu; `CashFlow(flow_type='withdrawal', amount=-, wallet_id=NULL)` (negatif kaydedilir → `net_deposited` toplam tek sorguda hesaplanır). |
| `list_flows(account_id, wallet_id?, limit)` | `occurred_at DESC` sıralı dict listesi: `id, account_id, wallet_id, flow_type, amount (Decimal), occurred_at, notes`. |
| `net_deposited(account_id, wallet_id?)` | `wallet_id=None` → `Σ deposit + Σ withdrawal` (withdrawal negatif). `wallet_id` verilirse → `Σ allocate + Σ reallocate` (cüzdana "kullanıcı tarafından koyulan net sermaye"). |

Bilinen `flow_type` kümesi (`KNOWN_FLOW_TYPES`):
`deposit, withdrawal, allocate, reallocate, trade_buy, trade_sell, commission, fee, dividend`.

**Önemli:** `allocate` ve `reallocate` kayıtlarını **bu modül yazmaz** —
`WalletService` yazar (tek transaction'da atomik olmalı).

### 4. `app/portfolio/positions.py` — PositionService

**Cost basis: weighted-average (avg cost).** BUY sonrası:
```
new_avg = (old_qty * old_avg + new_qty * new_price) / (old_qty + new_qty)
```
SELL sonrası avg_cost değişmez (kalan adetler için aynı maliyet).

| Metod | Davranış |
|---|---|
| `apply_buy(wallet_id, instr, qty, price, comm, tx_at, followed_bot, notes)` | `cash >= price*qty + comm` zorunlu. `portfolio` INSERT (action='BUY', realized_pnl=NULL). `open_positions` upsert (yoksa yarat, varsa avg_cost güncelle, `opened_at` ilk BUY'da set; sonraki BUY'larda korunur). `wallet.cash -= total_cost`. `CashFlow(flow_type='trade_buy', amount=-)`. Döner: `portfolio_id`. |
| `apply_sell(wallet_id, instr, qty, price, comm, tx_at, followed_bot, notes)` | `pos.quantity >= qty` zorunlu. `realized_pnl = (price - avg_cost)*qty - commission`. `hold_days = (tx_at - opened_at).days`. `portfolio` INSERT. `open_positions`: qty -=; **0'a düşerse SİL** (DB satırı kalmaz; tekrar BUY yapılırsa `opened_at` o günden başlar). `wallet.cash += (gross - comm)`. `CashFlow(flow_type='trade_sell', amount=+)`. Döner: `(portfolio_id, realized_pnl)`. |
| `list_open_positions(wallet_id, current_prices?)` | Cüzdan bazında `PositionView` listesi. |
| `list_all_open_positions(account_id, current_prices?)` | Hesabın **tüm cüzdanlarındaki** pozisyonlar (Wallet JOIN). |

**PositionView:**
```
instrument_id, ticker, quantity, avg_cost,
current_price?, market_value?, pnl_unrealized?, pnl_pct?,
weight_pct? (cüzdandaki ağırlık),
sector?
```

`weight_pct` cüzdandaki **toplam piyasa değerine** göre hesaplanır
(nakit hariç). `current_prices` eksikse ilgili alanlar None.

### 5. `app/portfolio/pnl.py` — PnLCalculator (**komisyon/vergi/kur**)

**Komisyon oranı:** `.env` `DEFAULT_COMMISSION_PCT` yüzde cinsinden (örn.
`0.2`); modül içinde `/100` ile **orana** çevrilir → `Decimal('0.002')`.
Sabit: `DEFAULT_COMMISSION_PCT: Decimal`.

**Vergi (indikatif, mali müşavir uyarısı zorunlu):**
- `BIST_TAX_PCT = 0.0` — yerli bireysel BIST hisse sermaye kazancı placeholder.
- `US_DIVIDEND_TAX_PCT = 0.15` — ABD temettü stopaj örneği.
- Sabit: `TAX_DISCLAIMER` — UI tooltip metni.

**Kur etkisi formülü (önemli):**

ABD hissesi (currency='USD'):
```
instrument_pnl_local = (sell_price - buy_price) * qty               [USD]
instrument_pnl_try   = instrument_pnl_local * fx_at_sell            [TRY]
fx_pnl               = (fx_at_sell - fx_at_buy) * buy_price * qty   [TRY]
total_pnl_try        = instrument_pnl_try + fx_pnl
```

TRY hissesi (BIST):
```
instrument_pnl_local = realized_pnl (TRY)
fx_pnl               = 0
total_pnl_try        = instrument_pnl_local
```

`get_fx_rate(pair='USDTRY', at?)` — `fx_rates` tablosundan `timestamp <= at`
filtresiyle en güncel. Yoksa **fallback 1.0** ve log uyarısı.

| Metod | Davranış |
|---|---|
| `get_fx_rate(pair, at?)` | Yukarıdaki açıklama. |
| `closed_trade_pnl(portfolio_id)` | Tek SELL satırı için detay kırılım. Buy referansı: aynı wallet+instrument'taki en yakın önceki BUY (`transaction_at <= sell_time`). |
| `position_pnl(wallet_id, instr, current_price, current_fx?)` | Açık pozisyonun unrealized kırılımı. Tahmini çıkış komisyonu: `DEFAULT_COMMISSION_PCT * qty * current_price`. |
| `wallet_total_pnl(wallet_id, current_prices, current_fx?)` | Realized (Σ portfolio.realized_pnl SELL) + unrealized (Σ position_pnl). Sade kabul: realized fx_pnl burada hesaplanmaz (per-trade çağrı maliyeti); detay isteyen UI `closed_trade_pnl` döngüsel çağırır. |

**PnLBreakdown:**
```
instrument_pnl_local, fx_pnl, total_pnl_try,
commission_total, tax_estimate, net_pnl_try
```

### 6. `app/portfolio/benchmark.py` — BenchmarkService

**TWR formülü** (Time-Weighted Return; para hareketleri getiriyi şişirmez):

Sade ilk faz uygulaması (daily_value tablosu yok):
```
V_start ≈ V_end - net_external
TWR     = (V_end - net_external) / V_start - 1
```
- `V_end` = `account.cash + Σ wallet.cash + Σ qty * avg_cost`
  (avg_cost bazlı valuation — günlük close yok).
- `net_external` = `Σ cash_flows.amount` (hesap için deposit+withdrawal;
  wallet için allocate+reallocate).

Doğru çok-dönem TWR (her cash flow zamanında V_t snapshot + geometrik
çarpım) için **Faz 4'te `daily_portfolio_value` tablosu** önerilir;
`BenchmarkService.time_weighted_return` o tablo geldiğinde içerden
delege edebilir (API imzası aynı kalır).

| Metod | Davranış |
|---|---|
| `time_weighted_return(account_id, wallet_id?, start?, end?)` | Yukarıdaki sade formül; yüzde döner (Decimal). `wallet_id=None` → hesap geneli; verilirse o cüzdan için allocate+reallocate net'i baz alınır. |
| `benchmark_return(ticker, start, end)` | `price_history` üzerinden basit close-to-close getiri. Verified close varsa onu kullanır. Veri yoksa 0 + log warning. |
| `compare(account_id, ticker, wallet_id?, start?, end?)` | `BenchmarkComparison` — portfolio_return, benchmark_return, alpha, beat_benchmark. `start=None` → `account.created_at`. `end=None` → bugün. |

Sabitler: `BIST_BENCHMARK='XU100'`, `SP500_BENCHMARK='^GSPC'`.

### 7. `app/portfolio/paper_trading.py` — PaperTradingService

**Aynı kod yolu prensibi:** `execute_paper_trade` → `PositionService.apply_buy/sell`.
Fark: `account.trading_mode == 'paper'` flag'i; trading-executor mod
kontrolü yapar ve paper modda gerçek emir göndermez.

| Metod | Davranış |
|---|---|
| `reset_paper_account(account_id, initial=100000)` | Tüm `portfolio`, `open_positions`, `cash_flows` satırlarını SİL; cüzdanların `allocated`/`cash_balance` 0'la; `account.cash = initial_balance`; `trading_mode='paper'`. **Audit silici** — yalnız paper kullanım için. |
| `execute_paper_trade(wallet_id, instr, action, qty, price)` | Mod kontrolü (`paper` değilse `ValueError`). Komisyon: `DEFAULT_COMMISSION_PCT * gross`. `transaction_at=now UTC`. `PositionService`'e delege. |
| `simulate_bot_picks(account_id, since, wallet_id)` | `bot_picks.picked_at >= since` filtreli pick'leri sırayla uygula: BUY pick → `apply_buy(price_at_pick)`; kapalıysa `apply_sell(price_at_close)`. Sizing: hesap nakdinin %5'i (basit equal-weight). Dön: `{picks_total, buys_executed, sells_executed, hit_target, expired, skipped, final_cash_balance, realized_pnl_total}`. |

Sabitler: `DEFAULT_PAPER_BALANCE = Decimal('100000')`, `PAPER_MODE = 'paper'`.

### 8. `app/portfolio/__init__.py` güncellemesi
Tüm yeni servisler + dataclass'lar + sabitler export edildi. Faz 2
ihraçları (`WatchlistService`, `BotPicksService` vb.) korundu. Modül
docstring'inde Faz 2 vs. Faz 3 Batch 2 ayrımı belgelendi.

## Cüzdan Matrisi Mantığı (özet)

```
account (1 satır)
  ├── cash_balance        ← dağıtılmamış serbest nakit (deposit/withdrawal hedefi)
  └── wallets (6 satır)
       ├── (bot,  short) → allocated, cash_balance, positions...
       ├── (bot,  mid)   →
       ├── (bot,  long)  →
       ├── (user, short) →
       ├── (user, mid)   →
       └── (user, long)  →

Para akışı:
  deposit       → account.cash ↑       (CashFlow wallet=NULL)
  allocate      → account.cash ↓, wallet.cash ↑  (CashFlow wallet=hedef)
  reallocate    → wallet_A.cash ↓, wallet_B.cash ↑ (iki CashFlow satırı)
  trade_buy     → wallet.cash ↓ (gross+comm)      (CashFlow trade_buy)
  trade_sell    → wallet.cash ↑ (gross-comm)      (CashFlow trade_sell)
  withdrawal    → account.cash ↓       (CashFlow wallet=NULL)
```

## TWR Formülü (detay)

Tam doğru çok-dönemli TWR:
```
Periyod [t0, t1, t2, ..., tN]
  t_i: cash flow zamanları (deposit/withdrawal/allocate/reallocate)

Her alt-dönem [t_{i-1}, t_i]:
  R_i = (V(t_i) - C_i) / V(t_{i-1}) - 1
    V(t)  : t anında portföy değeri (cash + positions market value)
    C_i   : alt-dönem sonunda gerçekleşen net cash flow (giriş +, çıkış -)

TWR = Π (1 + R_i) - 1
```

Faz 3 Batch 2'de implementasyon sade tek-dönem yaklaşımıdır
(`V_start ≈ V_end - net_external`) çünkü günlük portföy değeri
snapshot tablosu yok. Faz 4'te `daily_portfolio_value` tablosu
eklendiğinde formül tam-doğru hale getirilecek; API imzası
değişmeyecek.

## Kur Etkisi Ayrımı (örnek)

100 AAPL @ 150 USD alındı (kur 30 TRY/USD). 1 yıl sonra 100 AAPL @
180 USD satıldı (kur 34 TRY/USD), 5 USD komisyon.

```
realized_local = (180 - 150) * 100 - 5  = 2995 USD
instrument_pnl_try = 2995 * 34          = 101 830 TRY
fx_pnl = (34 - 30) * 150 * 100          = 60 000 TRY
total_pnl_try = 101 830 + 60 000        = 161 830 TRY
```

Kullanıcıya UI'da iki satır gösterilir:
- "Hisse getirisi (USD bazlı): +2995 USD ≈ +101 830 TRY"
- "Kur etkisi: +60 000 TRY"
- "Toplam TL bazlı: +161 830 TRY"

Bu ayrım olmadan kullanıcı "120 000 kazandım" sanır; aslında 60 000
TRY'si kur kazancı. Doküman §6.6'nın gereği.

## Paper Trading Kullanımı

```python
account_svc = AccountService(session_factory)
wallet_svc  = WalletService(session_factory)
pos_svc     = PositionService(session_factory)
paper       = PaperTradingService(session_factory, pos_svc)

# 1) Hesabı paper moduna al + sıfırla
acc_id = await account_svc.get_or_create()
await account_svc.set_trading_mode(acc_id, 'paper')
await paper.reset_paper_account(acc_id, initial_balance=Decimal('100000'))

# 2) Cüzdan matrisini yarat ve bot-uzun cüzdanına 10 000 TL allocate et
matrix = await wallet_svc.init_matrix(acc_id)
bot_long_id = matrix[('bot', 'long')]
await wallet_svc.allocate(bot_long_id, Decimal('10000'))

# 3) Tek bir paper trade
await paper.execute_paper_trade(bot_long_id, instr_id=42,
                                action='BUY', quantity=Decimal('10'),
                                price=Decimal('250'))

# 4) "Botu dinleseydin" simülasyonu
stats = await paper.simulate_bot_picks(
    account_id=acc_id, since=date(2026, 1, 1), wallet_id=bot_long_id
)
print(stats)
# {'picks_total': 18, 'buys_executed': 14, 'sells_executed': 9,
#  'hit_target': 6, 'expired': 3, 'skipped': 4,
#  'final_cash_balance': Decimal('11340.55'),
#  'realized_pnl_total':  Decimal('1450.20')}
```

## Gerekçe

Faz 3 Batch 2 görev kapsamı: cüzdan çekirdeği + P&L motoru + benchmark
+ paper trading. Bu paket olmadan `trading-executor` Faz 3'te emir
göndermek için cüzdan/pozisyon mutasyon API'sini bulamaz; `ui-developer`
Faz 3 Batch 4'te `portfolio_widget`, `budget_widget`, `history_widget`
ekranlarını dolduramaz. Bu yüzden 7 modül **tek batch** halinde
yazıldı — ara bağımlılıklar (örn. `WalletService` ↔ `CashFlowService` ↔
`PositionService`) tek seferde tutarlı tasarlanabilsin diye.

## Test / Doğrulama

- Runtime test sandbox dışında; AST/SQL doğruluğu elle gözden geçirildi.
- DB CHECK constraint'leriyle uyum: `trading_mode`, `pool`, `timeframe`,
  `action` enum'ları kaynak kodda doğrulanıyor.
- Decimal/float dönüşümü her yerde `_to_decimal` helper'ı ile;
  hiçbir yerde float aritmetik yok.

**test-engineer için kapsamlı test ipuçları:**

### `tests/test_account.py`
- `get_or_create` 2 kez çağrıldığında aynı id (idempotent).
- `set_trading_mode('invalid')` → `ValueError`.
- `set_risk_threshold(101)` → `ValueError`.
- `set_risk_threshold(50)` sonrası DB değeri 50.
- `get_snapshot` döndürdüğü `total_value == account.cash + Σ wallet.cash`.

### `tests/test_wallets.py`
- `init_matrix` 2 kez → 6 cüzdan, hiç duplicate yok.
- `init_matrix` mevcut bazı cüzdanlar varken → eksikleri ekler,
  varolanları dokunmaz.
- `get_wallet('bot', 'short')` → wallet_id.
- `get_wallet('invalid', ...)` → `ValueError`.
- `allocate`: account.cash 100 iken `allocate(wid, 30)` → account.cash=70,
  wallet.allocated=30, wallet.cash_balance=30, CashFlow satırı 1 adet.
- `allocate(wid, 200)` cash 100'den → `ValueError`.
- `reallocate(A, B, 20)`: A.cash -= 20, B.cash += 20, 2 cash_flow
  satırı (biri negatif biri pozitif), toplamı 0.
- `reallocate(A, A, 10)` → `ValueError`.
- Cross-account reallocate → `ValueError`.
- `compute_snapshot` 0 pozisyon → positions_value=0, pnl_unrealized=0.
- `compute_snapshot` 10 adet @ avg_cost=50, current=60 →
  positions_value=600, pnl_unrealized=100.

### `tests/test_cash_flows.py`
- `deposit(100)` → account.cash += 100, CashFlow(amount=100).
- `withdrawal(50)` → account.cash -= 50, CashFlow(amount=-50).
- `withdrawal(amount > cash)` → `ValueError`.
- `net_deposited` hesap için: 100 deposit + 30 withdrawal → 70.
- `net_deposited(wallet_id)`: allocate 50 + reallocate-in 20 = 70.
- `list_flows(limit=5)` → en yeni 5 kayıt, `occurred_at DESC`.

### `tests/test_positions.py`
- `apply_buy(qty=10, price=100, comm=2)` → wallet.cash -= 1002,
  open_positions oluşur (qty=10, avg_cost=100), portfolio satırı INSERT.
- İkinci BUY (qty=5, price=120, comm=1) → avg_cost = (10*100 + 5*120)
  / 15 = 106.6667. opened_at değişmez.
- `apply_buy` yetersiz cash → `ValueError`.
- `apply_sell(qty=5, price=130, comm=1)`:
  realized = (130 - 106.6667) * 5 - 1 = 115.66; portfolio satırı
  (action='SELL', realized_pnl≈115.66, hold_days dolu); pos.qty = 10.
- `apply_sell(qty=10)` → pos satırı SİLİNİR (qty=0).
- `apply_sell` yetersiz qty → `ValueError`.
- `list_open_positions(wallet_id)` current_prices ile → weight_pct
  toplam 100 (yuvarlama hariç).
- `list_all_open_positions(account_id)` → tüm cüzdanlar.

### `tests/test_pnl.py`
- `get_fx_rate('USDTRY')` yoksa → 1.0.
- `get_fx_rate('USDTRY', at=eski_tarih)` → o tarihten önceki en son.
- `closed_trade_pnl` TL hissesi → fx_pnl=0, total = realized.
- `closed_trade_pnl` USD hissesi: BUY 150 USD @ fx=30, SELL 180 USD @
  fx=34, qty=100, comm=0 → fx_pnl = (34-30)*150*100 = 60000;
  total_pnl_try ≈ realized*34 + 60000.
- `position_pnl` TL hissesi: avg=100, current=120, qty=10 →
  instrument_pnl_local=200, fx_pnl=0, total=200.
- `wallet_total_pnl` toplam = realized + Σ unrealized.

### `tests/test_benchmark.py`
- `benchmark_return` veri yoksa → 0 + warning.
- `benchmark_return` BIST100 başlangıç 8000 son 10000 → 25.0.
- `time_weighted_return` deposit 1000 + (positions değer 1200) →
  net_external=1000, V_end=1200, V_start=200; TWR = (1200-1000)/200 - 1
  = 0 → 0.0%. (Sade tek-dönem yaklaşımının doğru sınırı.)
- `compare(account, 'XU100')` → BenchmarkComparison; alpha = portfolio - bench.

### `tests/test_paper_trading.py`
- `reset_paper_account` 2 BUY sonrası: portfolio 0 satır, open_positions
  0, cash_flows 0, all wallets cash=0, account.cash=100000,
  trading_mode='paper'.
- `execute_paper_trade` mode='manual_parallel' iken → `ValueError`.
- `execute_paper_trade('BUY', 10, 100)` → apply_buy delege; comm=100*10*0.002=2.
- `simulate_bot_picks` 3 pick (1 hit, 1 expired, 1 open) →
  buys_executed=3, sells_executed=2, hit_target=1, expired=1.

**Fixture sözleşmesi:** `session_factory` Faz 2 testlerindeki ile aynı
(sync SQLAlchemy session). In-memory SQLite + `Base.metadata.create_all`
ile kurulum yeterli; PostgreSQL CHECK constraint testleri için
`pytest.mark.postgres` ile ayrı modül düşünülebilir.

## Notlar

### Tüketici agent'lar için duyurular

- **database-architect** (bilgi):
  - Faz 3 Batch 2'de DB şema değişikliği **YOK**. Tüm sorgular mevcut
    `0001_initial_schema.py` üzerinde çalışır.
  - Faz 4 için öneri: `daily_portfolio_value` tablosu (TWR doğruluğu)
    ve `cash_flows(account_id, occurred_at DESC)` indeksi eklenebilir
    (büyük ölçekte TWR sorgu performansı).

- **analysis-engine**:
  - Bu paketin doğrudan tüketicisi değil; ancak `recommender` çıktısı
    `BotPicksService.add_pick` → ileride paper trading veya gerçek
    trading-executor zincirine girer. Pipeline tasarımında bu zincir
    orchestrator tarafından kurulmalı.

- **data-collector**:
  - `PnLCalculator.get_fx_rate` `fx_rates` tablosundan okur — TCMB
    USDTRY scraper'ı bu tabloyu doldurmalı (mevcut, Faz 2'de eklendi).
  - `BenchmarkService.benchmark_return` `price_history` üzerinden
    `XU100` ve `^GSPC` ticker'larını arar; bu instrument'ların seed
    edilmiş olması ve günlük close fiyatlarının doldurulması gerekir
    (yoksa 0 + warning döner — degraded mod).

- **trading-executor**:
  - **`AccountService.set_trading_mode` tek geçerli giriş noktasıdır.**
    Emir göndermeden önce moda göre dallanma yap: `paper` → hiç gerçek
    emir, `PaperTradingService.execute_paper_trade` üzerinden simüle;
    `manual_parallel|semi_auto|full_auto` → gerçek emir + dolduktan
    sonra `PositionService.apply_buy/sell`.
  - `apply_buy/sell` çağrılarında `followed_bot` flag'i: bot picks'den
    geliyorsa `True`, kullanıcı manuel ise `False`. UI satır rozeti
    için kritik.
  - Komisyon `.env DEFAULT_COMMISSION_PCT` üzerinden tek kaynak;
    trading-executor da aynı sabiti `app.portfolio.pnl.DEFAULT_COMMISSION_PCT`
    import ederek tutarlı kalmalı.

- **ui-developer** (Faz 3 Batch 4):
  - `portfolio_widget.py`:
    - Üstte `AccountService.get_snapshot()` özet kartı.
    - 6 hücreli grid: `WalletService.list_wallets()` (allocated/cash/
      pnl gösterimi); detay sekmesinde `compute_snapshot(wid, prices)`.
    - Açık pozisyon tablosu: `PositionService.list_all_open_positions()`;
      sektör dağılım pasta grafiği `PositionView.sector` üzerinden.
    - P&L kırılımı bileşeni: `PnLCalculator.wallet_total_pnl()`;
      "Hisse getirisi" + "Kur etkisi" + "Toplam TL" + "Tahmini vergi"
      4 satır; vergi satırına `TAX_DISCLAIMER` tooltip.
  - `budget_widget.py`:
    - `WalletService.allocate/reallocate` formları.
    - `CashFlowService.deposit/withdrawal` formları.
    - 6 cüzdanın slider/input ile budgeting görünümü.
  - `history_widget.py`:
    - `CashFlowService.list_flows()` zaman çizgisi tablosu, `flow_type`
      rozeti ile renkli (deposit yeşil, withdrawal kırmızı, allocate
      mavi, reallocate turuncu, trade_* gri).
    - Portfolio (BUY/SELL) işlemleri için ayrı select gerekirse basit
      `select(Portfolio).order_by(transaction_at.desc())` — bu modül
      ayrı `list_transactions` helper'ı sunmuyor (gerekirse eklenir).
  - `benchmark_widget.py`:
    - `BenchmarkService.compare(account, 'XU100')` ve `compare(..., '^GSPC')`;
      iki çizgi grafik (portföy değeri vs. endeks normalize 100'e).
    - `beat_benchmark=True` yeşil rozet, `False` kırmızı.
  - `paper_trading_widget.py`:
    - "Paper Mode" toggle → `AccountService.set_trading_mode('paper')`.
    - "Reset" butonu → `PaperTradingService.reset_paper_account()`
      onay dialogu ile (DESTRUCTIVE).
    - "Botu Dinleseydin" panel: `simulate_bot_picks(since=tarih,
      wallet_id=bot-uzun)` çıktısı (final_cash, realized_pnl,
      hit_target / expired sayıları).

- **test-engineer**:
  - Yukarıdaki "Test / Doğrulama" bölümünde her modül için fixture +
    case listesi var.
  - `PnLCalculator` testleri için `fx_rates` tablosunda en az 2
    timestamp ile USDTRY seed gerekli.
  - `BenchmarkService.time_weighted_return` sade formülünün sınır
    durumlarını test et: `v_start <= 0` → 0.0 + warning.
  - `PaperTradingService.reset_paper_account` DESTRUCTIVE — test
    sonrası izolasyon için her testte fresh DB.

### Bilinen sınırlamalar
- **TWR sade tek-dönem**: gerçek geometrik zincirleme için günlük
  portföy değeri snapshot tablosu (`daily_portfolio_value`) gerekir;
  Faz 4'te eklenirse `BenchmarkService.time_weighted_return` o
  tabloyu kullanacak — API imzası değişmeyecek.
- **`compute_snapshot` valuation**: `positions_value` `current_prices`
  haritasındaki fiyatları kullanır; eksik enstrümanlar için
  `qty * avg_cost` (yani unrealized=0) varsayılır. UI eksik fiyatı
  "—" placeholder ile göstermeli.
- **`wallet_total_pnl` realized fx_pnl atlanmış**: per-trade hesabı
  uzun zincirde maliyetli; detay için UI her SELL satırında
  `closed_trade_pnl` çağırır. Toplam kart için bu sade kabul yeterli.
- **`simulate_bot_picks` sizing**: hesap nakdinin %5'i sabit. Daha
  sofistike (Kelly, volatilite ölçekli) sizing Faz 4 işidir.
- **Cost basis tek mod**: weighted-average. FIFO'ya geçmek için
  `cost_basis_method` parametresi eklenir; şimdilik gerek yok.
- **Tek-kullanıcı varsayımı**: `AccountService.get_or_create` ilk
  satırı tek-kullanıcı kabul eder. Çoklu kullanıcı için API
  genişletilmeli (auth katmanı geldiğinde).
- **Vergi hesapları indikatif**: `BIST_TAX_PCT=0`, `US_DIVIDEND_TAX_PCT=0.15`
  placeholder; gerçek hesap kullanıcının mali müşavirine bırakılır
  (`TAX_DISCLAIMER` UI'da gösterilmeli).
- **Bağımlılık değişikliği YOK**: yalnız mevcut `sqlalchemy` ve
  `loguru`. `pandas/numpy` bu batch'te gerekmedi (skaler hesaplar
  Decimal ile yapıldı; histori analizi Faz 4 işi).
