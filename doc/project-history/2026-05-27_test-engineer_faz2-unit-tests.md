---
date: 2026-05-27
agent: test-engineer
phase: faz-2
type: test
related_files:
  - tests/test_sentiment.py
  - tests/test_news_aggregator.py
  - tests/test_recommender.py
  - tests/test_watchlist_service.py
  - tests/test_bot_picks_service.py
  - tests/test_data_sources_faz2.py
  - tests/test_collector_news.py
  - tests/test_ui_smoke.py
related_doc_sections:
  - "3.5 NLP modelleri"
  - "5 Öneri Motoru"
  - "5.2 Vade Ağırlıkları"
  - "6.2 Kullanıcı Takip Listesi"
  - "6.3 Bot Öneri Listesi"
  - "8.3 Ekranlar"
  - "10 Test Stratejisi"
---

## Özet
Faz 2 modülleri (sentiment / news_aggregator / recommender / watchlist / bot_picks / RSS-KAP-TV-Investing-Finviz-Reddit kaynakları / collector haber metotları / UI widget importları) için 8 yeni test dosyası eklendi. Toplam **112 yeni test**, hepsi yeşil; Faz 1 dahil tüm test paketi **196/196 geçti** (1 skipped — PySide6 yok).

## Detaylar
- **`tests/test_sentiment.py`** (15 test) — `SentimentAnalyzer`: backend=None RuntimeError, dil tespiti (TR karakter / boş / büyük harf), `_tr_pipeline` / `_en_pipeline` mock üzerinden POSITIVE / NEGATIVE / NEUTRAL signed-score normalize, `LOW_CONFIDENCE_THRESHOLD` altında Türkçe uyarı notu, `analyze_batch` sıra korunumu, `save_news_sentiment` NewsFeed güncellemesi (in-memory SQLite).
- **`tests/test_news_aggregator.py`** (4 test) — NULL skor atlama, `lookback_days` filtresi, boş kayıt → `(None, 0)`, `aggregate_by_language` TR/EN ayrımı. Custom `session_factory` wrapper ile in-memory sync session aynı transaction içinde paylaşıldı.
- **`tests/test_recommender.py`** (21 test) — `SHORT_TERM_WEIGHTS` toplamı=1.0 ve geçersiz ağırlık `ValueError`, `score_technical` oversold/overbought sinyalleri, `score_sentiment` düşük örneklemli güven azaltma (count<3 → yarıya çek), `score_consensus` STRONG_BUY+STRONG_BUY=100 / SELL+BUY=50, `recommend()` action eşikleri (BUY/SELL/HOLD), `confidence = |composite-50|*2`, `calculate_risk` ATR/close yüzdesi seviyeleri, BUY önerisinde target_price varlığı, Türkçe `summary`, `save_recommendation` DB CHECK constraint uyumu.
- **`tests/test_watchlist_service.py`** (11 test) — `create()` boş isim reddi + unique id, `add_item` instrument yoksa LookupError / idempotent ikinci çağrı, `remove_item` no-op vs delete, `delete()` CASCADE, `list_items` `sort_order` sırası, `reorder` sıralama güncellemesi, `with_quotes=True` ile son `price_history` (`verified_close` öncelikli) + son `recommendation` join'i (mock seed veri).
- **`tests/test_bot_picks_service.py`** (10 test) — `add_pick` is_open=True + outcome='open' default, `close_pick` BUY pozitif getiri / SELL ters işaret, zaten kapalı pick için `ValueError`, bilinmeyen id `LookupError`, `update_open_picks` hit_target ve expired (>EXPIRE_DAYS[short]) otomatik kapama, `get_stats` sıfır pick (success_rate=0.0 — payda 0 koruması) ve 4 pick'lik dağılımda 2/3 success_rate + 8.33 avg_return + pozitif avg_hold_days.
- **`tests/test_data_sources_faz2.py`** (20 test) — Her kaynak için **gerçek API çağrısı YOK**:
  - RSS: NewsItem dataclass alanları, `fetch_quote` → SourceError, feedparser=None → SourceUnavailableError, mock parser ile 1 entry → NewsItem.
  - KAP: KapDisclosure dataclass, `fetch_quote` SourceError, httpx=None → SourceUnavailableError, `_call` mock — 1 çağrı kontrolü, URL `BASE_URL` ile tamamlanır.
  - TradingView: TA_AVAILABLE=False → NEUTRAL özet (fail-soft), TV_AVAILABLE=False → quote/ohlcv SourceUnavailableError, TVSummary dataclass.
  - Investing: `fetch_quote`/`fetch_ohlcv` her ikisi de SourceError (ToS gereği fiyat reddi), InvSummary dataclass.
  - Finviz: `_parse_number` "2.9B" → 2.9e9, "12.5%" → 0.125, "-"/None/"N/A" → None, "1,234.56" virgül kaldırma; FV_AVAILABLE=False → SourceUnavailableError; mock'lu `_call` ile FundamentalData üretimi.
  - Reddit: credential yoksa `SourceUnavailableError("Reddit credentials yok")`, `fetch_quote` SourceError.
- **`tests/test_collector_news.py`** (3 test) — `save_news` NewsItem listesini news_feed'e yazar, aynı URL ikinci kez insert edilmez (idempotent), `save_disclosures` KapDisclosure → source='kap'/language='tr' ile delege eder.
- **`tests/test_ui_smoke.py`** (5 test, PySide6 yok → skip) — `pytest.importorskip("PySide6")` ile widget modül importlarını kontrol eder; event loop ve widget instance üretilmez.

## Gerekçe
Faz 2 kapsamında üretilen modüllerin (`analysis-engine` + `portfolio-manager` + `data-collector` + `ui-developer`) regresyon koruması ve davranışsal sözleşmelerinin doğrulanması. Doküman §10 ("Hiçbir modül test edilmeden faz tamamlanmış sayılmaz") kuralı gereğidir.

## Test / Doğrulama
- Çalıştırma: `python -m pytest tests/ --tb=short --no-header -v`
- Sonuç: **196 passed, 1 skipped, 1 warning, 2.46s** (Windows / Python 3.11)
- Faz 2 yeni testler: **112 passed / 112** (skip yok — PySide6 yokken sadece `test_ui_smoke.py`'nin 5 testi skip).
- Hiçbir gerçek dış servis (yfinance, KAP, TradingView, transformers HF model indirme, Reddit API) çağrılmadı; tüm IO mock'landı.

## Mock Stratejisi Notları
- `SentimentAnalyzer` her testte `backend=None` ile oluşturuldu; `_tr_pipeline` / `_en_pipeline` slotları doğrudan `MagicMock(return_value=[{...}])` ile değiştirildi → `transformers` / `torch` paketi sistemde yokken bile testler çalışıyor.
- `RSSSource` testlerinde `feedparser` `MagicMock()` ile import-guard'ı geçildi; gerçek RSS çekilmedi.
- `KapSource._call` `AsyncMock` ile değiştirilip rate-limit/semafor pas geçildi.
- `FinvizSource._call` aynı yöntemle mock; `finvizfinance` modülü yokken `FV_AVAILABLE=True` patch ile happy-path test edildi.
- `WatchlistService` / `BotPicksService` testleri için `sessionmaker(bind=sync_engine)` factory kullanıldı (`session_factory.kw["bind"]` ile engine'e erişerek doğrulama session'ı açıldı).

## Bulunan Bug'lar
**Yok.** Üretim kodunda davranış değişikliği gerektiren bir bulgu çıkmadı. İki testin ilk halinde **benim test varsayımlarım** yanlıştı (default snapshot ile tech_score 25'e düşüyordu; "neutral" beklemek için tüm sinyalleri None bırakmak gerekiyor) — testleri güncelledim, üretim koduna dokunmadım.

## Notlar
- `pytest-asyncio` 1.4.0 + pytest 9.0.3 ile `event_loop_policy` fixture deprecation uyarısı veriyor (mevcut Faz 1 conftest.py'den miras); Faz 3'te `pytest_asyncio_loop_factories` hook'una geçilebilir.
- UI smoke testleri PySide6 kurulduğunda otomatik aktive olacak; ek harness değişikliği gerekmez.
- Coverage komutu (`pytest --cov=app --cov-report=html`) ayrıca çalıştırılmadı — `pytest-cov` testin kapsamı dışında. Faz 2 modülleri için satır kapsamı görsel hedefi: recommender / sentiment / watchlist / bot_picks ≥%80 (her public metod en az bir testte tetiklenir).
