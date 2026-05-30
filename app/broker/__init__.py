"""Aracı kurum entegrasyonu — soyut ``BaseBroker`` + adapter'lar + safety.

Faz 3 Batch 3'te eklendi (bkz. ``trading-executor`` agent):
- ``base_broker`` — Soyut ``BaseBroker`` arayüzü + DTO'lar (Faz 4 iskeleti).
- ``adapters.example_broker`` — Faz 4 PLACEHOLDER; tüm metodlar
  ``NotImplementedError("Faz 4 aktif değil")`` atar.
- ``safety`` — Kill switch, günlük limit, circuit breaker, audit log.

**Faz 4 KORUMASI:** Gerçek aracı kurum API'sine bağlı somut adapter
yazılana dek hiçbir gerçek emir gönderilmez. ``AutoGate``
``semi_auto`` / ``full_auto`` modlarını broker yokken BLOCKED yapar.

Yasal not (Doküman §7.1): Gerçek emirler yalnızca SPK lisanslı aracı
kurum üzerinden iletilir.
"""

from app.broker.base_broker import (
    BaseBroker,
    BrokerBalance,
    BrokerOrder,
    BrokerOrderResult,
    BrokerPosition,
)
from app.broker.safety import SafetyEngine, SafetyState

__all__ = [
    # base_broker
    "BaseBroker",
    "BrokerOrder",
    "BrokerOrderResult",
    "BrokerPosition",
    "BrokerBalance",
    # safety
    "SafetyEngine",
    "SafetyState",
]
