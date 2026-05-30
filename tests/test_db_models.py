"""SQLAlchemy modelleri birim testleri.

Doğrulanan davranışlar:
1. 19 tablonun ``__tablename__`` mevcut ve doğru.
2. UNIQUE / CHECK kısıtları SQLite üzerinde de zorlanır.
3. Para alanları ``Numeric(18, 4)``.
4. ``relationship`` tanımları import edilebilir.

SQLite ile test edildiği için: PostgreSQL ``JSONB`` -> JSON map'lendi
(conftest.py). CHECK constraint'leri SQLite'ta da geçerlidir.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import inspect, Numeric
from sqlalchemy.exc import IntegrityError

from app.db.models import (
    Alert,
    BacktestResult,
    Base,
    BotPick,
    CashFlow,
    DataSource,
    Discrepancy,
    FxRate,
    Instrument,
    NewsFeed,
    OpenPosition,
    Portfolio,
    PriceHistory,
    Recommendation,
    Setting,
    TechnicalSignal,
    UserAccount,
    Wallet,
    Watchlist,
    WatchlistItem,
)


# ---------------------------------------------------------------------------
# 1. 19 tablo + tablename
# ---------------------------------------------------------------------------


EXPECTED_TABLES = {
    "instruments": Instrument,
    "price_history": PriceHistory,
    "data_sources": DataSource,
    "discrepancies": Discrepancy,
    "technical_signals": TechnicalSignal,
    "news_feed": NewsFeed,
    "recommendations": Recommendation,
    "backtest_results": BacktestResult,
    "user_account": UserAccount,
    "wallets": Wallet,
    "cash_flows": CashFlow,
    "portfolio": Portfolio,
    "open_positions": OpenPosition,
    "watchlists": Watchlist,
    "watchlist_items": WatchlistItem,
    "bot_picks": BotPick,
    "alerts": Alert,
    "settings": Setting,
    "fx_rates": FxRate,
}


def test_nineteen_tables_have_correct_tablenames():
    """Her 19 model ``__tablename__`` doğru olmalı."""
    assert len(EXPECTED_TABLES) == 19
    for name, cls in EXPECTED_TABLES.items():
        assert cls.__tablename__ == name, f"{cls.__name__}.__tablename__ != {name}"


def test_metadata_contains_all_19_tables(sync_engine):
    """``Base.metadata`` üzerinde 19 tablo da kayıtlı olmalı."""
    insp = inspect(sync_engine)
    tables = set(insp.get_table_names())
    for name in EXPECTED_TABLES:
        assert name in tables, f"Tablo {name} metadata'da yok"


# ---------------------------------------------------------------------------
# 2. UNIQUE constraint'ler
# ---------------------------------------------------------------------------


def test_instruments_ticker_is_unique(db_session):
    """``instruments.ticker`` UNIQUE olmalı."""
    db_session.add(Instrument(ticker="AAPL", name="Apple"))
    db_session.commit()
    db_session.add(Instrument(ticker="AAPL", name="Apple Dup"))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_wallets_account_pool_timeframe_unique(db_session):
    """``wallets`` (account_id, pool, timeframe) UNIQUE olmalı."""
    acc = UserAccount(trading_mode="paper", risk_threshold=50.0)
    db_session.add(acc)
    db_session.commit()
    w1 = Wallet(account_id=acc.id, pool="bot", timeframe="short", allocated=1000, cash_balance=1000)
    db_session.add(w1)
    db_session.commit()
    w2 = Wallet(account_id=acc.id, pool="bot", timeframe="short", allocated=500, cash_balance=500)
    db_session.add(w2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 3. CHECK constraint'ler (SQLite üzerinde de etkin)
# ---------------------------------------------------------------------------


def test_wallets_pool_check_rejects_invalid_pool(db_session):
    """``wallets.pool`` IN ('bot','user') CHECK kuralı."""
    acc = UserAccount(trading_mode="paper", risk_threshold=50.0)
    db_session.add(acc)
    db_session.commit()
    bad = Wallet(account_id=acc.id, pool="invalid", timeframe="short")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_wallets_timeframe_check_rejects_invalid(db_session):
    """``wallets.timeframe`` IN ('short','mid','long') CHECK kuralı."""
    acc = UserAccount(trading_mode="paper", risk_threshold=50.0)
    db_session.add(acc)
    db_session.commit()
    bad = Wallet(account_id=acc.id, pool="bot", timeframe="ultralong")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_recommendations_action_check_rejects_invalid(db_session):
    """``recommendations.action`` IN ('BUY','HOLD','SELL') CHECK kuralı."""
    instr = Instrument(ticker="THYAO")
    db_session.add(instr)
    db_session.commit()
    bad = Recommendation(instrument_id=instr.id, action="MAYBE", timeframe="short")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_portfolio_action_check_rejects_invalid(db_session):
    """``portfolio.action`` IN ('BUY','SELL') CHECK kuralı."""
    instr = Instrument(ticker="GARAN")
    acc = UserAccount(trading_mode="paper", risk_threshold=50.0)
    db_session.add_all([instr, acc])
    db_session.commit()
    wallet = Wallet(account_id=acc.id, pool="bot", timeframe="mid")
    db_session.add(wallet)
    db_session.commit()
    bad = Portfolio(wallet_id=wallet.id, instrument_id=instr.id, action="HOLD")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_account_trading_mode_check_rejects_invalid(db_session):
    """``user_account.trading_mode`` IN ('manual_parallel','semi_auto','full_auto','paper')."""
    bad = UserAccount(trading_mode="god_mode", risk_threshold=50.0)
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_recommendations_timeframe_check_rejects_invalid(db_session):
    """``recommendations.timeframe`` IN ('short','mid','long') CHECK kuralı."""
    instr = Instrument(ticker="ASELS")
    db_session.add(instr)
    db_session.commit()
    bad = Recommendation(instrument_id=instr.id, action="BUY", timeframe="forever")
    db_session.add(bad)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


# ---------------------------------------------------------------------------
# 4. Para alanları Numeric(18, 4)
# ---------------------------------------------------------------------------


def _numeric_18_4(cls, col_name: str) -> bool:
    col = cls.__table__.c[col_name]
    return (
        isinstance(col.type, Numeric)
        and col.type.precision == 18
        and col.type.scale == 4
    )


def test_money_columns_are_numeric_18_4():
    """En az 5 modelde para alanı ``Numeric(18, 4)`` olmalı."""
    checks = {
        PriceHistory: "close",
        Discrepancy: "price_a",
        Recommendation: "target_price",
        UserAccount: "cash_balance",
        Wallet: "cash_balance",
        Portfolio: "price",
        OpenPosition: "avg_cost",
    }
    for cls, col_name in checks.items():
        assert _numeric_18_4(cls, col_name), (
            f"{cls.__name__}.{col_name} Numeric(18,4) değil "
            f"(precision={cls.__table__.c[col_name].type.precision}, "
            f"scale={cls.__table__.c[col_name].type.scale})"
        )
    # En az 5 sağlandı:
    assert len(checks) >= 5


def test_fx_rate_numeric_precision_18_6():
    """``fx_rates.rate`` ``Numeric(18, 6)`` (kur için ekstra hassasiyet)."""
    col = FxRate.__table__.c["rate"]
    assert isinstance(col.type, Numeric)
    assert col.type.precision == 18
    assert col.type.scale == 6


# ---------------------------------------------------------------------------
# 5. Relationship'ler import edilebilir
# ---------------------------------------------------------------------------


def test_instrument_price_history_relationship_attribute_exists():
    """``Instrument.price_history`` relationship descriptor olmalı."""
    assert hasattr(Instrument, "price_history")
    assert Instrument.price_history.property.mapper.class_ is PriceHistory


def test_wallet_positions_relationship_attribute_exists():
    """``Wallet.positions`` relationship descriptor olmalı."""
    assert hasattr(Wallet, "positions")
    assert Wallet.positions.property.mapper.class_ is OpenPosition


def test_instrument_alerts_relationship_works(db_session):
    """``Instrument.alerts`` ile ilişkili Alert satırları yüklenir."""
    instr = Instrument(ticker="AKBNK")
    db_session.add(instr)
    db_session.commit()
    a = Alert(instrument_id=instr.id, alert_type="price_above", threshold=100.0)
    db_session.add(a)
    db_session.commit()
    db_session.refresh(instr)
    assert len(instr.alerts) == 1
    assert instr.alerts[0].alert_type == "price_above"


# ---------------------------------------------------------------------------
# 6. Sağlık kontrolü: temel insert / select
# ---------------------------------------------------------------------------


def test_instrument_insert_and_select_roundtrip(db_session):
    """Tabloya yazıp okumak başarılı olmalı (şema sağlık testi)."""
    db_session.add(Instrument(ticker="SISE", name="Sisecam", exchange="BIST"))
    db_session.commit()
    found = db_session.query(Instrument).filter_by(ticker="SISE").one()
    assert found.name == "Sisecam"
