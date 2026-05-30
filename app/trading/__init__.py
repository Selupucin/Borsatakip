"""İşlem yürütme katmanı: modlar, manuel-eşli, risk skoru, auto gate, order manager.

Faz 3 Batch 3'te eklendi (bkz. ``trading-executor`` agent):
- ``execution_modes`` — ``TradingMode`` enum + ``MODE_REGISTRY`` + Faz koruması.
- ``manual_parallel`` — şu anki başlangıç modu (öneri/işaretleme/follow-rate).
- ``risk_scorer`` — 0..100 ağırlıklı risk skoru.
- ``auto_gate`` — mod + skor + safety kombinasyonu → karar.
- ``order_manager`` — emir doğrulama + hazırlama + (Faz 4'te) broker delegasyonu.

**Faz 4 KORUMASI:** ``semi_auto`` ve ``full_auto`` modları gerçek aracı
kurum entegrasyonu yapılmadığı için ``order_manager.execute()`` içinde
``NotImplementedError`` atar.
"""

from app.trading.auto_gate import AutoGate, GateResult, TradeDecision
from app.trading.execution_modes import (
    MODE_REGISTRY,
    ModeDescription,
    TradingMode,
    is_active_in_current_phase,
    requires_broker,
)
from app.trading.manual_parallel import (
    USER_ACTIONS,
    BotFollowRate,
    FollowVsSkipPnL,
    ManualParallelService,
    ManualTradeMarker,
    PendingRecommendationView,
)
from app.trading.order_manager import (
    VALID_ACTIONS,
    VALID_ORDER_TYPES,
    OrderManager,
    OrderRequest,
    OrderValidation,
)
from app.trading.risk_scorer import (
    RISK_LEVEL_THRESHOLDS,
    TradeRiskScore,
    TradeRiskScorer,
    risk_level_from_score,
)

__all__ = [
    # execution_modes
    "TradingMode",
    "ModeDescription",
    "MODE_REGISTRY",
    "is_active_in_current_phase",
    "requires_broker",
    # manual_parallel
    "USER_ACTIONS",
    "ManualTradeMarker",
    "PendingRecommendationView",
    "BotFollowRate",
    "FollowVsSkipPnL",
    "ManualParallelService",
    # risk_scorer
    "RISK_LEVEL_THRESHOLDS",
    "risk_level_from_score",
    "TradeRiskScore",
    "TradeRiskScorer",
    # auto_gate
    "TradeDecision",
    "GateResult",
    "AutoGate",
    # order_manager
    "VALID_ACTIONS",
    "VALID_ORDER_TYPES",
    "OrderRequest",
    "OrderValidation",
    "OrderManager",
]
