"""Borsa Bot uygulama giriş noktası.

PySide6 tabanlı masaüstü uygulamasını başlatır:
1. ``QApplication`` örneği yaratılır ve uygulama/organizasyon adı set edilir
   (``QSettings`` bu isimleri kullanarak kalıcı tercih saklar).
2. ``ThemeManager`` örneklenir; daha önce kaydedilmiş tema/accent tercihi
   varsa yüklenir, yoksa "auto" (sistem teması) uygulanır.
3. Ana pencere oluşturulup gösterilir ve Qt event loop'una girilir.

Çalıştırma:

.. code-block:: bash

    poetry run python -m app.main
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger
from PySide6.QtWidgets import QApplication

from app.config import USER_DATA_DIR, settings
from app.ui._backend_bridge import get_bridge
from app.ui.main_window import MainWindow
from app.ui.theme_manager import ThemeManager


def _ensure_first_run_bootstrap() -> None:
    """İlk açılış: DB yoksa şema kur + temel verileri seed et.

    Installer dağıtımında uygulama klasörü read-only olabilir; her şey
    USER_DATA_DIR altında oluşturulur (zaten config.py orayı kullanıyor).
    """
    db_path = Path(settings.resolved_sqlite_path)
    if db_path.exists() and db_path.stat().st_size > 0:
        logger.info("Mevcut DB bulundu: {}", db_path)
        return

    logger.info("İlk açılış — DB kuruluyor: {}", db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # SQLite uyumluluk shim'leri (JSONB→JSON, BigInteger→INTEGER) setup_db.py'da var
    # ama burada doğrudan create_all kullanıyoruz; aynı hook'ları uygulayalım.
    from sqlalchemy import BigInteger
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles

    @compiles(JSONB, "sqlite")
    def _jsonb_sqlite(_t, _c, **_kw):  # noqa: D401
        return "JSON"

    @compiles(BigInteger, "sqlite")
    def _bigint_sqlite(_t, _c, **_kw):  # noqa: D401
        return "INTEGER"

    from app.db.models import Base
    from app.db.session import get_sync_engine

    engine = get_sync_engine()
    Base.metadata.create_all(bind=engine)
    logger.info("Şema oluşturuldu")

    # Data sources seed
    from app.db.models import DataSource
    from app.db.session import SessionLocal

    default_sources = [
        "yfinance", "stooq", "alphavantage", "isyatirim", "kap",
        "tradingview", "investing", "finviz", "tcmb", "rss", "reddit",
    ]
    with SessionLocal() as session:
        for name in default_sources:
            session.add(DataSource(name=name, reliability_score=100.0, is_active=True))
        session.commit()

    # Instruments seed
    try:
        from scripts.seed_instruments import run as seed_instruments
        seed_instruments()
        logger.info("Instruments seed tamam")
    except Exception as exc:
        logger.warning("seed_instruments başarısız: {}", exc)


def main() -> int:
    """Uygulamayı başlatır ve Qt event loop'un çıkış kodunu döndürür."""
    # PyInstaller bundle ise stderr/stdout log dosyasına yönlendir
    if getattr(sys, "frozen", False):
        log_dir = USER_DATA_DIR / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        logger.add(str(log_dir / "borsa_bot_{time:YYYY-MM-DD}.log"), rotation="10 MB", retention=10)

    try:
        _ensure_first_run_bootstrap()
    except Exception as exc:
        logger.exception("İlk açılış bootstrap başarısız: {}", exc)

    app = QApplication(sys.argv)
    app.setApplicationName("Borsa Bot")
    app.setOrganizationName("BorsaBot")
    app.setOrganizationDomain("borsabot.local")

    theme = ThemeManager(app)
    theme.apply_saved_or_default()

    window = MainWindow(theme_manager=theme)
    window.show()

    # Arka plan döngülerini başlat (fiyat çekimi, indikatör, öneri, alarm).
    # Pencere açıldıktan sonra start edilir ki ilk tick UI hazırken çalışsın.
    scheduler = None
    try:
        from app.services.scheduler import SchedulerService

        bridge = get_bridge()
        if getattr(bridge, "available", True):
            scheduler = SchedulerService(bridge, main_window=window)
            scheduler.start()
            window._scheduler = scheduler
        else:
            logger.warning("BackendBridge kullanılamıyor — scheduler başlatılmadı")
    except Exception as exc:
        logger.exception("Scheduler başlatılamadı: {}", exc)

    logger.info("Borsa Bot başlatıldı")
    rc = app.exec()
    if scheduler is not None:
        scheduler.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
