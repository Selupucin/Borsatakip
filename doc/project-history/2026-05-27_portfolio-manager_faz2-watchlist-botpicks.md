---
date: 2026-05-27
agent: portfolio-manager
phase: faz-2
type: feature
related_files:
  - app/portfolio/__init__.py
  - app/portfolio/watchlist.py
  - app/portfolio/bot_picks.py
related_doc_sections:
  - "6.2 Kullanıcı Takip Listesi (Watchlist)"
  - "6.3 Bot Öneri Listesi (Bot Picks)"
  - "5.2 Vade Tanımları (kısa/orta/uzun süre eşikleri)"
  - "9. Veritabanı Şeması (watchlists, watchlist_items, bot_picks)"
---

## Özet
Faz 2 kapsamında **iki yeni servis** `app/portfolio/` altına eklendi:
`WatchlistService` (kullanıcı takip listeleri) ve `BotPicksService` (bot
öneri yaşam döngüsü + başarı oranı istatistikleri). Cüzdan matrisi,
cash_flows, açık pozisyon, P&L, benchmark ve paper trading **bilinçli
olarak Faz 3'e bırakıldı** (görev kapsamı dışı).

## Detaylar

### 1. `app/portfolio/watchlist.py` — WatchlistService

`session_factory: Callable[[], Session]` ile başlatılır (sözleşme
`app/analysis/news_aggregator.py` ile aynı — her çağrıda yeni sync
session). Tüm public metodlar `async`; DB erişimi sync SQLAlchemy 2.0
`select/update/delete` ile.

**Public API:**

| Metod | Davranış |
|---|---|
| `create(name)` | Yeni watchlist; whitespace stripleme + boş ad → `ValueError`. Aynı isimde başka liste olup olmadığı kontrol edilmez. |
| `rename(wl_id, new_name)` | Yoksa `LookupError`. |
| `delete(wl_id)` | `watchlist_items` `ON DELETE CASCADE` ile düşer (model `passive_deletes=True`). Yoksa sessiz geç. |
| `list_all()` | `sort_order ASC, created_at ASC`. `WatchlistSummary` (id, name, items_count, created_at). |
| `add_item(wl_id, ticker)` | Ticker upper'lanır; instrument yoksa `LookupError`. **Idempotent**: zaten varsa mevcut id döndürür (UNIQUE constraint'i ihlal etmez). Yeni sıra: `max(sort_order) + 1`. |
| `remove_item(wl_id, instr_id)` | `delete().where(...)` bulk. Yoksa sessizce geç. |
| `list_items(wl_id, with_quotes=False)` | `sort_order ASC, added_at ASC`. `with_quotes=True` → her instrument için son `price_history` (verified_close varsa onu, yoksa close) ve son `recommendation` join'lenir. **`daily_change_pct` Faz 3'te** (önceki günün close'una ihtiyaç var). |
| `reorder(wl_id, [instr_ids])` | Listenin indeksi → `sort_order` (0-tabanlı). Listede olmayan id'ler rowcount=0 ile sessiz atlanır. |

**Veri yapıları:**
- `WatchlistSummary(id, name, items_count, created_at)`
- `WatchlistItemView(instrument_id, ticker, name, exchange, sort_order,
  added_at, + Faz 3 runtime alanları: current_price: Decimal | None,
  daily_change_pct, volume, bot_action, bot_confidence)`.

**`with_quotes=True` join stratejisi (N+1 önleme):**
Her iki tablo için ayrı `GROUP BY instrument_id` subquery ile her hisse
için yalnız en yeni satır alınır (`MAX(timestamp)` / `MAX(generated_at)`).
Tek sorguda tüm watchlist'in fiyat & öneri haritası bellekte birleştirilir.

### 2. `app/portfolio/bot_picks.py` — BotPicksService

**Vade-expire eşikleri (`EXPIRE_DAYS` sabiti):**

| Timeframe | Gün |
|---|---|
| short | 7   |
| mid   | 90  |
| long  | 365 |

Doküman §5.2 vade tanımlarıyla (1–7 gün / 1–3 ay / 3–12 ay+) **alt
sınırlardan** seçildi — pick'in vade hedefine ulaşamadığı kabul edilmeden
önceki maksimum süre.

**Public API:**

| Metod | Davranış |
|---|---|
| `add_pick(instr_id, timeframe, action, confidence, current_price, target_price)` | `timeframe ∈ {short,mid,long}`, `action ∈ {BUY,HOLD,SELL}` doğrulaması (case normalize). `current_price>0` zorunlu. `is_open=True`, `outcome='open'` default; `picked_at=NOW UTC`. |
| `close_pick(pick_id, close_price, outcome)` | Outcome `'open'` reddedilir. Zaten kapalıysa `ValueError`. `return_pct` hesaplanır (formül aşağıda). |
| `update_open_picks({instr_id: current_price})` | Tüm açık pick'leri tarar; hit_target veya expired olanları kapatır. Döner: kapatılan pick id listesi. **Stop-loss tetiklemesi Faz 3** (risk modülü stop fiyatı üretene kadar). |
| `list_picks(timeframe=None, only_open=False, limit=50)` | `picked_at DESC`. Şeffaflık ilkesi (§6.3): hem açık hem kapalı pick'ler görünür. |
| `get_stats(timeframe)` | `BotPicksStats` — total/open/closed sayıları, outcome dağılımı, success_rate, avg_return_pct, avg_hold_days. |

**Veri yapıları:**
- `BotPickView` — UI satır görünümü (`Decimal` para alanları, `datetime` tz-aware).
- `BotPicksStats(timeframe, total_picks, open_picks, closed_picks,
  hit_target_count, stopped_count, expired_count, success_rate,
  avg_return_pct, avg_hold_days)`.

### 3. `app/portfolio/__init__.py` güncellemesi

İhraçlar: `WatchlistService`, `WatchlistSummary`, `WatchlistItemView`,
`BotPicksService`, `BotPickView`, `BotPicksStats`, `EXPIRE_DAYS`.
Modül docstring'inde Faz 2 vs. Faz 3 modülleri ayrıştırıldı.

## BotPicks formülleri (kritik)

### return_pct

```
base = (close_price - pick_price) / pick_price * 100

return_pct = base       if action == 'BUY'   # long: yükselince +
return_pct = -base      if action == 'SELL'  # short: düşünce +
return_pct = base       if action == 'HOLD'  # bilgi amaçlı
```

`pick_price == 0 / None` → `ValueError` (sıfıra bölme koruması).
Sonuç DB `Numeric(8,4)` ile uyumlu olacak şekilde `0.0001` quantize.

### success_rate

```
success_rate = hit_target_count / closed_picks      (closed > 0)
success_rate = 0.0                                   (closed == 0)
```

Açık pick'ler **paydaya alınmaz** — henüz sonuçlanmadıkları için sayım
adaletsiz olur. Sadece `is_open=False` olan pick'ler değerlendirmeye girer.

### avg_hold_days

`(closed_at - picked_at)` Python tarafında `timedelta.total_seconds() /
86400.0` ile hesaplanır (DB-agnostic; SQLite/PostgreSQL fark etmez).
Tz-naive `picked_at` savunmacı olarak UTC'ye terfi edilir
(`update_open_picks` içinde). Hiç closed pick yoksa `None`.

### avg_return_pct

`SELECT AVG(return_pct) WHERE is_open=False AND return_pct IS NOT NULL` —
`Numeric(8,4)` quantize. Hiç closed yoksa `None`.

### update_open_picks kapatma sırası

1. **hit_target öncelikli**:
   - `action=BUY` ve `current >= target` → `hit_target`
   - `action=SELL` ve `current <= target` → `hit_target`
2. **expired** (yalnız target tetiklenmediyse):
   - `picked_at + EXPIRE_DAYS[tf] < now` → `expired`
   - `close_price`: elimizdeki `current_prices` değeri; yoksa `pick_price`
     (yani `return_pct = 0` — gözlem yokken cezalandırma yapma).
3. **stopped**: Faz 3 (risk modülü stop fiyatı).

`current_prices` haritasında olmayan instrument'lar yalnız expire
kontrolünden geçer; hit_target değerlendirilemez.

## UI için API (ui-developer kullanır)

`bot_picks_widget.py` ve `watchlist_widget.py` için hazır:

- **Watchlist ekranı** (`watchlist_widget.py`):
  - Sol panel: `WatchlistService.list_all()` ile `WatchlistSummary` listesi
    (isim + öğe sayısı). Yeni/yeniden adlandır/sil butonları → `create` /
    `rename` / `delete`.
  - Ana tablo: seçili liste için `list_items(wl_id, with_quotes=True)` →
    `WatchlistItemView` satırları. Sürükle-bırak → `reorder(wl_id,
    [instr_ids])`. Hızlı arama Qt model proxy ile (servis sunmuyor).
  - Hisse ekle dialog: ticker input → `add_item(wl_id, ticker)`;
    `LookupError` → toast "Hisse bulunamadı, önce data-collector ile
    eklenmiş olmalı".

- **Bot Picks ekranı** (`bot_picks_widget.py`):
  - Üst sekmeler: short / mid / long → `list_picks(timeframe=tf, limit=...)`.
  - Her sekmenin üstünde özet kart: `get_stats(tf)` → "Başarı oranı %62,
    ortalama getiri +%4.1, ortalama tutma 4.2 gün" gibi.
  - Tablo satırları: `BotPickView` (ticker, action rozeti, conf %,
    price_at_pick, current/target, picked_at, outcome rozeti, return_pct).
  - "Watchlist'e ekle" sağ-tık menüsü → `WatchlistService.add_item`.

`Decimal` alanlar UI'da `locale.format_string` veya
`QLocale.toString` ile TL/USD formatlanmalı; ham float'a düşürülmemeli
(yuvarlama kaybı).

## Gerekçe

Görev (project-orchestrator) Faz 2 kapsamını "yalnız watchlist + bot
picks" olarak daralttı — cüzdan/cash_flow/P&L mantığı Faz 3'e bırakıldı
çünkü:

1. `analysis-engine` aynı fazda kısa vade recommender çıktısı üretti
   (`save_recommendation`). Bot picks tablosu bu çıktıyı **doğrudan
   takip etmeye hazır olmalı** — Faz 3 risk modülü gelmeden önce
   recommender → bot_picks pipeline'ı kurulmalı (orchestrator'ın
   "öneriyi neye göre kaydedeceğiz" sorusu).
2. Watchlist; UI'ın **ilk gerçek veriyle dolan ekranı** — `ui-developer`
   Faz 2 sonunda watchlist_widget'ı çalıştırabilmeli. CRUD servisinin
   önce gelmesi UI tasarımını bloklamamak için kritik.
3. Cüzdan matrisi 6 hücre × deposit/withdrawal/reallocate + TWR
   formülü + komisyon/vergi/kur ayrıştırması büyük bir paket — kendi
   fazını hak ediyor (Faz 3).

## Test / Doğrulama
- Sandbox'ta Python yorumlayıcısı yok; runtime test koşulamadı.
- AST/import doğruluğu, SQLAlchemy 2.0 `select/update/delete` sözdizimi
  ve mevcut model alan adları (özellikle `BotPick.is_open`,
  `Recommendation.action`, `WatchlistItem.sort_order`) elle gözden
  geçirildi.
- **test-engineer için önerilen testler:**
  - `tests/test_watchlist.py`
    - `create` → id pozitif; `create("   ")` → `ValueError`.
    - `add_item` idempotent: aynı ticker 2 kez → aynı id.
    - `add_item` ticker küçük harf → upper normalize ile bulunmalı.
    - `add_item` mevcut olmayan ticker → `LookupError`.
    - `remove_item` olmayan satır → exception YOK.
    - `reorder([id1, id2])` sonrası `list_items` sırası doğru.
    - `delete(wl_id)` → CASCADE ile items 0 satır.
    - `list_items(with_quotes=True)` fixture: 1 instrument + 2
      price_history + 1 recommendation → en yeni close + son action
      view'a yansır.
  - `tests/test_bot_picks.py`
    - `add_pick` geçersiz timeframe/action → `ValueError`.
    - `close_pick` → `is_open=False`, `return_pct` formülü doğru
      (BUY: pick=100, close=110 → +10.0; SELL: pick=100, close=90 → +10.0).
    - `update_open_picks` hit_target: BUY pick=100, target=120,
      current=125 → kapanır.
    - `update_open_picks` expired: short pick `picked_at=now-8d`,
      current_prices boş → kapanır (`return_pct=0`).
    - `get_stats` boş → `success_rate=0.0`, `avg_*=None`.
    - `get_stats` 3 closed (2 hit, 1 expired) → `success_rate=0.6667`.

## Notlar

### Tüketici agent'lar için duyurular

- **database-architect** (bilgi):
  - Faz 2'de DB şemasında değişiklik **YOK**. Tüm sorgular mevcut
    `0001_initial_schema.py` üzerinde çalışır.
  - Faz 3 cüzdan/cash_flows/positions/pnl çalışmaları başlarken
    `wallets`, `cash_flows`, `open_positions`, `portfolio` tabloları
    için ek indeksler değerlendirilmeli (özellikle
    `cash_flows(account_id, occurred_at DESC)` ve
    `portfolio(wallet_id, transaction_at DESC)` — sonuncusu mevcut).

- **analysis-engine**:
  - `BotPicksService.add_pick(...)` çıktısı `RecommendationOutput`
    alanlarıyla **tam uyumlu** (action/timeframe/confidence/
    target_price/current_price). Recommender pipeline'ı her başarılı
    `save_recommendation` sonrası aynı verilerle `add_pick` çağırabilir
    — orchestrator pipeline tasarımında bu zinciri kurmalı.

- **data-collector**:
  - `update_open_picks` günlük çalıştırılacak background worker'a
    bağlanmalı; `current_prices` haritası en güncel doğrulanmış
    fiyatlardan (verified_close) üretilmeli. Worker zamanlaması Faz 3
    `app/scheduler` (eğer açılırsa) veya basit `asyncio.create_task`
    içinde olabilir.
  - Watchlist'e `add_item` yapıldığında instrument'ın `price_history`
    tablosunda yeterli geçmişi yoksa background fetch tetiklenmeli
    (yeni hisse seed pipeline'ı).

- **ui-developer**:
  - "UI için API" bölümünde watchlist_widget ve bot_picks_widget için
    API kullanım rehberi var.
  - `WatchlistItemView` `daily_change_pct` Faz 2'de `None` döner — UI
    sütununu "—" placeholder ile göstermeli, panik yapmamalı.
  - `BotPicksStats.success_rate` `0..1` aralığında; UI'da `× 100` ile
    yüzde göster.

- **test-engineer**:
  - `session_factory` sözleşmesi `NewsAggregator` ile aynı — mevcut
    fixture'lar (`tests/test_news_aggregator.py`'da olduğu varsayılır)
    tekrar kullanılabilir.
  - In-memory SQLite ile model schema oluşturulabilir
    (`Base.metadata.create_all`) ama PostgreSQL CHECK constraint'lerini
    test etmek için ek `pytest.mark.postgres` fixture'ı düşünülebilir.

### Faz 3 için TODO listesi
- `app/portfolio/account.py` — UserAccount CRUD, trading_mode, risk_threshold.
- `app/portfolio/wallets.py` — 6 hücre (havuz × vade) matrisinin atomik
  yönetimi, `allocated` vs. `cash_balance` ayrımı, transfer atomicliği.
- `app/portfolio/cash_flows.py` — deposit / withdrawal / reallocate;
  reallocate'in iki cüzdan arası tek transaction'da toplam denkliği.
- `app/portfolio/positions.py` — FIFO veya weighted-avg cost; BUY/SELL
  sonrası `open_positions` upsert.
- `app/portfolio/pnl.py` — komisyon (`DEFAULT_COMMISSION_PCT`), kur
  ayrıştırması (`instrument_pnl_local` + `fx_pnl` + `total_pnl_try`),
  vergi/stopaj bilgilendirme.
- `app/portfolio/benchmark.py` — BIST 100 (XU100) / S&P 500 (^GSPC)
  zaman serisi; **time-weighted return** (cash_flows zamanında alt
  dönem böl → geometrik çarp); "endeksi yendi mi" boolean.
- `app/portfolio/paper_trading.py` — `trading_mode='paper'`; aynı kod
  yolu, sanal nakit; "botu dinleseydin" simülasyonu (bot picks → sanal
  pozisyon).
- **BotPicksService** Faz 3 eklemeleri:
  - Stop-loss outcome (`stopped`) — risk modülü stop fiyatı tablosuna
    yazınca tetik.
  - `add_pick` çağrısının `wallet_id` ile bağlanması (hangi cüzdana
    yansıyacak — bot havuzu × vade hücresine otomatik eşleme).
- **WatchlistService** Faz 3 eklemeleri:
  - `WatchlistItemView.daily_change_pct` — önceki günün close'una göre.
  - Watchlist export/import (CSV).

### Bilinen sınırlamalar
- `WatchlistItemView` runtime fiyat alanları `with_quotes=True` ile
  yalnız son fiyat ve son öneri doldurulur; `daily_change_pct` Faz 3'e
  kaldı (bir önceki güne ait close'a göre — basit ama ayrı query).
- `BotPicksService.update_open_picks` `current_prices` haritasında
  olmayan instrument'lar için yalnız expire kontrolü yapar; hedef
  fiyat değerlendirilemez (gözlem yok). Production'da collector worker
  haritayı tam doldurmalı.
- Tüm servisler `session_factory` üzerinden sync session açar ve
  fonksiyon sonunda commit eder — caller transaction kapsamı kontrolü
  yok (Faz 3'te `wallets/cash_flows` reallocate gibi çok adımlı
  operasyonlarda explicit transaction sözleşmesi gerekecek).
- Bağımlılık değişikliği YOK — yalnız mevcut `sqlalchemy` ve `loguru`
  kullanıldı.
