"""Async paralel veri toplayıcı.

Çekirdek mantık (doküman §4.2):
1. ``fetch_all_quotes`` / ``fetch_all_ohlcv``: aktif kaynakları
   ``asyncio.gather(..., return_exceptions=True)`` ile paralel sorgular —
   bir kaynak yavaşsa diğerleri beklemez.
2. Her kaynağın ``data_sources`` satırı güncellenir: ``total_requests``,
   ``failed_requests``, ``last_success``.
3. **5 art arda hata = otomatik devre dışı** (``SOURCE_FAILURE_LIMIT``,
   .env'den okunur). ``is_active=False`` set edilir; sonraki çağrılarda
   ``get_active_sources()`` bu kaynağı dışlar.
4. ``RateLimitError`` failure sayılmaz — kotadan kaynaklı, geçicidir
   (kaynak yine aktif kalır, sadece o istek atlanır).
5. ``save_to_db``: ``PriceHistory`` (her kaynağın kendi satırı) veya
   ``FxRate`` (TCMB için) tablolarına yazar.

DB session yönetimi: ``session_factory`` (async sessionmaker) constructor'a
verilir; collector her DB işlemi için bağımsız bir session açar — uzun
yaşam süresi olan session paylaşılmaz (asyncio güvenliği).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Iterable, Sequence

from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    RateLimitError,
    SourceError,
)
from app.data.sources.kap_source import KapDisclosure
from app.data.sources.rss_source import NewsItem
from app.db.models import DataSource, FxRate, NewsFeed, PriceHistory


class DataCollector:
    """Tüm aktif kaynakları paralel sorgulayan + DB'ye yazan toplayıcı."""

    def __init__(
        self,
        sources: Sequence[BaseSource],
        session_factory: async_sessionmaker,
    ) -> None:
        self._sources: list[BaseSource] = list(sources)
        self._session_factory = session_factory

        # Çalışma anı state — DB'ye yazma ile birlikte tutulur (DB ana kaynak,
        # bu yalnızca hızlı erişim/karar mantığı için).
        self._inactive: set[str] = set()  # is_active=False olan kaynak adları
        self._consecutive_failures: dict[str, int] = defaultdict(int)
        self._failure_limit = settings.source_failure_limit

    # ---- Public API ---------------------------------------------------------

    def get_active_sources(self) -> list[BaseSource]:
        """``data_sources.is_active=True`` olan kaynakları döndür."""
        return [s for s in self._sources if s.name not in self._inactive]

    async def fetch_all_quotes(self, ticker: str) -> list[Quote]:
        """Tüm aktif kaynaklardan paralel quote çek.

        Kullanım:
            quotes = await collector.fetch_all_quotes("THYAO.IS")
            verified, discrepancies = comparator.compare_quotes(quotes)
        """
        active = self.get_active_sources()
        if not active:
            logger.error("fetch_all_quotes: aktif kaynak yok")
            return []

        results = await asyncio.gather(
            *(self._safe_fetch_quote(s, ticker) for s in active),
            return_exceptions=False,  # _safe_fetch_quote zaten exception swallow
        )

        return [q for q in results if q is not None]

    async def fetch_all_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> dict[str, list[OHLCVBar]]:
        """Her kaynaktan OHLCV listesini ayrı bir dict altında döndür."""
        active = self.get_active_sources()
        if not active:
            logger.error("fetch_all_ohlcv: aktif kaynak yok")
            return {}

        async def _one(src: BaseSource) -> tuple[str, list[OHLCVBar] | None]:
            return src.name, await self._safe_fetch_ohlcv(src, ticker, start, end, interval)

        results = await asyncio.gather(*(_one(s) for s in active))
        return {name: bars for name, bars in results if bars}

    async def save_quotes(self, quotes: Iterable[Quote], instrument_id: int) -> int:
        """Quote listesini ``price_history`` tablosuna yaz (her kaynak ayrı satır).

        Aynı (instrument_id, source, timestamp) için UPSERT (do nothing).
        """
        rows = []
        for q in quotes:
            rows.append(
                {
                    "instrument_id": instrument_id,
                    "source": q.source,
                    "timestamp": q.timestamp,
                    "open": q.price,
                    "high": q.price,
                    "low": q.price,
                    "close": q.price,
                    "volume": q.volume,
                }
            )
        if not rows:
            return 0

        async with self._session_factory() as session:
            stmt = pg_insert(PriceHistory).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["instrument_id", "source", "timestamp"]
            )
            await session.execute(stmt)
            await session.commit()
        return len(rows)

    async def save_ohlcv(
        self,
        source_name: str,
        bars: Iterable[OHLCVBar],
        instrument_id: int,
    ) -> int:
        """OHLCV listesini ``price_history`` tablosuna yaz."""
        rows = [
            {
                "instrument_id": instrument_id,
                "source": source_name,
                "timestamp": b.timestamp,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
            }
            for b in bars
        ]
        if not rows:
            return 0
        async with self._session_factory() as session:
            stmt = pg_insert(PriceHistory).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["instrument_id", "source", "timestamp"]
            )
            await session.execute(stmt)
            await session.commit()
        return len(rows)

    async def save_fx(self, quotes: Iterable[Quote]) -> int:
        """TCMB gibi FX kaynaklarından gelen quote'ları ``fx_rates`` tablosuna yaz."""
        rows = [
            {
                "pair": q.ticker.upper().replace("/", ""),
                "rate": q.price,
                "timestamp": q.timestamp,
                "source": q.source,
            }
            for q in quotes
        ]
        if not rows:
            return 0
        async with self._session_factory() as session:
            stmt = pg_insert(FxRate).values(rows)
            stmt = stmt.on_conflict_do_nothing(index_elements=["pair", "timestamp"])
            await session.execute(stmt)
            await session.commit()
        return len(rows)

    async def save_news(
        self,
        items: Iterable[NewsItem],
        instrument_id: int | None = None,
    ) -> int:
        """RSS / haber kaynaklarından gelen ``NewsItem``'ları ``news_feed``'e
        idempotent şekilde yaz.

        ``NewsFeed`` modelinde URL üzerinde unique constraint yok (tasarım
        gereği — aynı haber farklı feed'lerde tekrarlanabilir, bilinçli
        duplicate'a izin verilir ama instrument bazında değil). Bu metod
        (instrument_id, url) çiftine göre manuel duplicate kontrolü yapar.

        ``instrument_id=None`` -> genel piyasa haberi olarak yazılır.

        Geriye eklenen yeni satır sayısını döner.
        """
        items = list(items)
        if not items:
            return 0

        urls = [it.url for it in items if it.url]
        if not urls:
            return 0
        inserted = 0

        async with self._session_factory() as session:
            # Mevcut (instrument_id, url) çiftlerini bir kerede çek
            existing_q = select(NewsFeed.url).where(NewsFeed.url.in_(urls))
            if instrument_id is None:
                existing_q = existing_q.where(NewsFeed.instrument_id.is_(None))
            else:
                existing_q = existing_q.where(NewsFeed.instrument_id == instrument_id)

            existing = {row[0] for row in (await session.execute(existing_q)).all()}

            rows: list[dict] = []
            for it in items:
                if not it.url or it.url in existing:
                    continue
                rows.append(
                    {
                        "instrument_id": instrument_id,
                        "source": it.source,
                        "title": it.title,
                        "url": it.url,
                        "published_at": it.published_at,
                        "language": it.language,
                        # sentiment / sentiment_score -> analysis-engine sonra dolduracak
                        "sentiment": None,
                        "sentiment_score": None,
                    }
                )

            if not rows:
                return 0

            await session.execute(NewsFeed.__table__.insert(), rows)
            await session.commit()
            inserted = len(rows)

        if inserted:
            logger.info(
                "save_news: {} yeni haber (instrument_id={})", inserted, instrument_id
            )
        return inserted

    async def save_disclosures(
        self,
        disclosures: Iterable[KapDisclosure],
        ticker_to_instrument_id: dict[str, int] | None = None,
    ) -> int:
        """KAP bildirimlerini ``news_feed`` tablosuna yaz.

        KAP açıklaması semantik olarak hem haber hem yapısal veridir. Faz 2'de
        ayrı bir tablo yok; bu nedenle haber akışına şu şekilde yazılır:
            - ``source`` = ``"kap"``
            - ``title``  = bildirim başlığı
            - ``url``    = KAP bildirim linki
            - ``language`` = ``"tr"``
            - ``instrument_id`` = ``ticker_to_instrument_id`` map'ten çözülür
              (ticker bilinmiyorsa NULL -> genel piyasa bildirimi)

        Faz 3'te ``corporate_disclosures`` tablosu eklenirse buradan migrate
        edilecek. Şimdilik mapping yeterli.
        """
        items = list(disclosures)
        if not items:
            return 0

        mapping = ticker_to_instrument_id or {}
        # KapDisclosure -> NewsItem dönüşümü, instrument_id bazında gruplama
        grouped: dict[int | None, list[NewsItem]] = defaultdict(list)
        for d in items:
            instr_id = mapping.get(d.ticker.upper()) if d.ticker else None
            news = NewsItem(
                title=d.title,
                url=d.url,
                published_at=d.published_at,
                source="kap",
                language="tr",
                summary=d.subject,
            )
            grouped[instr_id].append(news)

        total = 0
        for instr_id, news_list in grouped.items():
            total += await self.save_news(news_list, instrument_id=instr_id)
        return total

    async def disable_source(self, source_name: str, reason: str) -> None:
        """Kaynağı RAM + DB'de devre dışı bırak, sebebi logla."""
        if source_name in self._inactive:
            return
        self._inactive.add(source_name)
        logger.warning("DataCollector: '{}' devre dışı — sebep: {}", source_name, reason)

        async with self._session_factory() as session:
            ds = await self._get_or_create_ds(session, source_name)
            ds.is_active = False
            await session.commit()

    async def enable_source(self, source_name: str) -> None:
        """Kaynağı tekrar aktive et (manuel müdahale veya cooldown sonrası)."""
        self._inactive.discard(source_name)
        self._consecutive_failures[source_name] = 0
        async with self._session_factory() as session:
            ds = await self._get_or_create_ds(session, source_name)
            ds.is_active = True
            await session.commit()
        logger.info("DataCollector: '{}' tekrar aktif", source_name)

    async def sync_inactive_from_db(self) -> None:
        """DB'deki ``is_active=False`` kaynakları RAM cache'ine senkronla.

        Uygulama başlangıcında çağrılır.
        """
        async with self._session_factory() as session:
            rows = (await session.execute(select(DataSource))).scalars().all()
            self._inactive = {ds.name for ds in rows if not ds.is_active}
            logger.info("DataCollector: {} pasif kaynak yüklendi", len(self._inactive))

    # ---- Internal: safe-fetch + DB sayaç güncelleme -------------------------

    async def _safe_fetch_quote(self, src: BaseSource, ticker: str) -> Quote | None:
        try:
            q = await src.fetch_quote(ticker)
            await self._record_success(src.name)
            return q
        except RateLimitError as exc:
            # Kota durumu — failure sayma, kaynağı devre dışı bırakma
            logger.info("{}: rate limit ({}); bu ticker atlandı", src.name, exc)
            await self._record_request(src.name)  # total artar, failure artmaz
            return None
        except SourceError as exc:
            await self._record_failure(src.name, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001
            await self._record_failure(src.name, f"unexpected: {exc!r}")
            return None

    async def _safe_fetch_ohlcv(
        self,
        src: BaseSource,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> list[OHLCVBar] | None:
        try:
            bars = await src.fetch_ohlcv(ticker, start, end, interval)
            await self._record_success(src.name)
            return bars
        except RateLimitError as exc:
            logger.info("{}: rate limit ({}); ohlcv atlandı", src.name, exc)
            await self._record_request(src.name)
            return None
        except SourceError as exc:
            await self._record_failure(src.name, str(exc))
            return None
        except Exception as exc:  # noqa: BLE001
            await self._record_failure(src.name, f"unexpected: {exc!r}")
            return None

    async def _record_request(self, source_name: str) -> None:
        async with self._session_factory() as session:
            ds = await self._get_or_create_ds(session, source_name)
            ds.total_requests = (ds.total_requests or 0) + 1
            await session.commit()

    async def _record_success(self, source_name: str) -> None:
        self._consecutive_failures[source_name] = 0
        async with self._session_factory() as session:
            ds = await self._get_or_create_ds(session, source_name)
            ds.total_requests = (ds.total_requests or 0) + 1
            ds.last_success = datetime.utcnow()
            # comparator.update_reliability() ek bonus uygular; collector burada
            # neutral kalır.
            await session.commit()

    async def _record_failure(self, source_name: str, reason: str) -> None:
        self._consecutive_failures[source_name] += 1
        count = self._consecutive_failures[source_name]

        logger.warning(
            "{} hata #{}: {}", source_name, count, reason
        )

        async with self._session_factory() as session:
            ds = await self._get_or_create_ds(session, source_name)
            ds.total_requests = (ds.total_requests or 0) + 1
            ds.failed_requests = (ds.failed_requests or 0) + 1
            await session.commit()

        if count >= self._failure_limit:
            await self.disable_source(
                source_name,
                f"{count} art arda hata (limit={self._failure_limit}). Son: {reason}",
            )

    async def _get_or_create_ds(self, session, name: str) -> DataSource:
        ds = (
            await session.execute(select(DataSource).where(DataSource.name == name))
        ).scalar_one_or_none()
        if ds is None:
            ds = DataSource(name=name)
            session.add(ds)
            await session.flush()
        return ds


__all__ = ["DataCollector"]
