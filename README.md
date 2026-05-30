# Borsa Bot

BIST ve NYSE/NASDAQ borsalarını çoklu veri kaynağından gerçek zamanlı takip eden, teknik analiz, haber ve sosyal medya sentiment analizini birleştirerek kısa / orta / uzun vadeli hisse önerileri sunan kapsamlı masaüstü uygulaması.

> Detaylı proje dokümantasyonu için bkz. `doc/document.md`.

---

## Gereksinimler

- **Python 3.11+** (zorunlu)
- **PostgreSQL 15+** (yerel kurulum)
- **Poetry** (bağımlılık yönetimi) — `pip install poetry`
- **Playwright Chromium** (scraping için ilk kurulumda indirilir)
- İşletim sistemi: Windows, macOS veya Linux
- ~500 MB boş disk alanı (HuggingFace NLP modelleri ilk çalıştırmada indirilir)

---

## Kurulum

```bash
# 1. Bağımlılıkları kur
poetry install

# 2. Playwright Chromium'u indir (scraping için)
poetry run playwright install chromium

# 3. Ortam değişkenleri şablonunu kopyala ve düzenle
cp .env.example .env
# .env dosyasındaki DB bilgilerini ve API anahtarlarını doldur

# 4. Veritabanı şemasını kur
poetry run python scripts/setup_db.py

# 5. (İsteğe bağlı) Geliştirici ortamı yardımcı script'i
poetry run python scripts/setup_dev.py
```

---

## Çalıştırma

```bash
# Uygulamayı başlat
poetry run python -m app.main
```

---

## Geliştirme

```bash
# Testleri çalıştır
poetry run pytest

# Lint kontrolü
poetry run ruff check .

# Formatlama kontrolü
poetry run black --check .

# Otomatik formatla
poetry run black .

# Pre-commit hook'larını kur (commit öncesi otomatik kontrol)
poetry run pre-commit install
```

---

## Geliştirme Fazları

| Faz | İçerik | Durum |
|---|---|---|
| **Faz 1** (Hafta 1–8) | Temel altyapı: Poetry, PostgreSQL, SQLAlchemy modelleri, Alembic, birincil veri kaynakları (yfinance, Stooq, Alpha Vantage, İş Yatırım), async veri toplayıcı, karşılaştırma motoru, TA-Lib teknik analiz, PySide6 ana pencere + dark/light tema, finplot grafik, alarm sistemi | Başlıyor |
| **Faz 2** (Hafta 9–15) | Haber & sentiment: RSS toplayıcı, Türkçe BERT + FinBERT NLP, TradingView / Investing scraping (Playwright), watchlist, bot picks, öneri motoru ilk versiyon | Bekliyor |
| **Faz 3** (Hafta 16–24) | Öneri motoru, portföy & UI cilası: orta/uzun vade, backtesting (vectorbt), risk yönetimi, bütçe matrisi (havuz × vade = 6 cüzdan), kâr-zarar (komisyon/vergi/kur), endeks kıyaslaması, manuel-eşli mod, paper trading, PyInstaller paketleme | Bekliyor |
| **Faz 4** (İleride) | Firma bağlantısı & gerçek emir: aracı kurum entegrasyonu, yarı/tam otomatik mod, kill switch, audit log | Hazır bekler |

---

## Yasal Uyarı

Bu sistem **yatırım tavsiyesi vermez**. Ürettiği tüm öneri, skor ve analizler bilgilendirme ve karar destek amaçlıdır. Yatırım kararlarının sorumluluğu tamamen kullanıcıya aittir. Gerçek emirler yalnızca SPK lisanslı bir aracı kurum üzerinden iletilir.
