"""DB şema kurulum + temel kaynak seed scripti.

Kullanım
--------
::

    python scripts/setup_db.py             # Şemayı oluştur, data_sources seed et
    python scripts/setup_db.py --drop      # Önce tüm tabloları drop et, sonra kur
    python scripts/setup_db.py --seed      # Ek olarak seed_instruments.py de çalıştır
    python scripts/setup_db.py --drop --seed

Notlar:
- Şema kurulum yöntemi: önce Alembic ``upgrade head`` denenir, başarısız
  olursa ``Base.metadata.create_all`` ile fallback yapılır.
- ``data_sources`` seed işlemi idempotent'tir (``ON CONFLICT DO NOTHING``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Proje kökünü PYTHONPATH'e ekle.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db.models import Base, DataSource
from app.db.session import SessionLocal, get_sync_engine

# SQLite uyumluluğu: JSONB → JSON, BigInteger → INTEGER (autoincrement PK için)
from sqlalchemy import BigInteger
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles


@compiles(JSONB, "sqlite")
def _compile_jsonb_sqlite(_type, _compiler, **_kw):  # noqa: D401
    return "JSON"


@compiles(BigInteger, "sqlite")
def _compile_bigint_sqlite(_type, _compiler, **_kw):  # noqa: D401
    return "INTEGER"


# Doküman §3.4 + §9'da geçen tüm veri kaynakları.
# Reliability_score 100.0; gerçek değerler data-collector tarafından güncellenir.
DEFAULT_DATA_SOURCES: list[dict] = [
    {"name": "yfinance", "reliability_score": 100.0},
    {"name": "stooq", "reliability_score": 100.0},
    {"name": "alphavantage", "reliability_score": 100.0},
    {"name": "isyatirim", "reliability_score": 100.0},
    {"name": "kap", "reliability_score": 100.0},
    {"name": "tradingview", "reliability_score": 100.0},
    {"name": "investing", "reliability_score": 100.0},
    {"name": "finviz", "reliability_score": 100.0},
    {"name": "tcmb", "reliability_score": 100.0},
    {"name": "rss", "reliability_score": 100.0},
    {"name": "reddit", "reliability_score": 100.0},
]


# ---------------------------------------------------------------------------
# Şema kurulum
# ---------------------------------------------------------------------------


def drop_all() -> None:
    """Tüm tabloları drop et — DİKKAT: geri alınamaz."""

    print("[setup_db] Tüm tablolar drop ediliyor...")
    Base.metadata.drop_all(bind=get_sync_engine())
    # Alembic version tablosunu da temizle ki bir sonraki upgrade temiz başlasın.
    try:
        with get_sync_engine().begin() as conn:
            conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    except SQLAlchemyError as exc:
        print(f"[setup_db] alembic_version drop edilemedi (önemsiz): {exc}")
    print("[setup_db] Drop tamam.")


def create_schema() -> None:
    """Şemayı kur: PostgreSQL'de Alembic; SQLite'da doğrudan create_all."""

    print("[setup_db] Şema kuruluyor...")

    # SQLite dev modu için Alembic atla; env.py PostgreSQL'e hard-coded bağlanır.
    from app.config import settings
    if settings.use_sqlite:
        print("[setup_db] SQLite modu — Alembic atlandı, create_all kullanılıyor.")
        Base.metadata.create_all(bind=get_sync_engine())
        print("[setup_db] create_all tamam.")
        return

    try:
        from alembic import command  # type: ignore
        from alembic.config import Config  # type: ignore

        cfg_path = _PROJECT_ROOT / "alembic.ini"
        if cfg_path.exists():
            cfg = Config(str(cfg_path))
            command.upgrade(cfg, "head")
            print("[setup_db] Alembic upgrade head tamam.")
            return
        print("[setup_db] alembic.ini bulunamadı; create_all fallback.")
    except ImportError:
        print("[setup_db] alembic yok; create_all fallback.")
    except Exception as exc:  # pragma: no cover - migration runtime hatası
        print(f"[setup_db] Alembic başarısız ({exc}); create_all fallback.")

    Base.metadata.create_all(bind=get_sync_engine())
    print("[setup_db] create_all tamam.")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def seed_data_sources() -> int:
    """``data_sources`` tablosuna varsayılan kaynakları ekle (idempotent).

    Returns
    -------
    int
        Yeni eklenen satır sayısı.
    """

    added = 0
    with SessionLocal() as session:
        existing = {
            row[0]
            for row in session.execute(select(DataSource.name)).all()
        }
        for src in DEFAULT_DATA_SOURCES:
            if src["name"] in existing:
                continue
            session.add(
                DataSource(
                    name=src["name"],
                    reliability_score=src["reliability_score"],
                    is_active=True,
                )
            )
            added += 1
        session.commit()
    print(f"[setup_db] data_sources seed: {added} yeni satır eklendi.")
    return added


def seed_instruments() -> None:
    """``scripts/seed_instruments.py`` modülünü çağır."""

    from scripts import seed_instruments as seeder

    seeder.run()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Borsa Bot DB kurulum scripti")
    p.add_argument(
        "--drop",
        action="store_true",
        help="Önce tüm tabloları drop et (geri alınamaz)",
    )
    p.add_argument(
        "--seed",
        action="store_true",
        help="Ek olarak temel hisse listesini de yükle",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()

    if args.drop:
        drop_all()

    create_schema()
    seed_data_sources()

    if args.seed:
        seed_instruments()

    print("[setup_db] Tamam.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
