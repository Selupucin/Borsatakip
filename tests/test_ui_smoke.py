"""UI modüllerinin import-smoke testleri.

Qt event loop oluşturulmaz, widget instance üretilmez — yalnızca modül
import edilebilir mi, sınıflar attribute olarak erişilebilir mi kontrol
edilir. PySide6 kurulu değilse modül seviyesinde ``importorskip`` ile
tüm testler skip edilir.
"""

from __future__ import annotations

import pytest

# PySide6 yoksa tüm testler skip — modül import'u burada gerçekleşir.
pytest.importorskip("PySide6")


# ---------------------------------------------------------------------------
# Modül seviyesi import testleri
# ---------------------------------------------------------------------------


def test_news_widget_import():
    from app.ui import news_widget

    assert hasattr(news_widget, "NewsWidget")


def test_watchlist_widget_import():
    from app.ui import watchlist_widget

    assert hasattr(watchlist_widget, "WatchlistWidget")


def test_bot_picks_widget_import():
    from app.ui import bot_picks_widget

    assert hasattr(bot_picks_widget, "BotPicksWidget")


def test_recommendation_widget_import():
    from app.ui import recommendation_widget

    assert hasattr(recommendation_widget, "RecommendationWidget")


def test_main_window_import():
    from app.ui import main_window

    assert hasattr(main_window, "MainWindow")
