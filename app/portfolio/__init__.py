"""Portföy, cüzdan matrisi, watchlist, bot picks, paper trading.

Faz 2 (mevcut):
- ``watchlist`` — Kullanıcı takip listeleri (CRUD + öğe yönetimi).
- ``bot_picks`` — Bot öneri takibi + başarı oranı istatistikleri.

Faz 3 Batch 2 (eklendi):
- ``account`` — UserAccount CRUD, trading_mode, risk_threshold.
- ``wallets`` — 2 havuz × 3 vade = 6 bağımsız cüzdan matrisi.
- ``cash_flows`` — Deposit / withdrawal / net deposited audit servisi.
- ``positions`` — Açık pozisyon yönetimi (weighted-average cost),
  BUY/SELL → portfolio + open_positions + wallet mutasyonu.
- ``pnl`` — Komisyon / vergi / kur dahil P&L kırılımı (PnLBreakdown).
- ``benchmark`` — Time-Weighted Return + BIST 100 / S&P 500 kıyaslama.
- ``paper_trading`` — Sanal portföy modu (aynı kod yolu, flag farkı).
"""

from app.portfolio.account import (
    MAX_RISK_THRESHOLD,
    MIN_RISK_THRESHOLD,
    VALID_TRADING_MODES,
    AccountService,
    AccountSnapshot,
)
from app.portfolio.benchmark import (
    BIST_BENCHMARK,
    SP500_BENCHMARK,
    BenchmarkComparison,
    BenchmarkService,
)
from app.portfolio.bot_picks import (
    EXPIRE_DAYS,
    BotPicksService,
    BotPicksStats,
    BotPickView,
)
from app.portfolio.cash_flows import (
    KNOWN_FLOW_TYPES,
    CashFlowService,
)
from app.portfolio.paper_trading import (
    DEFAULT_PAPER_BALANCE,
    PAPER_MODE,
    PaperTradingService,
)
from app.portfolio.pnl import (
    BIST_TAX_PCT,
    DEFAULT_COMMISSION_PCT,
    DEFAULT_FX_PAIR,
    TAX_DISCLAIMER,
    US_DIVIDEND_TAX_PCT,
    PnLBreakdown,
    PnLCalculator,
)
from app.portfolio.positions import (
    PositionService,
    PositionView,
)
from app.portfolio.wallets import (
    VALID_POOLS,
    VALID_TIMEFRAMES,
    WalletService,
    WalletSnapshot,
)
from app.portfolio.watchlist import (
    WatchlistItemView,
    WatchlistService,
    WatchlistSummary,
)

__all__ = [
    # watchlist (Faz 2)
    "WatchlistService",
    "WatchlistSummary",
    "WatchlistItemView",
    # bot_picks (Faz 2)
    "BotPicksService",
    "BotPickView",
    "BotPicksStats",
    "EXPIRE_DAYS",
    # account (Faz 3 Batch 2)
    "AccountService",
    "AccountSnapshot",
    "VALID_TRADING_MODES",
    "MIN_RISK_THRESHOLD",
    "MAX_RISK_THRESHOLD",
    # wallets (Faz 3 Batch 2)
    "WalletService",
    "WalletSnapshot",
    "VALID_POOLS",
    "VALID_TIMEFRAMES",
    # cash_flows (Faz 3 Batch 2)
    "CashFlowService",
    "KNOWN_FLOW_TYPES",
    # positions (Faz 3 Batch 2)
    "PositionService",
    "PositionView",
    # pnl (Faz 3 Batch 2)
    "PnLCalculator",
    "PnLBreakdown",
    "DEFAULT_COMMISSION_PCT",
    "BIST_TAX_PCT",
    "US_DIVIDEND_TAX_PCT",
    "DEFAULT_FX_PAIR",
    "TAX_DISCLAIMER",
    # benchmark (Faz 3 Batch 2)
    "BenchmarkService",
    "BenchmarkComparison",
    "BIST_BENCHMARK",
    "SP500_BENCHMARK",
    # paper_trading (Faz 3 Batch 2)
    "PaperTradingService",
    "DEFAULT_PAPER_BALANCE",
    "PAPER_MODE",
]
