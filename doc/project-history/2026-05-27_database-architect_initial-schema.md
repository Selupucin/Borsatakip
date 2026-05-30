---
date: 2026-05-27
agent: database-architect
phase: faz-1
type: feature
related_files:
  - app/__init__.py
  - app/db/__init__.py
  - app/db/models.py
  - app/db/session.py
  - app/db/migrations/__init__.py
  - app/db/migrations/env.py
  - app/db/migrations/script.py.mako
  - app/db/migrations/versions/0001_initial_schema.py
  - alembic.ini
  - scripts/setup_db.py
  - scripts/seed_instruments.py
related_doc_sections:
  - "9. Veritabanı Şeması"
  - "10. Klasör Yapısı"
  - "3.3 Veritabanı"
---

## Özet
Borsa Bot Faz 1 veritabanı katmanı sıfırdan kuruldu: 19 tablonun SQLAlchemy 2.0
modelleri, sync + async session yönetimi, Alembic ortamı (revision `0001`), ilk
şema migration'ı ve `data_sources` + BIST/ABD hisse seed script'leri yazıldı.

## Detaylar

### Modeller — `app/db/models.py`
SQLAlchemy 2.0 `Mapped[]` + `mapped_column(...)` syntax; ortak `Base(DeclarativeBase)`.
Tüm 19 tablo:

| # | Tablo | Ana kolonlar |
|---|---|---|
| 1 | `instruments` | id, ticker (UNIQUE), name, exchange, sector, currency, created_at |
| 2 | `price_history` | id, instrument_id, source, timestamp, OHLC NUMERIC(18,4), volume, is_verified, verified_close |
| 3 | `data_sources` | id, name (UNIQUE), reliability_score, total_requests, failed_requests, last_success, is_active |
| 4 | `discrepancies` | id, instrument_id, timestamp, source_a/b, price_a/b, diff_pct, alert_sent |
| 5 | `technical_signals` | id, instrument_id, source, timestamp, rsi, macd, macd_signal, bb_upper/lower, ema_20/50/200, tv_summary, inv_summary |
| 6 | `news_feed` | id, instrument_id, source, title, url, published_at, language, sentiment, sentiment_score, processed_at |
| 7 | `recommendations` | id, instrument_id, generated_at, action, timeframe, confidence, risk_level, risk_score, target_price, stop_loss, take_profit, summary, tech/sentiment/fundamental_score |
| 8 | `backtest_results` | id, strategy_name, timeframe, start_date, end_date, total_return, sharpe_ratio, max_drawdown, win_rate, avg_hold_days, params JSONB, run_at |
| 9 | `user_account` | id, trading_mode, risk_threshold, initial_balance, cash_balance, currency, created_at |
| 10 | `wallets` | id, account_id, pool, timeframe, allocated, cash_balance |
| 11 | `cash_flows` | id, account_id, wallet_id, flow_type, amount, occurred_at, notes |
| 12 | `portfolio` | id, wallet_id, instrument_id, action, quantity, price, total, commission, realized_pnl, hold_days, followed_bot, transaction_at, notes |
| 13 | `open_positions` | id, wallet_id, instrument_id, quantity, avg_cost, opened_at |
| 14 | `watchlists` | id, name, sort_order, created_at |
| 15 | `watchlist_items` | id, watchlist_id (CASCADE), instrument_id, sort_order, added_at |
| 16 | `bot_picks` | id, instrument_id, timeframe, action, confidence, price_at_pick, target_price, picked_at, is_open, closed_at, price_at_close, outcome, return_pct |
| 17 | `alerts` | id, instrument_id, alert_type, threshold, is_active, triggered_at, created_at |
| 18 | `settings` | key (PK VARCHAR), value (TEXT) |
| 19 | `fx_rates` | id, pair, rate NUMERIC(18,6), timestamp, source |

### UNIQUE constraint'ler
- `instruments.ticker` — `uq_instruments_ticker`
- `price_history(instrument_id, source, timestamp)` — `uq_price_history_instr_src_ts`
- `data_sources.name` — `uq_data_sources_name`
- `wallets(account_id, pool, timeframe)` — `uq_wallets_account_pool_timeframe`
- `open_positions(wallet_id, instrument_id)` — `uq_open_positions_wallet_instrument`
- `watchlist_items(watchlist_id, instrument_id)` — `uq_watchlist_items_wl_instr`
- `fx_rates(pair, timestamp)` — `uq_fx_rates_pair_ts`

### CHECK constraint'ler
- `recommendations.action IN ('BUY','HOLD','SELL')` — `ck_recommendations_action`
- `recommendations.timeframe IN ('short','mid','long')` — `ck_recommendations_timeframe`
- `wallets.pool IN ('bot','user')` — `ck_wallets_pool`
- `wallets.timeframe IN ('short','mid','long')` — `ck_wallets_timeframe`
- `portfolio.action IN ('BUY','SELL')` — `ck_portfolio_action`
- `user_account.trading_mode IN ('manual_parallel','semi_auto','full_auto','paper')` — `ck_user_account_trading_mode`

### İndeksler
- `ix_price_history_instr_ts_desc` ON `price_history (instrument_id, timestamp DESC)`
- `ix_news_feed_instr_published_desc` ON `news_feed (instrument_id, published_at DESC)`
- `ix_recommendations_instr_generated_desc` ON `recommendations (instrument_id, generated_at DESC)`
- `ix_portfolio_wallet_tx_desc` ON `portfolio (wallet_id, transaction_at DESC)`
- `ix_bot_picks_timeframe_picked_desc` ON `bot_picks (timeframe, picked_at DESC)`
- `ix_alerts_instr_active` ON `alerts (instrument_id, is_active)`
- Tüm FK kolonları için ek tek-sütun indeksleri.

### Cascade davranışı
- `watchlist_items.watchlist_id` — `ON DELETE CASCADE` (kullanıcı listesi silinirse içerik de gider).
- `portfolio`, `cash_flows` — cascade YOK (audit log: soft delete tercih edilmeli).

### Tipler
- Tüm para alanları: `NUMERIC(18,4)`. FLOAT kullanılmadı.
- `fx_rates.rate`: `NUMERIC(18,6)`.
- Tüm timestamp'ler: `DateTime(timezone=True)` — PostgreSQL `TIMESTAMPTZ`.
- `processed_at`, `created_at`, `picked_at`, `generated_at`, `run_at`,
  `added_at`, `occurred_at` için `server_default=func.now()`.

### Session — `app/db/session.py`
- Sync engine: `psycopg2`, `pool_size=10`, `max_overflow=20`,
  `pool_pre_ping=True`, `pool_recycle=3600`, `echo=False`.
- Async engine: `asyncpg`, aynı havuz ayarlarıyla.
- `SessionLocal` (sync), `AsyncSessionLocal` (async) — `expire_on_commit=False`.
- `get_db()` ve `get_async_db()` jeneratör/async-jeneratör.
- `init_db()` → `Base.metadata.create_all(sync_engine)` (Alembic alternatifi,
  geliştirme için).
- `app.config.settings` varsa ondan, yoksa `DATABASE_URL` / `DB_*` env'lerden
  URL çözer (devops modülü hazır değilken sessiz fallback).

### Alembic
- `alembic.ini` proje kökünde; `script_location = app/db/migrations`,
  `prepend_sys_path = .`, `timezone = UTC`, post-write hook'lar olarak
  `black` + `ruff` tanımlı.
- `app/db/migrations/env.py` — modelleri import eder,
  `target_metadata = Base.metadata`, URL'i `app.config` ya da env'den alır,
  `compare_type=True`, `compare_server_default=True` ile autogenerate hazır.
- `app/db/migrations/script.py.mako` — standart Alembic şablonu.
- **Alembic revision: `0001`** — `0001_initial_schema.py`, `down_revision: None`.
  `upgrade()` tüm 19 tabloyu, FK'leri, UNIQUE/CHECK constraint'leri ve
  6 adet `... DESC` indeksini (raw SQL ile) kurar. `downgrade()` ters sırada
  drop eder.

### Setup ve seed script'leri
- `scripts/setup_db.py`:
  - `--drop` → Tüm tabloları drop eder, `alembic_version`'u temizler.
  - Şema kurulumu: önce `alembic upgrade head` denenir, başarısız olursa
    `Base.metadata.create_all` fallback.
  - `data_sources` seed: **11 kaynak** idempotent eklenir
    (`yfinance`, `stooq`, `alphavantage`, `isyatirim`, `kap`, `tradingview`,
    `investing`, `finviz`, `tcmb`, `rss`, `reddit`) — hepsi
    `reliability_score=100.0`, `is_active=True`.
  - `--seed` → ek olarak `seed_instruments.py`'yi çağırır.
- `scripts/seed_instruments.py`:
  - **BIST 30 hisse** (`exchange='BIST'`, `currency='TRY'`): THYAO, AKBNK,
    GARAN, ISCTR, YKBNK, ASELS, EREGL, KCHOL, SAHOL, BIMAS, FROTO, TUPRS,
    SISE, KOZAL, KOZAA, PGSUS, TKFEN, TAVHL, ARCLK, VESTL, HALKB, VAKBN,
    MGROS, TCELL, TTKOM, EKGYO, ENKAI, PETKM, KRDMD, HEKTS.
  - **ABD 20 hisse** (`exchange in {NASDAQ,NYSE}`, `currency='USD'`):
    AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, AMD, JPM, BAC, V, MA, JNJ,
    PFE, KO, PEP, WMT, DIS, NFLX, INTC.
  - Idempotent: mevcut ticker'lar tekrar eklenmez.

### Bağımlılıklar
`pyproject.toml` zaten devops tarafından yazılmış; gerekli paketler:
`SQLAlchemy ^2.0`, `alembic ^1.13`, `psycopg2-binary ^2.9`, `asyncpg ^0.29`.

## Gerekçe
Doküman §9 ve §10 birebir uygulandı. SQL CREATE blokları SQLAlchemy 2.0
modellerine taşındı; finansal yuvarlama hatalarını önlemek için tüm para
alanları `NUMERIC(18,4)` (kur `NUMERIC(18,6)`), zaman dilimi sorunlarını
önlemek için tüm timestamp'ler `TIMESTAMPTZ` seçildi. Doküman §6.1'in 6 cüzdan
matrisi `wallets(pool, timeframe)` üzerindeki UNIQUE + CHECK ile DB seviyesinde
garanti altına alındı. Doküman §5.3'teki öneri çıktısı (`action`, `timeframe`,
`risk_score`, `stop_loss`, `take_profit`) `recommendations` tablosunda CHECK ile
korunuyor. `verified_close` ve `data_sources.reliability_score`, §4.2 Veri
Karşılaştırma Motoru için hazır.

## Test / Doğrulama
- Henüz `pytest` tarafı yok; `test-engineer` ileride şu testleri eklemeli:
  - `tests/test_db_models.py` — `init_db()` + `Base.metadata.create_all`
    çalıştırıp tüm tabloların `inspect(engine).get_table_names()` listesinde
    olduğunu doğrulamak.
  - `tests/test_seed.py` — `seed_data_sources()` ve
    `seed_instruments.run()` çıktısı idempotent mi?
  - `tests/test_constraints.py` — `wallets.pool='invalid'` insert ederken
    `IntegrityError` bekle; aynısı `recommendations.action='MAYBE'` için.
- Python yorumlayıcısı bu sandbox'ta mevcut değildi; syntax doğrulaması
  yapılamadı. Devops `setup_dev.py` çalıştıktan sonra ilk komut:
  ```
  alembic upgrade head
  python scripts/setup_db.py --seed
  ```

## Notlar

### Tüketici agent'lar için duyurular

- **data-collector**:
  - `PriceHistory` modelinde `source` zorunlu, `(instrument_id, source, timestamp)`
    UNIQUE — aynı kaynak için aynı timestamp upsert mantığı gerekiyor.
  - `PriceHistory.verified_close` doldurulacak alan; karşılaştırma motoru
    çıktısı buraya yazılmalı, `is_verified=True` set edilmeli.
  - `DataSource.reliability_score`, `total_requests`, `failed_requests`,
    `last_success` — her HTTP cycle sonrası güncellenmeli.
  - `Discrepancy` modeli, kaynaklar arası tutarsızlık eşiği aşıldığında
    loglanır; `alert_sent=False` başlangıçta.
  - `FxRate` modeli TCMB kuru için; `(pair, timestamp)` UNIQUE.
- **analysis-engine**:
  - `TechnicalSignal`, `Recommendation`, `BacktestResult` modelleri hazır.
  - `Recommendation.action` ve `Recommendation.timeframe` CHECK constraint'li —
    geçersiz değer DB tarafından reddedilir, kod tarafında enum/Literal
    kullanmak güvenli.
  - `BacktestResult.params` `JSONB` — strateji ağırlıklarını sözlük olarak
    saklayabilirsiniz.
  - `Recommendation.tech_score`, `sentiment_score`, `fundamental_score`
    şeffaflık için ayrı kolonlar; öneri kartında açıklanabilirlik bunlardan
    üretilir.
- **portfolio-manager**:
  - `Wallet` 6'lı matris (pool × timeframe) UNIQUE+CHECK ile garanti.
  - `Portfolio.followed_bot` BOOLEAN, `realized_pnl` satışta doldurulmalı.
  - `OpenPosition (wallet_id, instrument_id)` UNIQUE; FIFO/ortalama maliyet
    hesabı uygulama tarafında.
  - `CashFlow.flow_type` için ileride CHECK eklemek isteyebilirsin
    (`'deposit','withdrawal','reallocate'`); şu an doküman'a uygun olarak
    serbest bırakıldı.
- **ui-developer**:
  - `Setting (key, value)` ile tema/dil/accent kalıcı saklanır.
  - `Watchlist` silindiğinde `WatchlistItem` cascade ile gider — UI tarafında
    "Listeyi sil" onay diyalogu gösterilmeli.

### Bilinen sınırlamalar / takip işler
- `app.config.settings` henüz hazır değilse env fallback devrede; devops
  modülü tamamlandığında `database_url_sync` / `database_url_async`
  attribute'larının var olduğundan emin olunmalı.
- `CashFlow.flow_type` ve `Alert.alert_type` için CHECK eklenmedi (doküman
  bunları enum olarak listelemiyor); ileride iş kuralı netleşince Alembic
  migration `0002`'de eklenebilir.
- Backward-incompatible değişiklik **YOK** — bu ilk şema.
- `alembic_version` tablosu otomatik oluşur; CI/CD'de `alembic upgrade head`
  zorunludur (devops bunu setup_dev.py'de halletmeli).
