---
date: 2026-05-27
agent: analysis-engine
phase: faz-2
type: feature
related_files:
  - app/analysis/__init__.py
  - app/analysis/sentiment.py
  - app/analysis/news_aggregator.py
  - app/analysis/recommender.py
related_doc_sections:
  - "3.5 Analiz Kütüphaneleri (Türkçe/İngilizce NLP modelleri)"
  - "5.1 Girdi Sinyalleri"
  - "5.2 Vade Tanımları (kısa vade: %60 / %30 / %10)"
  - "5.3 Öneri Çıktısı"
  - "5.6 Şeffaflık ve Açıklanabilirlik"
  - "9. Veritabanı Şeması (news_feed, recommendations)"
---

## Özet
Faz 2 kapsamında **NLP sentiment analizi** (TR + EN), **haber sentiment
agregasyonu** ve **kısa vade öneri motoru** ilk versiyonu yazıldı. Üç yeni
modül `app/analysis/` altına eklendi; agent `__init__.py` güncellendi. Faz 2
hedefi olan "teknik + sentiment + mutabakat" üçlüsünün kısa vadeli (1–7
gün) AL/BEKLE/SAT önerisine dönüştürülmesi tamamlandı. Orta/uzun vade,
fundamental, backtester ve risk modülleri Faz 3'e bırakıldı.

## Detaylar

### 1. `app/analysis/sentiment.py` — TR + EN sentiment analizi

**Backend tespiti (import-time):**

```
transformers + torch  → BACKEND = "transformers"   (birincil)
ikisi de yoksa        → BACKEND = None             (sadece mock için)
```

`BACKEND=None` modunda `analyze()` çağrıldığında `_load_tr_pipeline` /
`_load_en_pipeline` `RuntimeError` fırlatır — test-engineer mock
yerleştirebilir.

**Modeller:**

| Dil | Model | Çıktı etiketleri |
|---|---|---|
| TR | `savasy/bert-base-turkish-sentiment-cased` | positive / negative / neutral |
| EN | `ProsusAI/finbert` | positive / negative / neutral |

**Lazy load:** `__init__` sırasında ağ çağrısı **YOK**. `_tr_pipeline` ve
`_en_pipeline` ilk başta `None`; ilk `analyze()` çağrısında
`transformers.pipeline("sentiment-analysis", model=...)` ile yüklenir ve
bellekte tutulur. Sonraki çağrılar belleğe alınmış pipeline'ı kullanır.

**Dil otomatik tespiti — `detect_language(text)`:**
- Metinde TR'ye özgü karakter (`ç`, `ğ`, `ı`, `ö`, `ş`, `ü` + büyük
  varyantları) varsa `'tr'`, yoksa `'en'`.
- Boş/None metin → `'en'` (varsayılan FinBERT).
- Daha gelişmiş tespit (`langdetect`/`fasttext`) Faz 3'te eklenebilir.

**Skor normalize (`_signed_score`):**

| Etiket | Sonuç |
|---|---|
| positive | `+raw_score` (0..1) |
| negative | `-raw_score` (−1..0) |
| neutral  | `0.0` |

Sonuç `SentimentResult.score` alanında `[-1, +1]` aralığındadır. Ham
olasılıklar `raw_scores` dict'inde audit için saklanır.

**İroni / düşük güven uyarısı:** ham model olasılığı
`LOW_CONFIDENCE_THRESHOLD = 0.6` altındaysa `SentimentResult.notes`
listesine Türkçe uyarı eklenir: _"Düşük model güveni (%xx);
ironi/alay veya sektörel jargon nedeniyle yanlış olabilir."_

**API:**
- `SentimentAnalyzer(tr_model_name=None, en_model_name=None, backend=None)`
- `detect_language(text) -> str`
- `analyze(text, language=None) -> SentimentResult`
- `analyze_batch(texts, language=None) -> list[SentimentResult]`
  (şimdilik tekil çağrı döngüsü — transformers batching Faz 3)
- `save_news_sentiment(session, news_id, result)` — `NewsFeed.sentiment`,
  `NewsFeed.sentiment_score`, `NewsFeed.processed_at` günceller.
  Commit caller sorumluluğunda.

### 2. `app/analysis/news_aggregator.py` — hisse bazlı sentiment ortalaması

`NewsAggregator(session_factory)` — `session_factory` callable, her
çağrıda yeni `Session` döndürmeli (sessionmaker veya context-manager).

**API:**
- `await aggregate_sentiment(instrument_id, lookback_days=7, language=None)`
  → `(mean_score, count)` döndürür. `mean_score` `[-1, +1]`, hiç satır
  yoksa `(None, 0)`. `sentiment_score IS NULL` olan satırlar (henüz
  işlenmemiş haberler) ortalamaya dahil edilmez.
- `await aggregate_by_language(instrument_id, lookback_days=7)` →
  `{"tr": (mean, count), "en": (mean, count)}` UI kırılım göstergesi için.

SQL: `SELECT AVG(sentiment_score), COUNT(id) FROM news_feed WHERE
instrument_id=? AND sentiment_score IS NOT NULL AND published_at >= NOW()
- INTERVAL '7 days'`. SQLAlchemy 2.0 `select() / func.avg / func.count`
ile yazıldı.

API async fakat DB erişimi sync — Faz 3'te `asyncio.to_thread` veya
SQLAlchemy `AsyncSession`'a terfi düşünülebilir.

### 3. `app/analysis/recommender.py` — kısa vade öneri motoru

**Ağırlıklar (Doküman §5.2, sabit `SHORT_TERM_WEIGHTS`):**

| Sinyal | Ağırlık |
|---|---|
| Teknik | %60 |
| Sentiment | %30 |
| Kaynak mutabakatı (TV + Investing) | %10 |

Konstrüktör `weights=` ile override edilebilir; toplam 1.0 olmazsa
`ValueError`. Backtester Faz 3'te otomatik optimize edecek.

**Alt skorlar (hepsi 0..100; 50 nötr):**

- `score_technical(snapshot)` — RSI / MACD / EMA / Bollinger pozisyonunu
  heuristik ağırlıklarla birleştirir. Başlangıç 50, sonra ±5..±20 puanlık
  bileşenler eklenir, sonuç clamp.
- `score_sentiment(sentiment_score, count)` — `[-1, +1]` aralığını
  `50 + s × 50` ile 0..100'e taşır. `count < 3` ise skor 50'ye doğru ½
  oranında çekilir (örneklem düşük güveni).
- `score_consensus(tv, inv)` — TV/Inv özet etiketlerini
  STRONG_BUY=100, BUY=75, NEUTRAL=50, SELL=25, STRONG_SELL=0 olarak
  eşler; her ikisi varsa ortalama, biri yoksa diğerinin değeri,
  ikisi de yoksa 50.

**`action` eşikleri:**

| Bileşik skor | Action |
|---|---|
| `>= 60` | `BUY` |
| `<= 40` | `SELL` |
| arada  | `HOLD` |

**`confidence`** = `|composite − 50| × 2` — yani 0..100 (tam nötr = 0,
uçlarda 100).

**Risk (`calculate_risk` → `(level, score)`):**

| ATR/close (%) | level | score |
|---|---|---|
| `< 2.0` | low | `0..33` lineer |
| `2.0 – 5.0` | medium | `33..66` lineer |
| `>= 5.0` | high | `66..100` clamp |

RSI uç bölgesindeyse (`>= 80` veya `<= 20`) risk skoru `+10` artar,
`low` → `medium`'a yükselir.

**`target_price` (yalnızca BUY):** Bollinger üst bandı veya
`close + ATR`'den büyüğü; ikisi de yoksa `current_price × 1.03` fallback.
SAT/HOLD → `None`.

**Açıklanabilirlik — `summary` örneği:**

```
Kısa vade AL önerisi (güven %72). Teknik analiz pozitif (skor 78/100;
RSI=28.0 aşırı satım, MACD pozitif cross, trend yukarı (EMA20>50>200)).
Sentiment pozitif (5 haberden ortalama +0.40). Mutabakat: TV=BUY,
INV=NEUTRAL (skor 62/100). Risk: düşük.
```

`contributions` dict ek olarak `{tech: ağırlıklı_katkı, sentiment: ...,
consensus: ...}` döndürür — UI Faz 3'te pasta dilimi olarak gösterebilir.

**DB persist — `save_recommendation(session, output, instrument_id)`:**
`recommendations` tablosuna bir satır ekler.

| Alan | Değer |
|---|---|
| `action` | BUY/HOLD/SELL |
| `timeframe` | `'short'` (sabit Faz 2) |
| `confidence`, `tech_score`, `sentiment_score`, `risk_score` | 0..100 float |
| `risk_level` | low/medium/high |
| `target_price` | Decimal veya NULL |
| `summary` | Türkçe açıklama |
| `fundamental_score`, `stop_loss`, `take_profit` | **NULL** (Faz 3) |

DB CHECK constraint'leri: `action IN ('BUY','HOLD','SELL')` ve
`timeframe IN ('short','mid','long')` — çıktı bunlarla uyumlu.

### 4. `app/analysis/__init__.py` güncellemesi
Faz 2 tipleri ihraç edildi: `SentimentAnalyzer`, `SentimentLabel`,
`SentimentResult`, `LOW_CONFIDENCE_THRESHOLD`, `SENTIMENT_BACKEND`,
`NewsAggregator`, `ShortTermRecommender`, `RecommendationInput`,
`RecommendationOutput`, `SHORT_TERM_WEIGHTS`, `BUY_THRESHOLD`,
`SELL_THRESHOLD`, `MIN_SENTIMENT_COUNT`.

## Gerekçe
Doküman §11 Faz 2'de "NLP sentiment analizi" ve "öneri motoru ilk
versiyonu" listelenmiş. §5.2 kısa vade ağırlıklarını **%60 teknik + %30
sentiment + %10 mutabakat** olarak sabitliyor; bu modülde aynı dağılım
uygulandı.

Orta vade fundamental ağırlığı (%35) ve uzun vade fundamental (%50) için
`fundamental.py` ve farklı `Recommender` sınıfları gerekecek; bunlar Faz
3'e bırakıldı çünkü:
1. Temel veri kaynağı (KAP finansal tablolar, F/K) henüz collector
   tarafında yapılandırılmadı.
2. Backtester olmadan ağırlıklar keyfi kalır — Faz 3'te
   backtest-optimize ile birlikte yazılması mantıklı.
3. Stop-loss / take-profit teknik seviye hesabı risk.py'e ait; o da Faz
   3'te ATR pozisyon büyüklüğü ile birlikte yazılacak.

Sentiment modellerinde lazy load tercih edildi çünkü FinBERT ~440MB,
TR BERT ~440MB — uygulamanın açılış süresini artırmamak için ilk
analizden önce indirilmemeli. Test ortamında pipeline slot mock'lanır.

## Test / Doğrulama
- Sandbox'ta Python yorumlayıcısı yok; çalıştırılabilir test koşulamadı.
- AST/syntax elle gözden geçirildi.
- `test-engineer` için önerilen testler (Faz 2 unit testleri ayrı çağrıda
  yazılacak):
  - `tests/test_sentiment.py`
    - `BACKEND` `'transformers'` veya `None`.
    - `detect_language("Türkçe haber içeriği")` → `'tr'`,
      `detect_language("US Federal Reserve")` → `'en'`.
    - `SentimentAnalyzer(backend=None)` → `_load_tr_pipeline()` çağrısı
      `RuntimeError`.
    - `monkeypatch.setattr(analyzer, "_tr_pipeline", lambda t:
      [{"label": "POSITIVE", "score": 0.92}])` ile `analyze` çıktısı
      doğrulanır: `label=POSITIVE`, `score≈0.92`.
    - Ham olasılık 0.55 → `notes` non-empty (düşük güven uyarısı).
    - `save_news_sentiment`: stub session ile satır güncellemesi.
  - `tests/test_news_aggregator.py`
    - In-memory SQLite + `NewsFeed` fixture: 5 satır, sentiment_score
      [+0.5, -0.2, +0.8, NULL, +0.1] (NULL hariç ortalama ~+0.30).
    - `await aggregate_sentiment(instr_id)` → `(0.30, 4)` ya da yakın.
    - Boş tablo → `(None, 0)`.
    - `lookback_days` filtre testi (cutoff'tan eski satırın atlandığı).
  - `tests/test_recommender.py`
    - `score_technical`: RSI=25, MACD>signal, EMA uptrend → skor > 70.
    - `score_sentiment(0.6, 5)` ≈ 80; `score_sentiment(0.6, 1)` (low n)
      ≈ 65 (50'ye doğru çekilmiş).
    - `score_consensus("STRONG_BUY", "BUY")` ≈ 87.5; `(None, None)` → 50.
    - `calculate_risk` ATR yüzdesine göre branch kapsamı.
    - `recommend` end-to-end: `RecommendationInput` → `BUY` action,
      confidence > 60, summary Türkçe, contributions toplamı bileşik
      skora yakın.
    - `save_recommendation`: stub session.add çağrısı, DB CHECK uyumlu
      `action`/`timeframe`.

## Notlar

### Tüketici agent'lar için duyurular

- **devops-engineer**:
  - `transformers ^4.36` ve `torch ^2.1` `pyproject.toml`'da bağımlılık
    olarak mevcut olmalı. CUDA olmasa da `torch` CPU wheel yeterli
    (FinBERT/TR-BERT inference için).
  - İlk açılışta TR + EN modeli toplam ~880MB disk + bant genişliği
    tüketir; kullanıcı bir kez `analyze()` çağırdığında indirme başlar.
    `HF_HOME` env değişkeniyle önbellek yeri kontrol edilebilir.
  - CI'da gerçek model indirme yok — `SentimentAnalyzer(backend=None)`
    ve pipeline slot mock yeterli.

- **data-collector**:
  - `NewsAggregator.aggregate_sentiment` `news_feed.sentiment_score`
    `NULL` olan satırları atlar. Collector RSS satırı eklediğinde
    sentiment hesabı async görevle (örn. arka plan worker) tetiklenmeli;
    `SentimentAnalyzer.save_news_sentiment` API'sini kullansın.

- **test-engineer**:
  - `SentimentAnalyzer(backend=None)` test varsayılan modu.
  - `SHORT_TERM_WEIGHTS`, `BUY_THRESHOLD`, `SELL_THRESHOLD`,
    `MIN_SENTIMENT_COUNT`, `LOW_CONFIDENCE_THRESHOLD` sabitleri
    parametre tarama testlerinde fixture olarak kullanılabilir.
  - `NewsAggregator` `session_factory` callable bekler — testte
    `lambda: sessionmaker(bind=in_memory_engine)()` veya basit
    `session_factory = lambda: existing_session` yeterli.

- **ui-developer**:
  - `RecommendationOutput.summary` doğrudan kart altında Türkçe
    açıklama olarak gösterilebilir (UI'da format=`plain`).
  - `RecommendationOutput.contributions` dict'i (tech / sentiment /
    consensus → ağırlıklı katkı) ileride pasta grafiği için kullanılabilir.
  - `SentimentResult.notes` listesindeki uyarılar haber kartında
    küçük italic etiket olarak gösterilmeli (ironi uyarısı).

- **database-architect** (bilgi):
  - Faz 2'de DB şemasında değişiklik **YOK**. `news_feed.sentiment`,
    `news_feed.sentiment_score`, `news_feed.processed_at`,
    `recommendations.*` alanları mevcut (mevcut migration `0001`).
  - Faz 3 risk modülü için `recommendations.stop_loss`,
    `recommendations.take_profit` zaten mevcut; doldurma yalnızca Faz 3'te
    aktif olacak (şu an `NULL` yazılıyor).

### Faz 3 için TODO listesi
- `app/analysis/fundamental.py` — F/K, PD/DD, büyüme, sektör (KAP +
  yfinance financials).
- `MidTermRecommender` — `%40 teknik + %35 fundamental + %25 sentiment`.
- `LongTermRecommender` — `%50 fundamental + %30 teknik + %20 sentiment`.
- `app/analysis/risk.py` — ATR pozisyon büyüklüğü, stop-loss/take-profit
  teknik seviye hesabı, korelasyon matrisi.
- `app/analysis/backtester.py` — vectorbt, walk-forward, look-ahead bias
  önleme; sonuç → `backtest_results` tablosuna.
- Sentiment için transformers batching + GPU desteği (opsiyonel).
- `langdetect` veya `fasttext` ile daha doğru dil tespiti.
- Sosyal medya (Reddit/Twitter) sentiment'inin haber sentimenti ile
  ayrı kanal olarak birleştirilmesi.

### Bilinen sınırlamalar
- TR modeli (`savasy/bert-base-turkish-sentiment-cased`) genel amaçlı —
  finansal jargon (lot, taban-tavan, halka arz) için fine-tuned değil.
  Faz 4'te fine-tuning veya `dbmdz/bert-base-turkish-cased` üzerine
  finansal corpus eğitimi düşünülebilir.
- `analyze_batch` şu an gerçek batching yapmıyor (loop). `transformers`
  pipeline `batch_size=` parametresi Faz 3'te eklenecek.
- `score_technical` heuristik puanlama; backtester olmadan ağırlıklar
  kanıtlanmamış. Faz 3'te backtest-optimize edilecek.
- `NewsAggregator` async fakat sync SQLAlchemy session kullanıyor — IO
  blocking. Faz 3'te `AsyncSession`'a terfi planlanmalı.
- Bağımlılık değişikliği YOK — `pyproject.toml`'da `transformers ^4.36`,
  `torch ^2.1` zaten tanımlı (agent yaml'daki dependencies satırı).
  Devops doğrulamalı.
