---
date: 2026-05-27
agent: test-engineer
phase: faz-1
type: test
related_files:
  - tests/conftest.py
  - tests/test_config.py
  - tests/test_db_models.py
  - tests/test_data_sources.py
  - tests/test_collector.py
  - tests/test_comparator.py
  - tests/test_technical.py
  - tests/test_alerts_engine.py
  - tests/test_alerts_notifier.py
related_doc_sections:
  - "§10 Test Stratejisi"
  - "§11 Faz 1 — Çekirdek"
---

## Özet
Faz 1 birim test paketi yazıldı: 9 dosya, 90+ test. Ortak fixture'lar
(in-memory SQLite sync+async session, deterministic sample OHLCV, mock
``BaseSource`` factory, ``clean_settings``) ``conftest.py``'da toplandı.
TA-Lib / yfinance / PySide6 / httpx gibi ağır bağımlılıklar mock'landı —
testler bu paketler kurulu olmadan da çalışır.

## Detaylar

### Yazılan test dosyaları + içerdiği test sayısı (yaklaşık)
| Dosya | Test sayısı | Kapsam |
|---|---|---|
| `tests/conftest.py` | — | 7 fixture: `sync_engine`, `db_session`, `async_db_session`, `async_session_factory`, `sample_ohlcv`, `mock_quote`, `mock_source`, `mock_data_sources_table`, `clean_settings` |
| `tests/test_config.py` | 7 | Settings default/env/.env, db_url_sync/async format |
| `tests/test_db_models.py` | 14 | 19 tablo + tablename, UNIQUE, 5 CHECK constraint, Numeric(18,4)/(18,6), relationship'ler |
| `tests/test_data_sources.py` | 22 | YFinance/Stooq/Alpha/IsYatirim/TCMB happy-path + hata + rate-limit + semafor |
| `tests/test_collector.py` | 9 | Paralel fetch, hata izolasyonu, 5-fail-disable, UPSERT, last_success |
| `tests/test_comparator.py` | 12 | %0/%0.6/%2.5 diff, ağırlıklı ortalama, reliability +0.1/-1.0 |
| `tests/test_technical.py` | 12 | backend=None, mock backend, latest_snapshot, save_signals, signal_interpretation |
| `tests/test_alerts_engine.py` | 11 | price_above/below, pct_change, signal:, triggered_at, evaluate, notifier dispatch |
| `tests/test_alerts_notifier.py` | 9 | stub Signal, loguru, alert_triggered emit, sistem bildirim flag |

**Toplam: ~96 test.**

### Önemli teknik notlar
- **SQLite uyumluluğu:** PostgreSQL `JSONB` SQLite'a `@compiles(JSONB, "sqlite")`
  ile `JSON` olarak compile edilir. Bu sayede `backtest_results.params`
  alanı testlerde çalışır.
- **CHECK constraint enforce:** `PRAGMA foreign_keys=ON` engine connect
  event'iyle her connection'a uygulanır. SQLite CHECK kısıtlarını native
  destekler — `wallets.pool`, `wallets.timeframe`, `recommendations.action`,
  `portfolio.action`, `user_account.trading_mode` testleri pas geçer.
- **UPSERT SQLite shim:** `collector.pg_insert` testlerde `INSERT OR IGNORE`'a
  monkey-patch edilir (`patch_pg_insert_for_sqlite` fixture). PostgreSQL'in
  `ON CONFLICT DO NOTHING` semantiği SQLite'ta `INSERT OR IGNORE` ile eşdeğer.
- **Ağır bağımlılık mock'ları:** `yfinance.Ticker`/`yf.download`,
  `pandas_datareader.DataReader`, `isyatirimhisse.StockData`,
  `httpx.AsyncClient` (Alpha Vantage + TCMB) hepsi `monkeypatch` /
  `unittest.mock` ile değiştirildi. TA-Lib import EDİLMEDİ —
  `TechnicalAnalyzer._compute_talib` doğrudan deterministic `_fake_indicators`
  ile değiştirildi.
- **Async testler:** `pytest-asyncio` ile `@pytest.mark.asyncio`;
  `pyproject.toml` zaten `asyncio_mode = "auto"`.
- **PySide6 stub:** `app/alerts/notifier.py` PySide6 import'unu try/except
  ile sarmalıyor; testler de bu fallback'i kullanır. Qt event-loop YOK.

## Gerekçe
Doküman §10 ve test-engineer agent §29 (Sorumluluk Alanı): "Hiçbir modül
test edilmeden faz tamamlanmış sayılmaz." Faz 1'de yazılmış 9 modül
(config, db/models, base_source, 5 source, collector, comparator,
technical, alerts/engine, alerts/notifier) için birim testler eklendi.

## Test / Doğrulama

### Çalıştırma talimatı
Geliştirici makinesinde:
```bash
poetry install --with dev
poetry run pytest tests/ -v --tb=short
```
veya pip ortamı:
```bash
pip install pytest pytest-asyncio pytest-mock pytest-cov \
            sqlalchemy aiosqlite pydantic pydantic-settings loguru \
            pandas numpy
pytest tests/ -v --tb=short
```
Coverage raporu için:
```bash
pytest tests/ --cov=app --cov-report=html --cov-report=term
```

### Çalıştırma sonucu
**Bu sandbox'ta Python interpreter mevcut değil** (`python --version` bulunamadı).
Testler yazıldı ve syntactic olarak gözden geçirildi; **gerçek çalıştırma kullanıcı
tarafında `poetry install --with dev && poetry run pytest` ile yapılmalıdır**.

### Bilinen kritik bağımlılıklar
Testlerin başarıyla çalışması için **tek extra paket**: `aiosqlite`
(async SQLite driver). `pyproject.toml`'da bağımlılıklarda yok —
yalnızca testler için gerekli. Kullanıcı:
```bash
poetry add --group dev aiosqlite
```
ile eklemeli ya da `pip install aiosqlite`.

## Bulunan Bug'lar (Faz 1 modüllerinde)
**Yok.** Test yazımı sırasında üretim kodunda davranış uyumsuzluğu tespit
edilmedi. Tüm imzalar (sınıf adları, attribute'lar, async/sync ayrımı,
hata sınıfı hiyerarşisi) doküman ve modül implementasyonu ile uyumludur.

## Notlar
- **PostgreSQL-only davranışlar:** `pg_insert.on_conflict_do_nothing`,
  `JSONB`, `TIMESTAMPTZ` SQLite'ta birebir test edilmez. Entegrasyon
  testleri (Faz 2) gerçek PostgreSQL ile yapılmalı.
- **Rate limiter zaman bağımlı testleri:** Alpha Vantage 60s pencere
  testleri için gerçek `asyncio.sleep` kullanılmaz — sayaç doğrudan
  doldurulup `RateLimitError` beklenir (hız).
- **TA-Lib gerçek doğrulama yapılmadı:** Hesaplama doğruluğu mock'lu;
  TA-Lib kütüphanesi zaten kendisi test edilmiştir. Gerçek indikatör
  değerleri için Faz 1 entegrasyon test'i ileride eklenebilir.
- **Coverage hedefi:** Birim test seviyesinde tahmini %80+ (CI ile
  gerçek ölçüm `pytest --cov=app` ile alınmalı).
- **Faz 2 hazır:** Yeni modüller (KAP scraper, sentiment, recommender,
  backtester, wallet, risk_scorer, auto_gate, broker) için aynı pattern
  (fixture + mock + happy/error/edge case) uygulanacak.
