---
date: 2026-05-27
agent: data-collector
phase: faz-2
type: feature
related_files:
  - app/data/sources/rss_source.py
  - app/data/sources/kap_source.py
  - app/data/sources/tradingview_source.py
  - app/data/sources/investing_source.py
  - app/data/sources/finviz_source.py
  - app/data/sources/reddit_source.py
  - app/data/sources/__init__.py
  - app/data/collector.py
  - pyproject.toml
related_doc_sections:
  - "3.4 Veri Kaynakları (RSS, KAP, TradingView, Investing, Finviz, Reddit)"
  - "11 Faz 2 — Haber & Sentiment + Scraping + Listeler"
  - "15 Notlar ve Yasal Uyarılar (scraping ToS)"
---

## Özet

Faz 2 veri toplama katmanı: altı yeni kaynak eklendi (RSS, KAP, TradingView,
Investing.com, Finviz, Reddit). Her biri `BaseSource`'tan türer, async,
retry+backoff, header rotasyonu, kaynak-bazlı sıkı rate limit ile çalışır.
`DataCollector`'a `save_news` ve `save_disclosures` idempotent yazma metodları
eklendi. Tüm scraping modüllerinin başında ToS uyarısı + rate sınırı yer
alıyor.

## Eklenen Kaynaklar

| Dosya | Kaynak | İmplementasyon | Yeni Public API |
|---|---|---|---|
| `app/data/sources/rss_source.py` | `rss` | `feedparser` (sync) → `asyncio.to_thread`; 5 feed (Reuters Finance, Bloomberg TR, Mynet Finans, Dünya, KAP RSS) | `fetch_news(since=None) -> list[NewsItem]` |
| `app/data/sources/kap_source.py` | `kap` | `httpx` async; KAP'ın web UI'sinin kullandığı public JSON endpoint'leri | `fetch_disclosures(ticker=None, since=None)`, `fetch_company_financials(ticker)` |
| `app/data/sources/tradingview_source.py` | `tradingview` | `tvdatafeed` (OHLCV) + `tradingview_ta` (özet) lazy import; ikisi de yoksa fail-soft | `fetch_ohlcv`, `fetch_quote`, `fetch_technical_summary(ticker, exchange="BIST") -> TVSummary` |
| `app/data/sources/investing_source.py` | `investing` | Playwright async (tercih) → `httpx + bs4` fallback; ToS gereği fiyat scrape REDDEDİLİR | `fetch_technical_summary(ticker, exchange) -> InvSummary`, `fetch_analyst_ratings(ticker) -> dict` |
| `app/data/sources/finviz_source.py` | `finviz` | `finvizfinance` (sync) → `asyncio.to_thread`; ABD odaklı temel veri + analist | `fetch_quote`, `fetch_fundamentals(ticker) -> FundamentalData` |
| `app/data/sources/reddit_source.py` | `reddit` | `praw` (sync) → `asyncio.to_thread`; script-mode read-only; credential eksikse `SourceUnavailableError("Reddit credentials yok")` | `fetch_recent_posts(subreddit="wallstreetbets", ticker=None, limit=50) -> list[RedditPost]` |

## Yeni Dataclass'lar (immutable, slots)

| Dataclass | Modül | Alanlar |
|---|---|---|
| `NewsItem` | `rss_source` | title, url, published_at, source (feed adı), language ("tr"\|"en"), summary |
| `KapDisclosure` | `kap_source` | ticker, title, url, published_at, subject, category |
| `TVSummary` | `tradingview_source` | recommendation, oscillators_summary, ma_summary (hepsi STRONG_BUY/BUY/NEUTRAL/SELL/STRONG_SELL), support_levels, resistance_levels |
| `InvSummary` | `investing_source` | TVSummary ile birebir paralel şema (recommendation vocab aynı) |
| `FundamentalData` | `finviz_source` | ticker, pe_ratio, pb_ratio, market_cap, dividend_yield, eps, beta, target_price, sector, industry |
| `RedditPost` | `reddit_source` | title, body, score, num_comments, url, created_at, subreddit, ticker |

## BaseSource Sözleşmesi — Quote/OHLCV Davranışı

| Kaynak | `fetch_ohlcv` | `fetch_quote` | Sebep |
|---|---|---|---|
| `rss` | `SourceError` | `SourceError` | RSS sadece haber |
| `kap` | `SourceError` | `SourceError` | KAP bildirim/finansal — fiyat değil |
| `tradingview` | ✔ (`tvdatafeed`) | ✔ | tvdatafeed yetkisi yoksa `SourceUnavailableError` |
| `investing` | `SourceError` | `SourceError` | ToS gereği fiyat scrape edilmez |
| `finviz` | `SourceError` | ✔ | Tarihsel OHLCV Finviz'de uygun değil |
| `reddit` | `SourceError` | `SourceError` | Fiyat sağlamaz |

Collector `_safe_fetch_quote`/`_safe_fetch_ohlcv` `SourceError`'u failure
sayıyor — bu kaynakları `fetch_all_quotes`/`fetch_all_ohlcv` döngüsüne
sokmamak gerekiyor. **Önerilen kullanım:** `DataCollector` yeni-kaynakları
constructor'a vermeden, ayrı bir `NewsCollector` veya çağrı yerinde özel
metodlar (`save_news`, `save_disclosures`) ile çalıştırın. (Bu refaktör
Faz 2.1 işidir — bu kayıtta sınırı not ediyoruz.)

## DataCollector Güncellemeleri (KÜÇÜK)

| Yeni metod | Davranış |
|---|---|
| `save_news(items, instrument_id=None)` | `news_feed` tablosuna idempotent insert. URL üzerinde unique constraint olmadığı için (instrument_id, url) çiftine göre manuel duplicate kontrolü. `sentiment` ve `sentiment_score` NULL bırakılır — sentiment.py daha sonra dolduracak. |
| `save_disclosures(disclosures, ticker_to_instrument_id=None)` | KAP bildirimlerini `NewsItem` formatına çevirip `save_news` üzerinden yazıyor. `source="kap"`, `language="tr"`. Faz 3'te ayrı `corporate_disclosures` tablosu eklenirse migration gerekecek. |

`db/models.py` değişmedi — mevcut `NewsFeed` modeli yeterli.

## Rate-limit / ToS Özeti

| Kaynak | Konservatif Limit | Mekanizma | ToS Notu |
|---|---|---|---|
| `rss` | Feed başına 60 sn min interval | `_last_fetch` dict + asyncio.Lock | Yayıncı RSS, başlık+link kişisel kullanım |
| `kap` | 1 req / sn | Semaphore(1) + `_MIN_INTERVAL_S=1.0` | Kamuya açık ama ticari yeniden dağıtım yasak |
| `tradingview` | 1 req / 2 sn (~30/dk) | Semaphore(1) + `_MIN_INTERVAL_S=2.0` | TV ToS kişisel kullanım sınırlı izin |
| `investing` | 3 req / dk (20s interval) | Semaphore(1) + `_MIN_INTERVAL_S=20.0` | Scraping AÇIKÇA yasaklı — kişisel kullanım sınırı zorunlu |
| `finviz` | 1 req / 3 sn | Semaphore(1) + `_MIN_INTERVAL_S=3.0` | Kişisel/akademik; toplu scrape yasak |
| `reddit` | 1 req / sn (praw built-in 100/dk üstüne ek kibarlık) | Semaphore(1) + `_MIN_INTERVAL_S=1.0` | Resmi API; kullanıcı verisi anonim kalmalı |

Tüm kaynaklar: 2 deneme + exponential back-off; modern UA listesi
(`_USER_AGENTS`) + `random.choice` ile her istekte rotasyon
(rss/kap/tradingview/investing/finviz).

## Lazy / Soft Failure Stratejisi

Tüm dış lib'ler `try/except` ile sarıldı:

- `tvdatafeed` yok → `TV_AVAILABLE=False`, fiyat metodları
  `SourceUnavailableError`.
- `tradingview_ta` yok → teknik özet NEUTRAL döner (recommender'ı
  bloklamamak için).
- `playwright` yok → Investing httpx + bs4 fallback'e düşer.
- `bs4` yok → Investing regex-only parse'a düşer.
- `finvizfinance` yok → tüm Finviz metodları `SourceUnavailableError`.
- `praw` yok VEYA credentials eksik → Reddit `SourceUnavailableError`.
- `feedparser` yok → RSS `SourceUnavailableError`.
- `httpx` yok → KAP `SourceUnavailableError`.

Hiçbir import collector startup'ı patlatmaz; eksik bağımlılık kaynağa
göre kademeli devre dışı kalır.

## Yasal & Etik (DOC §15)

Her scraping source'unun başında standart yorum bloğu var:

```
# ============================================================
# KAYNAK: <name>
# YASAL:  <ToS özeti: kişisel kullanım / ticari yasak>
# RATE:   <kesin limit>
# robots.txt: <not>
# ============================================================
```

User-agent rotasyonu: 3-5 modern UA string (Chrome/Firefox/Safari Windows
+ macOS + Linux + mobile Safari), `random.choice` ile her istekte
değişiyor. Investing.com Cloudflare engelleme riski için Playwright
tercihli; başarısız olursa httpx fallback.

robots.txt programatik olarak kontrol edilmiyor (her modülün başında
yorum); ihtiyaç olursa Faz 2.1'de `app/data/robots_checker.py` eklenebilir.

## Bağımlılıklar (devops-engineer'a haber)

`pyproject.toml`'da değişen / eklenen satırlar:

```toml
# Web scraping bloğu
tradingview-ta = "^3.3"     # TradingView teknik özet (opsiyonel — yoksa NEUTRAL fallback)
httpx = "^0.27"             # async REST (KAP, TCMB, Alpha Vantage, Investing fallback)
```

Zaten listede olanlar yeterli: `feedparser`, `playwright`, `tvdatafeed`,
`finvizfinance`, `praw`, `beautifulsoup4`, `aiohttp` (aiohttp şu an
kullanılmıyor — httpx tercih ediliyor; ileride kaldırılabilir).

**Playwright için ek kurulum:** `playwright install chromium` ilk seferde
çalıştırılmalı. Devops setup script'inde otomatize edilebilir.

**Reddit credentials:** `.env` zaten `REDDIT_CLIENT_ID`,
`REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT` alanlarına sahip (config.py).
Credential olmadan kaynak otomatik pasif.

## DB Modeli

**Değişiklik yok.** Mevcut `NewsFeed` modeli RSS + KAP için yeterli.
`technical_signals.tv_summary` ve `inv_summary` sütunları zaten var
(faz-1 schema), `TVSummary.recommendation` ve `InvSummary.recommendation`
buraya doğrudan yazılabilir.

**Gelecek için not (database-architect):** Faz 3'te ihtiyaç oluşursa şu
tablolar eklenebilir:

- `corporate_disclosures` — KAP yapısal bildirimleri (kategori, subject
  alanları için ayrı tablo; şimdilik `news_feed`'de tutuluyor)
- `analyst_ratings` — Investing/Finviz analist konsensüsü (target_price,
  recommendation_avg)
- `fundamentals_snapshot` — Finviz `FundamentalData` zaman serisi
- `reddit_posts` — sosyal sentiment veri kümesi (şimdilik in-memory
  sentiment.py besleyici)

## analysis-engine İçin Yeni Veri Akışları

Faz 2 öneri motoru bu kaynaklardan şunları çekecek:

| Veri | Kaynak / Çıktı | Hedef |
|---|---|---|
| Türkçe + İngilizce haber metinleri | `RSSSource.fetch_news()` → `NewsItem.title + summary` | `sentiment.py` (BERT-Turkish + FinBERT) → `news_feed.sentiment` + `sentiment_score` |
| KAP özel durum bildirimleri | `KapSource.fetch_disclosures()` → `KapDisclosure` | Watchlist hisseleri için olay-bazlı uyarı + sentiment (TR FinBERT) |
| TradingView teknik özeti | `TradingViewSource.fetch_technical_summary()` → `TVSummary` | `technical_signals.tv_summary` (kaynak mutabakatı %10 ağırlık — DOC §5.1) |
| Investing teknik özeti | `InvestingSource.fetch_technical_summary()` → `InvSummary` | `technical_signals.inv_summary` (TV ile çakışırsa mutabakat skoru) |
| Investing analist görüşleri | `InvestingSource.fetch_analyst_ratings()` → dict | `recommendations.target_price` (analist konsensüs hedefi) |
| Finviz temel veriler | `FinvizSource.fetch_fundamentals()` → `FundamentalData` | `fundamental.py` skorlama (%20 ağırlık DOC §5.1) — F/K, PD/DD, beta |
| Reddit sentiment | `RedditSource.fetch_recent_posts(ticker=...)` | `sentiment.py` ek kaynak; başlık+body FinBERT'e besleme |

`recommender.py` bu çıktıların hepsini ağırlıklı toplayarak `recommendations`
satırı üretir; `analysis-engine` agent bu modüller hazır olduğunda
implementasyona başlayabilir.

## Test / Doğrulama

Test yazımı `test-engineer`'a bırakıldı (sub-agent kuralı). Beklenen
mock test paketi:

- `tests/test_rss_source.py` — mock feedparser response, since filter
- `tests/test_kap_source.py` — mock httpx (`respx` veya `httpx.MockTransport`)
- `tests/test_tradingview_source.py` — mock tvdatafeed, NEUTRAL fallback
- `tests/test_investing_source.py` — mock html parse, recommendation normalize
- `tests/test_finviz_source.py` — `_parse_number` edge case'leri, mock ticker_fundament
- `tests/test_reddit_source.py` — credential check, mock subreddit.new
- `tests/test_collector_save_news.py` — idempotent insert, instrument_id eşleme

## Notlar / Bilinen Sınırlamalar

1. **Investing slug heuristic:** `_guess_slug()` BIST için ticker'ı
   lower-case alır (genelde çalışır). ABD hisselerinde gerçek Investing
   slug'ı (örn. `apple-computer-inc`) gerekir — ileride
   `instruments` tablosuna `investing_slug` kolonu eklenmesi düşünülebilir
   (database-architect).

2. **TradingView ToS:** `tvdatafeed` guest session login gerektirebilir;
   production'da TV_USERNAME/TV_PASSWORD `.env`'e eklenmesi gerekebilir.
   (Şu an guest mode deneniyor — başarısız olursa SourceUnavailableError.)

3. **KAP endpoint stabilitesi:** KAP web UI JSON endpoint'leri public
   ama dokümante değil. `_FINANCIALS_PATHS` listesi try-chain ile çoklu
   varyantı deniyor. Endpoint değişirse `_raw_to_disclosure` ve
   `fetch_company_financials` güncellenmeli.

4. **DataCollector parallel-fetch sorunu:** `fetch_all_quotes` mevcut
   implementasyonu yeni kaynakların `fetch_quote`'unu çağıracak; çoğu
   `SourceError` döner ve **failure sayar**. Faz 2.1'de `BaseSource`'a
   `supports_quote: bool` flag'i eklenerek collector bu kaynakları
   atlasın. Şimdilik: yeni kaynakları **`DataCollector.sources` listesine
   ekleme**, doğrudan instance üzerinden çağırın.

5. **NewsFeed url unique constraint yok:** Aynı haber 2 farklı feed'de
   geçerse 2 satır yazılır (örn. Reuters + KAP aynı şirket). Bu
   kasıtlı — feed kapsamasını analiz etmek faydalı. Sentiment.py
   istemiyorsa application-level dedup yapar.

6. **Playwright kurulumu:** `playwright install chromium` ilk
   çalıştırmada ~150 MB indirir. CI/headless ortamlarda dependency.
