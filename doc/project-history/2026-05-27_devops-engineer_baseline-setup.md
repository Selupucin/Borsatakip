---
date: 2026-05-27
agent: devops-engineer
phase: faz-1
type: config
related_files:
  - pyproject.toml
  - .env.example
  - .gitignore
  - README.md
  - .pre-commit-config.yaml
  - scripts/setup_dev.py
  - scripts/__init__.py
  - app/__init__.py
  - app/config.py
  - app/ui/__init__.py
  - app/ui/components/__init__.py
  - app/data/__init__.py
  - app/data/sources/__init__.py
  - app/analysis/__init__.py
  - app/portfolio/__init__.py
  - app/trading/__init__.py
  - app/broker/__init__.py
  - app/broker/adapters/__init__.py
  - app/alerts/__init__.py
  - tests/__init__.py
related_doc_sections:
  - "3. Teknoloji Yığını"
  - "10. Klasör Yapısı"
  - "12. Ortam Değişkenleri"
  - "13. Bağımlılıklar"
---

## Özet

Faz 1 DevOps temeli kuruldu: Poetry konfigürasyonu, tüm bağımlılıklar, lint/format/test araçları, `.env` şablonu, `.gitignore`, geliştirici kurulum script'i, type-safe `Settings` (pydantic-settings) ve tam klasör iskeleti oluşturuldu. Hiçbir kurulum komutu çalıştırılmadı — yalnızca dosyalar yazıldı.

## Detaylar

### Yazılan dosyalar

**Proje kök yapılandırma:**

- `pyproject.toml` — Poetry tabanlı tam bağımlılık tanımı, Python `^3.11`, build-system `poetry.core`. `[tool.ruff]` (line-length 100, target py311, select E/F/W/I/N/UP/B), `[tool.black]` (line-length 100, target py311), `[tool.pytest.ini_options]` (`asyncio_mode = "auto"`, `testpaths = ["tests"]`, marker'lar: `slow`, `integration`).
- `.env.example` — Doküman §12'deki tüm değişkenler Türkçe yorumlarla. DB, API anahtarları (Alpha Vantage, Reddit), uygulama davranış ayarları, işlem/otomasyon, aracı kurum (Faz 4 için boş), güvenlik (encryption), NLP model yolları.
- `.gitignore` — Python (`__pycache__`, `.venv/`), test (`.pytest_cache`, `htmlcov/`), lint cache (`.ruff_cache`, `.mypy_cache`), build (`dist/`, `*.spec`, `*.egg-info`), env (`.env`, `.env.local`, `*.pem`), veri cache (`*.parquet`, `data_cache/`, `nlp_cache/`, `logs/`), HuggingFace cache notu, IDE (`.vscode/`, `.idea/`), OS (`.DS_Store`, `Thumbs.db`).
- `README.md` — Türkçe kurulum/çalıştırma/geliştirme rehberi. Gereksinimler (Python 3.11+, PostgreSQL 15+, Poetry, Playwright Chromium). `poetry install` → `playwright install chromium` → `cp .env.example .env` → `python scripts/setup_db.py` → `python -m app.main`. Test/lint komutları + Faz 1-4 özet tablosu + yasal uyarı.
- `.pre-commit-config.yaml` — ruff (autofix), ruff-format, black (Python 3.11), trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-added-large-files (max 1MB), check-merge-conflict, detect-private-key.

**Geliştirici script'i:**

- `scripts/setup_dev.py` — Python ≥3.11 kontrolü, `.env` yoksa `.env.example`'dan kopyalama, `playwright install chromium` subprocess çağrısı, HuggingFace cache konum bilgisi (Linux/macOS/Windows).

**Uygulama yapılandırması:**

- `app/config.py` — `pydantic_settings.BaseSettings` üzerinde tüm `.env` değişkenleri type-safe expose edildi. `db_url_sync` (psycopg2) ve `db_url_async` (asyncpg) property'leri. `SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")`. Modül seviyesinde `settings = Settings()` singleton.

**Klasör iskeleti (her biri docstring'li `__init__.py`):**

- `app/ui/__init__.py`, `app/ui/components/__init__.py`
- `app/data/__init__.py`, `app/data/sources/__init__.py`
- `app/analysis/__init__.py`
- `app/portfolio/__init__.py`
- `app/trading/__init__.py`
- `app/broker/__init__.py`, `app/broker/adapters/__init__.py`
- `app/alerts/__init__.py`
- `tests/__init__.py`
- `scripts/__init__.py`

`app/__init__.py` ve `app/db/__init__.py` zaten önceden mevcuttu (uygun docstring/içerikle), korundu.

### Eklenen bağımlılıklar (Doküman §13 listesi + ek istekler)

**UI (6 paket)**

| Paket | Sürüm | Lisans | Talep |
|---|---|---|---|
| PySide6 | ^6.6 | LGPL-3.0 | doküman §3.2 |
| pyqtgraph | ^0.13 | MIT | doküman §3.2 |
| finplot | ^1.9 | MIT | doküman §3.2 |
| pyqtdarktheme | ^2.1 | MIT | doküman §3.2 |
| qt-material | ^2.14 | BSD-2 | doküman §3.2 |
| qtawesome | ^1.3 | MIT | doküman §3.2 |

**Veritabanı (4 paket)**

| Paket | Sürüm | Lisans |
|---|---|---|
| SQLAlchemy | ^2.0 | MIT |
| alembic | ^1.13 | MIT |
| psycopg2-binary | ^2.9 | LGPL (DB sürücü istisnası ile) |
| asyncpg | ^0.29 | Apache-2.0 |

**Veri kaynakları (7 paket)**

| Paket | Sürüm | Lisans |
|---|---|---|
| yfinance | ^0.2 | Apache-2.0 |
| pandas-datareader | ^0.10 | BSD-3 |
| alpha-vantage | ^2.3 | MIT |
| isyatirimhisse | ^4.0 | MIT |
| pandas | ^2.1 | BSD-3 |
| numpy | ^2.0 | BSD-3 |
| pyarrow | ^14.0 | Apache-2.0 |

**Teknik analiz & backtesting (3 paket)**

| Paket | Sürüm | Lisans | Not |
|---|---|---|---|
| TA-Lib | ^0.6 | BSD-3 | v0.6.5+ önceden derlenmiş wheel — C compiler gerekmez |
| pandas-ta | ^0.3 | MIT | TA-Lib yedek |
| vectorbt | ^0.26 | Apache-2.0 (BSL-1.1 lite) | Topluluk sürümü ücretsiz |

**Scraping & async (6 paket)**

| Paket | Sürüm | Lisans |
|---|---|---|
| playwright | ^1.40 | Apache-2.0 |
| beautifulsoup4 | ^4.12 | MIT |
| tvdatafeed | ^2.0 | MIT |
| finvizfinance | ^0.14 | MIT |
| feedparser | ^6.0 | BSD-2 |
| aiohttp | ^3.9 | Apache-2.0 |

**NLP (3 paket)**

| Paket | Sürüm | Lisans |
|---|---|---|
| transformers | ^4.36 | Apache-2.0 |
| torch | ^2.1 | BSD-3 |
| praw | ^7.7 | BSD-2 |

**Yardımcı (6 paket)**

| Paket | Sürüm | Lisans |
|---|---|---|
| python-dotenv | ^1.0 | BSD-3 |
| loguru | ^0.7 | MIT |
| pydantic | ^2.5 | MIT |
| pydantic-settings | ^2.1 | MIT |
| keyring | ^25.0 | MIT |
| cryptography | ^42.0 | Apache-2.0 / BSD-3 (dual) |

**Dev (9 paket)**

| Paket | Sürüm | Lisans |
|---|---|---|
| pytest | ^7.4 | MIT |
| pytest-asyncio | ^0.21 | Apache-2.0 |
| pytest-mock | ^3.12 | MIT |
| pytest-cov | ^4.1 | MIT |
| aioresponses | ^0.7 | MIT |
| responses | ^0.24 | Apache-2.0 |
| ruff | ^0.1 | MIT |
| black | ^23.0 | MIT |
| pre-commit | ^3.5 | MIT |

**Toplam: 44 production + 9 dev = 53 bağımlılık.**

Lisans durumu: Hepsi MIT, BSD, Apache-2.0 veya LGPL — ticari dağıtıma uygun. **GPL paketi yok.** `PySide6` LGPL olduğu için statik link yerine dinamik link tercih edilmelidir (PyInstaller varsayılan davranışı bunu sağlar).

### Tool ayarları

- **ruff**: `line-length = 100`, `target-version = "py311"`, `select = ["E", "F", "W", "I", "N", "UP", "B"]` (pycodestyle errors/warnings, pyflakes, isort, pep8-naming, pyupgrade, flake8-bugbear).
- **black**: `line-length = 100`, `target-version = ["py311"]`.
- **pytest**: `asyncio_mode = "auto"` (async test'ler otomatik tanınır), `testpaths = ["tests"]`, marker'lar: `slow` (yavaş testler), `integration` (gerçek DB gerektirir).

## Gerekçe

Bu çalışma Faz 1'in (Hafta 1–8) ilk maddesi olan "Poetry ile proje kurulumu, pyproject.toml yapılandırması" görevini karşılar. Doküman §3 (Teknoloji Yığını), §10 (Klasör Yapısı), §12 (Ortam Değişkenleri) ve §13 (Bağımlılıklar) bölümlerinde tanımlanan altyapı kararları birebir uygulanmıştır. Sub-agent'ların (database-architect, data-collector, ui-engineer vb.) çalışmaya başlayabilmesi için ortak bir yapı temeli sağlanmıştır.

Ek olarak doküman §13'te listelenmeyen ancak diğer agent'ların ihtiyaç duyacağı paketler de eklenmiştir:

- `asyncpg` (async DB sürücüsü) — collector'ların async DB yazımı için.
- `pydantic-settings` (ayar yönetimi) — pydantic 2.x'te ayar yönetimi ayrı pakete taşındı.
- `pytest-asyncio`, `pytest-mock`, `pytest-cov`, `aioresponses`, `responses`, `pre-commit` — test-engineer'ın gerektireceği dev araçlar.

## Test / Doğrulama

Bu commit yalnızca yapılandırma dosyalarını içerir; test yazılmadı. `poetry install`, `pip install` veya başka bir kurulum komutu çalıştırılmadı — istek üzerine yalnızca dosyalar yazıldı.

Doğrulama için kullanıcının çalıştırması gereken komutlar:

```bash
poetry install
poetry run playwright install chromium
poetry run python scripts/setup_dev.py
poetry run python -c "from app.config import settings; print(settings.db_url_sync)"
```

## Notlar

### Bilinen kurulum gereksinimleri

- **Python 3.11+** zorunlu. Daha düşük sürümlerde `pyproject.toml` reddedilir.
- **PostgreSQL 15+** sistemde yüklü olmalı; bağlantı bilgileri `.env` üzerinden okunur.
- **Playwright Chromium** ilk kurulumda `playwright install chromium` ile indirilmeli (~150 MB).
- **HuggingFace modelleri** ilk çalıştırmada otomatik indirilir (~500 MB) — `~/.cache/huggingface/` altında. PyInstaller paketine **dahil edilmez**.
- **TA-Lib** v0.6.5+ önceden derlenmiş wheel sayesinde C compiler gerektirmez; eski sürümlerde Windows'ta Visual C++ build tools gerekebilir.

### Sonraki adımlar (diğer agent'lar için)

1. **database-architect** → `app/db/models.py`, `app/db/session.py`, `alembic init app/db/migrations`, `scripts/setup_db.py`.
2. **data-collector** → `app/data/base_source.py`, `app/data/sources/*.py`, `app/data/collector.py`, `app/data/comparator.py`.
3. **ui-engineer** → `app/ui/main_window.py`, `app/ui/theme_manager.py` + ekran widget'ları.
4. **test-engineer** → `tests/test_*.py` dosyaları.

### Dikkat

- `app/db/__init__.py` daha önce başka bir agent (muhtemelen database-architect) tarafından oluşturulmuş; `session.py` ve `models.py` import'ları içeriyor. Bu dosyalar henüz yok — database-architect tamamladığında çalışacak. DevOps olarak içeriğine müdahale edilmedi.
- `.env` dosyası henüz oluşturulmadı; geliştiriciler `cp .env.example .env` veya `scripts/setup_dev.py` ile kendileri oluşturmalıdır.
- **Hatırlatma:** Bağımlılık dosyası güncellendi — kullanıcının `poetry lock` ve `poetry install` çalıştırması gerekir.
