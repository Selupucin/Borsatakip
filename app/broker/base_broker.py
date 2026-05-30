"""Soyut BaseBroker arayüzü (Faz 4 İSKELETİ).

Doküman §7.6 (Firma Bağlantısı — Hazır Bekletilen Modül) referans
alınmıştır.

ŞU AN AKTİF DEĞİL — bu dosya yalnızca arayüz tanımıdır. Tüm somut
adapter'lar Faz 4'te (gerçek aracı kurum entegrasyonu) yazılır.
Mevcut ``app/broker/adapters/example_broker.py`` bir PLACEHOLDER'dır
ve tüm metodları ``NotImplementedError("Faz 4 aktif değil")`` atar.

Yasal not (Doküman §7.1):
- Gerçek emirler **yalnızca SPK lisanslı aracı kurum** üzerinden
  iletilir. Bu arayüze bağlanacak her adapter SPK lisanslı bir
  kurumun resmî API'sini kullanmak zorundadır.
- Kullanıcı kendisi aracı kurum **olamaz**.
- Sistem yalnızca kullanıcının kendi hesabını yönetir.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Optional


# ---------------------------------------------------------------------------
# Veri yapıları (broker bağımsız ortak DTO'lar)
# ---------------------------------------------------------------------------


@dataclass
class BrokerOrder:
    """Aracı kuruma gönderilecek emir DTO'su.

    Attributes
    ----------
    broker_order_id:
        Bizim tarafımızdan üretilen istemci-tarafı emir kimliği
        (idempotency için). Adapter, kurumun verdiği gerçek emir
        kimliğini ``BrokerOrderResult.broker_order_id`` üzerinde
        döndürür.
    ticker:
        Borsa sembolü (örn. ``THYAO``, ``AAPL``).
    side:
        ``'buy'`` veya ``'sell'``.
    quantity:
        Lot/adet.
    price:
        Limit emirde hedef fiyat; market emirde ``None`` olabilir.
    order_type:
        ``'market'`` veya ``'limit'``.
    """

    broker_order_id: str
    ticker: str
    side: str  # 'buy' | 'sell'
    quantity: Decimal
    price: Optional[Decimal]
    order_type: str  # 'market' | 'limit'


@dataclass
class BrokerOrderResult:
    """Aracı kurum API yanıtı."""

    success: bool
    broker_order_id: str
    filled_quantity: Decimal
    avg_fill_price: Optional[Decimal]
    commission: Decimal
    status: str  # 'filled' | 'partial' | 'rejected' | 'pending'
    timestamp: datetime
    raw_response: dict = field(default_factory=dict)


@dataclass
class BrokerPosition:
    """Aracı kurum tarafındaki açık pozisyon görünümü."""

    ticker: str
    quantity: Decimal
    avg_cost: Decimal
    market_value: Optional[Decimal] = None


@dataclass
class BrokerBalance:
    """Aracı kurum bakiye görünümü."""

    cash: Decimal
    total_equity: Decimal
    currency: str


# ---------------------------------------------------------------------------
# Soyut sınıf
# ---------------------------------------------------------------------------


class BaseBroker(ABC):
    """Soyut aracı kurum arayüzü.

    ŞU AN AKTİF DEĞİL — Faz 4. Tüm somut adapter'lar Faz 4'te yazılır.
    Burada sadece arayüz tanımlanır.

    Notes
    -----
    Tüm metodlar **async** olmalı (I/O ağırlıklı; ``asyncio`` ile paralel
    çağrı). Adapter implementasyonu kendi içinde sync API kullanıyorsa
    ``asyncio.to_thread`` ile sarmalamalı.

    Adapter ekleme rehberi (Faz 4):
    1. ``BaseBroker`` türevi sınıf yaz (``adapters/<kurum>_broker.py``).
    2. ``authenticate``, ``place_order``, ``cancel_order``, ``get_order``,
       ``get_positions``, ``get_balance``, ``is_market_open`` metodlarını
       implemente et.
    3. API anahtarlarını ``keyring`` ile sakla; düz metin ASLA.
    4. ``OrderManager`` ``broker`` parametresine bu instance verilir.
    """

    #: Adapter adı (örn. ``'isyatirim'``, ``'matriks'``). Override edilir.
    name: str = "base"

    # ----------------------------------------------------------- yaşam döngüsü

    @abstractmethod
    async def authenticate(self) -> bool:
        """Aracı kuruma kimlik doğrulaması yap.

        Returns
        -------
        bool
            Başarılıysa ``True``.
        """

        raise NotImplementedError

    # ----------------------------------------------------------- emir

    @abstractmethod
    async def place_order(self, order: BrokerOrder) -> BrokerOrderResult:
        """Yeni emir gönder."""

        raise NotImplementedError

    @abstractmethod
    async def cancel_order(self, broker_order_id: str) -> bool:
        """Açık bir emri iptal et."""

        raise NotImplementedError

    @abstractmethod
    async def get_order(self, broker_order_id: str) -> BrokerOrderResult:
        """Belirli bir emrin güncel durumunu sorgula."""

        raise NotImplementedError

    # ----------------------------------------------------------- pozisyon/bakiye

    @abstractmethod
    async def get_positions(self) -> list[BrokerPosition]:
        """Aracı kurum tarafındaki tüm açık pozisyonlar."""

        raise NotImplementedError

    @abstractmethod
    async def get_balance(self) -> BrokerBalance:
        """Hesap bakiyesi."""

        raise NotImplementedError

    # ----------------------------------------------------------- piyasa

    @abstractmethod
    async def is_market_open(self) -> bool:
        """İlgili borsa şu an açık mı?"""

        raise NotImplementedError


__all__ = [
    "BrokerOrder",
    "BrokerOrderResult",
    "BrokerPosition",
    "BrokerBalance",
    "BaseBroker",
]
