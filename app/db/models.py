"""SQLAlchemy 2.0 modelleri.

Doküman §9 (Veritabanı Şeması) referans alınarak yazılmıştır.

Tasarım kuralları:
- SQLAlchemy 2.0 ``Mapped`` + ``mapped_column`` syntax (eski ``Column`` tarzı kullanılmaz).
- Para alanları ``NUMERIC(18, 4)``; döviz kuru için ``NUMERIC(18, 6)``. FLOAT yasak.
- Tüm timestamp alanları ``TIMESTAMP WITH TIME ZONE`` (PostgreSQL ``TIMESTAMPTZ``).
- Varsayılan oluşturma zamanı için ``server_default=func.now()`` (DB tarafında UTC).
- ``watchlist_items`` ``ON DELETE CASCADE``; ``portfolio`` / ``cash_flows`` audit
  için cascade kullanmaz (soft-delete tercih edilir).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Tüm modellerin türediği taban sınıf (SQLAlchemy 2.0 declarative)."""


# ---------------------------------------------------------------------------
# 1. instruments
# ---------------------------------------------------------------------------


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("ticker", name="uq_instruments_ticker"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False, unique=True, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(200))
    exchange: Mapped[Optional[str]] = mapped_column(String(20))  # BIST, NYSE, NASDAQ
    sector: Mapped[Optional[str]] = mapped_column(String(100))
    currency: Mapped[Optional[str]] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    # Karşı ilişkiler
    price_history: Mapped[List["PriceHistory"]] = relationship(
        back_populates="instrument", cascade="all, delete-orphan", passive_deletes=True
    )
    discrepancies: Mapped[List["Discrepancy"]] = relationship(back_populates="instrument")
    technical_signals: Mapped[List["TechnicalSignal"]] = relationship(back_populates="instrument")
    news: Mapped[List["NewsFeed"]] = relationship(back_populates="instrument")
    recommendations: Mapped[List["Recommendation"]] = relationship(back_populates="instrument")
    portfolio_txs: Mapped[List["Portfolio"]] = relationship(back_populates="instrument")
    open_positions: Mapped[List["OpenPosition"]] = relationship(back_populates="instrument")
    watchlist_items: Mapped[List["WatchlistItem"]] = relationship(back_populates="instrument")
    bot_picks: Mapped[List["BotPick"]] = relationship(back_populates="instrument")
    alerts: Mapped[List["Alert"]] = relationship(back_populates="instrument")


# ---------------------------------------------------------------------------
# 2. price_history
# ---------------------------------------------------------------------------


class PriceHistory(Base):
    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "source", "timestamp", name="uq_price_history_instr_src_ts"
        ),
        Index(
            "ix_price_history_instr_ts_desc",
            "instrument_id",
            text("timestamp DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    open: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    high: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    low: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    close: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    volume: Mapped[Optional[int]] = mapped_column(BigInteger)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    verified_close: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))

    instrument: Mapped["Instrument"] = relationship(back_populates="price_history")


# ---------------------------------------------------------------------------
# 3. data_sources
# ---------------------------------------------------------------------------


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("name", name="uq_data_sources_name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    reliability_score: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("100.0")
    )
    total_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    failed_requests: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    last_success: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))


# ---------------------------------------------------------------------------
# 4. discrepancies
# ---------------------------------------------------------------------------


class Discrepancy(Base):
    __tablename__ = "discrepancies"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    source_a: Mapped[Optional[str]] = mapped_column(String(50))
    source_b: Mapped[Optional[str]] = mapped_column(String(50))
    price_a: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    price_b: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    diff_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    alert_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    instrument: Mapped["Instrument"] = relationship(back_populates="discrepancies")


# ---------------------------------------------------------------------------
# 5. technical_signals
# ---------------------------------------------------------------------------


class TechnicalSignal(Base):
    __tablename__ = "technical_signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    source: Mapped[Optional[str]] = mapped_column(String(50))
    timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    rsi: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    macd: Mapped[Optional[float]] = mapped_column(Numeric(12, 6))
    macd_signal: Mapped[Optional[float]] = mapped_column(Numeric(12, 6))
    bb_upper: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    bb_lower: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    ema_20: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    ema_50: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    ema_200: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    tv_summary: Mapped[Optional[str]] = mapped_column(String(20))
    inv_summary: Mapped[Optional[str]] = mapped_column(String(20))

    instrument: Mapped["Instrument"] = relationship(back_populates="technical_signals")


# ---------------------------------------------------------------------------
# 6. news_feed
# ---------------------------------------------------------------------------


class NewsFeed(Base):
    __tablename__ = "news_feed"
    __table_args__ = (
        Index(
            "ix_news_feed_instr_published_desc",
            "instrument_id",
            text("published_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("instruments.id"), index=True
    )
    source: Mapped[Optional[str]] = mapped_column(String(100))
    title: Mapped[Optional[str]] = mapped_column(Text)
    url: Mapped[Optional[str]] = mapped_column(Text)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    language: Mapped[Optional[str]] = mapped_column(String(10))
    sentiment: Mapped[Optional[str]] = mapped_column(String(20))
    sentiment_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 4))
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    instrument: Mapped[Optional["Instrument"]] = relationship(back_populates="news")


# ---------------------------------------------------------------------------
# 7. recommendations
# ---------------------------------------------------------------------------


class Recommendation(Base):
    __tablename__ = "recommendations"
    __table_args__ = (
        CheckConstraint(
            "action IN ('BUY','HOLD','SELL')", name="ck_recommendations_action"
        ),
        CheckConstraint(
            "timeframe IN ('short','mid','long')", name="ck_recommendations_timeframe"
        ),
        Index(
            "ix_recommendations_instr_generated_desc",
            "instrument_id",
            text("generated_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    risk_level: Mapped[Optional[str]] = mapped_column(String(20))
    risk_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    target_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    take_profit: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    tech_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    sentiment_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    fundamental_score: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))

    instrument: Mapped["Instrument"] = relationship(back_populates="recommendations")


# ---------------------------------------------------------------------------
# 8. backtest_results
# ---------------------------------------------------------------------------


class BacktestResult(Base):
    __tablename__ = "backtest_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    strategy_name: Mapped[Optional[str]] = mapped_column(String(100))
    timeframe: Mapped[Optional[str]] = mapped_column(String(20))
    start_date: Mapped[Optional[date]] = mapped_column(Date)
    end_date: Mapped[Optional[date]] = mapped_column(Date)
    total_return: Mapped[Optional[float]] = mapped_column(Numeric(10, 4))
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    max_drawdown: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))
    win_rate: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    avg_hold_days: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    params: Mapped[Optional[dict]] = mapped_column(JSONB)
    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


# ---------------------------------------------------------------------------
# 9. user_account
# ---------------------------------------------------------------------------


class UserAccount(Base):
    __tablename__ = "user_account"
    __table_args__ = (
        CheckConstraint(
            "trading_mode IN ('manual_parallel','semi_auto','full_auto','paper')",
            name="ck_user_account_trading_mode",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trading_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'manual_parallel'")
    )
    risk_threshold: Mapped[float] = mapped_column(
        Numeric(5, 2), nullable=False, server_default=text("50.0")
    )
    initial_balance: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    cash_balance: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default=text("'TRY'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    wallets: Mapped[List["Wallet"]] = relationship(back_populates="account")
    cash_flows: Mapped[List["CashFlow"]] = relationship(back_populates="account")


# ---------------------------------------------------------------------------
# 10. wallets
# ---------------------------------------------------------------------------


class Wallet(Base):
    __tablename__ = "wallets"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "pool", "timeframe", name="uq_wallets_account_pool_timeframe"
        ),
        CheckConstraint("pool IN ('bot','user')", name="ck_wallets_pool"),
        CheckConstraint(
            "timeframe IN ('short','mid','long')", name="ck_wallets_timeframe"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_account.id"), nullable=False, index=True
    )
    pool: Mapped[str] = mapped_column(String(10), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False)
    allocated: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )
    cash_balance: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )

    account: Mapped["UserAccount"] = relationship(back_populates="wallets")
    cash_flows: Mapped[List["CashFlow"]] = relationship(back_populates="wallet")
    transactions: Mapped[List["Portfolio"]] = relationship(back_populates="wallet")
    positions: Mapped[List["OpenPosition"]] = relationship(back_populates="wallet")


# ---------------------------------------------------------------------------
# 11. cash_flows
# ---------------------------------------------------------------------------


class CashFlow(Base):
    __tablename__ = "cash_flows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("user_account.id"), nullable=False, index=True
    )
    wallet_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("wallets.id"), index=True
    )
    flow_type: Mapped[Optional[str]] = mapped_column(String(20))
    amount: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)

    account: Mapped["UserAccount"] = relationship(back_populates="cash_flows")
    wallet: Mapped[Optional["Wallet"]] = relationship(back_populates="cash_flows")


# ---------------------------------------------------------------------------
# 12. portfolio
# ---------------------------------------------------------------------------


class Portfolio(Base):
    __tablename__ = "portfolio"
    __table_args__ = (
        CheckConstraint("action IN ('BUY','SELL')", name="ck_portfolio_action"),
        Index(
            "ix_portfolio_wallet_tx_desc",
            "wallet_id",
            text("transaction_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=False, index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(10), nullable=False)
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    total: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    commission: Mapped[float] = mapped_column(
        Numeric(18, 4), nullable=False, server_default=text("0")
    )
    realized_pnl: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    hold_days: Mapped[Optional[int]] = mapped_column(Integer)
    followed_bot: Mapped[Optional[bool]] = mapped_column(Boolean)
    transaction_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    wallet: Mapped["Wallet"] = relationship(back_populates="transactions")
    instrument: Mapped["Instrument"] = relationship(back_populates="portfolio_txs")


# ---------------------------------------------------------------------------
# 13. open_positions
# ---------------------------------------------------------------------------


class OpenPosition(Base):
    __tablename__ = "open_positions"
    __table_args__ = (
        UniqueConstraint(
            "wallet_id", "instrument_id", name="uq_open_positions_wallet_instrument"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    wallet_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("wallets.id"), nullable=False, index=True
    )
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    quantity: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    avg_cost: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    wallet: Mapped["Wallet"] = relationship(back_populates="positions")
    instrument: Mapped["Instrument"] = relationship(back_populates="open_positions")


# ---------------------------------------------------------------------------
# 14. watchlists
# ---------------------------------------------------------------------------


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[Optional[str]] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    items: Mapped[List["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan", passive_deletes=True
    )


# ---------------------------------------------------------------------------
# 15. watchlist_items
# ---------------------------------------------------------------------------


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (
        UniqueConstraint(
            "watchlist_id", "instrument_id", name="uq_watchlist_items_wl_instr"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    watchlist_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("watchlists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    watchlist: Mapped["Watchlist"] = relationship(back_populates="items")
    instrument: Mapped["Instrument"] = relationship(back_populates="watchlist_items")


# ---------------------------------------------------------------------------
# 16. bot_picks
# ---------------------------------------------------------------------------


class BotPick(Base):
    __tablename__ = "bot_picks"
    __table_args__ = (
        Index(
            "ix_bot_picks_timeframe_picked_desc",
            "timeframe",
            text("picked_at DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    timeframe: Mapped[Optional[str]] = mapped_column(String(20))
    action: Mapped[Optional[str]] = mapped_column(String(10))
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    price_at_pick: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    target_price: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    picked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    price_at_close: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    outcome: Mapped[Optional[str]] = mapped_column(String(20))
    return_pct: Mapped[Optional[float]] = mapped_column(Numeric(8, 4))

    instrument: Mapped["Instrument"] = relationship(back_populates="bot_picks")


# ---------------------------------------------------------------------------
# 17. alerts
# ---------------------------------------------------------------------------


class Alert(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_instr_active", "instrument_id", "is_active"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    instrument_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("instruments.id"), nullable=False, index=True
    )
    alert_type: Mapped[Optional[str]] = mapped_column(String(50))
    threshold: Mapped[Optional[float]] = mapped_column(Numeric(18, 4))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    triggered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    instrument: Mapped["Instrument"] = relationship(back_populates="alerts")


# ---------------------------------------------------------------------------
# 18. settings (anahtar-değer)
# ---------------------------------------------------------------------------


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(Text)


# ---------------------------------------------------------------------------
# 19. fx_rates
# ---------------------------------------------------------------------------


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (
        UniqueConstraint("pair", "timestamp", name="uq_fx_rates_pair_ts"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pair: Mapped[str] = mapped_column(String(10), nullable=False)
    rate: Mapped[Optional[float]] = mapped_column(Numeric(18, 6))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[Optional[str]] = mapped_column(String(50))


__all__ = [
    "Base",
    "Instrument",
    "PriceHistory",
    "DataSource",
    "Discrepancy",
    "TechnicalSignal",
    "NewsFeed",
    "Recommendation",
    "BacktestResult",
    "UserAccount",
    "Wallet",
    "CashFlow",
    "Portfolio",
    "OpenPosition",
    "Watchlist",
    "WatchlistItem",
    "BotPick",
    "Alert",
    "Setting",
    "FxRate",
]
