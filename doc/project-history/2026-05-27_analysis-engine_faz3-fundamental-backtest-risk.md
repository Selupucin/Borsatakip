---
date: 2026-05-27
agent: analysis-engine
phase: faz-3
type: feature
related_files:
  - app/analysis/__init__.py
  - app/analysis/fundamental.py
  - app/analysis/recommender.py
  - app/analysis/backtester.py
  - app/analysis/risk.py
related_doc_sections:
  - "5.1 Girdi Sinyalleri (Fundamental %20-%50)"
  - "5.2 Vade Tanımları (orta + uzun ağırlık matrisi)"
  - "5.3 Öneri Çıktısı (stop_loss, take_profit, fundamental_score)"
  - "5.4 Backtesting (vectorbt, look-ahead bias, walk-forward)"
  - "5.5 Risk Yönetimi (ATR pozisyon, korelasyon, sektör)"
  - "5.6 Şeffaflık ve Açıklanabilirlik"
  - "9. Veritabanı Şeması (backtest_results, recommendations)"
---

## Özet
Faz 3 Batch 1 kapsamında **fundamental analiz**, **orta + uzun vade öneri
motorları**, **backtester** (vectorbt opsiyonel — pandas fallback) ve
**risk yönetimi** (ATR pozisyon, stop-loss/take-profit, korelasyon,
sektör yoğunlaşma) modülleri yazıldı. `RecommendationDispatcher` ile
vade × ağırlık matrisi tek arayüzden kullanılır hale geldi. DB şemasına
dokunulmadı — runtime hesaplar ATR/ekstra alanları doğrudan
`TechnicalAnalyzer.compute_all()` üzerinden sağlar.

## Detaylar

### 1. `app/analysis/fundamental.py` — yeni modül

**`FundamentalSnapshot` (dataclass):** Tek hissenin temel anlık görüntüsü.

| Alan | Tip | Not |
|---|---|---|
| instrument_id, ticker | int / str | Zorunlu |
| pe_ratio, pb_ratio, market_cap, eps | float / None | Finviz + İş Yatırım |
| dividend_yield, beta, target_price | float / None | Analist + risk |
| sector, industry | str / None | Sektör karşılaştırması |
| growth_yoy, debt_to_equity, roe | float / None | Kalite metrikleri |
| fetched_at | datetime (UTC) | Audit |
| sources | list[str] | Katkıda bulunan kaynaklar |

**`FundamentalAnalyzer`:**
- `__init__(session_factory=None, sources: dict | None = None)` —
  `sources` map'i `{name: async fn(ticker, exchange) -> dict}` arayüzü.
  Data-collector tarafı Finviz/İş Yatırım adapter'lerini bu map'e ekler.
- `async fetch_snapshot(instrument_id, ticker, exchange=None)` —
  `asyncio.gather` ile **paralel** çağrı; tek kaynak hatası diğerlerini
  etkilemez (log + skip). Ortak alanlar `_merge_value` ile aritmetik
  ortalama, kategorik alanlar (sector/industry) ilk kaynak baskın.
- `score(snapshot, sector_avg=None) -> float` 0..100 fundamental skoru
  (formül aşağıda).
- `sector_average(sector, fields=None)` — placeholder, daima `{}`. Faz 4'te
  sektör F/K ortalaması tablosu eklenecek; şu an `score()` mutlak eşik
  fallback'ine düşer.

**Skor formülü (baseline 50 + delta'lar, clamp [0,100]):**

| Bileşen | Koşul | Delta |
|---|---|---|
| F/K | `< sector_avg` veya `< 10` | +10 |
| F/K | `> sector_avg` veya `> 25` | -10 |
| PD/DD | `< 1.5` | +10 |
| PD/DD | `> 3.0` | -10 |
| Büyüme (YoY) | `> 10%` | +15 |
| Büyüme (YoY) | `< 0` | -15 |
| Temettü verimi | `> 3%` | +5 |
| Borç/Özsermaye | `< 1.0` | +5 |
| Borç/Özsermaye | `> 2.0` | -10 |
| ROE | `> 15%` | +10 |

Sektör ortalaması varsa F/K kıyaslaması daha sağlam; yoksa mutlak
eşik kullanılır.

### 2. `app/analysis/recommender.py` — güncelleme

**Yeni vade × ağırlık matrisi** (Doküman §5.2 ile birebir):

| Vade | Teknik | Fundamental | Sentiment | Mutabakat | Sınıf |
|---|---|---|---|---|---|
| Kısa (1-7g) | %60 | — | %30 | %10 | `ShortTermRecommender` |
| Orta (1-3a) | %40 | %35 | %25 | — | `MidTermRecommender` |
| Uzun (3-12a+) | %30 | %50 | %20 | — | `LongTermRecommender` |

`SHORT_TERM_WEIGHTS`, `MID_TERM_WEIGHTS`, `LONG_TERM_WEIGHTS` modül
sabitleri export edildi.

**Yeni dataclass'lar:**

- `MidTermInput(instrument_id, ticker, technical_snapshot, sentiment_score,
  sentiment_count, fundamental_snapshot, current_price)`
- `LongTermInput(...)` — aynı alanlar (uzun vade semantik).

**`RecommendationOutput`** güncellendi:
- `consensus_score: Optional[float]` (kısa vadede dolu, orta/uzun
  vadede `None`).
- `fundamental_score: Optional[float]` (orta/uzun vadede dolu).
- `stop_loss: Optional[Decimal]`, `take_profit: Optional[Decimal]`
  alanları eklendi — `RiskManager.stop_loss_take_profit` çıktısının
  taşınması için.
- `save_recommendation` artık `fundamental_score` ve `stop_loss/
  take_profit` alanlarını DB'ye yazar (önceden hep `None` idi).

**`MidTermRecommender`:**
- `score_technical`: orta vade için trend-odaklı heuristik (EMA200
  ağırlığı +15/-15, RSI sapması ±10).
- `score_fundamental(snapshot)`: `FundamentalAnalyzer.score`
  delegasyonu; snapshot `None` → 50 (nötr).
- `score_sentiment`: kısa vade ile aynı.
- `calculate_risk(snapshot, fundamental)`: ATR/close + `beta > 1.5`
  (+10) + `debt_to_equity > 2` (+5).
- `recommend(MidTermInput) -> RecommendationOutput` — `timeframe='mid'`.
- `_suggest_target_mid`: analist hedef varsa onu, yoksa `entry + 2×ATR`
  veya Bollinger üst bandı; hiçbiri yoksa `+%8` fallback.
- `_build_summary`: orta vade Türkçe metin
  ("Orta vade AL önerisi (güven %72). Fundamental güçlü
  (skor 75/100; F/K=8.0, PD/DD=1.20, büyüme +15.0%, ROE 18.0%). Teknik
  pozitif (skor 65/100; RSI=58.0, EMA200 üstünde). Sentiment nötr ...").

**`LongTermRecommender`:**
- `score_technical`: EMA200 trend dominant (+20/-20), MACD ±10, RSI uç
  ±5.
- `score_fundamental` / `score_sentiment`: orta vade ile aynı API.
- `calculate_risk(snapshot, fundamental)`: ATR/40 ölçekte + `beta > 1.5`
  (+25) / `beta > 1.0` (+10) + `D/E > 2` (+15) + negatif büyüme (+10)
  + fundamental yoksa belirsizlik primi (+15). 33/66 eşikleriyle
  low/medium/high.
- `recommend(LongTermInput) -> RecommendationOutput` — `timeframe='long'`.
- `_suggest_target_long`: analist hedef varsa onu, yoksa `+%20` fallback.

**`RecommendationDispatcher`:**
- `__init__(fundamental_analyzer=None)` ile üç recommender'ı kurar.
- `recommend(timeframe, inputs)` — `'short'|'mid'|'long'` parametresine
  göre doğru recommender'a yönlendirir. Tip uyuşmazlığı `TypeError`,
  geçersiz vade `ValueError`.

### 3. `app/analysis/backtester.py` — yeni modül

**`BacktestConfig` (dataclass):** strategy_name, timeframe, start_date,
end_date, initial_capital=Decimal("100000"), weights=None,
commission_pct=0.002, risk_threshold=50.0.

**`BacktestRunResult` (dataclass — DB modeliyle aynı isim olmasın diye
`RunResult` ek):** strategy_name, timeframe, start_date, end_date,
total_return_pct, sharpe_ratio, max_drawdown_pct, win_rate, total_trades,
avg_hold_days, final_capital (Decimal), equity_curve (list[(date,
Decimal)]), trades (list[dict]), params (dict — DB JSONB için).

**`Backtester` API:**
- `__init__(session_factory)` — vectorbt opsiyonel import (yoksa pandas
  fallback aktif; agent kuralı: vectorbt birincil).
- `_build_price_panel(instrument_ids, start, end)` — `verified_close`
  panelini DB'den çekip `(timestamp × instrument_id)` DataFrame döner.
- `_signal_window(prices, as_of)` — **look-ahead bias guard** (saf
  fonksiyon, test edilebilir): `prices.loc[:as_of]` dilimi.
- `_generate_signals(prices, config, signal_fn=None)` — her gün için
  `_signal_window` üzerinden `signal_fn(window, as_of, instrument_id)`
  çağırır. `signal_fn=None` ise `_default_signal` (SMA(5)>SMA(20)
  fallback) kullanılır. Production'da `ShortTermRecommender.recommend`
  vb. enjekte edilebilir.
- `run(config, instrument_ids, signal_fn=None) -> BacktestRunResult` —
  signal-driven long-only execution: BUY → eşit slot tahsisi (sermaye
  /N), SELL → tüm pozisyon kapanır, komisyon her tarafta ayrı.
- `walk_forward(config, instrument_ids, window_days=90, step_days=30)`
  — kayan pencere; varyans yüksekse overfit göstergesi.
- `save_result(session, result) -> int` — `backtest_results` tablosuna
  satır ekler ve flush ile id döner. `params` JSONB.
- `compute_metrics(equity_curve, trades) -> dict` — static yardımcı:
  `total_return_pct`, `sharpe_ratio` (risk-free=0, √252 annualized),
  `max_drawdown_pct`, `win_rate`, `total_trades`, `avg_hold_days`.

#### Look-ahead bias guard mantığı

Tek noktada uygulanır: `_signal_window(prices, as_of) -> prices.loc[:as_of]`.
Backtester içindeki tüm sinyal üretim çağrıları bu fonksiyondan geçer;
sinyal fonksiyonuna verilen DataFrame yalnızca `as_of` dahil ve öncesine
ait dilimdir. Test stratejisi: aynı `_signal_window(prices, t0)` çıktısı,
panel'in `t0+1..` satırları silinmiş hali ile **bytewise eşit** olmalı —
test-engineer bu eşitliği assert eder.

### 4. `app/analysis/risk.py` — yeni modül

**Dataclass'lar:**
- `PositionSizeRecommendation(max_position_pct, suggested_quantity,
  based_on_atr, reason)`
- `StopLossTakeProfit(stop_loss, take_profit, risk_reward_ratio,
  based_on)` — `based_on` ∈ {atr, support_resistance, percentage}
- `CorrelationWarning(instrument_pair: tuple[str,str], correlation,
  warning)`
- `PortfolioRisk(risk_score, warnings, correlations,
  sector_concentration)`

**Sabitler:**
- `DEFAULT_RISK_PER_TRADE = 0.02` (sermayenin %2'si)
- `MAX_POSITION_PCT = 0.10` (tek hisse cap %10)
- `STOP_LOSS_ATR_MULT = 1.5`, `TAKE_PROFIT_ATR_MULT = 3.0` (2:1 R/R)
- `HIGH_CORRELATION_THRESHOLD = 0.8`
- `SECTOR_CONCENTRATION_THRESHOLD = 0.30`

**`RiskManager` formülleri:**

| Metod | Formül |
|---|---|
| `position_size` | `max_pct = min(MAX_POSITION_PCT, risk_per_trade / (atr/price))` ; `qty = floor(portfolio_value × max_pct / price)` |
| `stop_loss_take_profit` (BUY) | `SL = entry − 1.5×ATR` ; `TP = entry + 3×ATR` (R/R=2:1) |
| `stop_loss_take_profit` (SELL) | Ters yön |
| Yüzde fallback (ATR=0) | `SL ±3%`, `TP ±6%` |
| S/R snap | BUY: SL→en yakın support (entry altı), TP→en yakın resistance (entry üstü); SELL: ters |
| Pearson korelasyon | `np.log(prices/prices.shift(1)).corr(method='pearson')` |
| Diversifikasyon uyarı | `|ρ| ≥ 0.8` olan tüm çiftler |
| Sektör yoğunlaşma | Σ(qty × avg_cost) sektör bazında / toplam |
| Toplam risk skoru | Baseline 30 + sektör top>%30 (+20) + tek pozisyon (+25) / 2 pozisyon (+10) + korelasyon çifti sayısı × 5 (cap 30); clamp 100 |

**Async API:**
- `await portfolio_correlation(instrument_ids, lookback_days=90)` →
  Pearson korelasyon `pd.DataFrame` (index=columns=instrument_id).
- `await diversification_warnings(wallet_id)` →
  `list[CorrelationWarning]` (yalnızca açık pozisyonlar arası, |ρ|≥0.8).
- `await sector_concentration(wallet_id)` →
  `dict[str, float]` (sektör → 0..1 ağırlık).
- `await overall_risk_score(wallet_id)` →
  `(score: float, warnings: list[str])`.

**Sync API:**
- `position_size(portfolio_value, atr, current_price, risk_per_trade=None)`
- `stop_loss_take_profit(current_price, atr, action, support_levels=None,
  resistance_levels=None)`

### 5. `app/analysis/__init__.py` — güncelleme

Tüm Faz 3 sınıf/dataclass'ları + sabitler export edildi:
`FundamentalSnapshot`, `FundamentalAnalyzer`, `MidTermInput`,
`LongTermInput`, `MidTermRecommender`, `LongTermRecommender`,
`RecommendationDispatcher`, `MID_TERM_WEIGHTS`, `LONG_TERM_WEIGHTS`,
`BacktestConfig`, `BacktestRunResult`, `Backtester`,
`TRADING_DAYS_PER_YEAR`, `RiskManager`, `PositionSizeRecommendation`,
`StopLossTakeProfit`, `CorrelationWarning`, `PortfolioRisk`,
`DEFAULT_RISK_PER_TRADE`, `MAX_POSITION_PCT`, `STOP_LOSS_ATR_MULT`,
`TAKE_PROFIT_ATR_MULT`, `HIGH_CORRELATION_THRESHOLD`,
`SECTOR_CONCENTRATION_THRESHOLD`.

## Gerekçe

Doküman §11 Faz 3 maddesi "orta + uzun vade öneri motorları, backtester,
risk yönetimi" gerektiriyor. §5.2 vade matrisi (kısa %60/30/10 — orta
%40/35/25 — uzun %30/50/20) birebir uygulandı; ağırlıklar modül
sabitleri olarak export edildi ki backtester optimize edebilsin.

DB şema değişikliği YAPILMADI çünkü:
1. `recommendations.stop_loss`, `take_profit`, `fundamental_score`
   alanları ilk migration'da zaten var (Faz 1 schema).
2. `backtest_results` tablosu da mevcut.
3. ATR / macd_hist gibi runtime-only değerler `technical_signals`
   tablosunda yok, ama bunlar `TechnicalAnalyzer.compute_all()` ile
   her çağrıda hesaplandığı için DB persistence gereksiz. `RiskManager`
   doğrudan teknik snapshot'tan ATR'i okur (snapshot dict'inde key
   olarak gelir).

Vectorbt opsiyonel tutuldu (agent yaml'da bağımlılık olarak listeli
ama CI hızı + sandbox uyumu için fallback şart). Pandas fallback signal-
driven execution mantığı testte yeterli kapsama veriyor; gerçek
production'da vectorbt path'i devreye girer (kurulu ise import edilir).

Look-ahead bias guard tek noktada (`_signal_window`) toplandı — agent
kuralı kritik, test edilebilir hale getirildi. Aksi halde backtest
sonuçları "kâhin" davranış nedeniyle keyfi kalır.

## Test / Doğrulama

Sandbox'ta Python yok; syntax + tip kontrolü AST gözden geçirme ile
yapıldı. Çalıştırılabilir test `test-engineer` tarafından yazılacak.

**Önerilen test paketi (test-engineer için):**

- `tests/test_fundamental.py`
  - `FundamentalAnalyzer.score`: PE=5, PB=1.0, growth=0.2, ROE=0.20 →
    skor >= 90 (tüm pozitif delta'lar).
  - `score(snapshot)`: snapshot tüm alanlar None → 50 (baseline).
  - `fetch_snapshot`: iki dummy async kaynak (`{'pe_ratio': 10}` +
    `{'pe_ratio': 12, 'sector': 'Tech'}`) → ortalama PE=11, sector='Tech'.
  - Kaynak exception fırlatırsa snapshot yine döner (skip).

- `tests/test_recommender_mid_long.py`
  - `MidTermRecommender.recommend`: tech=70, fundamental=80, sentiment=60
    → composite ≈ 70 → BUY; timeframe='mid'; summary "Orta vade" içerir.
  - `LongTermRecommender.recommend`: fundamental=85, tech=55, sentiment=50
    → composite ≈ 65 → BUY; timeframe='long'.
  - `RecommendationDispatcher.recommend('mid', short_input)` →
    `TypeError`.
  - `RecommendationDispatcher.recommend('weekly', ...)` → `ValueError`.
  - Ağırlık toplamı 0.9 ile init → `ValueError`.

- `tests/test_backtester.py`
  - **Look-ahead bias guard**:
    `_signal_window(prices, t0)` → `prices.loc[:t0]` ile bytewise eşit;
    `prices.iloc[t0_idx+1:]` dilimi sinyal fonksiyonuna asla geçmez.
  - `compute_metrics`: bilinen equity_curve `[100, 110, 105, 120]` ve
    bir kapanan trade (pnl=20, hold_days=2) → `total_return_pct=20.0`,
    `sharpe_ratio` > 0, `max_drawdown_pct ≈ 4.55`, `win_rate=100.0`,
    `avg_hold_days=2.0`.
  - `_default_signal`: SMA(5)>SMA(20) yukarı kesiş → BUY.
  - `run` smoke test: 30 günlük sentetik fiyat serisi + default signal →
    `BacktestRunResult.total_trades > 0`.
  - `walk_forward`: 180 günlük panel, window_days=60, step_days=30 →
    4 sonuç döner.

- `tests/test_risk.py`
  - `position_size(100000, atr=2, price=100)` → ATR/price=2%,
    rpt=2% → raw_pct=1.0, cap=0.10 → `max_position_pct=0.10`,
    `suggested_quantity=100`.
  - `position_size(100000, atr=10, price=100)` → ATR/price=10%,
    rpt=2% → raw_pct=0.2 → cap önce 0.10'a düşer; qty=100.
  - ATR=0 → cap'in yarısı; reason metni "ATR=0" içerir.
  - `stop_loss_take_profit(100, atr=2, action='BUY')` → SL=97,
    TP=106, R/R=3.0, based_on='atr'.
  - SELL tarafı: SL > entry > TP.
  - S/R snap: `support_levels=[98]` → SL=98 (snap'lenir), based_on=
    'support_resistance'.
  - `portfolio_correlation`: shape `(N, N)`, diagonal=1.0, simetri.
  - `diversification_warnings`: iki hisse |ρ|=0.9 → 1 uyarı; |ρ|=0.5 → 0.
  - `sector_concentration`: 60/40 ağırlık → `{'Bank': 0.6, 'Tech': 0.4}`.

## Notlar

### Tüketici agent'lar için duyurular

- **database-architect** (bilgi):
  - DB şemasında **değişiklik YOK**. `recommendations.stop_loss`,
    `take_profit`, `fundamental_score` alanları zaten mevcut migration'da
    (Faz 1). `backtest_results` tablosu da hazır.
  - Faz 4'te `fundamental_snapshots` tablosu eklenirse `FundamentalSnapshot`
    persistence eklenebilir; şu an snapshot'lar memory-only.
  - Faz 4'te sektör F/K ortalaması için cache tablosu (`sector_averages`)
    düşünülebilir — `FundamentalAnalyzer.sector_average` şu an placeholder.

- **data-collector**:
  - `FundamentalAnalyzer` sources arayüzü: `async (ticker, exchange) ->
    dict` callable. `finviz_source.fetch_overview(ticker)` ve
    `isyatirim_source.fetch_fundamentals(ticker)` adapter'leri bu
    imzaya uygun yazıldığında dispatcher otomatik birleştirir.
  - Dict döndürürken alan adları: `pe_ratio`, `pb_ratio`, `market_cap`,
    `eps`, `dividend_yield`, `beta`, `target_price`, `sector`,
    `industry`, `growth_yoy`, `debt_to_equity`, `roe` — bilinmeyen
    alanlar yok sayılır.

- **portfolio-manager**:
  - `RiskManager.diversification_warnings(wallet_id)` UI'a bağlanmalı —
    portföy panelinde her cüzdan için uyarı listesi gösterilebilir.
  - `RiskManager.overall_risk_score(wallet_id)` cüzdan dashboard'da
    0..100 risk göstergesi olarak kullanılabilir.
  - `RiskManager.position_size` yeni pozisyon açma akışında "önerilen
    adet" hesabı için çağrılmalı.
  - `RiskManager._fetch_open_positions` `OpenPosition.quantity > 0`
    şartına bağlı — portfolio-manager pozisyon kapattığında satırı
    silmeli (veya quantity=0 set etmeli).

- **trading-executor**:
  - Backtester `risk_threshold` parametresi mevcut otomasyon eşiği
    (`UserAccount.risk_threshold`) ile **aynı anlamı** taşır —
    `Recommendation.risk_score > threshold` ise simülasyonda işlem
    atlanır. Production trading-executor aynı eşiği canlıda uygular.

- **ui-developer**:
  - **Backtest sonuç paneli**: `BacktestRunResult.equity_curve` zaman
    serisi grafiği, `total_return_pct` / `sharpe_ratio` /
    `max_drawdown_pct` / `win_rate` metrik kartları, `trades` tablosu.
  - **Risk uyarı paneli**: `RiskManager.overall_risk_score` skoru
    göstergesi + `warnings` listesi; korelasyon matrisi heatmap olarak
    `portfolio_correlation` çıktısı.
  - **Açıklanabilirlik**: `RecommendationOutput.contributions` dict'i
    pasta grafiği olarak (`tech`, `fundamental`, `sentiment` /
    `consensus` katkıları).
  - **Vade kartı**: `timeframe` ∈ {short, mid, long} her biri için ayrı
    renk/etiket; `summary` doğrudan Türkçe açıklama olarak render.

- **devops-engineer**:
  - `vectorbt ^0.26` `pyproject.toml`'da opsiyonel olarak listelenmeli
    (bağımlılık değil — agent yaml'da listeli). Production wheel
    PyInstaller paketleme aşamasında dahil edilirse `Backtester._vbt`
    aktif olur; yoksa pandas fallback otomatik devreye girer.
  - `scipy` Sharpe için zorunlu değil — `numpy.std + mean` ile hesaplanıyor.
  - Yeni runtime bağımlılık YOK; `numpy`, `pandas`, `sqlalchemy`,
    `loguru` zaten Faz 1'de mevcut.

- **test-engineer**:
  - `_signal_window` look-ahead bias guard testi **kritik** — strateji
    güveni bu testin yeşil olmasına bağlı.
  - `Backtester(session_factory=None)` ile `compute_metrics` ve
    `_signal_window` DB'siz test edilebilir.
  - `FundamentalAnalyzer(session_factory=None, sources={})` boş
    snapshot döndürür — `score` baseline testi için yeterli.
  - `RiskManager(session_factory=None)` ile `position_size` ve
    `stop_loss_take_profit` DB'siz test edilebilir; async metodlar
    in-memory SQLite + `PriceHistory`/`OpenPosition` fixture'ları
    gerektirir.

### Bilinen sınırlamalar

- `FundamentalAnalyzer.sector_average` şu an boş dict döndürür; sektör
  F/K karşılaştırması mutlak eşiklere düşer. Faz 4'te DB cache veya
  toplu Finviz tarama eklenmeli.
- `_default_signal` sadece SMA(5)>SMA(20); production'da `signal_fn=`
  ile `ShortTermRecommender.recommend` (veya orta/uzun vade) enjekte
  edilmeli — örnek wrapper Faz 3 Batch 2'de yazılabilir.
- Backtester equity_curve günlük; intraday (saatlik) test yok.
- Walk-forward dışı out-of-sample bölünmesi (train/test split) yok —
  Faz 4'te eklenebilir.
- `RiskManager.sector_concentration` maliyet bazlı; güncel fiyatla
  revaluation portfolio-manager sorumluluğunda.
- `RiskManager.portfolio_correlation` async ama sync DB session
  kullanıyor — IO blocking. Faz 4'te `AsyncSession` terfi düşünülmeli.
- Yüksek korelasyon eşiği 0.8 sabit; sektör tipine göre değişken
  (örn. bankacılık doğal yüksek korelasyon) Faz 4'te eklenebilir.
