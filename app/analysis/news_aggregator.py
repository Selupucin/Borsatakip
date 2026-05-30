"""Haber sentiment toplayıcı — bir hisse için son N gündeki ortalama.

Doküman §5.1 (Sentiment skoru girdisi) için yardımcı modül. Bir hisse
hakkında ``news_feed`` tablosunda bulunan ve sentiment'i hesaplanmış
satırların ortalama skorunu döndürür; öneri motoruna girdi olur.

Tasarım kuralları:
- ``session_factory`` callable; her çağrıda yeni ``Session`` dönmeli
  (``sessionmaker(bind=engine)`` veya context-manager fabrikası).
- ``aggregate_sentiment`` **async** API — collector / öneri motoru async
  pipeline'ında doğal kullanılır. Şu anki DB erişimi sync SQLAlchemy
  session ile yapılır (modeller sync); async sarmalayıcı Faz 3'te
  ``asyncio.to_thread`` ile değiştirilebilir.
- ``sentiment_score`` ``NULL`` olan satırlar (henüz işlenmemiş haberler)
  ortalama dışı bırakılır.
- ``language`` filtresi opsiyonel — gelecekteki "yalnız TR" / "yalnız EN"
  agregasyonu için.
- Hiç eşleşen satır yoksa ``(None, 0)`` döner; caller bunu "bilgi yok"
  olarak yorumlar.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, func, select


class NewsAggregator:
    """Hisse bazlı haber sentiment agregasyonu.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
        Test ortamında ``sessionmaker(bind=in_memory_sqlite)`` ya da
        bağlam yöneticisi (``contextmanager``) sağlayan bir fabrika
        geçilebilir.
    """

    def __init__(self, session_factory: Callable[[], object]) -> None:
        self.session_factory = session_factory

    # ----------------------------------------------------------- public API

    async def aggregate_sentiment(
        self,
        instrument_id: int,
        lookback_days: int = 7,
        language: Optional[str] = None,
    ) -> tuple[Optional[float], int]:
        """Son ``lookback_days`` gün içindeki haberlerin sentiment ortalaması.

        Returns
        -------
        tuple
            ``(mean_score, count)`` — ``mean_score`` ``[-1, +1]`` aralığında
            float; ``count`` ortalamaya katkı yapan satır sayısı. Hiç satır
            yoksa ``(None, 0)``.
        """

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=lookback_days)
        # İçe aktarmayı geciktiriyoruz — modül app.db olmadan da yüklensin.
        from app.db.models import NewsFeed  # noqa: WPS433

        conditions = [
            NewsFeed.instrument_id == instrument_id,
            NewsFeed.sentiment_score.isnot(None),
            NewsFeed.published_at >= cutoff,
        ]
        if language:
            conditions.append(NewsFeed.language == language)

        session = self.session_factory()
        try:
            stmt = select(
                func.avg(NewsFeed.sentiment_score),
                func.count(NewsFeed.id),
            ).where(and_(*conditions))
            mean, count = session.execute(stmt).one()
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                close()

        count_int = int(count or 0)
        if count_int == 0 or mean is None:
            logger.debug(
                "aggregate_sentiment: instrument_id={} için son {} günde "
                "skorlu haber bulunamadı.",
                instrument_id,
                lookback_days,
            )
            return None, 0

        mean_float = float(mean)
        # Numeric kolon [-1, +1] olmalı ama savunmacı clamp.
        mean_float = max(-1.0, min(1.0, mean_float))
        return mean_float, count_int

    async def aggregate_by_language(
        self, instrument_id: int, lookback_days: int = 7
    ) -> dict[str, tuple[Optional[float], int]]:
        """TR ve EN ortalamalarını ayrı ayrı döndürür.

        UI'da "TR sentiment +0.40 (5 haber) / EN sentiment +0.15 (8 haber)"
        gibi kırılım göstermek için.
        """

        tr = await self.aggregate_sentiment(
            instrument_id, lookback_days=lookback_days, language="tr"
        )
        en = await self.aggregate_sentiment(
            instrument_id, lookback_days=lookback_days, language="en"
        )
        return {"tr": tr, "en": en}


__all__ = ["NewsAggregator"]
