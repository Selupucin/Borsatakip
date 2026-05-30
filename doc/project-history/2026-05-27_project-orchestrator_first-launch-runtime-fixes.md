---
date: 2026-05-27
agent: project-orchestrator
phase: faz-3
type: fix
related_files:
  - .env
  - app/config.py
  - app/db/session.py
  - scripts/setup_db.py
  - tests/test_config.py
  - app/ui/_backend_bridge.py
  - app/ui/*.py
related_doc_sections:
  - "§11 Faz 1-3"
---

## Özet
Kullanıcının normalde manuel yapması gereken kurulum + ilk çalıştırma orchestrator tarafından gerçekleştirildi. Uygulama SQLite üzerinde başarıyla başlatıldı, default hesap + 6 cüzdan matrisi otomatik oluşturuldu.

## Yapılanlar

### 1. Bağımlılık kurulumu (pip --user)
Heavy olmayan production bağımlılıkları kuruldu:
- **UI:** PySide6 6.11.1, pyqtgraph, pyqtdarktheme, qtawesome
- **DB:** SQLAlchemy 2.0.50, alembic, psycopg2-binary, asyncpg
- **Veri:** pandas 3.0.3, numpy, pyarrow, aiohttp, httpx, beautifulsoup4, feedparser, yfinance, pandas-datareader, isyatirimhisse
- **Yardımcı:** python-dotenv, loguru, pydantic 2.x, pydantic-settings, keyring, cryptography, plyer

**Kurulmayanlar (opsiyonel, sentiment/backtest gerektiğinde):** torch, transformers, vectorbt, tradingview-ta, playwright, finvizfinance, praw, TA-Lib (Windows için wheel sorunu, pandas-ta yedek).

### 2. PostgreSQL → SQLite Dev Fallback
PostgreSQL 18 sistemde kurulu ama servis admin yetkisi gerektiriyor (sandbox engelliyor). Çözüm:
- `app/config.py`'a `use_sqlite: bool = False` + `sqlite_path` alanı eklendi
- `db_url_sync` / `db_url_async` property'leri `use_sqlite=True` ise `sqlite:///path` döndürüyor
- `.env` dosyası `USE_SQLITE=true` ile yaratıldı (dev profili)

### 3. Düzeltilen Bug'lar
- **`session.py`** — `_resolve_urls` yanlış attribute adı arıyordu (`database_url_sync` yerine gerçek isim `db_url_sync`). `settings.use_sqlite` etkisini hiç görmüyordu, hep PostgreSQL URL build ediyordu.
- **`scripts/setup_db.py`** — eski `sync_engine` import'u + Alembic env.py PostgreSQL'e zorla bağlanıyordu. `get_sync_engine()` lazy import + SQLite modunda Alembic atlama mantığı eklendi.
- **SQLite uyumluluk** — `setup_db.py`'da `@compiles(JSONB, "sqlite")` → JSON ve `@compiles(BigInteger, "sqlite")` → INTEGER hook'ları (testlerdeki conftest pattern'i ile aynı).
- **`tests/test_config.py`** — 3 DB URL testi ortam `USE_SQLITE=true` set olduğunda fail ediyordu; her birine `use_sqlite=False` parametresi eklendi.

### 4. DB Setup + Seed (SQLite)
```
python scripts/setup_db.py --seed
→ SQLite modu — Alembic atlandı, create_all kullanılıyor.
→ create_all tamam.
→ data_sources seed: 11 yeni satır eklendi.
→ BIST: 30 yeni / 30 toplam; ABD: 20 yeni / 20 toplam.
```
DB dosyası: `borsa_bot.db` proje kökünde.

### 5. Backend Bağlantısı (paralel agent)
`app/ui/_backend_bridge.py` yeni — `BackendBridge` singleton + `QThreadPool` worker (async-to-Qt köprüsü). 12 widget gerçek servislere bağlandı:

| Widget | Bağlandığı servisler |
|---|---|
| `dashboard.py` | AccountService, WalletService, PositionService, BenchmarkService, ManualParallelService |
| `portfolio_widget.py` | AccountService, WalletService, PositionService, BenchmarkService |
| `budget_widget.py` | WalletService.allocate/reallocate, CashFlowService.deposit/withdrawal, PaperTradingService.reset |
| `trading_widget.py` | AccountService.set_*, ManualParallelService.mark_*, SafetyEngine.* |
| `history_widget.py` | Portfolio JOIN wallets JOIN instruments raw async query; CSV/Excel export |
| `watchlist_widget.py` | WatchlistService.list_all/list_items/create/delete/add_item |
| `bot_picks_widget.py` | BotPicksService.list_picks(timeframe, only_open) |
| `news_widget.py` | NewsFeed tablosundan async select |
| `recommendation_widget.py` | backtest_results, RiskManager.*, BotPicksService.add_pick |
| `alerts_widget.py` | Alert tablosu select/insert, Notifier.alert_triggered → ToastManager |
| `settings_widget.py` | AccountService.set_trading_mode, set_risk_threshold |
| `main_window.py` | bridge'i her widget'a iletir, kill switch toolbar |

### 6. Test Sonucu
**394/394 test geçti** (0 başarısız). UI smoke testler artık skip değil — PySide6 kurulu olduğu için 5'i de çalıştı.

### 7. Uygulamanın İlk Çalıştırması
```
python -m app.main (otomatik 2sn timer ile test edildi)
→ Window: 1195x720, Title="Borsa Bot", Theme=auto
→ Dashboard ekranı varsayılan
→ UserAccount otomatik oluştu (id=1, 15000 TRY, mode=manual_parallel)
→ Wallet matrisi init: 6 cüzdan yaratıldı
→ Pencere durumu kaydedildi (QSettings)
→ Exit code 0
```

## Gerekçe
Kullanıcı "proje hazır olduğunda başlat kontrol edelim" dedi. Manuel adımları otomatize ederek uygulamanın gerçekten çalıştığını gösterdim. Bug'lar (yanlış attribute adı, PostgreSQL hard-coded) sadece runtime'da görüldü — testler mock'lu olduğu için kaçırmıştı. Bu klasik bir entegrasyon test boşluğu.

## Test / Doğrulama
- pytest: 394/394 geçti, 0 failed
- python -m app.main: pencere açıldı, backend hesap+cüzdan yarattı, temiz kapandı
- DB: SQLite dosyası 11+30+20 satır seed ile hazır

## Notlar (Kullanıcı için)

**Şu an çalışan dev profili:**
- DB: SQLite (`borsa_bot.db`)
- UI: PySide6 6.11.1
- Sentiment / backtest / scraping: mock — placeholder data ile

**PostgreSQL'e geçmek için (önerilen production yapısı):**
1. PostgreSQL service'i admin olarak başlat: `Start-Service postgresql-x64-18` (Admin PowerShell)
2. DB ve user oluştur:
   ```sql
   CREATE DATABASE borsa_bot;
   CREATE USER borsa WITH PASSWORD 'guvenli-sifre';
   GRANT ALL PRIVILEGES ON DATABASE borsa_bot TO borsa;
   ```
3. `.env`'i güncelle: `USE_SQLITE=false`, `DB_USER=borsa`, `DB_PASSWORD=...`
4. `python scripts/setup_db.py --seed`
5. `python -m app.main`

**Opsiyonel ağır bağımlılıklar:**
- Sentiment (~880 MB): `pip install torch transformers` — lazy load, ilk sentiment çağrısında modeller indirilir
- Backtest: `pip install vectorbt` — yoksa pandas fallback
- TradingView scraping: `pip install playwright tvdatafeed tradingview-ta && playwright install chromium`
- Finviz/Reddit: `pip install finvizfinance praw`
- TA-Lib (Windows): https://github.com/cgohlke/talib-build/releases — pre-built wheel

**Faz 4 (gerçek aracı kurum) hâlâ AKTİF DEĞİL** — kullanıcının onayı ile başlatılır.

## Sonraki Öneri
- UI'ı manuel olarak `python -m app.main` ile başlatıp her ekrana göz atın
- BIST 30 + ABD 20 hisse seedlendi; bir watchlist oluşturup hisse ekleyebilirsiniz
- Veri çekimi için en az `yfinance` çalışıyor — gerçek fiyat akışını test edebilirsiniz
- Sentiment/scraping istiyorsanız opsiyonel bağımlılıkları kurun
