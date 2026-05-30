# Borsa Takip ve Analiz Botu — Proje Dokümantasyonu

**Versiyon:** 1.0.0  
**Tarih:** Mayıs 2026  
**Platform:** Masaüstü (Windows / macOS / Linux)  
**Durum:** Planlama tamamlandı — geliştirme başlangıcına hazır  
**Başlangıç modu:** Manuel-eşli (firma bağlantısı olmadan, kullanıcının kendi parasıyla paralel)

---

## 1. Proje Özeti

Bu proje; BIST ve NYSE/NASDAQ borsalarını çoklu veri kaynağından gerçek zamanlı takip eden, verileri karşılaştırmalı olarak doğrulayan, haber ve sosyal medya sentiment analizini teknik analizle birleştiren ve kullanıcıya kısa, orta ve uzun vadeli hisse önerileri sunan kapsamlı bir masaüstü uygulamasıdır.

Sistemin temel amacı yalnızca fiyat göstermek değil; birden fazla kaynaktan gelen veriyi çapraz doğrulayarak, haberleri ve piyasa duygusu analizini teknik sinyallerle harmanlayarak **hangi hissenin hangi vadede alınması gerektiğine dair nesnel bir öneri motoru** oluşturmaktır.

---

## 2. Hedefler

- Birden fazla API ve scraping kaynağından veri toplayarak tutarsızlıkları tespit etmek
- Teknik analiz indikatörlerini hesaplamak ve TradingView / Investing.com sinyalleriyle karşılaştırmak
- Türkçe ve İngilizce finansal haberleri NLP ile analiz ederek sentiment skoru üretmek
- Kısa (1–7 gün), orta (1–3 ay) ve uzun (3–12 ay+) vadeli hisse önerileri üretmek
- Bütçeyi havuz (bot / kullanıcı) ve vade bazında esnek dağıtmak, her cüzdanı ayrı izlemek
- Komisyon, vergi ve kur etkisi dahil net kâr-zararı doğru hesaplamak ve endekse göre kıyaslamak
- Risk skoruna göre yarı/tam otomatik işlem ve onay mekanizması sunmak
- Botun performansını backtest ile doğrulamak ve şeffaf öneri gerekçesi göstermek
- Fiyat ve sinyal alarmları ile kritik hareketlerde kullanıcıyı anlık uyarmak

---

## 3. Teknoloji Yığını

### 3.1 Programlama Dili

| Bileşen | Teknoloji | Gerekçe |
|---|---|---|
| Ana dil | Python 3.11+ | Finans ekosistemi olgunluğu, NLP ve veri bilimi kütüphane zenginliği |
| Paket yönetimi | Poetry | Bağımlılık kilitleme, sanal ortam yönetimi |
| Linter / formatter | ruff + black | Hızlı kod kalite kontrolü |
| Test | pytest | Birim ve entegrasyon testleri |
| Paketleme | PyInstaller | Tek çalıştırılabilir .exe / .app üretimi |

### 3.2 Arayüz (UI)

| Bileşen | Teknoloji | Gerekçe |
|---|---|---|
| UI framework | **PySide6** | Qt6 tabanlı, cross-platform, production kanıtlanmış. **PyQt6 yerine PySide6 seçildi** — teknik olarak neredeyse aynı, ancak PySide6 LGPL lisanslı (ücretsiz, ticari kullanıma açık); PyQt6 ise GPL/ticari lisans gerektirir. 2026'da Qt için varsayılan tavsiye PySide6 |
| Grafik bileşeni | pyqtgraph | Gerçek zamanlı yüksek performanslı finans grafikleri; tema kütüphaneleriyle uyumlu |
| Mum grafiği | pyqtgraph + finplot | finplot, pyqtgraph üzerine kurulu, TradingView benzeri profesyonel mum grafikleri sağlar |
| Tema sistemi | qdarktheme + qt-material | Runtime'da dark/light geçişi, Material Design teması, accent renk özelleştirme |
| İkonlar | qtawesome | Font Awesome / Material Design ikonları — modern, ölçeklenebilir |
| Stil sistemi | Qt Style Sheets (QSS) | Özel bileşen stilleri için |

> **Not:** PySide6'ya geçildiği için tüm `import PyQt6` ifadeleri `import PySide6` olur. API neredeyse birebir aynıdır, geçiş maliyeti minimaldir.

### 3.3 Veritabanı

| Bileşen | Teknoloji | Gerekçe |
|---|---|---|
| Ana veritabanı | PostgreSQL 15+ | Güçlü sorgu performansı, zaman serisi verileri için ideal, kullanıcı sistemde yüklü |
| ORM | SQLAlchemy 2.0 | Model yönetimi, migration desteği |
| Migration | Alembic | Şema versiyonlama |
| Fiyat geçmişi önbelleği | Parquet dosyaları (pandas) | Hızlı disk okuma, az yer |

### 3.4 Veri Kaynakları

#### Birincil API'ler (Ücretsiz)

| Kaynak | Kapsam | Kütüphane | Not |
|---|---|---|---|
| yfinance | NYSE / NASDAQ OHLCV, finansallar | `yfinance` | 15–20 dk gecikmeli. Yahoo web yapısı değişince kırılabilir — tek başına güvenilmemeli |
| Stooq | NYSE / NASDAQ + BIST OHLCV geçmişi | `pandas-datareader` / doğrudan CSV | API key gerektirmez, yfinance kırıldığında birincil yedek |
| Alpha Vantage | NYSE / NASDAQ + teknik indikatör API | `alpha_vantage` | Ücretsiz: günde 25 istek — sadece doğrulama/çapraz kontrol için |
| İş Yatırım | BIST fiyat geçmişi + finansal tablolar | `isyatirimhisse` veya doğrudan JSON endpoint | BIST için birincil kaynak. Resmi değil, kişisel kullanım, aşırı istek atmamaya dikkat |
| KAP | BIST şirket bildirimleri, finansal tablolar | REST / scraping | Resmi Kamuyu Aydınlatma Platformu — temel veri ve bildirimler için |
| Finviz | Temel veriler, analist hedefleri (ABD) | `finvizfinance` | Scraping tabanlı kütüphane |
| Reddit (praw) | WSB ve hisse toplulukları sentiment | `praw` | Reddit resmi API |

> **Önemli düzeltme:** Hisse fiyat verisi için TCMB veya MKK uygun kaynak değildir — TCMB döviz/faiz makroekonomik verisi sunar, MKK ise kayıt/saklama kuruluşudur, anlık fiyat API'si yoktur. BIST fiyat verisi için doğru kaynaklar İş Yatırım, Stooq ve KAP'tır. (TCMB yalnızca döviz kuru verisi için ayrı bir yardımcı kaynak olarak kalabilir.)

#### Web Scraping Kaynakları

| Kaynak | Çekilen Veri | Araç |
|---|---|---|
| TradingView | Teknik analiz özeti (al/sat/nötr), destek-direnç | `tvdatafeed` + Playwright |
| Investing.com | Analist görüşleri, ekonomik takvim, teknik göstergeler | Playwright (investpy bakımı durmuş olabilir, yedek olarak doğrudan scraping) |
| Matriks / piyasa siteleri | BIST gerçek zamanlıya yakın fiyat ve derinlik | Playwright + BeautifulSoup |
| Reddit | Hisse tartışmaları, topluluk gündemi | `praw` (resmi API, snscrape yerine — snscrape Twitter kısıtları sonrası kararsız) |

#### Haber Kaynakları (RSS)

- Reuters Finance (İngilizce)
- Bloomberg TR
- Mynet Finans
- Dünya Gazetesi
- KAP (Kamuyu Aydınlatma Platformu) bildirimleri

### 3.5 Analiz Kütüphaneleri

| Bileşen | Teknoloji | Kullanım |
|---|---|---|
| Veri işleme | pandas 2.0+ + numpy 2.0 | Tüm veri manipülasyonu |
| Teknik analiz | TA-Lib 0.6.x | RSI, MACD, Bollinger, EMA/SMA, Stokastik ve 150+ indikatör. v0.6.5+ ile önceden derlenmiş wheel sayesinde `pip install TA-Lib` artık C compiler gerektirmiyor |
| Alternatif | pandas-ta veya mintalib | TA-Lib kurulamazsa yedek (mintalib Polars/numpy destekli, daha hızlı) |
| Async veri çekimi | asyncio + aiohttp | Tüm kaynaklar paralel sorgulanır |
| Türkçe NLP | HuggingFace — `savasy/bert-base-turkish-sentiment-cased` | Türkçe haber sentiment |
| İngilizce NLP | FinBERT (`ProsusAI/finbert`) | İngilizce finansal metin sentiment |
| RSS okuyucu | feedparser | Haber akışı çekimi |

---

## 4. Sistem Mimarisi

### 4.1 Katmanlar

```
┌─────────────────────────────────────────────────────┐
│                  UI Katmanı (PySide6)                │
│ Dashboard│Watchlist│BotPicks│Grafik│Portföy│Geçmiş   │
└─────────────────────┬───────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│              Öneri Motoru (Recommendation Engine)    │
│  Kısa Vade │ Orta Vade │ Uzun Vade │ Skor Birleştirme│
└──────┬──────────────┬──────────────┬────────────────┘
       │              │              │
┌──────▼──────┐ ┌─────▼──────┐ ┌───▼──────────────────┐
│  Teknik     │ │  Sentiment │ │  Veri Karşılaştırma   │
│  Analiz     │ │  Analizi   │ │  Motoru               │
│  Motoru     │ │  (NLP)     │ │  (Doğrulama)          │
└──────┬──────┘ └─────┬──────┘ └───┬──────────────────┘
       │              │              │
┌──────▼──────────────▼──────────────▼──────────────┐
│              Veri Toplama Katmanı                   │
│  yfinance │ Stooq │ İş Yatırım │ KAP │ Alpha Vantage  │
│  TradingView │ Investing.com │ Finviz │ RSS │ Reddit  │
└─────────────────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────┐
│              PostgreSQL Veritabanı                   │
└─────────────────────────────────────────────────────┘
```

### 4.2 Veri Karşılaştırma Motoru

Tüm kaynaklar `asyncio` ile paralel sorgulanır. Karşılaştırma mantığı:

1. **Paralel çekim:** Her kaynak için ayrı async worker. Biri yavaşsa diğerleri beklemez.
2. **Tutarsızlık tespiti:** Kaynaklar arası fiyat farkı `%0.5`'i geçerse `discrepancies` tablosuna yazılır. `%2`'yi geçerse anlık alarm tetiklenir.
3. **Güvenilirlik ağırlıklandırması:** Her kaynağın geçmiş hata sayısı ve tutarlılık geçmişine göre bir güven skoru hesaplanır. Ağırlıklı ortalama ile "doğrulanan fiyat" belirlenir ve ana tabloya yazılır.
4. **Kaynak devre dışı bırakma:** Bir kaynak art arda 5 kez hata verirse otomatik olarak geçici olarak devre dışı bırakılır.

---

## 5. Öneri Motoru

Sistemin en kritik bileşenidir. Üç veri akışını birleştirerek puanlama yapar:

### 5.1 Girdi Sinyalleri

| Sinyal Grubu | Ağırlık | İçerik |
|---|---|---|
| Teknik analiz | %40 | RSI, MACD, Bollinger, EMA çaprazlamaları, hacim analizi |
| Sentiment skoru | %30 | Haber sentiment + sosyal medya duygusu (Türkçe + İngilizce) |
| Temel analiz verileri | %20 | F/K oranı, PD/DD, büyüme, sektör karşılaştırması |
| Kaynak mutabakatı | %10 | TradingView ve Investing.com sinyal uyumu |

### 5.2 Vade Tanımları

Her vade hem farklı bir sinyal ağırlığına hem de farklı bir **tarama sıklığına** sahiptir. Vade seçimi botun ne sıklıkla tarama yapacağını otomatik belirler; kısa vadeli fırsatlar hızlı gelip geçtiği için sık, uzun vadeli pozisyonlar için seyrek tarama yapılır.

| Vade | Süre | Tarama Sıklığı | Ağırlıklı Sinyal |
|---|---|---|---|
| Kısa vade | 1 – 7 gün | Günlük (gün içi opsiyonel) | Teknik analiz (%60) + Sentiment (%30) + Kaynak mutabakatı (%10) |
| Orta vade | 1 – 3 ay | Haftalık | Teknik analiz (%40) + Temel analiz (%35) + Sentiment (%25) |
| Uzun vade | 3 – 12 ay+ | Aylık | Temel analiz (%50) + Teknik analiz (%30) + Sentiment (%20) |

> Tarama sıklığı vade seçimine göre otomatik ayarlanır; istenirse ileri ayarlardan manuel değiştirilebilir.

### 5.3 Öneri Çıktısı

Her hisse için üretilen öneri kartı şu bilgileri içerir:

- **Öneri:** AL / BEKLE / SAT
- **Vade:** Kısa / Orta / Uzun
- **Güven skoru:** 0–100 arası (tüm sinyallerin ağırlıklı bileşimi)
- **Gerekçe özeti:** Hangi sinyallerin ağırlık taşıdığı
- **Risk seviyesi:** Düşük / Orta / Yüksek (volatilite + likidite bazlı)
- **Hedef fiyat:** Teknik direnç ve analist hedefleri ortalaması
- **Uyarılar:** Aktif haberler, KAP bildirimleri, anomali tespiti

### 5.4 Backtesting (Geçmişe Yönelik Doğrulama)

> **Yeni eklenen kritik bileşen.** Öneri motorunun güvenilir olabilmesi için ürettiği sinyallerin geçmiş veriyle test edilmesi şarttır. Aksi halde ağırlıklar keyfî kalır.

- Her vade için öneri stratejisi geçmiş veriye uygulanır (`backtrader` veya `vectorbt` kütüphanesi).
- Ölçülen metrikler: toplam getiri, Sharpe oranı, maksimum düşüş (max drawdown), kazanan işlem oranı, ortalama tutma süresi.
- Sinyal ağırlıkları (%40 teknik, %30 sentiment vb.) sabit değil — backtest sonuçlarına göre periyodik olarak optimize edilir.
- **Look-ahead bias** önlenir: geçmiş tarihteki bir öneri yalnızca o tarihte mevcut olan veriyle hesaplanır.
- Sonuçlar `backtest_results` tablosunda saklanır ve UI'da strateji performans panelinde gösterilir.

### 5.5 Risk Yönetimi

> **Yeni eklenen bileşen.** Öneri kadar "ne kadar al" ve "ne zaman çık" da önemlidir.

- **Pozisyon büyüklüğü:** Volatiliteye göre önerilen maksimum pozisyon yüzdesi (ATR tabanlı).
- **Stop-loss / take-profit:** Her öneri için teknik seviyelere dayalı otomatik öneri.
- **Portföy çeşitlendirme uyarısı:** Tek sektöre veya tek hisseye aşırı yoğunlaşma tespit edilirse uyarı.
- **Korelasyon analizi:** Portföydeki hisseler arası korelasyon hesaplanır, gizli risk yoğunlaşması bildirilir.

### 5.6 Şeffaflık ve Açıklanabilirlik

> Sistem bir "kara kutu" olmamalı. Her öneri için hangi sinyalin ne kadar katkı yaptığı kullanıcıya gösterilir (örn. "Bu AL önerisinin %55'i pozitif haber akışından, %30'u RSI aşırı satım bölgesinden geliyor"). Bu, kullanıcının kendi kararını bilinçli vermesini sağlar.

---

## 6. Takip Listeleri ve Portföy Yönetimi

Sistem iki ayrı liste mantığı üzerine kuruludur: **kullanıcının kendi listesi** ve **botun öneri listesi**. İkisi yan yana izlenir, böylece kullanıcı kendi seçimleriyle botun önerilerini karşılaştırabilir.

### 6.1 Bütçe Dağılımı (Havuz × Vade Matrisi)

Kullanıcı başlangıç bütçesini (örn. 15.000 TL) tamamen kendi tercihine göre, iki boyutlu bir matris üzerinden dağıtır:

- **Havuz boyutu:** Bot havuzu (botun önerilerine ayrılan kısım) ve kendi havuzum (kullanıcının kendi seçimleri).
- **Vade boyutu:** Kısa, orta, uzun.

Bu da toplam **2 havuz × 3 vade = 6 bağımsız cüzdan** oluşturur. Her cüzdan ayrı izlenir ve her birinde bağımsız işlem yapılır.

| Havuz \ Vade | Kısa | Orta | Uzun |
|---|---|---|---|
| Bot havuzu | bağımsız cüzdan | bağımsız cüzdan | bağımsız cüzdan |
| Kendi havuzum | bağımsız cüzdan | bağımsız cüzdan | bağımsız cüzdan |

**Kurallar:**

- Dağılım tamamen kullanıcının kontrolündedir. İstediği hücreye istediği tutarı koyabilir, bazı hücreleri 0 TL bırakabilir, hatta tüm bütçeyi tek bir hücreye yönlendirebilir.
- **Bütçe sabit değildir.** Kullanıcı istediği zaman para ekleyebilir (deposit), çekebilir (withdrawal) veya cüzdanlar arası yeniden dağıtım yapabilir. Başlangıç tutarı (örn. 15.000 TL) yalnızca bir başlangıç noktasıdır, üst sınır değildir.
- Her para ekleme/çekme işlemi `cash_flows` tablosuna kaydedilir; böylece "ne kadar yatırdım, ne kadar çektim, net getirim ne" doğru hesaplanır.
- Getiri hesabı, yatırılan/çekilen para hareketlerini dışlayarak yapılır (time-weighted return) — yani hesaba para eklemek "kazanç" gibi görünmez.
- Her hücrenin kendi nakit bakiyesi, açık pozisyonları ve kâr-zararı vardır.
- Analiz ekranı performansı hem hücre bazında (örn. "bot–uzun vade %12 getiri"), hem havuz bazında (bot toplam vs. kendi toplam), hem de vade bazında (kısa vs. orta vs. uzun) karşılaştırmalı gösterir.

### 6.2 Kullanıcı Takip Listesi (Watchlist)

- Kullanıcı istediği hisseleri ekleyip çıkarabilir; birden fazla isimlendirilmiş liste oluşturabilir (örn. "Uzun Vade", "Temettü", "Gözlem").
- Her liste için anlık fiyat, günlük değişim %, hacim ve botun o hisse için ürettiği güncel öneri (AL/BEKLE/SAT) gösterilir.
- Listeye eklenen her hisse için otomatik veri çekimi ve sinyal hesaplama başlar.
- Sürükle-bırak ile sıralama, hızlı arama ve filtreleme.

### 6.3 Bot Öneri Listesi (Bot Picks)

- Bot, tüm taranan evren içinden kendi en yüksek güven skorlu önerilerini ayrı bir listede tutar.
- Her vade (kısa / orta / uzun) için ayrı bot listesi: "Botun bu hafta öne çıkardıkları", "Orta vade fırsatlar" vb.
- Bot listesindeki her öneri zaman içinde **takip edilir**: öneri verildiği andaki fiyat kaydedilir, sonraki performans ölçülür.
- Botun geçmiş önerilerinin başarı oranı şeffaf şekilde gösterilir (örn. "Kısa vade önerilerinin son 30 günde %62'si hedefe ulaştı"). Bu, kullanıcının bota ne kadar güveneceğine kendi karar vermesini sağlar.
- Kullanıcı, bot listesindeki bir hisseyi tek tıkla kendi watchlist'ine veya ilgili vade cüzdanına ekleyebilir.

### 6.4 Portföy ve Bakiye Takibi

Kullanıcının gerçek/sanal portföyünün yönetildiği bölüm. Hem toplam hem de her bir cüzdan (havuz × vade) bazında ayrı ayrı görüntülenebilir:

| Gösterilen Bilgi | Açıklama |
|---|---|
| Toplam bakiye | Nakit + açık pozisyonların güncel değeri |
| Nakit bakiye | Kullanılabilir nakit (cüzdan bazında ve toplam) |
| Toplam kazanç / kayıp | Gerçekleşen + gerçekleşmemiş kâr-zarar (TL ve %) |
| Günlük değişim | Portföyün bugünkü değer değişimi |
| Cüzdan kırılımı | Her havuz × vade cüzdanının ayrı bakiyesi, getirisi ve pozisyonları |
| Havuz karşılaştırması | Bot havuzu toplam getiri vs. kendi havuzum toplam getiri |
| Vade karşılaştırması | Kısa vs. orta vs. uzun vade performansı |
| Açık pozisyonlar | Her hisse için: adet, maliyet, güncel fiyat, kâr-zarar, ağırlık %, hangi cüzdan |
| Sektör dağılımı | Pasta grafiği ile portföyün sektörel kırılımı |
| Performans grafiği | Zaman içinde portföy değeri + BIST 100 / S&P 500 endeks kıyaslaması |

### 6.5 İşlem Geçmişi (Transaction History)

- Tüm geçmiş alış/satış işlemleri kronolojik liste halinde: tarih, hisse, işlem tipi, adet, fiyat, toplam tutar, komisyon, gerçekleşen kâr-zarar, hangi cüzdan (havuz × vade).
- Filtreleme: tarihe, hisseye, işlem tipine, cüzdana göre.
- Her kapanan pozisyon için "tutma süresi" ve "getiri %" otomatik hesaplanır.
- CSV / Excel olarak dışa aktarım.
- İsteğe bağlı: işlemin o anki bot önerisiyle uyumlu olup olmadığı etiketlenir (kullanıcı botu dinledi mi, dinlemediyse sonuç ne oldu?).

### 6.6 Komisyon, Vergi ve Kur Etkisi

> Net kâr-zararın doğru hesaplanması için bu üç faktör mutlaka dikkate alınır. Aksi halde "kazandım" sandığın şey aslında masraf veya kur etkisi olabilir.

- **Komisyon:** Her işlemde aracı kurum komisyonu hesaba katılır. Net kâr-zarar komisyon düşülerek gösterilir; varsayılan oran ayarlardan değiştirilebilir.
- **Vergi/stopaj:** BIST ve ABD hisselerinde farklı vergilendirme olabilir (örn. ABD temettüsünde stopaj). Bu, bilgilendirme amaçlı hesaplanır; kesin vergi durumu için kullanıcı kendi mali müşavirine danışmalıdır.
- **Kur etkisi:** ABD (NYSE/NASDAQ) hisseleri USD bazlıdır. TL'ye çevrildiğinde oluşan kur kazancı/kaybı, hissenin kendi getirisinden **ayrı** gösterilir. Böylece "hisse mi kazandırdı, kur mu?" sorusu net yanıtlanır.
- TCMB kur verisi bu hesaplama için yardımcı kaynak olarak kullanılır.

### 6.7 Endeks Kıyaslaması (Benchmark)

> "%5,6 kazandım" tek başına anlamlı değildir — ancak endekse göre değerlendirilince anlam kazanır.

- Portföy performansı ilgili endekslerle kıyaslanır: BIST hisseleri için BIST 100, ABD hisseleri için S&P 500.
- "Endeksi yendin mi?" göstergesi: portföy getirisi vs. endeks getirisi aynı dönemde karşılaştırmalı grafikle gösterilir.
- Bu kıyaslama hem toplam portföy hem de bot havuzu / kendi havuzun bazında ayrı yapılabilir.

### 6.8 Sanal Portföy (Paper Trading) Modu

> Yeni kullanıcılar gerçek para riske atmadan botu test edebilsin diye sanal portföy modu eklenir. Başlangıç sanal bakiyesi tanımlanır, tüm işlemler gerçek fiyatlarla ama sanal parayla yapılır. Bot önerilerinin gerçek performansını risksiz görmenin en iyi yolu.

---

## 7. İşlem Modları, Otomasyon ve Aracı Kurum

> Bu bölüm sistemin gerçek parayla işlem yapması için kritik ve yasal açıdan en hassas kısmıdır. Dikkatle okunmalıdır.

### 7.1 Yasal Çerçeve

- Türkiye'de bireysel yatırımcının algoritmik / otomatik alım-satım yapması **yasaldır** ve aktif olarak kullanılmaktadır.
- Ancak emirler **doğrudan borsaya değil, SPK (Sermaye Piyasası Kurulu) lisanslı bir aracı kurum üzerinden** iletilmek zorundadır. Türkiye'de aracı kurumların faaliyet göstermesi için SPK lisansı zorunludur.
- Kullanıcı kendisi aracı kurum olamaz; mevcut bir aracı kurumun API'sine bağlanıp emirleri onların altyapısı üzerinden iletir.
- Sistem yalnızca kullanıcının kendi hesabını, kendi adına yönetir. Başkası adına işlem veya portföy yönetimi yapmak ayrı bir SPK yetki belgesi gerektirir ve bu projenin kapsamı dışındadır.

### 7.2 Entegrasyon Yolları

Sistem üç olası yoldan biriyle gerçek emir gönderebilir:

| Yöntem | Açıklama | Not |
|---|---|---|
| Veri terminali modülü | Matriks Prime / İdeal Data gibi terminallerin algoritmik işlem modülü. Birçok aracı kuruma bağlanır | En erişilebilir; terminal lisansı gerekir |
| Doğrudan aracı kurum API'si | REST veya FIX protokolü sunan aracı kurumun API'si | Borsa İstanbul'da FIX/OUCH protokolü kullanan kurum listeleri resmidir |
| Aracı platform API'si | TradeWise gibi, bağlı aracı kurum hesabına emir ileten köprü platformlar | Üçüncü taraf bağımlılığı |

> **Önemli:** Hangi aracı kurum/yöntem seçileceği baştan netleşmelidir, çünkü her kurumun API yapısı, emir formatı ve kuralları farklıdır. Entegrasyon modülü (`broker/` klasörü) bu seçime göre yazılır. Soyut bir `BaseBroker` arayüzü tanımlanır, her aracı kurum için ayrı bir uygulama (adapter) yazılır — böylece kurum değiştirmek kolaylaşır.

### 7.3 İşlem Modları

Sistem dört modda çalışabilir; kullanıcı hangisini istediğini açıkça seçer:

| Mod | Davranış | Firma bağlantısı | Önerilen Kullanım |
|---|---|---|---|
| Manuel-eşli (paralel) | Bot öneri/işlem üretir, kullanıcı bunu kendi aracı kurum uygulamasında elle yapar; bot işlemi takip eder | Gerekmez | **Şu anki başlangıç modu** |
| Yarı-otomatik | Bot emri hazırlar, kullanıcı tek tıkla onaylar; emri bot gönderir | Gerekir | Firma bağlandıktan sonra |
| Tam otomatik | Bot, kullanıcı onayı olmadan emir gönderir (riskli işlemlerde onay ister) | Gerekir | Sistem kanıtlandıktan sonra |
| Sanal (paper) | Gerçek fiyat, sanal para. Hiç gerçek emir yok | Gerekmez | İsteğe bağlı test modu |

#### Manuel-eşli (paralel) mod — şu anki öncelik

Bu mod, firma bağlantısı kurmadan gerçek parayla çalışmayı sağlar:

- Kullanıcı kendi aracı kurumuna kendi parasını yatırır ve bu tutarı bota bildirir (her cüzdan için ayrı ayrı tanımlanabilir).
- Bot gerçek piyasa verisiyle öneri ve işlem sinyalleri üretir.
- Kullanıcı bu işlemleri kendi aracı kurum uygulamasında **elle** gerçekleştirir.
- Gerçekleştirdiği işlemi bota işaretler (ya da bot "bu öneriyi uyguladın mı?" diye sorar).
- Bot, kullanıcının portföyünü, kâr-zararını, performansını ve botu ne kadar dinlediğini takip eder.
- Yani bot "beyin", kullanıcı "el" rolündedir. Firma bağlantısı modülü hazır bekler, ileride aktive edilir.

### 7.4 Risk Tabanlı Otomasyon

Otomasyon seviyesi işlemin riskine göre dinamik ayarlanır:

- Her işlem için bir **risk skoru** hesaplanır (volatilite + işlem tutarı + likidite + portföy ağırlığı).
- Kullanıcı bir **risk eşiği** belirler.
- Tam otomatik modda bile, bir işlemin risk skoru eşiği aşarsa bot **otomatik olarak durur ve onay sorar**: "Bu işlem yüksek riskli, yine de yapayım mı?"
- Risk eşiğin altındaki işlemler tam otomatik modda doğrudan gerçekleşir.
- Yarı-otomatik modda her işlem zaten onay ister (risk skorundan bağımsız).
- Böylece kullanıcı hız (düşük riskli işlemler otomatik) ile güvenlik (riskli işlemler onaylı) arasında otomatik denge kurar.

### 7.5 Güvenlik ve Koruma Önlemleri

Gerçek parayla işlem yapan bir sistemde bunlar zorunludur:

- **API anahtarları şifreli saklanır** (sistem keyring / OS güvenli depo); asla düz metin veya kod içinde tutulmaz. (Firma bağlandığında geçerli.)
- **2FA ve IP kısıtlaması** aracı kurum tarafında aktif edilmelidir.
- **Günlük işlem limiti:** Bot bir günde belirli tutarın/işlem sayısının üzerine çıkamaz.
- **Acil durdurma (kill switch):** Tek tıkla tüm otomatik işlemleri anında durdurma.
- **Maksimum zarar kesici (circuit breaker):** Portföy belirli bir yüzde düşerse bot otomatik durur ve kullanıcıyı uyarır.
- **Risk eşiği kontrolü:** Yukarıda tanımlanan risk-tabanlı onay mekanizması.
- **Emir doğrulama:** Her otomatik emir gönderilmeden önce mantık kontrolünden geçer (anormal fiyat, anormal miktar tespiti).
- **Tam denetim kaydı (audit log):** Her emir, kim/ne zaman/neden tetikledi bilgisiyle loglanır.

### 7.6 Firma Bağlantısı — Hazır Bekletilen Modül

Aracı kurum entegrasyonu şu an aktif edilmez, ancak mimaride hazır tutulur:

- Soyut `BaseBroker` arayüzü baştan tanımlanır; manuel-eşli moddan gerçek emir moduna geçiş yalnızca bir adapter yazıp aktive etmekle olur.
- Kullanıcı arayüzünde "Firma bağla" seçeneği görünür ama pasif/yakında durumundadır.
- Bu sayede sistem bugün manuel-eşli modda tam çalışır, firma bağlantısı geldiğinde kod yapısı değişmeden yarı/tam otomatiğe terfi eder.

### 7.7 Yasal ve Güvenlik Notu

Gerçek emir gönderme aşamasına geçildiğinde emirler yalnızca SPK lisanslı aracı kurum üzerinden iletilir. Manuel-eşli modda kullanıcı işlemleri zaten kendi hesabında kendisi yaptığı için bu aşamada ek bir aracılık riski yoktur. Tam otomatiğe geçmeden önce yarı-otomatik ve risk-eşikli onay mekanizmaları test edilmelidir.

---

## 8. Arayüz Tasarımı (UI/UX)

Arayüz, üst kalite, modern ve kullanıcı dostu olacak şekilde tasarlanır. Tasarım ilkeleri ve bileşenler:

### 8.1 Tasarım İlkeleri

- **Temiz ve ferah:** Bilgi yoğunluğu yüksek bir finans uygulaması olmasına rağmen, boşluk (whitespace) ve görsel hiyerarşi ile karmaşa önlenir.
- **Veri görselleştirme önceliği:** Sayılar tablolarda boğulmaz; renk kodlaması (yeşil/kırmızı), mini grafikler (sparkline) ve göstergelerle hızlı okunur.
- **Tutarlılık:** Tüm ekranlarda aynı renk paleti, tipografi ve bileşen davranışı.
- **Geri bildirim:** Her işlem için anlık görsel geri bildirim (yükleniyor animasyonu, başarı/hata bildirimi).
- **Erişilebilirlik:** Yeterli kontrast, klavye kısayolları, ölçeklenebilir font.

### 8.2 Dark / Light Mod

- **Çift tema desteği** `qdarktheme` ile sağlanır: kullanıcı dark, light veya "sistem ayarını takip et (auto)" seçeneklerinden birini seçer.
- Tema değişimi **runtime'da anında** uygulanır, yeniden başlatma gerekmez.
- Grafik renkleri (mum grafiği, indikatörler) da temaya göre otomatik uyarlanır — dark modda koyu zemin, light modda açık zemin.
- Tema tercihi kullanıcı ayarlarında saklanır, sonraki açılışta hatırlanır.
- Accent (vurgu) rengi özelleştirilebilir (örn. mavi, yeşil, mor).

### 8.3 Ekran Yapısı

| Ekran | İçerik |
|---|---|
| Dashboard (Ana ekran) | Portföy özeti, bot öne çıkanları, piyasa özeti, önemli haberler — tek bakışta genel durum |
| Watchlist | Kullanıcının takip listeleri, anlık fiyat ve bot sinyali |
| Bot Picks | Botun vade bazlı öneri listeleri ve geçmiş başarı oranı |
| Grafik & Analiz | Detaylı mum grafiği, indikatör katmanları, çizim araçları |
| Portföy | Bakiye, açık pozisyonlar, kâr-zarar, performans grafiği, sektör dağılımı |
| Bütçe Dağılımı | Havuz × vade matrisi, cüzdan bazlı bütçe tahsisi, para ekleme/çekme |
| İşlem & Otomasyon | İşlem modu seçimi, risk eşiği ayarı, bekleyen öneri onayı, manuel-eşli işlem işaretleme, kill switch |
| İşlem Geçmişi | Tüm geçmiş işlemler, filtreleme, dışa aktarım |
| Haberler | Haber akışı + sentiment göstergesi, hisse bazlı filtreleme |
| Alarmlar | Alarm kuralları yönetimi, tetiklenme geçmişi |
| Ayarlar | Tema, veri kaynakları, bildirim tercihleri, API anahtarları, güvenlik |

### 8.4 Modern UI Bileşenleri

- **Yan navigasyon menüsü (sidebar):** İkon + etiketli, daraltılabilir.
- **Kart tabanlı düzen:** Metrikler ve özetler kartlar halinde, gölge yerine ince kenarlıklarla flat tasarım.
- **Sparkline grafikler:** Liste satırlarında mini trend grafikleri.
- **Renk kodlu göstergeler:** Yeşil/kırmızı kazanç-kayıp, sinyal rozetleri.
- **Bildirim sistemi (toast):** Sağ üstte beliren, kendiliğinden kaybolan bildirimler.
- **Yükleme iskeletleri (skeleton loaders):** Veri çekilirken boş ekran yerine iskelet animasyonu.
- **Responsive layout:** Pencere boyutuna göre uyarlanan ızgara düzeni.

---

## 9. Veritabanı Şeması

### 9.1 Tablolar

```sql
-- Hisse senetleri ana tablosu
CREATE TABLE instruments (
    id          SERIAL PRIMARY KEY,
    ticker      VARCHAR(20) UNIQUE NOT NULL,
    name        VARCHAR(200),
    exchange    VARCHAR(20),   -- BIST, NYSE, NASDAQ
    sector      VARCHAR(100),
    currency    VARCHAR(10),
    created_at  TIMESTAMP DEFAULT NOW()
);

-- Fiyat geçmişi (çoklu kaynak)
CREATE TABLE price_history (
    id           BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    source       VARCHAR(50),  -- yfinance, alphavantage, tcmb, vb.
    timestamp    TIMESTAMP NOT NULL,
    open         NUMERIC(18,4),
    high         NUMERIC(18,4),
    low          NUMERIC(18,4),
    close        NUMERIC(18,4),
    volume       BIGINT,
    is_verified  BOOLEAN DEFAULT FALSE,
    verified_close NUMERIC(18,4),   -- Ağırlıklı ortalama "doğru fiyat"
    UNIQUE(instrument_id, source, timestamp)
);

-- Kaynak güvenilirlik tablosu
CREATE TABLE data_sources (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(50) UNIQUE,
    reliability_score NUMERIC(5,2) DEFAULT 100.0,
    total_requests  INT DEFAULT 0,
    failed_requests INT DEFAULT 0,
    last_success    TIMESTAMP,
    is_active       BOOLEAN DEFAULT TRUE
);

-- Kaynak tutarsızlık logu
CREATE TABLE discrepancies (
    id            BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    timestamp     TIMESTAMP,
    source_a      VARCHAR(50),
    source_b      VARCHAR(50),
    price_a       NUMERIC(18,4),
    price_b       NUMERIC(18,4),
    diff_pct      NUMERIC(8,4),
    alert_sent    BOOLEAN DEFAULT FALSE
);

-- Teknik analiz sinyalleri
CREATE TABLE technical_signals (
    id            BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    source        VARCHAR(50),  -- hesaplanan veya tradingview/investing
    timestamp     TIMESTAMP,
    rsi           NUMERIC(8,4),
    macd          NUMERIC(12,6),
    macd_signal   NUMERIC(12,6),
    bb_upper      NUMERIC(18,4),
    bb_lower      NUMERIC(18,4),
    ema_20        NUMERIC(18,4),
    ema_50        NUMERIC(18,4),
    ema_200       NUMERIC(18,4),
    tv_summary    VARCHAR(20),  -- STRONG_BUY, BUY, NEUTRAL, SELL, STRONG_SELL
    inv_summary   VARCHAR(20)
);

-- Haber ve sentiment
CREATE TABLE news_feed (
    id            BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    source        VARCHAR(100),
    title         TEXT,
    url           TEXT,
    published_at  TIMESTAMP,
    language      VARCHAR(10),  -- tr, en
    sentiment     VARCHAR(20),  -- positive, negative, neutral
    sentiment_score NUMERIC(5,4),
    processed_at  TIMESTAMP DEFAULT NOW()
);

-- Öneri motoru çıktıları
CREATE TABLE recommendations (
    id            BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    generated_at  TIMESTAMP DEFAULT NOW(),
    action        VARCHAR(10),  -- BUY, HOLD, SELL
    timeframe     VARCHAR(20),  -- short, mid, long
    confidence    NUMERIC(5,2),
    risk_level    VARCHAR(20),  -- low, medium, high (etiket)
    risk_score    NUMERIC(5,2), -- 0-100 sayısal risk (otomasyon eşiği için)
    target_price  NUMERIC(18,4),
    stop_loss     NUMERIC(18,4),
    take_profit   NUMERIC(18,4),
    summary       TEXT,
    tech_score    NUMERIC(5,2),
    sentiment_score NUMERIC(5,2),
    fundamental_score NUMERIC(5,2)
);

-- Backtest sonuçları
CREATE TABLE backtest_results (
    id            BIGSERIAL PRIMARY KEY,
    strategy_name VARCHAR(100),
    timeframe     VARCHAR(20),
    start_date    DATE,
    end_date      DATE,
    total_return  NUMERIC(10,4),
    sharpe_ratio  NUMERIC(8,4),
    max_drawdown  NUMERIC(8,4),
    win_rate      NUMERIC(5,2),
    avg_hold_days NUMERIC(8,2),
    params        JSONB,        -- Kullanılan sinyal ağırlıkları
    run_at        TIMESTAMP DEFAULT NOW()
);

-- Kullanıcı hesabı ve bakiye
CREATE TABLE user_account (
    id            SERIAL PRIMARY KEY,
    trading_mode  VARCHAR(20) DEFAULT 'manual_parallel',  -- manual_parallel, semi_auto, full_auto, paper
    risk_threshold NUMERIC(5,2) DEFAULT 50.0,  -- Bu skorun üstündeki işlemler onay ister
    initial_balance NUMERIC(18,4),         -- Başlangıç bütçesi (örn. 15000)
    cash_balance  NUMERIC(18,4) DEFAULT 0, -- Dağıtılmamış serbest nakit
    currency      VARCHAR(10) DEFAULT 'TRY',
    created_at    TIMESTAMP DEFAULT NOW()
);

-- Cüzdanlar: havuz × vade matrisi (6 bağımsız cüzdan)
CREATE TABLE wallets (
    id            SERIAL PRIMARY KEY,
    account_id    INT REFERENCES user_account(id),
    pool          VARCHAR(10),   -- 'bot' veya 'user'
    timeframe     VARCHAR(20),   -- 'short', 'mid', 'long'
    allocated     NUMERIC(18,4) DEFAULT 0,  -- Bu cüzdana ayrılan bütçe
    cash_balance  NUMERIC(18,4) DEFAULT 0,  -- Cüzdanın kullanılabilir nakdi
    UNIQUE(account_id, pool, timeframe)
);

-- Para giriş/çıkış hareketleri (deposit, withdrawal, yeniden tahsis)
CREATE TABLE cash_flows (
    id            BIGSERIAL PRIMARY KEY,
    account_id    INT REFERENCES user_account(id),
    wallet_id     INT REFERENCES wallets(id),  -- NULL ise hesap geneli
    flow_type     VARCHAR(20),   -- 'deposit', 'withdrawal', 'reallocate'
    amount        NUMERIC(18,4),
    occurred_at   TIMESTAMP DEFAULT NOW(),
    notes         TEXT
);

-- Portföy işlemleri (cüzdan bazlı)
CREATE TABLE portfolio (
    id            BIGSERIAL PRIMARY KEY,
    wallet_id     INT REFERENCES wallets(id),   -- Hangi cüzdandan
    instrument_id INT REFERENCES instruments(id),
    action        VARCHAR(10),  -- BUY, SELL
    quantity      NUMERIC(18,4),
    price         NUMERIC(18,4),
    total         NUMERIC(18,4),
    commission    NUMERIC(18,4) DEFAULT 0,
    realized_pnl  NUMERIC(18,4),         -- Satışta gerçekleşen kâr-zarar
    hold_days     INT,                   -- Pozisyon tutma süresi (satışta)
    followed_bot  BOOLEAN,               -- İşlem bot önerisiyle uyumlu muydu?
    transaction_at TIMESTAMP,
    notes         TEXT
);

-- Açık pozisyonlar (cüzdan bazlı özet)
CREATE TABLE open_positions (
    id            BIGSERIAL PRIMARY KEY,
    wallet_id     INT REFERENCES wallets(id),
    instrument_id INT REFERENCES instruments(id),
    quantity      NUMERIC(18,4),
    avg_cost      NUMERIC(18,4),         -- Ortalama maliyet
    opened_at     TIMESTAMP,
    UNIQUE(wallet_id, instrument_id)
);

-- Kullanıcı takip listeleri (watchlist)
CREATE TABLE watchlists (
    id            SERIAL PRIMARY KEY,
    name          VARCHAR(100),          -- "Uzun Vade", "Temettü" vb.
    sort_order    INT DEFAULT 0,
    created_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE watchlist_items (
    id            BIGSERIAL PRIMARY KEY,
    watchlist_id  INT REFERENCES watchlists(id) ON DELETE CASCADE,
    instrument_id INT REFERENCES instruments(id),
    sort_order    INT DEFAULT 0,
    added_at      TIMESTAMP DEFAULT NOW(),
    UNIQUE(watchlist_id, instrument_id)
);

-- Bot öneri listesi ve takibi
CREATE TABLE bot_picks (
    id            BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    timeframe     VARCHAR(20),           -- short, mid, long
    action        VARCHAR(10),
    confidence    NUMERIC(5,2),
    price_at_pick NUMERIC(18,4),         -- Öneri anındaki fiyat
    target_price  NUMERIC(18,4),
    picked_at     TIMESTAMP DEFAULT NOW(),
    -- Takip / sonuç
    is_open       BOOLEAN DEFAULT TRUE,
    closed_at     TIMESTAMP,
    price_at_close NUMERIC(18,4),
    outcome       VARCHAR(20),           -- hit_target, stopped, expired, open
    return_pct    NUMERIC(8,4)
);

-- Alarmlar
CREATE TABLE alerts (
    id            BIGSERIAL PRIMARY KEY,
    instrument_id INT REFERENCES instruments(id),
    alert_type    VARCHAR(50),  -- price_above, price_below, pct_change, signal
    threshold     NUMERIC(18,4),
    is_active     BOOLEAN DEFAULT TRUE,
    triggered_at  TIMESTAMP,
    created_at    TIMESTAMP DEFAULT NOW()
);

-- Kullanıcı ayarları (tema dahil)
CREATE TABLE settings (
    key           VARCHAR(100) PRIMARY KEY,
    value         TEXT
    -- örn: theme='dark'|'light'|'auto', accent_color='blue', language='tr'
);

-- Döviz kuru geçmişi (USD/TRY — kur etkisi hesabı için)
CREATE TABLE fx_rates (
    id            BIGSERIAL PRIMARY KEY,
    pair          VARCHAR(10),   -- 'USDTRY' vb.
    rate          NUMERIC(18,6),
    timestamp     TIMESTAMP NOT NULL,
    source        VARCHAR(50),   -- TCMB vb.
    UNIQUE(pair, timestamp)
);
```

---

## 10. Klasör Yapısı

```
borsa-bot/
├── pyproject.toml
├── README.md
├── document.md
├── .env.example
│
├── app/
│   ├── __init__.py
│   ├── main.py                  # Uygulama giriş noktası
│   │
│   ├── ui/                      # PySide6 arayüz bileşenleri
│   │   ├── main_window.py        # Ana pencere + sidebar navigasyon
│   │   ├── theme_manager.py      # Dark/light tema yönetimi (qdarktheme)
│   │   ├── dashboard.py          # Ana ekran özeti
│   │   ├── chart_widget.py       # Mum grafiği + indikatörler (finplot)
│   │   ├── watchlist_widget.py   # Kullanıcı takip listeleri
│   │   ├── bot_picks_widget.py   # Bot öneri listesi + başarı oranı
│   │   ├── portfolio_widget.py   # Bakiye, pozisyon, kâr-zarar
│   │   ├── budget_widget.py      # Bütçe matrisi, para ekleme/çekme
│   │   ├── trading_widget.py     # İşlem modu, emir onayı, kill switch
│   │   ├── history_widget.py     # İşlem geçmişi
│   │   ├── news_widget.py        # Haber akışı + sentiment
│   │   ├── alerts_widget.py      # Alarm yönetimi
│   │   ├── settings_widget.py    # Ayarlar (tema, kaynak, bildirim, güvenlik)
│   │   ├── recommendation_widget.py  # Öneri kartı + açıklanabilirlik
│   │   └── components/           # Tekrar kullanılabilir bileşenler
│   │       ├── metric_card.py    # Metrik kartı
│   │       ├── sparkline.py      # Mini trend grafiği
│   │       ├── toast.py          # Bildirim balonu
│   │       └── skeleton.py       # Yükleme iskeleti
│   │
│   ├── data/                    # Veri toplama katmanı
│   │   ├── base_source.py       # Soyut kaynak sınıfı
│   │   ├── sources/
│   │   │   ├── yfinance_source.py
│   │   │   ├── stooq_source.py
│   │   │   ├── alphavantage_source.py
│   │   │   ├── isyatirim_source.py   # BIST birincil kaynağı
│   │   │   ├── kap_source.py         # KAP bildirimleri
│   │   │   ├── tradingview_source.py
│   │   │   ├── investing_source.py
│   │   │   ├── finviz_source.py
│   │   │   ├── tcmb_source.py        # USD/TRY kur verisi (kur etkisi için)
│   │   │   └── rss_source.py
│   │   ├── collector.py         # Async paralel veri toplayıcı
│   │   └── comparator.py        # Veri karşılaştırma motoru
│   │
│   ├── analysis/                # Analiz motorları
│   │   ├── technical.py         # TA-Lib indikatör hesaplamaları
│   │   ├── sentiment.py         # NLP sentiment analizi
│   │   ├── fundamental.py       # Temel analiz verileri
│   │   ├── recommender.py       # Öneri motoru (skor birleştirme)
│   │   ├── backtester.py        # Backtesting (vectorbt)
│   │   └── risk.py              # Risk yönetimi (stop-loss, korelasyon)
│   │
│   ├── portfolio/               # Portföy ve liste yönetimi
│   │   ├── account.py           # Bakiye, kâr-zarar hesabı
│   │   ├── wallets.py           # Cüzdan matrisi (havuz × vade) yönetimi
│   │   ├── cash_flows.py        # Para ekleme/çekme/yeniden tahsis
│   │   ├── positions.py         # Açık pozisyon yönetimi
│   │   ├── pnl.py               # Komisyon/vergi/kur dahil net kâr-zarar
│   │   ├── benchmark.py         # Endeks kıyaslaması (BIST 100 / S&P 500)
│   │   ├── watchlist.py         # Takip listesi mantığı
│   │   ├── bot_picks.py         # Bot öneri listesi + takip
│   │   └── paper_trading.py     # Sanal portföy modu
│   │
│   ├── trading/                 # İşlem yürütme katmanı
│   │   ├── execution_modes.py    # manuel-eşli / yarı / tam otomatik / sanal
│   │   ├── manual_parallel.py    # Manuel-eşli mod: öneri üret, takip et
│   │   ├── risk_scorer.py        # İşlem risk skoru hesaplama
│   │   ├── auto_gate.py          # Risk eşiği + onay kapısı mantığı
│   │   └── order_manager.py      # Emir oluşturma, doğrulama
│   │
│   ├── broker/                  # Aracı kurum entegrasyonu (HAZIR BEKLER)
│   │   ├── base_broker.py       # Soyut BaseBroker arayüzü
│   │   ├── adapters/            # Her aracı kurum için ayrı adapter
│   │   │   └── example_broker.py
│   │   └── safety.py            # Limit, kill switch, circuit breaker
│   │
│   ├── db/                      # Veritabanı katmanı
│   │   ├── models.py            # SQLAlchemy modelleri
│   │   ├── session.py           # PostgreSQL bağlantı yönetimi
│   │   └── migrations/          # Alembic migration dosyaları
│   │
│   ├── alerts/                  # Alarm sistemi
│   │   ├── engine.py
│   │   └── notifier.py
│   │
│   └── config.py                # Uygulama ayarları (.env okuma)
│
├── tests/
│   ├── test_data_sources.py
│   ├── test_comparator.py
│   ├── test_technical.py
│   ├── test_sentiment.py
│   ├── test_recommender.py
│   ├── test_backtester.py
│   ├── test_wallets.py          # Cüzdan matrisi + para giriş/çıkış
│   ├── test_risk_scorer.py      # Risk skoru hesaplama
│   ├── test_auto_gate.py        # Risk eşiği + onay kapısı
│   └── test_broker.py           # BaseBroker arayüzü (mock adapter)
│
└── scripts/
    ├── setup_db.py              # PostgreSQL şema kurulumu
    └── seed_instruments.py      # Temel hisse listesi yükleme
```

---

## 11. Geliştirme Fazları

### Faz 1 — Temel Altyapı (Hafta 1–8)

- [ ] Poetry ile proje kurulumu, pyproject.toml yapılandırması
- [ ] PostgreSQL şema kurulumu (setup_db.py)
- [ ] SQLAlchemy modelleri ve Alembic migration
- [ ] Birincil veri kaynakları entegrasyonu (yfinance, Stooq, Alpha Vantage, İş Yatırım)
- [ ] Async veri toplayıcı (collector.py)
- [ ] Veri karşılaştırma motoru (comparator.py)
- [ ] TA-Lib teknik analiz motoru
- [ ] PySide6 ana pencere + sidebar navigasyon iskeleti
- [ ] Dark/light tema altyapısı (theme_manager.py, qdarktheme)
- [ ] Grafik bileşeni (finplot mum grafikleri, temaya duyarlı)
- [ ] Alarm sistemi (fiyat ve yüzde değişim alarmları)
- [ ] pytest birim testleri

### Faz 2 — Haber & Sentiment + Scraping + Listeler (Hafta 9–15)

- [ ] RSS haber toplayıcı (feedparser)
- [ ] Türkçe NLP entegrasyonu (HuggingFace BERT-Turkish)
- [ ] İngilizce NLP entegrasyonu (FinBERT)
- [ ] TradingView scraping (tvdatafeed + Playwright)
- [ ] Investing.com scraping (Playwright)
- [ ] Kullanıcı takip listeleri (watchlist) — oluştur, düzenle, sırala
- [ ] Bot öneri listesi (bot picks) ve öneri takip mekanizması
- [ ] Öneri motorunun ilk versiyonu (kısa vade ağırlıklı)
- [ ] UI'ya haber paneli, sentiment göstergesi, watchlist ve bot picks ekranları

### Faz 3 — Öneri Motoru, Portföy & UI Cilası (Hafta 16–24)

- [ ] Orta ve uzun vade öneri mantığı
- [ ] Temel analiz verileri entegrasyonu (Finviz + yfinance finansallar + İş Yatırım finansal tabloları)
- [ ] Backtesting motoru (vectorbt) ve strateji performans paneli
- [ ] Risk yönetimi modülü (stop-loss, pozisyon büyüklüğü, korelasyon analizi)
- [ ] Sinyal ağırlıklarının backtest sonuçlarına göre optimizasyonu
- [ ] Öneri açıklanabilirlik paneli (hangi sinyal ne kadar katkı yaptı)
- [ ] Portföy yönetimi: bakiye, açık pozisyon, kâr-zarar, performans grafiği
- [ ] Komisyon / vergi / kur etkisi dahil net kâr-zarar hesabı (pnl.py)
- [ ] Endeks kıyaslaması: BIST 100 / S&P 500 benchmark (benchmark.py)
- [ ] USD/TRY kur verisi entegrasyonu (TCMB)
- [ ] Bütçe dağılım matrisi (havuz × vade = 6 cüzdan) ve cüzdan bazlı işlem
- [ ] Para ekleme / çekme / yeniden tahsis (cash_flows) ve time-weighted getiri
- [ ] Havuz ve vade bazlı karşılaştırmalı performans analizi
- [ ] İşlem geçmişi ekranı + filtreleme
- [ ] Manuel-eşli (paralel) mod: öneri üret, kullanıcı elle uygular, bot takip eder
- [ ] İşlem risk skoru hesaplama (risk_scorer)
- [ ] Risk eşiği + onay kapısı mantığı (auto_gate)
- [ ] Sanal portföy (paper trading) modu
- [ ] Bot picks başarı oranı istatistikleri
- [ ] UI cilası: metrik kartları, sparkline, toast bildirimleri, skeleton loader
- [ ] Accent renk özelleştirme, ayarlar ekranı
- [ ] KAP bildirimi takibi
- [ ] Kaynak güvenilirlik skorlama sistemi iyileştirmesi
- [ ] CSV / Excel dışa aktarım
- [ ] PyInstaller ile paketleme ve dağıtım
- [ ] Kapsamlı entegrasyon testleri

### Faz 4 — Firma Bağlantısı & Gerçek Emir (İLERİDE — Hazır Bekler)

> Bu faz şimdilik AKTIF EDİLMEZ. Soyut `BaseBroker` arayüzü Faz 3'te hazır tutulur; manuel-eşli mod tam çalışırken firma bağlantısı geldiğinde kod yapısı değişmeden aktive edilir. En riskli aşamadır, yalnızca önceki modlar kanıtlandıktan sonra başlatılır.

- [ ] Aracı kurum / yöntem seçimi ve API dokümantasyonu incelemesi
- [ ] Seçilen aracı kurum için BaseBroker adapter'ının yazılması
- [ ] Emir gönderme (order_manager'a gerçek gönderim eklenmesi)
- [ ] API anahtarlarının güvenli (şifreli) saklanması
- [ ] Yarı-otomatik modun gerçek emirle aktive edilmesi
- [ ] Güvenlik katmanı: günlük limit, kill switch, circuit breaker, audit log
- [ ] Otomatik işlem ekranı (trading_widget.py) gerçek emir desteği
- [ ] Küçük tutarlarla canlı test
- [ ] Tam otomatik mod (risk eşikli onay ile, yeterli güven sağlandıktan sonra)

---

## 12. Ortam Değişkenleri (.env)

```env
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=borsa_bot
DB_USER=postgres
DB_PASSWORD=

# API Anahtarları
ALPHA_VANTAGE_API_KEY=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
REDDIT_USER_AGENT=BorsaBot/1.0

# Uygulama Ayarları
DATA_REFRESH_INTERVAL=60        # saniye
DISCREPANCY_THRESHOLD_PCT=0.5   # % fiyat farkı uyarı eşiği
ALERT_DISCREPANCY_PCT=2.0       # % fiyat farkı alarm eşiği
SOURCE_FAILURE_LIMIT=5          # art arda hata limiti

# İşlem ve Otomasyon
TRADING_MODE=manual_parallel    # manual_parallel | semi_auto | full_auto | paper
RISK_THRESHOLD=50               # bu skorun üstündeki işlemler onay ister (0-100)
DAILY_TRADE_LIMIT=10            # günlük maksimum otomatik işlem sayısı
MAX_DRAWDOWN_PCT=15             # circuit breaker: bu % düşüşte bot durur
DEFAULT_COMMISSION_PCT=0.2      # varsayılan işlem komisyonu %

# Aracı Kurum (ileride aktive edilir — şimdilik boş)
BROKER_NAME=                    # seçilen aracı kurum adı
BROKER_API_KEY=                 # güvenli depoda saklanır, burada boş bırakılır
BROKER_API_SECRET=

# Güvenlik
ENCRYPTION_ENABLED=true         # API anahtarları OS keyring ile şifrelenir

# NLP Model Yolları (ilk çalıştırmada otomatik indirilir)
NLP_MODEL_TR=savasy/bert-base-turkish-sentiment-cased
NLP_MODEL_EN=ProsusAI/finbert
```

---

## 13. Bağımlılıklar (pyproject.toml)

```toml
[tool.poetry.dependencies]
python = "^3.11"

# UI
PySide6 = "^6.6"          # PyQt6 yerine — LGPL lisans, ücretsiz
pyqtgraph = "^0.13"
finplot = "^1.9"          # TradingView benzeri mum grafikleri
pyqtdarktheme = "^2.1"    # Dark/light tema (qdarktheme)
qt-material = "^2.14"     # Material Design teması (opsiyonel)
qtawesome = "^1.3"        # Modern ikonlar

# Veritabanı
SQLAlchemy = "^2.0"
alembic = "^1.13"
psycopg2-binary = "^2.9"

# Veri
yfinance = "^0.2"
pandas-datareader = "^0.10"   # Stooq yedek kaynağı
alpha-vantage = "^2.3"
isyatirimhisse = "^4.0"       # BIST birincil kaynağı
pandas = "^2.1"
numpy = "^2.0"
pyarrow = "^14.0"      # Parquet desteği

# Teknik analiz
TA-Lib = "^0.6"        # v0.6.5+ önceden derlenmiş wheel ile kolay kurulum
pandas-ta = "^0.3"     # Yedek

# Backtesting & risk
vectorbt = "^0.26"     # Hızlı vektörel backtesting
# alternatif: backtrader = "^1.9"

# Web scraping
playwright = "^1.40"
beautifulsoup4 = "^4.12"
tvdatafeed = "^2.0"
finvizfinance = "^0.14"
feedparser = "^6.0"
aiohttp = "^3.9"

# NLP
transformers = "^4.36"
torch = "^2.1"
praw = "^7.7"

# Yardımcı
python-dotenv = "^1.0"
loguru = "^0.7"
pydantic = "^2.5"
keyring = "^25.0"      # API anahtarlarının OS güvenli deposunda saklanması
cryptography = "^42.0" # Hassas veri şifreleme

[tool.poetry.dev-dependencies]
pytest = "^7.4"
ruff = "^0.1"
black = "^23.0"
```

---

## 14. Riskler ve Önlemler

| Risk | Olasılık | Çözüm |
|---|---|---|
| yfinance kırılması (Yahoo yapı değişikliği) | Yüksek | Stooq ve İş Yatırım birincil yedek olarak hazır; tek kaynağa bağımlı kalınmaz |
| API rate limit aşımı | Orta | Her kaynak için ayrı istek kuyruğu ve bekleme süresi yönetimi |
| Scraping engelleme | Yüksek | User-agent rotasyonu, bekleme süreleri, proxy desteği hazır tutulacak |
| Aşırı istek nedeniyle İş Yatırım/site engellemesi | Orta | İstek hızı sınırlandırılır, önbellek kullanılır, gece toplu çekim yapılır |
| NLP model bellek kullanımı | Orta | Model lazy loading — yalnızca analiz sırasında yüklenir |
| BIST gerçek zamanlı veri eksikliği | Yüksek | Birden fazla BIST kaynağı + 15 dk gecikmeli veri kabul edilebilir başlangıç |
| Backtest aşırı uydurma (overfitting) | Yüksek | Out-of-sample test, walk-forward analizi, look-ahead bias kontrolü |
| Hatalı otomatik emir (kod hatası) | Yüksek | Yarı-otomatik varsayılan mod, emir doğrulama, günlük limit, kill switch |
| Aşırı kayıp (piyasa hareketi) | Yüksek | Circuit breaker (otomatik durdurma), stop-loss, pozisyon büyüklüğü limiti |
| API anahtarı sızması | Orta | Şifreli saklama, 2FA, IP kısıtlaması, anahtarlar koda yazılmaz |
| Aracı kurum API değişikliği | Orta | Soyut BaseBroker arayüzü, adapter başına izole kod, hızlı güncelleme |
| TradingView / Investing yapı değişikliği | Orta | Scraping modülleri bağımsız tutulur, hızlı güncelleme yapılabilir |
| PostgreSQL bağlantı hatası | Düşük | Bağlantı havuzu (connection pool) + otomatik yeniden bağlanma |

---

## 15. Notlar ve Yasal Uyarılar

- **Bu sistem yatırım tavsiyesi vermez.** Ürettiği tüm öneri, skor ve analizler yalnızca bilgilendirme ve karar destek amaçlıdır. Yatırım kararlarının sorumluluğu tamamen kullanıcıya aittir. Sistem kâr garantisi sunmaz ve geçmiş performans gelecekteki sonuçların göstergesi değildir.
- Ücretsiz API'lerin gecikme süreleri ve günlük kota limitleri üretim ortamında izlenmelidir.
- **Web scraping yasal sorumluluğu:** TradingView, Investing.com, İş Yatırım gibi sitelerin kullanım koşulları (ToS) scraping yapmadan önce mutlaka incelenmelidir. Çoğu kaynak yalnızca kişisel kullanıma izin verir; ticari kullanım veya yeniden dağıtım yasaktır. Aşırı istek atmak hem yasal hem teknik (IP engelleme) risk taşır.
- HuggingFace modelleri ilk çalıştırmada otomatik indirilir (~500 MB). Sonraki çalışmalarda önbellekten kullanılır.
- Veri kaynakları arasında çelişki olması normaldir (farklı gecikme, farklı hesaplama). Karşılaştırma motorunun amacı bu çelişkiyi tespit edip en güvenilir veriyi seçmektir, çelişkiyi tamamen ortadan kaldırmak değil.
- Sentiment analizi modelleri %100 doğru değildir; özellikle ironi, alay ve sektörel jargon yanlış sınıflandırılabilir. Sentiment skoru tek başına değil, diğer sinyallerle birlikte değerlendirilmelidir.
- **Otomatik işlem ve aracı kurum:** Gerçek emirler yalnızca SPK lisanslı bir aracı kurum üzerinden iletilir. Sistem yalnızca kullanıcının kendi hesabını, kendi adına yönetir; başkası adına işlem/portföy yönetimi ayrı SPK yetkisi gerektirir ve kapsam dışıdır. Tam otomatik işlem öncesi sanal ve yarı-otomatik modlarda yeterince test edilmelidir. Kullanılacak aracı kurumun API kullanım koşulları ve algoritmik işlem kuralları incelenmelidir.