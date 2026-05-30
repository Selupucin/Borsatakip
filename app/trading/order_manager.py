"""Order Manager — emir oluşturma, doğrulama, hazırlama (Faz 3 Batch 3).

Doküman §7.4 ve §7.5 referans alınmıştır.

Sorumluluk:
- ``OrderRequest`` yapısı (kullanıcı veya bot tarafından üretilen emir).
- ``validate()`` — mantık kontrolü (quantity > 0, anormal fiyat sapması,
  anormal işlem tutarı tespiti).
- ``prepare()`` — DB'ye placeholder + audit log.
- ``execute()`` — moda göre delege:

  - ``manual_parallel`` → kullanıcının elle uygulaması beklenir,
    yalnızca "bekliyor" kaydı tutulur.
  - ``paper`` → (Faz 3 sonrası) ``PaperTradingService`` çağrılır;
    bu sınıf henüz mevcut değilse uyarı + dry-run.
  - ``semi_auto`` / ``full_auto`` → ``broker.place_order()`` çağrılır;
    broker yoksa **``NotImplementedError("Faz 4 aktif değil")``**.

**Faz 4 KORUMASI:** Bu modül gerçek aracı kurum API'sine asla doğrudan
HTTP isteği atmaz — yalnızca ``BaseBroker`` adapter'ı üzerinden gider.
Adapter ``NotImplementedError`` atarsa bu hata kullanıcıya yansıtılır.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from loguru import logger

from app.trading.execution_modes import TradingMode, requires_broker


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

#: ``OrderRequest.action`` izinli değerleri.
VALID_ACTIONS: frozenset[str] = frozenset({"BUY", "SELL"})

#: ``OrderRequest.order_type`` izinli değerleri.
VALID_ORDER_TYPES: frozenset[str] = frozenset({"market", "limit"})


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class OrderRequest:
    """Bir alım veya satım emrinin kullanıcı/bot tarafındaki temsili.

    Attributes
    ----------
    instrument_id:
        ``instruments.id`` referansı.
    action:
        ``'BUY'`` veya ``'SELL'``.
    quantity:
        Lot/adet — pozitif Decimal.
    price:
        Market emirde tahmini gönderim fiyatı (validation için);
        limit emirde hedef fiyat.
    order_type:
        ``'market'`` veya ``'limit'``.
    wallet_id:
        Hangi cüzdandan (havuz × vade) çıkacağı.
    triggered_by_recommendation_id:
        Bot önerisinden tetiklendiyse ``recommendations.id``; aksi
        halde ``None`` (kullanıcı manuel emri).
    notes:
        Serbest metin not (audit log için).
    """

    instrument_id: int
    action: str
    quantity: Decimal
    price: Decimal
    order_type: str
    wallet_id: int
    triggered_by_recommendation_id: Optional[int] = None
    notes: str = ""


@dataclass
class OrderValidation:
    """Validasyon sonucu."""

    is_valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class OrderManager:
    """Emir doğrulama + hazırlama + (mod bazlı) gönderim.

    Parameters
    ----------
    session_factory:
        SQLAlchemy ``Session`` fabrikası (audit/placeholder yazımı için).
    broker:
        Bağlı ``BaseBroker`` adapter'ı veya ``None``. Faz 4 öncesi
        ``None``.
    """

    #: Son fiyattan ±%X sapma → anormal (validate uyarı/hata verir).
    PRICE_DEVIATION_PCT: float = 0.20

    #: Tek işlem portföyün %X'inden büyükse anormal.
    MAX_TRADE_VALUE_PCT: float = 0.30

    def __init__(
        self,
        session_factory: Any,
        broker: Optional[Any] = None,
    ) -> None:
        self.session_factory = session_factory
        self.broker = broker

    # ----------------------------------------------------------- helpers

    @staticmethod
    def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    # ----------------------------------------------------------- validate

    def validate(
        self,
        order: OrderRequest,
        last_price: Decimal,
        portfolio_value: Decimal,
    ) -> OrderValidation:
        """Emir mantık kontrolü.

        Hata listesi (``errors``) doluyken ``is_valid=False`` döner.
        Uyarılar (``warnings``) bilgilendirme amaçlıdır, emiri engellemez.
        """

        errors: list[str] = []
        warnings: list[str] = []

        # 1) Aksiyon ve order_type.
        action = (order.action or "").upper()
        if action not in VALID_ACTIONS:
            errors.append(
                f"Geçersiz action: {order.action!r}. Beklenen: {sorted(VALID_ACTIONS)}"
            )
        order_type = (order.order_type or "").lower()
        if order_type not in VALID_ORDER_TYPES:
            errors.append(
                f"Geçersiz order_type: {order.order_type!r}. "
                f"Beklenen: {sorted(VALID_ORDER_TYPES)}"
            )

        # 2) Miktar pozitif.
        qty = self._to_decimal(order.quantity)
        if qty <= 0:
            errors.append("quantity > 0 olmalı.")

        # 3) Fiyat pozitif.
        price = self._to_decimal(order.price)
        if price <= 0:
            errors.append("price > 0 olmalı.")

        last = self._to_decimal(last_price)
        pv = self._to_decimal(portfolio_value)

        # 4) Anormal fiyat sapması (son fiyattan ±%X).
        if last > 0 and price > 0:
            deviation = abs(price - last) / last
            if float(deviation) >= self.PRICE_DEVIATION_PCT:
                errors.append(
                    f"Anormal fiyat: hedef {price} son fiyattan "
                    f"%{float(deviation)*100:.1f} sapıyor "
                    f"(eşik: %{self.PRICE_DEVIATION_PCT*100:.0f})."
                )

        # 5) Anormal tutar (portföyün %X'inden büyük).
        if pv > 0 and price > 0 and qty > 0:
            trade_value = price * qty
            pct = float(trade_value) / float(pv)
            if pct >= self.MAX_TRADE_VALUE_PCT:
                errors.append(
                    f"İşlem tutarı portföyün %{pct*100:.1f}'i "
                    f"(eşik: %{self.MAX_TRADE_VALUE_PCT*100:.0f}). "
                    "Manuel onay olmadan reddedildi."
                )
        elif pv <= 0:
            warnings.append(
                "Portföy değeri bilinmiyor — tutar oran kontrolü atlandı."
            )

        return OrderValidation(
            is_valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings,
        )

    # ----------------------------------------------------------- prepare

    async def prepare(self, order: OrderRequest) -> dict:
        """Emir için DB'ye placeholder + audit log.

        Notes
        -----
        DB'ye gerçek bir ``portfolio`` satırı yazmaz (uygulama anına
        kadar belirsiz). Audit için loguru'ya yapılandırılmış kayıt
        düşer; ek olarak çağıran tarafa hazırlanmış metadata döndürür.

        Returns
        -------
        dict
            ``{'order': OrderRequest, 'prepared_at': datetime, 'audit_id': str}``
        """

        prepared_at = datetime.now(timezone.utc)
        audit_id = (
            f"ord-{int(prepared_at.timestamp() * 1000)}-"
            f"{order.wallet_id}-{order.instrument_id}"
        )

        logger.info(
            "Order prepared: audit_id={}, wallet={}, instrument={}, "
            "action={}, qty={}, price={}, type={}",
            audit_id,
            order.wallet_id,
            order.instrument_id,
            order.action,
            order.quantity,
            order.price,
            order.order_type,
        )

        return {
            "order": order,
            "prepared_at": prepared_at,
            "audit_id": audit_id,
        }

    # ----------------------------------------------------------- execute

    async def execute(self, order: OrderRequest, mode: TradingMode) -> dict:
        """Moda göre delege.

        Returns
        -------
        dict
            Sonuç sözlüğü. ``status`` alanı:

            - ``'awaiting_manual'`` (manual_parallel)
            - ``'paper_executed'`` (paper — Faz 3 PaperTradingService'e devir)
            - ``'broker_executed'`` (semi/full auto — Faz 4)

        Raises
        ------
        NotImplementedError
            ``semi_auto`` veya ``full_auto`` modunda ``self.broker is None``
            veya broker adapter'ı kendisi ``NotImplementedError`` atarsa
            ("Faz 4 aktif değil").
        """

        # manual_parallel — sadece beklenir.
        if mode == TradingMode.MANUAL_PARALLEL:
            logger.info(
                "manual_parallel: kullanıcının elle uygulaması bekleniyor "
                "(wallet={}, instrument={}, action={})",
                order.wallet_id,
                order.instrument_id,
                order.action,
            )
            return {
                "status": "awaiting_manual",
                "mode": mode.value,
                "message": (
                    "Bu modda emir gönderilmez; kullanıcı kendi aracı "
                    "kurum uygulamasında elle uygular ve sonra "
                    "ManualParallelService.mark_applied() ile işaretler."
                ),
            }

        # paper — PaperTradingService varsa delege; yoksa dry-run.
        if mode == TradingMode.PAPER:
            paper_service = getattr(self, "paper_service", None)
            if paper_service is not None:
                exec_fn = getattr(paper_service, "execute_paper_trade", None)
                if callable(exec_fn):
                    result = await exec_fn(order)
                    return {
                        "status": "paper_executed",
                        "mode": mode.value,
                        "result": result,
                    }
            logger.warning(
                "PAPER mod: PaperTradingService bağlı değil → dry-run. "
                "Order: wallet={}, instrument={}, action={}, qty={}, price={}",
                order.wallet_id,
                order.instrument_id,
                order.action,
                order.quantity,
                order.price,
            )
            return {
                "status": "paper_executed",
                "mode": mode.value,
                "result": {
                    "dry_run": True,
                    "instrument_id": order.instrument_id,
                    "action": order.action,
                    "quantity": str(order.quantity),
                    "price": str(order.price),
                },
            }

        # semi_auto / full_auto — broker zorunlu. Faz 4 koruması.
        if requires_broker(mode):
            if self.broker is None:
                raise NotImplementedError(
                    f"Faz 4 aktif değil: '{mode.value}' modu için aracı kurum "
                    "adapter'ı (BaseBroker) bağlanmamış. "
                    "trading-executor şu an yalnızca manual_parallel ve paper "
                    "modlarında gerçek/sanal işlem üretebilir."
                )
            # Broker bağlı — yine de varsayılan adapter ExampleBroker
            # kendisi NotImplementedError atar. Buraya gelmemiz Faz 4'te.
            place_fn = getattr(self.broker, "place_order", None)
            if not callable(place_fn):
                raise NotImplementedError(
                    "Broker.place_order tanımlı değil — Faz 4 aktif değil."
                )
            # BrokerOrder'a köprü Faz 4'te yazılır.
            raise NotImplementedError(
                "Faz 4 aktif değil: broker.place_order köprüsü henüz yazılmadı."
            )

        # Bilinmeyen mod
        raise ValueError(f"Bilinmeyen mod: {mode!r}")


__all__ = [
    "VALID_ACTIONS",
    "VALID_ORDER_TYPES",
    "OrderRequest",
    "OrderValidation",
    "OrderManager",
]
