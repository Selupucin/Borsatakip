"""``app.config.Settings`` birim testleri.

Doğrulanan davranışlar:
1. Settings varsayılan değerleriyle yüklenir (env yokken).
2. ``.env`` dosyası yokken Settings çökmez.
3. ``db_url_sync`` ve ``db_url_async`` doğru driver prefix'i ile üretir.
"""

from __future__ import annotations

import importlib

import pytest


def test_settings_default_values_load_without_env():
    """Settings hiçbir env değişkeni olmadan default'larla yüklenir."""
    from app.config import Settings

    s = Settings(_env_file=None)  # .env okumayı zorla devre dışı bırak

    assert s.db_host == "localhost"
    assert s.db_port == 5432
    assert s.db_name == "borsa_bot"
    assert s.db_user == "postgres"
    assert s.discrepancy_threshold_pct == 0.5
    assert s.alert_discrepancy_pct == 2.0
    assert s.source_failure_limit == 5
    assert s.trading_mode == "manual_parallel"
    assert s.risk_threshold == 50.0


def test_settings_no_env_file_does_not_crash(monkeypatch, tmp_path):
    """``.env`` yokken Settings sessizce default'lara düşer, exception YOK."""
    # cwd'yi geçici bir dizine taşı ki .env aranamasın
    monkeypatch.chdir(tmp_path)
    from app.config import Settings

    # Açıkça _env_file=None vererek arama yapılmamasını da garantiliyoruz.
    s = Settings(_env_file=None)
    assert s.db_user == "postgres"  # default


def test_db_url_sync_uses_psycopg2_driver():
    """``db_url_sync`` ``postgresql+psycopg2://...`` formatında olmalı."""
    from app.config import Settings

    s = Settings(
        _env_file=None,
        use_sqlite=False,
        db_user="alice",
        db_password="secret",
        db_host="myhost",
        db_port=5433,
        db_name="bb_test",
    )
    url = s.db_url_sync
    assert url == "postgresql+psycopg2://alice:secret@myhost:5433/bb_test"


def test_db_url_async_uses_asyncpg_driver():
    """``db_url_async`` ``postgresql+asyncpg://...`` formatında olmalı."""
    from app.config import Settings

    s = Settings(
        _env_file=None,
        use_sqlite=False,
        db_user="bob",
        db_password="pw",
        db_host="dbhost",
        db_port=5432,
        db_name="bb_async",
    )
    url = s.db_url_async
    assert url == "postgresql+asyncpg://bob:pw@dbhost:5432/bb_async"


def test_db_url_async_and_sync_share_same_host_db():
    """Aynı Settings instance için sync/async URL'ler aynı host/db'yi kullanır."""
    from app.config import Settings

    s = Settings(_env_file=None, use_sqlite=False)
    assert s.db_url_sync.endswith(f"@{s.db_host}:{s.db_port}/{s.db_name}")
    assert s.db_url_async.endswith(f"@{s.db_host}:{s.db_port}/{s.db_name}")
    # Driver farkı:
    assert "+psycopg2" in s.db_url_sync
    assert "+asyncpg" in s.db_url_async


def test_settings_singleton_importable():
    """Modül singleton'u (``settings``) import edilebilir ve Settings tipindedir."""
    from app.config import Settings, settings

    assert isinstance(settings, Settings)


def test_settings_env_override(monkeypatch):
    """Ortam değişkenleri okunabilir (case-insensitive)."""
    monkeypatch.setenv("DB_USER", "envuser")
    monkeypatch.setenv("DB_PORT", "9999")
    from app.config import Settings

    s = Settings(_env_file=None)
    assert s.db_user == "envuser"
    assert s.db_port == 9999
