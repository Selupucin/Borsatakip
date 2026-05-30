"""Alembic env.py — modeller ``app.db.models`` üzerinden yüklenir, URL
``app.config.settings`` veya ortam değişkenlerinden çözülür.
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool


# Proje kökünü PYTHONPATH'e ekle (alembic.ini içinden de yapılıyor ama emniyet için).
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


# Modelleri yükle — ``target_metadata`` için zorunlu.
from app.db.models import Base  # noqa: E402


# Alembic Config objesi.
config = context.config


# Logging yapılandırması.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ---------------------------------------------------------------------------
# URL çözümleme: önce app.config, sonra DATABASE_URL, sonra DB_* env'leri
# ---------------------------------------------------------------------------


def _resolve_url() -> str:
    try:
        from app.config import settings  # type: ignore

        url = (
            getattr(settings, "database_url_sync", None)
            or getattr(settings, "database_url", None)
        )
        if url:
            return url.replace("+asyncpg", "+psycopg2")
    except Exception:
        pass

    env_url = os.getenv("DATABASE_URL")
    if env_url:
        return env_url.replace("+asyncpg", "+psycopg2")

    user = os.getenv("DB_USER", "postgres")
    password = os.getenv("DB_PASSWORD", "postgres")
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "borsa_bot")
    return f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{name}"


_url = _resolve_url()
config.set_main_option("sqlalchemy.url", _url)


# Hedef metadata — autogenerate için.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """SQL çıktısı üreten offline mod."""

    context.configure(
        url=_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Engine ile bağlanan online mod."""

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
