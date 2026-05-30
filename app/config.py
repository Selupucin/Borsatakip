"""Uygulama genel ayarları.

`.env` dosyasından okunan tüm ortam değişkenleri burada type-safe biçimde
pydantic-settings ile expose edilir. Uygulama içinde `from app.config import settings`
ile erişilir; `settings` modül seviyesinde singleton'dur.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _user_data_dir() -> Path:
    """Kullanıcı verisinin kalıcı dizini.

    Windows: %LOCALAPPDATA%\\BorsaBot
    macOS:   ~/Library/Application Support/BorsaBot
    Linux:   ~/.local/share/BorsaBot

    Dizin yoksa yaratılır. Installer ile dağıtımda DB ve .env burada saklanır;
    uygulama klasörü read-only olabilir.
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    path = base / "BorsaBot"
    path.mkdir(parents=True, exist_ok=True)
    return path


USER_DATA_DIR: Path = _user_data_dir()


def _resolve_env_file() -> str:
    """`.env` dosyasını bul: 1) cwd 2) user data dir 3) None (default'lar)."""
    cwd_env = Path(".env")
    if cwd_env.exists():
        return str(cwd_env)
    user_env = USER_DATA_DIR / ".env"
    if user_env.exists():
        return str(user_env)
    return str(cwd_env)  # pydantic-settings bunu None olarak ele alabilsin


class Settings(BaseSettings):
    """Tüm ortam değişkenlerini tek bir type-safe nesnede toplar."""

    # -------------------------------------------------------------------------
    # PostgreSQL
    # -------------------------------------------------------------------------
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "borsa_bot"
    db_user: str = "postgres"
    db_password: str = ""

    # -------------------------------------------------------------------------
    # API anahtarları
    # -------------------------------------------------------------------------
    alpha_vantage_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "BorsaBot/1.0"

    # -------------------------------------------------------------------------
    # Uygulama davranış ayarları
    # -------------------------------------------------------------------------
    data_refresh_interval: int = 60
    discrepancy_threshold_pct: float = 0.5
    alert_discrepancy_pct: float = 2.0
    source_failure_limit: int = 5

    # -------------------------------------------------------------------------
    # İşlem ve otomasyon
    # -------------------------------------------------------------------------
    trading_mode: str = "manual_parallel"  # manual_parallel | semi_auto | full_auto | paper
    risk_threshold: float = 50.0
    daily_trade_limit: int = 10
    max_drawdown_pct: float = 15.0
    default_commission_pct: float = 0.2

    # -------------------------------------------------------------------------
    # Aracı kurum (Faz 4)
    # -------------------------------------------------------------------------
    broker_name: str = ""
    broker_api_key: str = ""
    broker_api_secret: str = ""

    # -------------------------------------------------------------------------
    # Güvenlik
    # -------------------------------------------------------------------------
    encryption_enabled: bool = True

    # -------------------------------------------------------------------------
    # NLP modelleri
    # -------------------------------------------------------------------------
    nlp_model_tr: str = "savasy/bert-base-turkish-sentiment-cased"
    nlp_model_en: str = "ProsusAI/finbert"

    # -------------------------------------------------------------------------
    # Dev/SQLite fallback
    # -------------------------------------------------------------------------
    # USE_SQLITE=true ile PostgreSQL yerine local SQLite dosyası kullanılır.
    # Faz 1-3 development sırasında PostgreSQL kurulu değilse uygulama çalışsın diye.
    use_sqlite: bool = True
    sqlite_path: str = ""  # boşsa USER_DATA_DIR/borsa_bot.db kullanılır

    # -------------------------------------------------------------------------
    # Auto-update (GitHub Releases)
    # -------------------------------------------------------------------------
    # Boş bırakırsanız app/__version__.py içindeki __repo__'dan üretilir:
    # https://api.github.com/repos/Selupucin/Borsatakip/releases/latest
    update_check_url: str = ""
    update_check_interval_hours: float = 6.0
    update_auto_check: bool = True

    # -------------------------------------------------------------------------
    # Türetilmiş alanlar
    # -------------------------------------------------------------------------
    @property
    def resolved_sqlite_path(self) -> str:
        """SQLite path: env override > USER_DATA_DIR/borsa_bot.db default."""
        if self.sqlite_path:
            return self.sqlite_path
        return str(USER_DATA_DIR / "borsa_bot.db")

    @property
    def data_dir(self) -> Path:
        """Kullanıcı veri dizini (DB + logs + cache)."""
        return USER_DATA_DIR

    @property
    def db_url_sync(self) -> str:
        if self.use_sqlite:
            # Windows path için sqlite:///ABS_PATH (3 slash, sonra abs path)
            return f"sqlite:///{self.resolved_sqlite_path}"
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def db_url_async(self) -> str:
        if self.use_sqlite:
            return f"sqlite+aiosqlite:///{self.resolved_sqlite_path}"
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    model_config = SettingsConfigDict(
        env_file=_resolve_env_file(),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Modül seviyesinde singleton — tüm uygulama bunu import eder.
settings = Settings()
