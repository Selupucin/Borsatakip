"""Her aracı kurum için ayrı BaseBroker adapter implementasyonu.

Faz 4 KORUMASI: Bu klasörde şu an yalnızca ``ExampleBroker``
placeholder'ı bulunur. Tüm metodları ``NotImplementedError("Faz 4
aktif değil")`` atar. Gerçek SPK lisanslı aracı kurum adapter'ı Faz 4'te
eklenecek (örn. ``IsYatirimBroker``).
"""

from app.broker.adapters.example_broker import ExampleBroker

__all__ = ["ExampleBroker"]
