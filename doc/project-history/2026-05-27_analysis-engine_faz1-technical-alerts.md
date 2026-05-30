---
date: 2026-05-27
agent: analysis-engine
phase: faz-1
type: feature
related_files:
  - app/analysis/__init__.py
  - app/analysis/technical.py
  - app/alerts/__init__.py
  - app/alerts/engine.py
  - app/alerts/notifier.py
related_doc_sections:
  - "3.5 Analiz Kütüphaneleri"
  - "8.4 Modern UI Bileşenleri (toast)"
  - "9. Veritabanı Şeması (technical_signals, alerts)"
  - "11. Faz 1 — TA-Lib teknik analiz motoru + Alarm sistemi"
---

## Özet
Faz 1 kapsamında **TA-Lib teknik analiz motoru** ve **alarm sistemi** sıfırdan
kuruldu. 12 indikatör (RSI, MACD-3, BB-3, EMA-3, Stoch-2, ATR) vectorized
pandas üzerinde hesaplanır; backend kademesi `talib → pandas_ta → None`. Alarm
motoru fiyat / yüzde / teknik sinyal tiplerinde tetikleme yapar, bildirim
katmanı log + Qt signal + opsiyonel OS bildirimi olarak yayar.

Faz 1 dışı modüller (`sentiment.py`, `fundamental.py`, `recommender.py`,
`backtester.py`, `risk.py`) **bilinçli olarak yazılmadı**; Faz 2 / Faz 3'e
ait.

## Detaylar

### 1. `app/analysis/technical.py` — TA-Lib teknik analiz motoru

**Backend seçimi (import-time):**

```
talib            → BACKEND = "talib"          (birincil)
pandas_ta        → BACKEND = "pandas_ta"      (fallback)
ikisi de yoksa  → BACKEND = None              (sadece mock için)
```

`BACKEND = None` durumunda `TechnicalAnalyzer.compute_all()` çağrısı
`RuntimeError` fırlatır — test-engineer monkey-patch ile metodları mock'layabilir.

**Hesaplanan 12 seri (vectorized, satır-loop YOK):**

| İndikatör | Parametre | Backend kolonu |
|---|---|---|
| RSI | 14 | `rsi` |
| MACD line | 12,26,9 | `macd` |
| MACD signal | 9 | `macd_signal` |
| MACD histogram | — | `macd_hist` |
| Bollinger upper | 20, 2σ | `bb_upper` |
| Bollinger middle | SMA 20 | `bb_middle` |
| Bollinger lower | 20, 2σ | `bb_lower` |
| EMA 20 / 50 / 200 | — | `ema_20`, `ema_50`, `ema_200` |
| Stochastic %K | 14,3,3 | `stoch_k` |
| Stochastic %D | 3 | `stoch_d` |
| ATR | 14 | `atr` |

**Kapı kuralları:**
- Giriş DataFrame'inden fiyat olarak öncelikle `verified_close` kullanılır;
  yoksa `close`'a düşer (`_pick_close()`).
- `high`, `low`, `open` zorunlu kolonlar (`_require_columns`).
- Lookback yetmeyen satırlar `NaN` olarak bırakılır — **drop edilmez**.
- DB'ye yazımda `NaN → None` dönüşümü `_nan_to_none()` ile yapılır.

**API:**
- `TechnicalAnalyzer(backend=None)` — `None` argümanı modül seviyesindeki
  `BACKEND`'i kullanır.
- `compute_all(df) -> TechnicalIndicators` — tüm 12 seriyi tek geçişte
  hesaplar.
- `latest_snapshot(df) -> dict` — son satırı `TechnicalSignal`-uyumlu dict
  olarak döndürür (timestamp dahil, UTC).
- `save_signals(session, instrument_id, snapshot, source="computed")` —
  `TechnicalSignal` satırı ekler. `bb_middle`, `macd_hist`, `stoch_k/d`, `atr`
  DB modelinde **yok** — saklanmaz (yalnızca runtime yorumlama için döner).
  Bu eksikler ileride database-architect tarafından eklenirse migration
  `0002` gerekecek.

**Yorumlama yardımcısı — `signal_interpretation(snapshot, prev_snapshot=None)`:**

| Kategori | Çıktı |
|---|---|
| `rsi` | `oversold` (<30) / `overbought` (>70) / `neutral` |
| `macd` | `bullish_cross` / `bearish_cross` (önceki snapshot gerekli) |
| `bollinger` | `squeeze` (band genişliği orta-bandın <%5'i) / `breakout_upper` / `breakout_lower` / `inside` |
| `trend` | `uptrend` (EMA20>50>200) / `downtrend` / `mixed` |

### 2. `app/alerts/engine.py` — alarm motoru

**Alarm tipleri** (sabitler dışa açık):

| `alert_type` | Anlam | Tetikleme koşulu |
|---|---|---|
| `price_above` | Fiyat eşiği geçti | `price >= threshold` |
| `price_below` | Fiyat eşiğin altına indi | `price <= threshold` |
| `pct_change` | Günlük değişim eşiği | `abs(pct_change) >= threshold` |
| `signal:<kind>` | Teknik sinyal oluştu | `signal_type == kind` |

`signal:<kind>` formundaki kullanılan `<kind>` değerleri sabit olarak dışa
açıldı: `rsi_oversold`, `rsi_overbought`, `macd_bullish_cross`,
`macd_bearish_cross`, `bb_squeeze`, `bb_breakout_upper`, `bb_breakout_lower`.

**API:**
- `AlertEngine(session_factory, notifier=None)` — `session_factory` her
  çağrıda yeni `Session` döner (sessionmaker veya context-manager fabrikası
  olabilir).
- `await check_price_alerts(instrument_id, current_price)`
- `await check_pct_change_alerts(instrument_id, pct_change)`
- `await check_signal_alerts(instrument_id, signal_type)`
- `await evaluate(instrument_id, snapshot)` — tek noktadan tüm tipleri
  değerlendirir; `snapshot` `{price, pct_change, signals: list[str]}` anahtarlarını
  alır.

**Tetiklenme davranışı:**
- Eşleşen alarmın `alerts.triggered_at = now(UTC)` güncellenir, oturum
  commit edilir.
- `is_active` **KAPATILMAZ** — kullanıcı tekrar tetiklenmesini isteyebilir.
- Her tetikleme için `TriggeredAlert` dataclass'ı dönülür ve notifier'a
  `notify()` çağrısı yapılır.
- Notifier hatası alarm akışını kesmez (`logger.exception` ile yutulur).

### 3. `app/alerts/notifier.py` — bildirim katmanı

**3 hedefli yayım:**
1. `loguru.logger.warning("ALARM: ...")` — kalıcı log.
2. Qt `Signal(dict)` — `Notifier.alert_triggered` — UI tarafı bağlanır.
3. Opsiyonel sistem bildirimi — `plyer` varsa kullanılır, yoksa
   `win10toast`'a düşer; ikisi de yoksa sessiz atlanır
   (`enable_system_notifications` parametresi ile kapatılabilir).

**PySide6 fallback:**
- PySide6 import edilemiyorsa `QObject` no-op taban sınıfla, `Signal(...)`
  ise saf-Python `_StubSignal` (`connect/disconnect/emit` destekli) ile
  değiştirilir.
- Bu sayede headless test ortamında Notifier doğrudan örneklenebilir,
  `notify()` çalışır ve `alert_triggered.connect(my_slot)` test edilebilir.

**Payload (Qt signal ile UI'a giden):**
```
{
  "alert_id": int,
  "instrument_id": int,
  "alert_type": "price_above" | "price_below" | "pct_change" | "signal:<kind>",
  "triggered_value": "12.3400",   # Decimal -> str
  "threshold": "12.0000",
  "message": "Hisse #5: fiyat 12.34 eşik 12.00 seviyesini YUKARI kırdı.",
  "triggered_at": "2026-05-27T10:15:00+00:00",  # ISO 8601 UTC
}
```

`Decimal` ve `datetime` değerleri Qt/JSON dostu hale `_qt_safe()` ile
çevrilir (string).

## Gerekçe
Doküman §11 Faz 1'de "TA-Lib teknik analiz motoru" ve "Alarm sistemi
(fiyat ve yüzde değişim alarmları)" iki ayrı checkbox olarak listelenmiş.
Sentiment/temel/öneri/backtest/risk modülleri sırasıyla Faz 2 ve Faz 3'e
ait olduğu için bilinçli olarak yazılmadı (analysis-engine agent
kurallarında "Yapmadığın işler" altında değil ama doküman fazlarına bağlı
kalındı).

TA-Lib yerine pandas_ta fallback'i konuldu çünkü `TA-Lib v0.6.5+`
pre-built wheel'leri tüm platformlarda çalışmıyor olabilir (özellikle
ARM macOS / Linux non-x86); pandas_ta saf-Python yedek. `BACKEND=None`
modu ise test-engineer'ın hesaplamaları mock'layabilmesi içindir.

Tüm para olmayan indikatör değerleri `Numeric(8,4)` / `Numeric(12,6)` /
`Numeric(18,4)` DB kolonlarına `float` üzerinden yazılır — pandas seri
çıktıları zaten `float64`. Para hesaplaması bu modülde **yok** (analiz
sınıfı kuralı).

## Test / Doğrulama

- Bu sandbox'ta Python yorumlayıcısı yok; çalıştırılabilir test koşulamadı.
- AST düzeyinde syntax doğrulanamadı (Python eksik), ancak elle gözden
  geçirildi.
- `test-engineer` için önerilen testler:
  - `tests/test_technical.py`
    - `BACKEND` modül seviyesi sabitinin `talib`/`pandas_ta`/`None`'dan
      biri olduğunu doğrula.
    - `TechnicalAnalyzer(backend=None).compute_all(df)` → `RuntimeError`.
    - `monkeypatch.setattr(TechnicalAnalyzer, "_compute_talib", lambda self,h,l,c: ...)`
      ile mock backend ile snapshot doğrula.
    - `_pick_close`: `verified_close` varsa onu seçtiğini, hepsi NaN'sa
      `close`'a düştüğünü kontrol et.
    - 250 satırlık rasgele OHLCV ile `compute_all` çalıştırıp 12 serinin
      uzunluğunun girdiyle aynı olduğunu, ilk N satırın NaN, son satırın
      sayısal olduğunu doğrula.
    - `signal_interpretation` için sınır durumları: RSI=29.99 / 30.01,
      MACD bullish/bearish cross, BB squeeze (band genişliği <%5).
  - `tests/test_alert_engine.py`
    - `AlertEngine.check_price_alerts` — `price_above` ve `price_below`
      ayrı ayrı tetiklenir, `triggered_at` güncellenir, `is_active`
      değişmez.
    - `check_pct_change_alerts` — pozitif/negatif eşik karşılaştırması.
    - `check_signal_alerts` — `alert_type='signal:rsi_oversold'` yalnızca
      `signal_type='rsi_oversold'` geçilince tetiklenir.
    - `evaluate` — `snapshot={"price":..., "pct_change":..., "signals":[...]}`
      ile tüm tipler birleşik test edilir.
    - Notifier hatası yutuldumu (exception fırlatan stub notifier).
  - `tests/test_notifier.py`
    - PySide6 import'u patch'lenip yokmuş gibi yapılarak `_StubSignal`
      branch'i test edilir; `notify()` sonrası bağlı slot çağrılıyor mu.
    - `enable_system_notifications=False` ile sistem bildirim çağrısının
      atlandığı doğrulanır.

## Notlar

### Tüketici agent'lar için duyurular

- **ui-developer**:
  - `from app.alerts import Notifier` ile tek `Notifier` instance'ı
    uygulama yaşam döngüsünde paylaşılmalı.
  - `notifier.alert_triggered` Qt `Signal(dict)`'ine bir toast slot'u
    bağlanır: `notifier.alert_triggered.connect(self.show_toast)`.
  - Slot imzası: `def show_toast(self, payload: dict)` — payload yapısı bu
    dosyada "Payload" bloğunda; özellikle `message` alanı doğrudan
    toast başlığı olarak kullanılabilir.
  - Alarm yönetimi ekranında `Alert.is_active` toggling ve `threshold`
    düzenleme `AlertEngine`'i HİÇ değiştirmeden çalışır (engine her
    çağrıda DB'den okur).
  - Alarm tipi `signal:<kind>` formatında saklandığı için, alarm
    ekranında dropdown sabit listesi `engine.py`'deki
    `SIGNAL_*` sabitlerinden üretilmeli.

- **test-engineer**:
  - `TechnicalAnalyzer(backend=None)` test ortamında varsayılan
    konfigürasyon; gerçek hesaplamaya gerek olmayan tüm testlerde bu
    kullanılır.
  - TA-Lib / pandas_ta yüklü değilse `from app.analysis import
    TechnicalAnalyzer` import edilir ama `compute_all` çağrılırsa
    `RuntimeError` döner — bu beklenen davranıştır.
  - `Notifier` headless test için yapılı: `from app.alerts import Notifier;
    n = Notifier(enable_system_notifications=False); n.alert_triggered.connect(...)`
    PySide6 olmadan da çalışır.
  - `AlertEngine` `session_factory` parametresi test için
    `sessionmaker(bind=in_memory_sqlite)` ile çağrılabilir; `Alert`
    tablosu yeterli.

- **data-collector**:
  - `TechnicalAnalyzer.compute_all` `df['verified_close']` kolonunu
    bekler — comparator çıktısı bu kolona yazılmış olmalı (zaten
    `PriceHistory.verified_close` modelde var).
  - Engine OHLCV `pd.DataFrame` istiyor (`open, high, low, close,
    volume` + opsiyonel `verified_close`). Collector tarafı bu çerçeveyi
    `PriceHistory` sorgusundan üretmeli.

- **database-architect** (bilgi):
  - Faz 1'de `TechnicalSignal` modeline ek alan **eklenmedi**. Ancak
    sonradan ileride şu alanlar yararlı olabilir:
    `macd_hist NUMERIC(12,6)`, `bb_middle NUMERIC(18,4)`,
    `stoch_k NUMERIC(8,4)`, `stoch_d NUMERIC(8,4)`,
    `atr NUMERIC(18,4)`. Faz 3 risk modülü `atr`'a ihtiyaç duyacak —
    o zaman Alembic `0002` migration gerekecek.
  - `alerts.alert_type` için CHECK constraint Faz 1'de eklenmedi çünkü
    `signal:<kind>` formu serbest string istiyor; mevcut model uyumlu.

### Bilinen sınırlamalar / takip işler
- TA-Lib veya pandas_ta yoksa `compute_all` çalışmaz — devops bu paketlerin
  varlığını `setup_dev.py`'da en az birini sağlayacak şekilde garanti
  etmeli.
- `AlertEngine` şu an **sync** SQLAlchemy session bekliyor; collector
  async ise `loop.run_in_executor` veya sync-session sarmalayıcı
  gerekebilir. Faz 2'de async versiyona terfi düşünülebilir.
- `signal_interpretation` MACD cross için `prev_snapshot` ister; çağıran
  taraf en az son 2 snapshot'ı belleğinde tutmalı (cache/state).
- Bağımlılık değişikliği YOK — `pyproject.toml`'da zaten tanımlı
  `TA-Lib ^0.6`, `pandas-ta ^0.3`, `loguru ^0.7`, `pandas ^2.1`,
  `numpy ^2.0`, `PySide6 ^6.6` paketleri kullanılıyor. `plyer` opsiyonel
  sistem bildirimi için eklenirse kullanılır; bağımlılık olarak
  zorunlu değil.
