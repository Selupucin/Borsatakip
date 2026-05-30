"""Reddit veri kaynağı (PRAW — resmi API üzerinden subreddit postları).

# ============================================================
# KAYNAK: reddit (praw)
# YASAL:  Reddit resmi API kullanımı geliştirici hesap + uygulama
#         kaydı gerektirir; ToS gereği kullanıcı verisi anonim
#         kalmalı, yeniden satılmamalı, GDPR/KVKK uyumlu işlenmelidir.
#         Read-only script application yeterlidir; OAuth flow burada
#         kullanılmaz (script-mode access token).
# RATE:   Reddit API kuralı: kimliklenmiş istek için 100 req/dk.
#         Praw default oran sınırlayıcıyı içerir — bu modül ayrıca
#         saniyede en fazla 1 istek (kibarlık) uygular.
# robots.txt: Resmi API üzerinden gittiğimiz için robots.txt kapsamı
#             dışındayız (API ayrı bir sözleşme).
# ============================================================

Çevre değişkenleri (.env):
    REDDIT_CLIENT_ID
    REDDIT_CLIENT_SECRET
    REDDIT_USER_AGENT

Bu üçünden biri eksikse modül ``SourceUnavailableError("Reddit credentials yok")``
fırlatır — collector kaynağı pasif tutar.

``BaseSource`` sözleşmesinde quote/ohlcv anlamsız; ``SourceError`` ile reddedilir.

Public API:
    ``fetch_recent_posts(subreddit, ticker=None, limit=50)`` -> list[RedditPost]
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from app.config import settings
from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    SourceError,
    SourceUnavailableError,
)


try:  # pragma: no cover - import guard
    import praw  # type: ignore
    PRAW_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    praw = None  # type: ignore
    PRAW_AVAILABLE = False
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RedditPost:
    """Tek bir subreddit gönderisi (üst-seviye post; yorumlar ayrı çekilir)."""

    title: str
    body: str  # selftext (link postlarda boş)
    score: int  # net upvotes
    num_comments: int
    url: str  # reddit permalink
    created_at: datetime  # UTC
    subreddit: str
    ticker: Optional[str] = None  # filtre ile gelen post için ipucu


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class RedditSource(BaseSource):
    name = "reddit"

    _MIN_INTERVAL_S = 1.0
    _MAX_RETRY = 2
    _BACKOFF_BASE = 2.0
    _semaphore = asyncio.Semaphore(1)

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        self._client = None  # lazy

        if not PRAW_AVAILABLE:
            logger.warning(
                "praw import edilemedi ({}). RedditSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )

        # Credential check
        self._has_creds = bool(
            settings.reddit_client_id
            and settings.reddit_client_secret
            and settings.reddit_user_agent
        )
        if not self._has_creds:
            logger.warning("reddit: credentials eksik — kaynak pasif.")

    # ---- BaseSource: fiyat reddedilir --------------------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        raise SourceError("reddit: fiyat verisi sağlamaz")

    async def fetch_quote(self, ticker: str) -> Quote:
        raise SourceError("reddit: quote sağlamaz")

    async def is_alive(self) -> bool:
        if not PRAW_AVAILABLE or not self._has_creds:
            return False
        try:
            posts = await self.fetch_recent_posts(subreddit="wallstreetbets", limit=1)
            return True if posts is not None else False
        except Exception:
            return False

    # ---- Public: posts ------------------------------------------------------

    async def fetch_recent_posts(
        self,
        subreddit: str = "wallstreetbets",
        ticker: str | None = None,
        limit: int = 50,
    ) -> list[RedditPost]:
        """Son ``limit`` adet subreddit gönderisini çek.

        ``ticker`` verilirse client-side filtre uygulanır (title/body içinde
        case-insensitive sembol arar — örn. ``$TSLA`` veya ``TSLA``).
        Praw'ın subreddit.search() de kullanılabilir ama gürültülü olabilir.
        """
        if not PRAW_AVAILABLE:
            raise SourceUnavailableError("praw kurulu değil")
        if not self._has_creds:
            raise SourceUnavailableError("Reddit credentials yok")

        client = self._ensure_client()

        posts = await self._call(self._fetch_new, client, subreddit, limit)
        if ticker:
            t = ticker.strip().upper()
            posts = [
                p for p in posts
                if t in p.title.upper() or t in (p.body or "").upper()
            ]
            # Ticker bilgisini ekle
            posts = [
                RedditPost(
                    title=p.title, body=p.body, score=p.score,
                    num_comments=p.num_comments, url=p.url,
                    created_at=p.created_at, subreddit=p.subreddit,
                    ticker=t,
                )
                for p in posts
            ]
        return posts

    # ---- Internal ----------------------------------------------------------

    def _ensure_client(self):  # pragma: no cover - external IO
        if self._client is None:
            self._client = praw.Reddit(
                client_id=settings.reddit_client_id,
                client_secret=settings.reddit_client_secret,
                user_agent=settings.reddit_user_agent,
                check_for_async=False,
            )
            self._client.read_only = True
        return self._client

    @staticmethod
    def _fetch_new(client, subreddit: str, limit: int) -> list[RedditPost]:  # pragma: no cover - external IO
        sub = client.subreddit(subreddit)
        out: list[RedditPost] = []
        for submission in sub.new(limit=limit):
            try:
                ts = datetime.fromtimestamp(
                    float(submission.created_utc), tz=timezone.utc
                )
            except Exception:
                ts = datetime.now(tz=timezone.utc)
            out.append(
                RedditPost(
                    title=submission.title or "",
                    body=getattr(submission, "selftext", "") or "",
                    score=int(getattr(submission, "score", 0) or 0),
                    num_comments=int(getattr(submission, "num_comments", 0) or 0),
                    url=f"https://www.reddit.com{submission.permalink}",
                    created_at=ts,
                    subreddit=subreddit,
                )
            )
        return out

    async def _call(self, sync_fn, *args):
        """Senkron praw çağrısını semafor + min-interval + retry ile sar."""
        last_exc: Exception | None = None
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)

            for attempt in range(self._MAX_RETRY):
                try:
                    result = await asyncio.to_thread(sync_fn, *args)
                    self._last_call_ts = asyncio.get_event_loop().time()
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "reddit deneme {}/{} başarısız: {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    self._last_call_ts = asyncio.get_event_loop().time()
        raise SourceUnavailableError(f"reddit retry tükendi: {last_exc}")


__all__ = ["RedditSource", "RedditPost"]
