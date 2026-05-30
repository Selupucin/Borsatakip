"""RSS haber kaynağı (Reuters, Bloomberg TR, Mynet, Dünya, KAP).

# ============================================================
# KAYNAK: rss
# YASAL:  RSS feed'leri yayıncı tarafından kamuya açık olarak sunulur;
#         başlık ve link okumak ToS uyumlu KİŞİSEL KULLANIM kapsamındadır.
#         Tam metin çekmek (full-text scrape) bu modülün dışındadır ve
#         genellikle telif/ToS ihlali olur. Ticari kullanım/yeniden
#         dağıtım YASAK.
# RATE:   Feed başına en az 60 saniye bekleme. Aynı feed'i sık sık çekmek
#         gereksiz — yayıncılar çoğunlukla dakikalar / saatler arası
#         güncelliyor.
# robots.txt: RSS yolları kural olarak crawl edilebilir; ancak yine de
#             agresif istek atılmaz (rate limit yukarıda).
# ============================================================

Mimari notlar:
- ``feedparser`` senkron + saf-Python; ``asyncio.to_thread`` ile sarılır.
- ``BaseSource`` sözleşmesi quote/ohlcv zorunlu kıldığı için bu metodlar
  ``SourceError("RSS sadece haber için")`` ile fail-fast döner — collector
  RSS'i fiyat sorgusuna sokmaz.
- ``fetch_news(since=...)`` çoklu feed'i paralel çekip ``NewsItem`` listesi
  döndürür. Idempotency (aynı URL ikinci kez insert edilmemesi) collector
  tarafında ``save_news`` içinde yapılır.
- Dil etiketi (``tr``/``en``) feed-bazında sabit map'ten gelir; her haber
  için ayrı dil tespiti yapılmaz (Faz 2'de sentiment.py istenirse override
  edebilir).
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from loguru import logger

from app.data.base_source import BaseSource, OHLCVBar, Quote, SourceError, SourceUnavailableError


try:  # pragma: no cover - import guard
    import feedparser  # type: ignore
except Exception as exc:  # pragma: no cover
    feedparser = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# Feed kataloğu
# ---------------------------------------------------------------------------

# (feed_name, url, language).
# 2025-2026 itibarıyla çalışıyor durumda olanlar — eski Reuters feed'i 404
# döndüğü için kaldırıldı, yerine birden fazla TR finans kaynağı eklendi.
DEFAULT_FEEDS: list[tuple[str, str, str]] = [
    # Türkçe finans/ekonomi
    ("ntv_ekonomi", "https://www.ntv.com.tr/ekonomi.rss", "tr"),
    ("hurriyet_ekonomi", "https://www.hurriyet.com.tr/rss/ekonomi", "tr"),
    ("milliyet_ekonomi", "https://www.milliyet.com.tr/rss/rssNew/ekonomiRss.xml", "tr"),
    ("haberturk_ekonomi", "https://www.haberturk.com/rss/ekonomi.xml", "tr"),
    ("mynet_finans", "https://www.mynet.com/finans/rss.xml", "tr"),
    ("dunya_gazetesi", "https://www.dunya.com/rss?dunya", "tr"),
    ("bloomberg_tr", "https://www.bloomberght.com/rss", "tr"),
    ("aa_ekonomi", "https://www.aa.com.tr/tr/rss/default?cat=ekonomi", "tr"),
    ("kap_disclosures", "https://www.kap.org.tr/tr/api/disclosures.rss", "tr"),
    # İngilizce uluslararası
    ("yahoo_finance", "https://finance.yahoo.com/news/rssindex", "en"),
    ("cnbc_business", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147", "en"),
    ("investing_news", "https://www.investing.com/rss/news.rss", "en"),
    ("marketwatch", "https://feeds.content.dowjones.io/public/rss/RSSMarketsMain", "en"),
]


# Modern, makul UA listesi. random.choice ile her fetch'te dönüşümlü kullanılır.
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]


def _pick_user_agent() -> str:
    """User-Agent rotasyonu — her çağrıda rastgele."""
    return random.choice(_USER_AGENTS)


# ---------------------------------------------------------------------------
# Veri yapısı
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NewsItem:
    """RSS / haber kaynağından gelen tek bir haber kaydı.

    ``instrument_id`` veritabanına yazılırken collector tarafından (varsa)
    ticker eşlemesinden türetilir; burada haber kaynağında tanımlı değildir.
    """

    title: str
    url: str
    published_at: datetime
    source: str  # feed adı (örn. "reuters_finance")
    language: str  # "tr" | "en"
    summary: Optional[str] = None


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class RSSSource(BaseSource):
    name = "rss"

    # Feed başına min bekleme — saniye. Aynı feed'i bu süreden önce tekrar
    # çekmek anlamsız (yayıncılar genelde dakikalar arası güncelliyor).
    _MIN_INTERVAL_S = 60.0

    def __init__(self, feeds: Iterable[tuple[str, str, str]] | None = None) -> None:
        super().__init__()
        self._feeds: list[tuple[str, str, str]] = list(feeds or DEFAULT_FEEDS)
        self._last_fetch: dict[str, float] = {}  # feed_name -> monotonic ts
        self._lock = asyncio.Lock()

        if feedparser is None:
            logger.warning(
                "feedparser import edilemedi: {}. RSSSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )

    # ---- BaseSource sözleşmesi (RSS desteklemez) ---------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        raise SourceError("RSS sadece haber için — OHLCV desteklenmez")

    async def fetch_quote(self, ticker: str) -> Quote:
        raise SourceError("RSS sadece haber için — quote desteklenmez")

    async def is_alive(self) -> bool:
        """En az bir feed'in parse edilebildiğini doğrula (boş gelse bile OK)."""
        if feedparser is None or not self._feeds:
            return False
        try:
            name, url, _ = self._feeds[0]
            await asyncio.to_thread(self._parse_feed, url)
            return True
        except Exception:
            return False

    # ---- Public: fetch_news ------------------------------------------------

    async def fetch_news(
        self, since: datetime | None = None
    ) -> list[NewsItem]:
        """Tüm yapılandırılmış RSS feed'lerini paralel çek ve ``NewsItem``
        listesi döndür.

        - ``since=None`` -> tüm feed entry'leri (çoğu yayıncı son 20-50 entry
          döner; bu pencere uygulamada yeterli).
        - ``since=ts``   -> ``published_at >= ts`` filtrelenir.
        - Aynı feed ``_MIN_INTERVAL_S`` saniyeden önce tekrar çekilmez (boş
          liste döner).

        Idempotency (URL tekrar insert engeli) burada YAPILMAZ — DB tarafı
        (``DataCollector.save_news``) sorumludur.
        """
        if feedparser is None:
            raise SourceUnavailableError("feedparser kurulu değil")
        if not self._feeds:
            return []

        async def _one(entry: tuple[str, str, str]) -> list[NewsItem]:
            return await self._fetch_one_feed(entry, since)

        results = await asyncio.gather(
            *(_one(e) for e in self._feeds),
            return_exceptions=True,
        )

        all_items: list[NewsItem] = []
        for res in results:
            if isinstance(res, Exception):
                logger.warning("rss: bir feed başarısız: {}", res)
                continue
            all_items.extend(res)

        # En yeni en başta — UI/DB için pratik
        all_items.sort(key=lambda n: n.published_at, reverse=True)
        return all_items

    # ---- Internal ----------------------------------------------------------

    async def _fetch_one_feed(
        self, entry: tuple[str, str, str], since: datetime | None
    ) -> list[NewsItem]:
        name, url, lang = entry

        # Min-interval guard
        async with self._lock:
            now = asyncio.get_event_loop().time()
            last = self._last_fetch.get(name, 0.0)
            if now - last < self._MIN_INTERVAL_S:
                logger.debug("rss: '{}' min-interval içinde, atlandı", name)
                return []
            self._last_fetch[name] = now

        try:
            parsed = await asyncio.to_thread(self._parse_feed, url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rss: '{}' parse hatası: {}", name, exc)
            return []

        items: list[NewsItem] = []
        for raw in getattr(parsed, "entries", []) or []:
            try:
                item = self._entry_to_news_item(raw, name, lang)
            except Exception as exc:  # noqa: BLE001
                logger.debug("rss: '{}' entry atlandı: {}", name, exc)
                continue
            if since is not None and item.published_at < since:
                continue
            items.append(item)
        return items

    @staticmethod
    def _parse_feed(url: str):  # pragma: no cover - external IO
        # feedparser request_headers ile UA gönderebilir
        return feedparser.parse(
            url,
            request_headers={
                "User-Agent": _pick_user_agent(),
                "Accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
            },
        )

    @staticmethod
    def _entry_to_news_item(raw, feed_name: str, language: str) -> NewsItem:
        title = (raw.get("title") or "").strip()
        url = (raw.get("link") or "").strip()
        if not title or not url:
            raise ValueError("title/link boş")

        published_at = RSSSource._extract_published(raw)
        summary = raw.get("summary") or raw.get("description")
        if summary:
            summary = str(summary).strip()
        return NewsItem(
            title=title,
            url=url,
            published_at=published_at,
            source=feed_name,
            language=language,
            summary=summary,
        )

    @staticmethod
    def _extract_published(raw) -> datetime:
        """feedparser ``published_parsed`` / ``updated_parsed`` -> datetime (UTC)."""
        for key in ("published_parsed", "updated_parsed", "created_parsed"):
            tm = raw.get(key)
            if tm:
                try:
                    return datetime(*tm[:6], tzinfo=timezone.utc)
                except Exception:  # noqa: BLE001
                    continue
        # Fallback: şimdi
        return datetime.now(tz=timezone.utc)


__all__ = ["RSSSource", "NewsItem", "DEFAULT_FEEDS"]
