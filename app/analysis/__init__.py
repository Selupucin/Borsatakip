"""Analiz motorları: teknik, sentiment, temel, öneri, backtest, risk.

Faz 1: ``technical`` modülü.
Faz 2: ``sentiment``, ``news_aggregator`` ve ``recommender`` (kısa vade).
Faz 3 Batch 1: ``fundamental``, ``backtester``, ``risk`` modülleri +
``recommender`` orta/uzun vade + dispatcher.
"""

from app.analysis.backtester import (
    TRADING_DAYS_PER_YEAR,
    Backtester,
    BacktestConfig,
    BacktestRunResult,
)
from app.analysis.fundamental import FundamentalAnalyzer, FundamentalSnapshot
from app.analysis.news_aggregator import NewsAggregator
from app.analysis.recommender import (
    BUY_THRESHOLD,
    LONG_TERM_WEIGHTS,
    MID_TERM_WEIGHTS,
    MIN_SENTIMENT_COUNT,
    RISK_HIGH_MIN_ATR_PCT,
    RISK_LOW_MAX_ATR_PCT,
    SELL_THRESHOLD,
    SHORT_TERM_WEIGHTS,
    LongTermInput,
    LongTermRecommender,
    MidTermInput,
    MidTermRecommender,
    RecommendationDispatcher,
    RecommendationInput,
    RecommendationOutput,
    ShortTermRecommender,
)
from app.analysis.risk import (
    DEFAULT_RISK_PER_TRADE,
    HIGH_CORRELATION_THRESHOLD,
    MAX_POSITION_PCT,
    SECTOR_CONCENTRATION_THRESHOLD,
    STOP_LOSS_ATR_MULT,
    TAKE_PROFIT_ATR_MULT,
    CorrelationWarning,
    PortfolioRisk,
    PositionSizeRecommendation,
    RiskManager,
    StopLossTakeProfit,
)
from app.analysis.sentiment import (
    LOW_CONFIDENCE_THRESHOLD,
    SentimentAnalyzer,
    SentimentLabel,
    SentimentResult,
)
from app.analysis.sentiment import BACKEND as SENTIMENT_BACKEND
from app.analysis.technical import (
    BACKEND,
    TechnicalAnalyzer,
    TechnicalIndicators,
    signal_interpretation,
)

__all__ = [
    # technical (Faz 1)
    "BACKEND",
    "TechnicalAnalyzer",
    "TechnicalIndicators",
    "signal_interpretation",
    # sentiment (Faz 2)
    "SENTIMENT_BACKEND",
    "LOW_CONFIDENCE_THRESHOLD",
    "SentimentAnalyzer",
    "SentimentLabel",
    "SentimentResult",
    # news_aggregator (Faz 2)
    "NewsAggregator",
    # recommender (Faz 2 + Faz 3 — kısa/orta/uzun + dispatcher)
    "BUY_THRESHOLD",
    "SELL_THRESHOLD",
    "SHORT_TERM_WEIGHTS",
    "MID_TERM_WEIGHTS",
    "LONG_TERM_WEIGHTS",
    "MIN_SENTIMENT_COUNT",
    "RISK_LOW_MAX_ATR_PCT",
    "RISK_HIGH_MIN_ATR_PCT",
    "RecommendationInput",
    "RecommendationOutput",
    "MidTermInput",
    "LongTermInput",
    "ShortTermRecommender",
    "MidTermRecommender",
    "LongTermRecommender",
    "RecommendationDispatcher",
    # fundamental (Faz 3)
    "FundamentalSnapshot",
    "FundamentalAnalyzer",
    # backtester (Faz 3)
    "TRADING_DAYS_PER_YEAR",
    "BacktestConfig",
    "BacktestRunResult",
    "Backtester",
    # risk (Faz 3)
    "DEFAULT_RISK_PER_TRADE",
    "MAX_POSITION_PCT",
    "STOP_LOSS_ATR_MULT",
    "TAKE_PROFIT_ATR_MULT",
    "HIGH_CORRELATION_THRESHOLD",
    "SECTOR_CONCENTRATION_THRESHOLD",
    "PositionSizeRecommendation",
    "StopLossTakeProfit",
    "CorrelationWarning",
    "PortfolioRisk",
    "RiskManager",
]
