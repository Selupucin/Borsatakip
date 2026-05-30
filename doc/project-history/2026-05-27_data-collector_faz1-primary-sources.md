---
date: 2026-05-27
agent: data-collector
phase: faz-1
type: feature
related_files:
  - app/data/base_source.py
  - app/data/collector.py
  - app/data/comparator.py
  - app/data/sources/__init__.py
  - app/data/sources/yfinance_source.py
  - app/data/sources/stooq_source.py
  - app/data/sources/alphavantage_source.py
  - app/data/sources/isyatirim_source.py
  - app/data/sources/tcmb_source.py
  - app/data/__init__.py
related_doc_sections:
  - "3.4 Veri Kaynakları"
  - "4.2 Veri Karşılaştırma Motoru"
  - "9 Veritabanı Şeması (instruments, price_history, data_sources, discrepancies, fx_rates)"
---

## Özet

Faz 1 veri toplama katmanı kuruldu: soyut `BaseSource` arayüzü, beş birincil
kaynak adaptörü (yfinance, Stooq, Alpha Vantage, İş Yatırım, TCMB), async
paralel `DataCollector` ve `PriceComparator` karşılaştırma motoru.

## Yazılan Dosyalar

| Dosya | Görev |
|---|---|
| `app/data/base_source.py` | Soyut `BaseSource` ABC + `OHLCVBar`/`Quote` dataclass'ları + hata hiyerarşisi (`SourceError`, `SourceUnavailableError`, `RateLimitError`) |
| `app/data/sources/yfinance_source.py` | NYSE/NASDAQ + BIST (`.IS`) — `asyncio.to_thread` ile sync lib sarması |
| `app/data/sources/stooq_source.py` | NYSE/NASDAQ + BIST yedek — `pandas_datareader`, `THYAO.IS` -> `thyao.tr` çevirisi |
| `app/data/sources/alphavantage_source.py` | Doğrulama için ABD — saf `httpx` async + ikili rate limiter (25/gün + 5/dk) |
| `app/data/sources/isyatirim_source.py` | BIST birincil — `isyatirimhisse` + 2 istek/sn üst sınır |
| `app/data/sources/tcmb_source.py` | SADECE FX (USDTRY/EURTRY/...) — hisse ticker'ında `SourceError` |
| `app/data/collector.py` | `DataCollector`: `asyncio.gather` paralel quote/OHLCV, DB sayaçları, 5-fail-disable, `PriceHistory`/`FxRate` UPSERT |
| `app/data/comparator.py` | `PriceComparator`: ağırlıklı ortalama `verified_close`, çiftli discrepancy, reliability güncelleme |

## Faz 1 vs Faz 2

**Faz 1'de aktif (5 birincil kaynak):**
- `yfinance` — ABD birincil, BIST yedek
- `stooq` — ABD yedek, BIST yedek (yfinance ve isyatirim ikisi de düşerse)
- `alphavantage` — doğrulama / tie-breaker (kotaya takılınca atlanır)
- `isyatirim` — BIST birincil
- `tcmb` — döviz kuru tek otorite (USD/TRY vb.)

**Faz 2'ye bırakıldı:** `kap`, `tradingview`, `investing`, `finviz`, `rss`,
`reddit` (praw). Bunlar veri toplama katmanı için ek modüllerdir; öneri
motoru (analysis-engine) önce temel fiyat verisi üzerinden tasarlanır,
sonra sentiment/teknik özet kaynakları eklenir.

## Rate-limit & Hız Sınırlandırma Özeti

| Kaynak | Konservatif limit | Uygulama |
|---|---|---|
| yfinance | 1 istek/sn (resmi limit yok, IP throttling var) | `asyncio.Semaphore(1)` |
| stooq | 1 istek/sn (403 riski) | `asyncio.Semaphore(1)` |
| alphavantage | **25/gün + 5/dk** (ücretsiz plan) | İkili pencere `_RateLimiter` — kota dolunca `RateLimitError` |
| isyatirim | 2 istek/sn (sözleşmesiz; nazik kullanım hayati) | Semaphore(1) + `_MIN_INTERVAL_S=0.5s` |
| tcmb | 1 istek/sn | Semaphore(1) + 1s aralık |

Tüm kaynaklar: 2–3 deneme + exponential back-off (1s → 2s → 4s).

## 5-Fail-Disable Mantığı

`DataCollector`:
1. Her başarılı çağrı: `consecutive_failures[source]=0`,
   `data_sources.total_requests++`, `last_success=NOW()`.
2. Her başarısız çağrı (`SourceError` veya beklenmeyen exception):
   `consecutive_failures++`, `failed_requests++`.
3. `consecutive_failures >= SOURCE_FAILURE_LIMIT` (.env vars. 5) →
   `disable_source()` çağrılır: RAM `_inactive` set'ine eklenir +
   `data_sources.is_active=False` set edilir.
4. `RateLimitError` **failure sayılmaz** — kotadan kaynaklı geçici durum;
   sadece `total_requests` artar, kaynak aktif kalır.
5. Manuel `enable_source()` veya orchestrator cooldown ile geri açılabilir.

## Reliability Skorlama Formülü

`PriceComparator.update_reliability(source, success)`:

```
success  -> score += 0.1   (max 100.0)
failure  -> score -= 1.0   (min 0.0)
```

Başarısızlık 10× daha sert cezalandırılır. Uzun süre stabil kaynaklar
100'e yaklaşır; kararsızlar hızla düşer.

`compare_quotes()` ağırlıklı ortalamayı bu skorla hesaplar:
```
verified_close = Σ(price_i × reliability_i) / Σ(reliability_i)
```
Tüm kaynaklar 0 puansa fallback olarak basit ortalama alınır.

## Tutarsızlık Eşikleri (.env)

- `DISCREPANCY_THRESHOLD_PCT=0.5` → `discrepancies` satırı yazılır
- `ALERT_DISCREPANCY_PCT=2.0` → satırın `alert_sent=True` set edilir
  (alarm motoru — Faz 2 — bu bayrakları okuyacak)

Çiftli karşılaştırma: O(n²) ama n ≤ 5 olduğundan ihmal edilebilir.

## analysis-engine İçin Not

`PriceHistory.verified_close` artık üretilen "doğru fiyat"tır. Teknik
indikatörler (RSI/MACD/EMA/Bollinger) **bu sütun üzerinden** hesaplanmalı,
tekil kaynağın `close` değerine bağlanmamalı. Aksi halde tek kaynaktaki
spike/glitch bütün sinyal hattını bozar.

`is_verified=True` olan satırlar comparator'dan geçmiştir; analiz motoru
filtre olarak bunu kullanabilir.

## Bağımlılıklar (devops-engineer'a haber)

Bu modüllerin çalışması için `pyproject.toml`'a eklenmesi gereken paketler
(henüz kurulu olmayabilir — Faz 1 setup'ında eklenecek):

- `yfinance` (zaten agent kuralında listede)
- `pandas-datareader` (Stooq için)
- `httpx` (Alpha Vantage + TCMB — `aiohttp` yerine seçildi; daha
  modern, requests-uyumlu API)
- `isyatirimhisse` (BIST)
- `loguru` (tüm modüller tarafından kullanılıyor)

Not: Alpha Vantage'ın `alpha_vantage` Python lib'i yerine direkt REST
tercih edildi — async-native, daha az sürpriz, kota mantığı tam kontrolde.

## DB Modeli

**Değişiklik yok.** Mevcut modeller (`PriceHistory`, `DataSource`,
`Discrepancy`, `FxRate`, `Instrument`) olduğu gibi kullanıldı.
`database-architect`'in ürettiği şema veri toplama ihtiyaçlarını
karşılıyor.

PostgreSQL-özel `ON CONFLICT DO NOTHING` (`sqlalchemy.dialects.postgresql.insert`)
kullanıldı — aynı (instrument_id, source, timestamp) için tekrar çağrılarda
hata atılmaz.

## Test / Doğrulama

Bağımlılıklar henüz kurulu olmayabilir; import-time test yapılmadı.
Faz 1 setup'tan sonra `tests/test_data_sources.py` ve `tests/test_comparator.py`
eklenecek (mock kaynak + sentetik quote serileri ile).

## Notlar

- TCMB modülünde `_PAIR_SERIES` haritası başlangıçta beş çift içeriyor
  (USD/EUR/GBP/CHF/JPY → TRY). Yeni çift eklemek tek satırlık değişiklik.
- `isyatirim_source.py` içindeki sütun adı eşleştirmesi (`tarih/acilis/...`)
  `isyatirimhisse` sürümleri arasında değişebileceği için esnek
  (`_resolve_columns`) tutuldu — kütüphane güncellendiğinde test edilmeli.
- `loguru` zaten kuralda standart logger; structured log için ileride
  `logger.bind(source=...)` pattern'ine geçilebilir.
- Faz 2'de eklenecek scraping kaynakları (TradingView, Investing) için
  `BaseSource`'a `User-Agent rotation` ve `robots.txt` kontrol hook'ları
  eklemek gerekecek (şu an gerek yok — REST/lib kaynakları).
