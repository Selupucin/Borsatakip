"""Auto Gate — onay kapısı mantığı (Faz 3 Batch 3).

Doküman §7.4 (Risk Tabanlı Otomasyon) ve §7.5 (Güvenlik) referans
alınmıştır.

``AutoGate`` üç girdiden bir karar üretir:

1. **Mod** (``TradingMode``): kullanıcının seçtiği işlem modu.
2. **Risk skoru** (``TradeRiskScore``): işleme özgü 0..100 skor.
3. **Safety durumu** (``SafetyEngine``): kill switch, günlük limit,
   circuit breaker.

Karar matrisi (özet):

================  ========================================  ===========================
Mod               Koşul                                     Çıktı (TradeDecision)
================  ========================================  ===========================
manual_parallel   her zaman                                 MANUAL_ONLY
paper             safety OK                                 EXECUTE_AUTO
semi_auto         broker yok                                BLOCKED (Faz 4 koruması)
semi_auto         broker var, safety OK                     REQUEST_CONFIRMATION
full_auto         broker yok                                BLOCKED (Faz 4 koruması)
full_auto         broker var, skor < threshold, safety OK   EXECUTE_AUTO
full_auto         broker var, skor >= threshold             REQUEST_CONFIRMATION
herhangi          safety NOT OK (kill / limit / breaker)    BLOCKED
================  ========================================  ===========================

**Faz 4 koruması:** ``semi_auto`` veya ``full_auto`` modu seçilmiş
ancak ``broker`` (BaseBroker adapter) bağlanmamışsa karar
**BLOCKED** olur; UI kullanıcıya "Bu mod için aracı kurum
entegrasyonu gerekli (Faz 4 — henüz aktif değil)" uyarısı gösterir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any, Optional

from loguru import logger

from app.trading.execution_modes import (
    MODE_REGISTRY,
    TradingMode,
    is_active_in_current_phase,
    requires_broker,
)
from app.trading.risk_scorer import TradeRiskScore, TradeRiskScorer


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class TradeDecision(str, Enum):
    """AutoGate çıktı kararı."""

    EXECUTE_AUTO = "execute_auto"
    """Tam otomatik gönderim — kullanıcı onayı aranmaz."""

    REQUEST_CONFIRMATION = "request_confirmation"
    """Kullanıcıdan açık onay iste (semi_auto her zaman; full_auto
    yalnızca skor eşiği aşarsa)."""

    MANUAL_ONLY = "manual_only"
    """``manual_parallel`` modu — bot yalnızca öneri üretir,
    kullanıcı kendi aracı kurum uygulamasında elle yapar."""

    BLOCKED = "blocked"
    """Güvenlik kuralları veya Faz 4 koruması engelledi."""


# ---------------------------------------------------------------------------
# Veri yapısı
# ---------------------------------------------------------------------------


@dataclass
class GateResult:
    """AutoGate karar çıktısı."""

    decision: TradeDecision
    risk_score: TradeRiskScore
    reasons: list[str]
    mode: TradingMode
    is_phase_active: bool = True
    broker_attached: bool = False
    safety_ok: bool = True
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class AutoGate:
    """Mod + risk skoru + safety kombinasyonu → karar.

    Parameters
    ----------
    risk_scorer:
        ``TradeRiskScorer`` örneği (bilgi: ``calculate()`` çağrılır
        eğer ``evaluate`` ham parametre alıyorsa; varsayılan akışta
        zaten hesaplanmış ``TradeRiskScore`` verilir).
    safety_engine:
        ``SafetyEngine`` örneği (``can_trade(account_id)`` çağrılır).
    broker:
        Bağlı ``BaseBroker`` örneği veya ``None``. Faz 4 koruması
        için ``None`` ise semi/full auto BLOCKED olur.
    """

    def __init__(
        self,
        risk_scorer: TradeRiskScorer,
        safety_engine: Any,
        broker: Optional[Any] = None,
    ) -> None:
        self.risk_scorer = risk_scorer
        self.safety_engine = safety_engine
        self.broker = broker

    # ----------------------------------------------------------- karar

    async def evaluate(
        self,
        mode: TradingMode,
        risk_score: TradeRiskScore,
        threshold: float,
        account_id: int,
        instrument_id: int,
        trade_value: Decimal,
    ) -> GateResult:
        """Kararı üret.

        Notes
        -----
        ``risk_score.requires_confirmation`` ``TradeRiskScorer.calculate``
        içinde zaten ayarlanır; burada ek olarak ``threshold`` ile bağımsız
        kontrol de yapılır (parametre uyuşmazlığına karşı savunma).
        """

        reasons: list[str] = []
        broker_attached = self.broker is not None
        phase_active = is_active_in_current_phase(mode)

        # 1) Safety — her modda öncelikli kontrol.
        safety_ok = True
        safety_reasons: list[str] = []
        try:
            can, why = await self.safety_engine.can_trade(account_id)
            safety_ok = bool(can)
            safety_reasons = list(why or [])
        except Exception as exc:  # noqa: BLE001
            # Safety motoru bir şekilde patladıysa konservatif: engelle.
            logger.error("SafetyEngine hatası — işlem engellendi: {}", exc)
            safety_ok = False
            safety_reasons = [f"SafetyEngine hatası: {exc}"]

        if not safety_ok:
            reasons.extend(safety_reasons)
            reasons.append("Safety motoru işlemi engelledi.")
            logger.warning(
                "AutoGate BLOCKED (safety): account={}, instrument={}, reasons={}",
                account_id,
                instrument_id,
                safety_reasons,
            )
            return GateResult(
                decision=TradeDecision.BLOCKED,
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
                is_phase_active=phase_active,
                broker_attached=broker_attached,
                safety_ok=False,
            )

        # 2) Mod bazlı karar.
        if mode == TradingMode.MANUAL_PARALLEL:
            reasons.append(
                "Manuel-eşli mod: bot yalnızca öneri üretir, "
                "kullanıcı kendi aracı kurum uygulamasında elle yapar."
            )
            return GateResult(
                decision=TradeDecision.MANUAL_ONLY,
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
                is_phase_active=phase_active,
                broker_attached=broker_attached,
                safety_ok=True,
            )

        if mode == TradingMode.PAPER:
            reasons.append("Paper mod: sanal portföyde otomatik uygulanır.")
            return GateResult(
                decision=TradeDecision.EXECUTE_AUTO,
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
                is_phase_active=phase_active,
                broker_attached=broker_attached,
                safety_ok=True,
            )

        # 3) Faz 4 koruması: semi/full auto için broker zorunlu.
        if requires_broker(mode) and not broker_attached:
            reasons.append(
                f"'{mode.value}' modu için aracı kurum bağlantısı gerekli "
                "(Faz 4 — henüz aktif değil)."
            )
            logger.warning(
                "AutoGate BLOCKED (Faz 4 koruması): mode={}, broker=None",
                mode.value,
            )
            return GateResult(
                decision=TradeDecision.BLOCKED,
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
                is_phase_active=False,
                broker_attached=False,
                safety_ok=True,
            )

        # 4) semi_auto → her zaman onay iste (skordan bağımsız).
        if mode == TradingMode.SEMI_AUTO:
            reasons.append(
                "Yarı-otomatik mod: her işlem için kullanıcı onayı zorunlu."
            )
            return GateResult(
                decision=TradeDecision.REQUEST_CONFIRMATION,
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
                is_phase_active=phase_active,
                broker_attached=broker_attached,
                safety_ok=True,
            )

        # 5) full_auto → skor < threshold ise otomatik; aksi halde onay.
        if mode == TradingMode.FULL_AUTO:
            exceeds = (
                risk_score.requires_confirmation
                or risk_score.score >= float(threshold)
            )
            if exceeds:
                reasons.append(
                    f"Risk skoru {risk_score.score:.1f} ≥ eşik {threshold:.1f}: "
                    "tam otomatik modda bile onay isteniyor."
                )
                reasons.extend(risk_score.reasons)
                return GateResult(
                    decision=TradeDecision.REQUEST_CONFIRMATION,
                    risk_score=risk_score,
                    reasons=reasons,
                    mode=mode,
                    is_phase_active=phase_active,
                    broker_attached=broker_attached,
                    safety_ok=True,
                )
            reasons.append(
                f"Risk skoru {risk_score.score:.1f} < eşik {threshold:.1f}: "
                "tam otomatik gönderim."
            )
            return GateResult(
                decision=TradeDecision.EXECUTE_AUTO,
                risk_score=risk_score,
                reasons=reasons,
                mode=mode,
                is_phase_active=phase_active,
                broker_attached=broker_attached,
                safety_ok=True,
            )

        # 6) Bilinmeyen mod (savunmacı fallback).
        reasons.append(f"Bilinmeyen mod: {mode!r} → engellendi.")
        return GateResult(
            decision=TradeDecision.BLOCKED,
            risk_score=risk_score,
            reasons=reasons,
            mode=mode,
            is_phase_active=False,
            broker_attached=broker_attached,
            safety_ok=False,
        )

    # ----------------------------------------------------------- yardımcı

    def describe_mode(self, mode: TradingMode) -> str:
        """UI tooltip için Türkçe mod açıklaması."""

        meta = MODE_REGISTRY.get(mode)
        return meta.description_tr if meta else "(bilinmeyen mod)"


__all__ = [
    "TradeDecision",
    "GateResult",
    "AutoGate",
]
