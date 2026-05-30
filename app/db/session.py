"""PostgreSQL bağlantı yönetimi (sync + async).

- Sync engine: ``psycopg2`` sürücüsü, FastAPI / script kullanım için ``get_db()``.
- Async engine: ``asyncpg`` sürücüsü, ``data-collector`` ve UI background
  görevleri için ``get_async_db()``.
- Bağlantı havuzu doküman §3.3 ve database-architect kurallarına göre:
  ``pool_size=10``, ``max_overflow=20``, ``pool_pre_ping=True``,
  ``pool_recycle=3600``.

``app.config`` modülü devops-engineer tarafından paralel olarak yazılıyor;
henüz hazır değilse ortam değişkenlerinden okuyoruz (sessiz fallback).
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import Base


# ---------------------------------------------------------------------------
# Bağlantı URL'i — config varsa kullan, yoksa env'den oku
# ---------------------------------------------------------------------------


def _build_url_from_env(driver: str) -> str:
    """``postgresql+<driver>://user:pass@host:port/db`` URL'i kur."""

    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "borsa_bot")
    return f"postgresql+{driver}://{user}:{password}@{host}:{port}/{name}"


def _resolve_urls() -> tuple[str, str]:
    """Sync ve async URL'leri çöz. ``app.config`` varsa öncelikli; yoksa env."""

    sync_url: str | None = None
    async_url: str | None = None

    try:
        from app.config import settings  # type: ignore

        # Settings'deki gerçek property isimleri: db_url_sync / db_url_async
        sync_url = (
            getattr(settings, "db_url_sync", None)
            or getattr(settings, "database_url_sync", None)
            or getattr(settings, "database_url", None)
        )
        async_url = (
            getattr(settings, "db_url_async", None)
            or getattr(settings, "database_url_async", None)
        )
    except Exception:
        # Config henüz yazılmamış veya yüklenemedi — env fallback.
        pass

    if not sync_url:
        sync_url = os.getenv("DATABASE_URL") or _build_url_from_env("psycopg2")
    if not async_url:
        async_url = os.getenv("DATABASE_URL_ASYNC") or _build_url_from_env("asyncpg")

    # Eğer config sadece tek URL verdiyse (örn. psycopg2 schema'sı), async tarafı
    # için sürücüyü değiştir.
    if sync_url and "+asyncpg" in sync_url:
        sync_url = sync_url.replace("+asyncpg", "+psycopg2")
    if async_url and "+psycopg2" in async_url:
        async_url = async_url.replace("+psycopg2", "+asyncpg")
    if async_url and "+asyncpg" not in async_url and async_url.startswith("postgresql://"):
        async_url = async_url.replace("postgresql://", "postgresql+asyncpg://", 1)

    return sync_url, async_url


# ---------------------------------------------------------------------------
# Engine'ler — LAZY (modül import-time'da kurulmazlar)
# ---------------------------------------------------------------------------
#
# Sync ve async engine'ler ilk get_*() / SessionLocal çağrısında üretilir.
# Sebep: testler psycopg2/asyncpg kurulu olmadan modülleri import edebilsin.

_POOL_KW = dict(
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    pool_recycle=3600,
)


_sync_engine: Engine | None = None
_async_engine: AsyncEngine | None = None
_SessionLocal: sessionmaker[Session] | None = None
_AsyncSessionLocal: async_sessionmaker[AsyncSession] | None = None


def get_sync_engine() -> Engine:
    """Sync engine — lazy. İlk çağrıda yaratılır."""
    global _sync_engine
    if _sync_engine is None:
        sync_url, _ = _resolve_urls()
        _sync_engine = create_engine(sync_url, echo=False, future=True, **_POOL_KW)
    return _sync_engine


def get_async_engine() -> AsyncEngine:
    """Async engine — lazy. İlk çağrıda yaratılır."""
    global _async_engine
    if _async_engine is None:
        _, async_url = _resolve_urls()
        _async_engine = create_async_engine(async_url, echo=False, future=True, **_POOL_KW)
    return _async_engine


def _get_session_local() -> sessionmaker[Session]:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            bind=get_sync_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _SessionLocal


def _get_async_session_local() -> async_sessionmaker[AsyncSession]:
    global _AsyncSessionLocal
    if _AsyncSessionLocal is None:
        _AsyncSessionLocal = async_sessionmaker(
            bind=get_async_engine(),
            autoflush=False,
            expire_on_commit=False,
            class_=AsyncSession,
        )
    return _AsyncSessionLocal


def reset_engines() -> None:
    """Test izolasyonu için engine cache'ini temizle."""
    global _sync_engine, _async_engine, _SessionLocal, _AsyncSessionLocal
    _sync_engine = None
    _async_engine = None
    _SessionLocal = None
    _AsyncSessionLocal = None


class _LazyProxy:
    """``SessionLocal()`` çağrısını lazy session factory'e yönlendiren proxy."""

    def __init__(self, getter):
        self._getter = getter

    def __call__(self, *args, **kwargs):
        return self._getter()(*args, **kwargs)

    def __getattr__(self, item):
        return getattr(self._getter(), item)


SessionLocal = _LazyProxy(_get_session_local)
AsyncSessionLocal = _LazyProxy(_get_async_session_local)


# ---------------------------------------------------------------------------
# Dependency-style yardımcılar
# ---------------------------------------------------------------------------


def get_db() -> Generator[Session, None, None]:
    """Sync session üretici. FastAPI / CLI script'leri için ``Depends``."""

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


async def get_async_db() -> AsyncGenerator[AsyncSession, None]:
    """Async session üretici. async/await context'lerinde kullan."""

    async with AsyncSessionLocal() as session:
        yield session


# ---------------------------------------------------------------------------
# Alembic alternatifi: doğrudan metadata'dan şema kur
# ---------------------------------------------------------------------------


def init_db() -> None:
    """Tüm tabloları yaratır (geliştirme / test için hızlı yol).

    Üretimde Alembic migration kullanılmalıdır.
    """

    Base.metadata.create_all(bind=get_sync_engine())


__all__ = [
    "get_sync_engine",
    "get_async_engine",
    "SessionLocal",
    "AsyncSessionLocal",
    "get_db",
    "get_async_db",
    "init_db",
    "reset_engines",
]
