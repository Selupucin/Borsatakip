"""Faz 1 birim testleri için ortak pytest fixture'ları.

Tasarım kuralları:
- In-memory SQLite kullanılır (PostgreSQL'e bağımlılık yok). PostgreSQL'e
  özgü ``JSONB``, ``INSERT ... ON CONFLICT``, ``Numeric`` precision check'leri
  SQLite üzerinde de çalışacak şekilde uyarlandı.
- Her test başında ``Base.metadata.create_all`` ile temiz şema kurulur,
  test sonunda tüm transaction rollback edilir.
- Async + sync iki ayrı engine sunulur — ``DataCollector`` ve ``PriceComparator``
  async sessionmaker bekler, ``AlertEngine`` sync session.
- TA-Lib / yfinance / PySide6 gibi ağır bağımlılıklar import EDİLMEZ —
  testler bunları mock'lar.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator, Iterator
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

# JSONB SQLite'ta yok; testler için JSON ile değiştir.
# SQLAlchemy'nin sqlite dialect'i JSONB için type compiler tanımlamadığı
# sürece "type_compiler can't render JSONB" hatası verir. Aşağıdaki compiles
# kaydı, SQLite üzerinde JSONB'yi ham JSON metin tipi olarak emit eder.
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy import BigInteger


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):  # noqa: D401
    return "JSON"


# SQLite'ta BIGINT primary key autoincrement çalışmaz (sadece INTEGER PRIMARY KEY
# rowid alias'tır). Test ortamında BigInteger'ı INTEGER'a derle ki PK üretsin.
@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(_type, _compiler, **_kw):  # noqa: D401
    return "INTEGER"


from app.data.base_source import BaseSource, Quote, OHLCVBar  # noqa: E402
from app.db.models import Base, DataSource  # noqa: E402


# ---------------------------------------------------------------------------
# SQLite FK + CHECK enforce
# ---------------------------------------------------------------------------


@event.listens_for(Engine, "connect")
def _enable_sqlite_fk(dbapi_connection, connection_record):  # noqa: ARG001
    """SQLite üzerinde FK ve CHECK kısıtlarını zorla."""
    try:
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON;")
        cur.close()
    except Exception:  # noqa: BLE001
        # asyncpg / psycopg gibi PostgreSQL sürücülerinde no-op
        pass


# ---------------------------------------------------------------------------
# Engine + session fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sync_engine() -> Iterator[Engine]:
    """SQLite in-memory sync engine (test başı temiz şema)."""
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def db_session(sync_engine: Engine) -> Iterator[Session]:
    """Test başı temiz, sonunda rollback edilen sync Session."""
    SessionLocal = sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False, class_=Session
    )
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest_asyncio.fixture
async def async_db_session() -> AsyncIterator[AsyncSession]:
    """Async in-memory SQLite — Faz 1 collector/comparator için."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    SessionFactory = async_sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
    )
    session = SessionFactory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def async_session_factory():
    """Async sessionmaker döndürür — DataCollector/PriceComparator için.

    Engine fixture'ın yaşam süresi test boyuncadır; collector kendi
    session'larını her çağrıda açar.
    """
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, class_=AsyncSession
    )
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


# ---------------------------------------------------------------------------
# OHLCV / Quote sample data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """100 satır deterministic OHLCV DataFrame (numpy seed=42)."""
    np.random.seed(42)
    n = 100
    # Sinüs + noise close serisi — RSI/MACD'nin bütün lookback'leri için yeterli
    t = np.arange(n)
    base = 100.0 + 5.0 * np.sin(t / 10.0)
    noise = np.random.normal(0, 0.5, n)
    close = base + noise
    open_ = close + np.random.normal(0, 0.3, n)
    high = np.maximum(open_, close) + np.abs(np.random.normal(0, 0.4, n))
    low = np.minimum(open_, close) - np.abs(np.random.normal(0, 0.4, n))
    volume = np.random.randint(1_000, 10_000, n)

    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


@pytest.fixture
def mock_quote():
    """Sabit ``Quote`` üretici factory."""

    def _make(
        ticker: str = "AAPL",
        price: str | Decimal = "150.00",
        source: str = "yfinance",
        ts: datetime | None = None,
        volume: int | None = 1000,
    ) -> Quote:
        return Quote(
            ticker=ticker,
            price=Decimal(str(price)),
            timestamp=ts or datetime(2024, 1, 15, 16, 0, tzinfo=timezone.utc),
            source=source,
            volume=volume,
        )

    return _make


# ---------------------------------------------------------------------------
# Mock BaseSource factory
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_source():
    """Konfigüre edilebilir mock BaseSource üretici factory."""

    def _make(name: str = "mock"):
        # Her çağrıda farklı sınıf üretirsek class attribute side-effect olmaz.
        cls_name = f"_Mock_{name}_{id(name)}"
        new_cls = type(cls_name, (BaseSource,), {"name": name})

        async def _fetch_ohlcv(self, ticker, start, end, interval="1d"):
            return []

        async def _fetch_quote(self, ticker):
            raise NotImplementedError

        new_cls.fetch_ohlcv = _fetch_ohlcv  # type: ignore[attr-defined]
        new_cls.fetch_quote = _fetch_quote  # type: ignore[attr-defined]
        instance = new_cls()
        # AsyncMock'larla override et — testler davranışı belirlesin
        instance.fetch_quote = AsyncMock()  # type: ignore[method-assign]
        instance.fetch_ohlcv = AsyncMock()  # type: ignore[method-assign]
        return instance

    return _make


# ---------------------------------------------------------------------------
# DB seed helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def mock_data_sources_table(async_session_factory):
    """3 DataSource satırı insert et — collector/comparator testleri için."""
    async with async_session_factory() as session:
        rows = [
            DataSource(name="yfinance", reliability_score=100.0),
            DataSource(name="stooq", reliability_score=95.0),
            DataSource(name="isyatirim", reliability_score=90.0),
        ]
        session.add_all(rows)
        await session.commit()
    return ["yfinance", "stooq", "isyatirim"]


# ---------------------------------------------------------------------------
# Settings mock
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_settings(monkeypatch):
    """``app.config.settings`` üzerinde test ayarları override eder.

    pytest test runs arasında singleton mutation'ı önlemek için key/value
    bazında monkeypatch.setattr() kullanılır.
    """
    from app import config as _cfg

    overrides = {
        "discrepancy_threshold_pct": 0.5,
        "alert_discrepancy_pct": 2.0,
        "source_failure_limit": 5,
        "trading_mode": "paper",
        "alpha_vantage_api_key": "TEST_KEY",
    }
    for k, v in overrides.items():
        monkeypatch.setattr(_cfg.settings, k, v, raising=False)
    return _cfg.settings


# ---------------------------------------------------------------------------
# Event loop policy — pytest-asyncio auto modu için
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy():
    """pytest-asyncio default loop policy. Windows ProactorEventLoop sorunlarını
    önler."""
    return asyncio.DefaultEventLoopPolicy()
