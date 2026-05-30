"""Faz 1 başlangıç şeması — tüm 19 tablo.

Revision ID: 0001
Revises:
Create Date: 2026-05-27

Doküman §9'daki şemayı birebir kurar. Tüm para alanları NUMERIC(18,4),
kur alanı NUMERIC(18,6); tüm timestamp'ler TIMESTAMPTZ.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- 1. instruments -----
    op.create_table(
        "instruments",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("exchange", sa.String(length=20), nullable=True),
        sa.Column("sector", sa.String(length=100), nullable=True),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("ticker", name="uq_instruments_ticker"),
    )
    op.create_index("ix_instruments_ticker", "instruments", ["ticker"])

    # ----- 2. price_history -----
    op.create_table(
        "price_history",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("open", sa.Numeric(18, 4), nullable=True),
        sa.Column("high", sa.Numeric(18, 4), nullable=True),
        sa.Column("low", sa.Numeric(18, 4), nullable=True),
        sa.Column("close", sa.Numeric(18, 4), nullable=True),
        sa.Column("volume", sa.BigInteger(), nullable=True),
        sa.Column(
            "is_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("verified_close", sa.Numeric(18, 4), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.UniqueConstraint(
            "instrument_id",
            "source",
            "timestamp",
            name="uq_price_history_instr_src_ts",
        ),
    )
    op.create_index(
        "ix_price_history_instrument_id", "price_history", ["instrument_id"]
    )
    op.execute(
        "CREATE INDEX ix_price_history_instr_ts_desc "
        "ON price_history (instrument_id, timestamp DESC)"
    )

    # ----- 3. data_sources -----
    op.create_table(
        "data_sources",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column(
            "reliability_score",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("100.0"),
        ),
        sa.Column(
            "total_requests",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "failed_requests",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("last_success", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.UniqueConstraint("name", name="uq_data_sources_name"),
    )

    # ----- 4. discrepancies -----
    op.create_table(
        "discrepancies",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_a", sa.String(length=50), nullable=True),
        sa.Column("source_b", sa.String(length=50), nullable=True),
        sa.Column("price_a", sa.Numeric(18, 4), nullable=True),
        sa.Column("price_b", sa.Numeric(18, 4), nullable=True),
        sa.Column("diff_pct", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "alert_sent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
    )
    op.create_index(
        "ix_discrepancies_instrument_id", "discrepancies", ["instrument_id"]
    )

    # ----- 5. technical_signals -----
    op.create_table(
        "technical_signals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rsi", sa.Numeric(8, 4), nullable=True),
        sa.Column("macd", sa.Numeric(12, 6), nullable=True),
        sa.Column("macd_signal", sa.Numeric(12, 6), nullable=True),
        sa.Column("bb_upper", sa.Numeric(18, 4), nullable=True),
        sa.Column("bb_lower", sa.Numeric(18, 4), nullable=True),
        sa.Column("ema_20", sa.Numeric(18, 4), nullable=True),
        sa.Column("ema_50", sa.Numeric(18, 4), nullable=True),
        sa.Column("ema_200", sa.Numeric(18, 4), nullable=True),
        sa.Column("tv_summary", sa.String(length=20), nullable=True),
        sa.Column("inv_summary", sa.String(length=20), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
    )
    op.create_index(
        "ix_technical_signals_instrument_id",
        "technical_signals",
        ["instrument_id"],
    )

    # ----- 6. news_feed -----
    op.create_table(
        "news_feed",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=True),
        sa.Column("sentiment", sa.String(length=20), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(5, 4), nullable=True),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
    )
    op.create_index(
        "ix_news_feed_instrument_id", "news_feed", ["instrument_id"]
    )
    op.execute(
        "CREATE INDEX ix_news_feed_instr_published_desc "
        "ON news_feed (instrument_id, published_at DESC)"
    )

    # ----- 7. recommendations -----
    op.create_table(
        "recommendations",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("risk_level", sa.String(length=20), nullable=True),
        sa.Column("risk_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("target_price", sa.Numeric(18, 4), nullable=True),
        sa.Column("stop_loss", sa.Numeric(18, 4), nullable=True),
        sa.Column("take_profit", sa.Numeric(18, 4), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("tech_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("sentiment_score", sa.Numeric(5, 2), nullable=True),
        sa.Column("fundamental_score", sa.Numeric(5, 2), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.CheckConstraint(
            "action IN ('BUY','HOLD','SELL')", name="ck_recommendations_action"
        ),
        sa.CheckConstraint(
            "timeframe IN ('short','mid','long')",
            name="ck_recommendations_timeframe",
        ),
    )
    op.create_index(
        "ix_recommendations_instrument_id",
        "recommendations",
        ["instrument_id"],
    )
    op.execute(
        "CREATE INDEX ix_recommendations_instr_generated_desc "
        "ON recommendations (instrument_id, generated_at DESC)"
    )

    # ----- 8. backtest_results -----
    op.create_table(
        "backtest_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("strategy_name", sa.String(length=100), nullable=True),
        sa.Column("timeframe", sa.String(length=20), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("total_return", sa.Numeric(10, 4), nullable=True),
        sa.Column("sharpe_ratio", sa.Numeric(8, 4), nullable=True),
        sa.Column("max_drawdown", sa.Numeric(8, 4), nullable=True),
        sa.Column("win_rate", sa.Numeric(5, 2), nullable=True),
        sa.Column("avg_hold_days", sa.Numeric(8, 2), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ----- 9. user_account -----
    op.create_table(
        "user_account",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "trading_mode",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'manual_parallel'"),
        ),
        sa.Column(
            "risk_threshold",
            sa.Numeric(5, 2),
            nullable=False,
            server_default=sa.text("50.0"),
        ),
        sa.Column("initial_balance", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "cash_balance",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "currency",
            sa.String(length=10),
            nullable=False,
            server_default=sa.text("'TRY'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "trading_mode IN ('manual_parallel','semi_auto','full_auto','paper')",
            name="ck_user_account_trading_mode",
        ),
    )

    # ----- 10. wallets -----
    op.create_table(
        "wallets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("pool", sa.String(length=10), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=False),
        sa.Column(
            "allocated",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "cash_balance",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.ForeignKeyConstraint(["account_id"], ["user_account.id"]),
        sa.UniqueConstraint(
            "account_id",
            "pool",
            "timeframe",
            name="uq_wallets_account_pool_timeframe",
        ),
        sa.CheckConstraint("pool IN ('bot','user')", name="ck_wallets_pool"),
        sa.CheckConstraint(
            "timeframe IN ('short','mid','long')", name="ck_wallets_timeframe"
        ),
    )
    op.create_index("ix_wallets_account_id", "wallets", ["account_id"])

    # ----- 11. cash_flows -----
    op.create_table(
        "cash_flows",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("account_id", sa.Integer(), nullable=False),
        sa.Column("wallet_id", sa.Integer(), nullable=True),
        sa.Column("flow_type", sa.String(length=20), nullable=True),
        sa.Column("amount", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["user_account.id"]),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"]),
    )
    op.create_index("ix_cash_flows_account_id", "cash_flows", ["account_id"])
    op.create_index("ix_cash_flows_wallet_id", "cash_flows", ["wallet_id"])

    # ----- 12. portfolio -----
    op.create_table(
        "portfolio",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=10), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("price", sa.Numeric(18, 4), nullable=True),
        sa.Column("total", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "commission",
            sa.Numeric(18, 4),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("realized_pnl", sa.Numeric(18, 4), nullable=True),
        sa.Column("hold_days", sa.Integer(), nullable=True),
        sa.Column("followed_bot", sa.Boolean(), nullable=True),
        sa.Column("transaction_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"]),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.CheckConstraint(
            "action IN ('BUY','SELL')", name="ck_portfolio_action"
        ),
    )
    op.create_index("ix_portfolio_wallet_id", "portfolio", ["wallet_id"])
    op.create_index(
        "ix_portfolio_instrument_id", "portfolio", ["instrument_id"]
    )
    op.execute(
        "CREATE INDEX ix_portfolio_wallet_tx_desc "
        "ON portfolio (wallet_id, transaction_at DESC)"
    )

    # ----- 13. open_positions -----
    op.create_table(
        "open_positions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("wallet_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("quantity", sa.Numeric(18, 4), nullable=True),
        sa.Column("avg_cost", sa.Numeric(18, 4), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"]),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.UniqueConstraint(
            "wallet_id",
            "instrument_id",
            name="uq_open_positions_wallet_instrument",
        ),
    )
    op.create_index(
        "ix_open_positions_wallet_id", "open_positions", ["wallet_id"]
    )
    op.create_index(
        "ix_open_positions_instrument_id",
        "open_positions",
        ["instrument_id"],
    )

    # ----- 14. watchlists -----
    op.create_table(
        "watchlists",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=100), nullable=True),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ----- 15. watchlist_items -----
    op.create_table(
        "watchlist_items",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("watchlist_id", sa.Integer(), nullable=False),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"], ["watchlists.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
        sa.UniqueConstraint(
            "watchlist_id",
            "instrument_id",
            name="uq_watchlist_items_wl_instr",
        ),
    )
    op.create_index(
        "ix_watchlist_items_watchlist_id",
        "watchlist_items",
        ["watchlist_id"],
    )
    op.create_index(
        "ix_watchlist_items_instrument_id",
        "watchlist_items",
        ["instrument_id"],
    )

    # ----- 16. bot_picks -----
    op.create_table(
        "bot_picks",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("timeframe", sa.String(length=20), nullable=True),
        sa.Column("action", sa.String(length=10), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("price_at_pick", sa.Numeric(18, 4), nullable=True),
        sa.Column("target_price", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "picked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "is_open",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("price_at_close", sa.Numeric(18, 4), nullable=True),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("return_pct", sa.Numeric(8, 4), nullable=True),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
    )
    op.create_index(
        "ix_bot_picks_instrument_id", "bot_picks", ["instrument_id"]
    )
    op.execute(
        "CREATE INDEX ix_bot_picks_timeframe_picked_desc "
        "ON bot_picks (timeframe, picked_at DESC)"
    )

    # ----- 17. alerts -----
    op.create_table(
        "alerts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("instrument_id", sa.Integer(), nullable=False),
        sa.Column("alert_type", sa.String(length=50), nullable=True),
        sa.Column("threshold", sa.Numeric(18, 4), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["instrument_id"], ["instruments.id"]),
    )
    op.create_index("ix_alerts_instrument_id", "alerts", ["instrument_id"])
    op.create_index(
        "ix_alerts_instr_active", "alerts", ["instrument_id", "is_active"]
    )

    # ----- 18. settings -----
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=100), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
    )

    # ----- 19. fx_rates -----
    op.create_table(
        "fx_rates",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("pair", sa.String(length=10), nullable=False),
        sa.Column("rate", sa.Numeric(18, 6), nullable=True),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=True),
        sa.UniqueConstraint("pair", "timestamp", name="uq_fx_rates_pair_ts"),
    )


def downgrade() -> None:
    # Drop bağımlılıklara göre ters sırada.
    op.drop_table("fx_rates")
    op.drop_table("settings")
    op.drop_index("ix_alerts_instr_active", table_name="alerts")
    op.drop_index("ix_alerts_instrument_id", table_name="alerts")
    op.drop_table("alerts")
    op.execute("DROP INDEX IF EXISTS ix_bot_picks_timeframe_picked_desc")
    op.drop_index("ix_bot_picks_instrument_id", table_name="bot_picks")
    op.drop_table("bot_picks")
    op.drop_index(
        "ix_watchlist_items_instrument_id", table_name="watchlist_items"
    )
    op.drop_index(
        "ix_watchlist_items_watchlist_id", table_name="watchlist_items"
    )
    op.drop_table("watchlist_items")
    op.drop_table("watchlists")
    op.drop_index(
        "ix_open_positions_instrument_id", table_name="open_positions"
    )
    op.drop_index("ix_open_positions_wallet_id", table_name="open_positions")
    op.drop_table("open_positions")
    op.execute("DROP INDEX IF EXISTS ix_portfolio_wallet_tx_desc")
    op.drop_index("ix_portfolio_instrument_id", table_name="portfolio")
    op.drop_index("ix_portfolio_wallet_id", table_name="portfolio")
    op.drop_table("portfolio")
    op.drop_index("ix_cash_flows_wallet_id", table_name="cash_flows")
    op.drop_index("ix_cash_flows_account_id", table_name="cash_flows")
    op.drop_table("cash_flows")
    op.drop_index("ix_wallets_account_id", table_name="wallets")
    op.drop_table("wallets")
    op.drop_table("user_account")
    op.drop_table("backtest_results")
    op.execute("DROP INDEX IF EXISTS ix_recommendations_instr_generated_desc")
    op.drop_index(
        "ix_recommendations_instrument_id", table_name="recommendations"
    )
    op.drop_table("recommendations")
    op.execute("DROP INDEX IF EXISTS ix_news_feed_instr_published_desc")
    op.drop_index("ix_news_feed_instrument_id", table_name="news_feed")
    op.drop_table("news_feed")
    op.drop_index(
        "ix_technical_signals_instrument_id", table_name="technical_signals"
    )
    op.drop_table("technical_signals")
    op.drop_index(
        "ix_discrepancies_instrument_id", table_name="discrepancies"
    )
    op.drop_table("discrepancies")
    op.drop_table("data_sources")
    op.execute("DROP INDEX IF EXISTS ix_price_history_instr_ts_desc")
    op.drop_index(
        "ix_price_history_instrument_id", table_name="price_history"
    )
    op.drop_table("price_history")
    op.drop_index("ix_instruments_ticker", table_name="instruments")
    op.drop_table("instruments")
