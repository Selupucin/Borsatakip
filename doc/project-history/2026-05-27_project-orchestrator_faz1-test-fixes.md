---
date: 2026-05-27
agent: project-orchestrator
phase: faz-1
type: fix
related_files:
  - app/db/session.py
  - app/db/__init__.py
  - tests/conftest.py
  - tests/test_collector.py
related_doc_sections:
  - "§3.3 Veritabanı / §10 Klasör Yapısı"
---

## Özet
Faz 1 birim testleri çalıştırılınca 3 farklı hata bulundu ve düzeltildi. Sonuç: **112/112 test geçiyor**.

## Detaylar

### 1. `app/db/session.py` — modül import-time'da `psycopg2` zorunluluğu (P0)
Eski kod, modül import edilir edilmez `create_engine(...)` çağrısı yapıyordu → testler bile `psycopg2` yüklü olmadan başlatılamıyordu.

**Düzeltme:** Engine'ler lazy hale getirildi.
- `get_sync_engine()`, `get_async_engine()` factory'leri
- `SessionLocal` / `AsyncSessionLocal` → `_LazyProxy` (ilk çağrıda gerçek session_maker'ı kurar)
- `reset_engines()` test izolasyonu için
- `init_db()` artık `get_sync_engine()` üzerinden çalışıyor
- `app/db/__init__.py` re-export'ları güncellendi (`sync_engine`/`async_engine` → `get_sync_engine`/`get_async_engine`)

### 2. `tests/conftest.py` — SQLite'ta `BigInteger` PK autoincrement (P1)
Modeller `BigInteger` primary key kullanıyor (PostgreSQL'de `BIGSERIAL`). SQLite'ta `BIGINT PRIMARY KEY` autoincrement yapmaz; sadece `INTEGER PRIMARY KEY` rowid alias'tır.

**Düzeltme:** Compile hook eklendi:
```python
@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(_type, _compiler, **_kw):
    return "INTEGER"
```
12 test (alerts.id INSERT'i çağıran tüm testler) geçer hale geldi.

### 3. `tests/test_collector.py` — iki ayrı bug
**3a) Abstract class instantiation:**
`type(name, (BaseSource,), {"name": name})` ile yaratılan sınıfta `fetch_ohlcv`/`fetch_quote` sınıf body'sinde olmadığı için instance üretilemiyordu (`TypeError: Can't instantiate abstract class`). Çözüm: tüm helper'larda metodları `type()` üçüncü argümanına dict olarak geç.

**3b) UPSERT shim — `values()` chain'i abstract metodu kaybediyor:**
Patched `pg_insert` üzerinde `stmt.on_conflict_do_nothing = ...` ataması yapılıyordu ama `pg_insert(...).values(rows)` yeni stmt nesnesi döndürdüğü için custom metod kayboluyordu.

Çözüm: SQLAlchemy 1.4+'da `sqlalchemy.dialects.sqlite.insert` zaten `on_conflict_do_nothing` destekliyor — `pg_insert` doğrudan `sqlite_insert` ile değiştirildi:
```python
monkeypatch.setattr(col_mod, "pg_insert", sqlite_insert)
```

## Gerekçe
Kullanıcı "test ederek devam et" talebi nedeniyle Faz 2'ye geçmeden önce tüm testlerin yeşil olması şart. Python 3.11.9 winget ile yüklendi; pytest + minimum bağımlılık kuruldu; testler defalarca çalıştırıldı.

## Test / Doğrulama
- `pytest tests/ --tb=short` çıktısı: `112 passed, 1 warning in 1.67s`
- 8 test dosyası: config, db_models, data_sources, collector, comparator, technical, alerts_engine, alerts_notifier
- Sadece pytest-asyncio'nun `event_loop_policy` fixture deprecation uyarısı var (kod hatası değil)

## Notlar
- **Üretim koduna dokunan değişiklikler:** Yalnızca `app/db/session.py` ve `app/db/__init__.py`. Hiçbir uygulama davranışı değişmedi (lazy initialization tamamen transparent).
- **Test koduna dokunulan:** `tests/conftest.py`, `tests/test_collector.py`.
- **Faz 2'ye geçişe hazır.** RSS, NLP, scraping kaynakları ve kısa vade öneri motoru bir sonraki batch.
- TA-Lib, PySide6, yfinance gibi heavy bağımlılıklar testlerde **mock'lanıyor** — kurulum gerekmedi.
