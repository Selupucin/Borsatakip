---
date: 2026-05-27
agent: project-orchestrator
phase: faz-3
type: docs
related_files:
  - doc/project-history/*
  - scripts/build_exe.py
related_doc_sections:
  - "§11 Geliştirme Fazları — Faz 1-3 tamamlandı"
---

## Özet
Faz 1, 2 ve 3 tamamlandı. **389 birim test geçti, 1 PySide6-bağımlı UI smoke testi skip**, 0 başarısız. PyInstaller paketleme scripti hazır. Faz 4 (aracı kurum entegrasyonu) AKTİF DEĞİL — `BaseBroker` arayüzü ve `NotImplementedError` korumaları hazır bekliyor.

## Detaylar

### Faz 1 — Temel Altyapı (✓)
- Poetry konfigürasyonu, 53 bağımlılık (`pyproject.toml`)
- 19 SQLAlchemy 2.0 tablo modeli + Alembic 0001 migration + seed (11 kaynak, 30 BIST + 20 ABD hisse)
- 5 birincil veri kaynağı: yfinance, stooq, alphavantage, isyatirim, tcmb
- Async paralel `DataCollector` + reliability-ağırlıklı `PriceComparator`
- TA-Lib teknik analiz (11 indikatör, talib→pandas_ta→None backend kademesi)
- Alarm motoru + Qt-uyumlu Notifier
- PySide6 ana pencere + qdarktheme + finplot iskelet + tema runtime geçiş
- **112 birim test, hepsi yeşil**

### Faz 2 — Haber + NLP + Scraping + Listeler (✓)
- 6 yeni veri kaynağı: RSS (5 feed), KAP, TradingView (tv_available kontrol), Investing (ToS uyumlu), Finviz, Reddit (praw)
- TR (`savasy/bert-base-turkish-sentiment-cased`) + EN (`ProsusAI/finbert`) NLP, lazy load
- News aggregator (lookback ortalama sentiment)
- `ShortTermRecommender` (60% teknik / 30% sentiment / 10% mutabakat)
- `WatchlistService` + `BotPicksService` (başarı oranı, hit_target/expired)
- 4 yeni UI: news_widget, watchlist_widget, bot_picks_widget, recommendation_widget
- **+84 yeni birim test → 196/197 (1 PySide6 skip)**

### Faz 3 — Öneri Motoru + Portföy + Trading + Cila (✓)
- **Batch 1 (analysis):** fundamental.py (F/K, PD/DD, growth, D/E, ROE → 0-100 skor), MidTermRecommender (40/35/25), LongTermRecommender (50/30/20), RecommendationDispatcher, Backtester (vectorbt opsiyonel, look-ahead bias guard, Sharpe/DD/win_rate), RiskManager (ATR pozisyon büyüklüğü, SL/TP, korelasyon matrisi, sektör yoğunlaşması)
- **Batch 2 (portfolio):** AccountService, WalletService (2×3=6 cüzdan matrisi), CashFlowService (deposit/withdrawal/reallocate), PositionService (weighted-avg cost FIFO), PnLCalculator (komisyon/vergi/kur ayrımı), BenchmarkService (TWR + BIST100/S&P500 kıyas), PaperTradingService
- **Batch 3 (trading + broker):** TradingMode enum (4 mod), ManualParallelService (şu anki başlangıç modu), TradeRiskScorer (4 bileşen), AutoGate (karar matrisi), OrderManager (±%20 sapma + %30 hacim validasyonu), `BaseBroker` ABC (7 abstract), ExampleBroker placeholder (tüm metodlar NotImplementedError), SafetyEngine (kill switch + daily limit + circuit breaker + audit log)
- **Batch 4 (UI cilası):** 4 reusable component (MetricCard, Sparkline, ToastNotification, SkeletonLoader), 4 yeni widget (portfolio, budget, trading, history), 4 güncelleme (recommendation, main_window, dashboard, settings)
- **+193 yeni birim test → 389/390 yeşil**

## Faz 4 Koruması (4 Katman)
1. `TradingMode.is_active_in_current_phase()` → `semi_auto`/`full_auto` False
2. `AutoGate.evaluate()` → broker yoksa bu modlar BLOCKED
3. `OrderManager.execute()` → bu modlarda `NotImplementedError("Faz 4 aktif değil")`
4. `ExampleBroker` → tüm 7 metod aynı `NotImplementedError`
5. UI: bu modlar combo'da disabled + "Yakında - Faz 4" rozet

## Gerekçe
Doküman §11'deki faz sırasına harfiyen uyuldu. Her batch sonrası testler çalıştırıldı, hatalar düzeltildi (Faz 1'de session.py lazy engine + SQLite BigInteger compile + abstract class instantiation + UPSERT shim — 4 fix). Faz 4 (gerçek aracı kurum) altyapı hazır ama AKTİF EDİLMEDİ.

## Test / Doğrulama
- `pytest tests/ --tb=line -q` → **389 passed, 1 skipped, 0 failed** (4.28 saniye)
- 26 test dosyası
- Mock'lar: yfinance, stooq, alphavantage, isyatirim, tcmb, RSS, KAP, TradingView, Investing, Finviz, Reddit, transformers, torch, vectorbt, PySide6 (smoke import için pytest.importorskip)
- SQLite in-memory DB her test için temiz şema
- Look-ahead bias guard test edildi (Backtester._signal_window)
- Auto gate karar matrisi mod×skor×safety tam test
- Faz 4 koruması test edildi (NotImplementedError)

## Notlar
- **PyInstaller scripti hazır** (`scripts/build_exe.py`) — kullanıcı `poetry run python scripts/build_exe.py` ile .exe üretebilir
- **TA-Lib, PySide6, vectorbt, transformers, torch henüz kurulu değil** — test'lerde mock kullanılıyor. Gerçek çalıştırma için `poetry install` + `playwright install chromium` gerekli
- **PostgreSQL kurulu olmalı** — production çalıştırma için. Testler SQLite kullanıyor
- **Faz 4'e geçiş için:** Bir SPK lisanslı aracı kurum seç → BaseBroker adapter yaz → semi_auto'yu aktive et → küçük tutarlı canlı test → full_auto. **Kullanıcı onayı şart.**

## Bilinen Sınırlamalar
- HuggingFace modelleri ilk çalıştırmada indirilir (~880 MB), CI'da mock
- TCMB sadece kur için (hisse fiyatı DEĞİL — kuralı zorlanıyor)
- TradingView/Investing scraping yapısı değişirse adapter güncellenmeli
- BIST gerçek zamanlı veri 15 dk gecikmeli (yfinance/stooq) — Faz 4'te aracı kurum ile gerçek zamanlı erişim

## İstatistikler
| Metrik | Değer |
|---|---|
| Yazılan üretim modülü | 60+ Python dosyası |
| Yazılan test dosyası | 26 |
| Toplam test | 389 passed + 1 skipped |
| Toplam kod satırı (kabaca) | ~10000+ |
| project-history kaydı | 16 dosya |
| Tamamlanan faz | 3 / 4 (Faz 4 ileride) |
| Test çalıştırma süresi | 4.28 saniye |
