"""ExampleBroker — Faz 4 PLACEHOLDER adapter.

UYARI
-----
Bu dosya hiçbir gerçek aracı kurum API'sine bağlanmaz. Tüm metodlar
``NotImplementedError("Faz 4 aktif değil")`` atar.

Faz 4 başlatıldığında bu dosya bir **SPK lisanslı kurum** için
yeniden yazılır (doküman §7.1 ve §7.6):

1. Sınıf adı kuruma göre değiştirilir (örn. ``IsYatirimBroker``).
2. API anahtarları ``keyring`` ile şifreli saklanır — düz metin
   parametre ASLA kabul edilmez.
3. ``authenticate``, ``place_order``, vb. metodlar kurumun resmî
   REST/FIX API'siyle entegre edilir.
4. Test adapter'ı (mock) ayrı bir dosyada (``test_broker.py``) tutulur.

Bu placeholder'ın amacı ``BaseBroker`` arayüzünün somut bir örnekle
test edilebilir ve UI'da "Yakında — Faz 4" mesajıyla gösterilebilir
olmasıdır.
"""

from __future__ import annotations

from typing import Optional

from app.broker.base_broker import (
    BaseBroker,
    BrokerBalance,
    BrokerOrder,
    BrokerOrderResult,
    BrokerPosition,
)


_PHASE_4_MESSAGE = (
    "Faz 4 aktif değil. Gerçek aracı kurum entegrasyonu yapılmadı. "
    "Bu yalnızca BaseBroker arayüzünün placeholder örneğidir."
)


class ExampleBroker(BaseBroker):
    """Faz 4 için PLACEHOLDER adapter.

    Hiçbir gerçek aracı kurum API'sine bağlanmaz. Tüm metodlar
    ``NotImplementedError`` atar. UI tarafında "Firma bağla" akışında
    bu sınıfın varlığı gösterilir ama hiçbir gerçek emir tetiklenemez.
    """

    name: str = "example"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
    ) -> None:
        # Anahtarlar burada SAKLANMAZ. Faz 4'te keyring üzerinden
        # şifreli okuma yapılacak. Düz metin parametre yalnızca
        # imza uyumu için kabul edilir, kullanılmaz.
        self._api_key_provided = api_key is not None
        self._api_secret_provided = api_secret is not None

    # ----------------------------------------------------------- yaşam döngüsü

    async def authenticate(self) -> bool:
        raise NotImplementedError(_PHASE_4_MESSAGE)

    # ----------------------------------------------------------- emir

    async def place_order(self, order: BrokerOrder) -> BrokerOrderResult:
        raise NotImplementedError(_PHASE_4_MESSAGE)

    async def cancel_order(self, broker_order_id: str) -> bool:
        raise NotImplementedError(_PHASE_4_MESSAGE)

    async def get_order(self, broker_order_id: str) -> BrokerOrderResult:
        raise NotImplementedError(_PHASE_4_MESSAGE)

    # ----------------------------------------------------------- pozisyon/bakiye

    async def get_positions(self) -> list[BrokerPosition]:
        raise NotImplementedError(_PHASE_4_MESSAGE)

    async def get_balance(self) -> BrokerBalance:
        raise NotImplementedError(_PHASE_4_MESSAGE)

    # ----------------------------------------------------------- piyasa

    async def is_market_open(self) -> bool:
        raise NotImplementedError(_PHASE_4_MESSAGE)


__all__ = ["ExampleBroker"]
