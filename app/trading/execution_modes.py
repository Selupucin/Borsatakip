"""İşlem modları — Faz 3 Batch 3.

Doküman §7.3 (İşlem Modları) referans alınmıştır.

Sistem dört modda çalışabilir:

================  ===========================  =====
Mod               Davranış                     Faz
================  ===========================  =====
manual_parallel   Bot öneri üretir, kullanıcı  AKTİF
                  elle uygular, bot takip eder
paper             Sanal para, gerçek fiyat     AKTİF (Faz 3)
semi_auto         Bot emri hazırlar, kullanıcı Faz 4
                  tek tıkla onaylar
full_auto         Bot otonom emir (risk eşiği  Faz 4
                  aşılırsa onay)
================  ===========================  =====

**Faz 4 KORUMASI:** ``semi_auto`` ve ``full_auto`` modları kod
altyapısı olarak hazırdır, ancak gerçek aracı kurum entegrasyonu
yapılmadığı için ``order_manager.execute()`` bu modlarda
``NotImplementedError("Faz 4 aktif değil")`` atar.

Tasarım notları:
- ``TradingMode`` ``str, Enum`` — DB CHECK constraint değerleriyle
  birebir eşleşir (``'manual_parallel' | 'semi_auto' | 'full_auto' | 'paper'``).
- ``MODE_REGISTRY`` her mod için davranışsal meta-veri taşır
  (broker gerekir mi, otomatik emir gönderir mi, kullanıcı her
  işlemi onaylar mı, Türkçe açıklama).
- ``is_active_in_current_phase`` UI ve ``AutoGate`` tarafından kullanılır:
  Faz 4 aktif edilene kadar yalnızca ``manual_parallel`` ve ``paper``
  modları gerçek anlamda işlevseldir.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------


class TradingMode(str, Enum):
    """Sistem işlem modu enum'u — DB CHECK constraint ile birebir eşleşir."""

    MANUAL_PARALLEL = "manual_parallel"
    """ŞU ANKİ BAŞLANGIÇ MODU — bot öneri, kullanıcı elle uygular."""

    SEMI_AUTO = "semi_auto"
    """Faz 4 — bot emri hazırlar, kullanıcı tek tıkla onaylar."""

    FULL_AUTO = "full_auto"
    """Faz 4 — bot otonom emir, risk eşiği aşılırsa onay ister."""

    PAPER = "paper"
    """Sanal para, gerçek fiyat — risksiz test modu."""


# ---------------------------------------------------------------------------
# Mod meta-verisi
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModeDescription:
    """Bir işlem modunun davranışsal meta-verisi.

    Attributes
    ----------
    mode:
        İlgili ``TradingMode`` değeri.
    requires_broker:
        Modun çalışması için gerçek aracı kurum bağlantısı (BaseBroker
        adapter'ı) gerekli mi?
    automated_execution:
        Bot bu modda gerçek emri otonom (kullanıcı dokunmadan) gönderir
        mi? ``manual_parallel`` ve ``semi_auto`` için ``False``;
        ``full_auto`` için ``True``; ``paper`` için ``True`` (sanal).
    user_confirms_each_trade:
        Her işlem için kullanıcının açık onayı gerekir mi?
        ``manual_parallel`` zaten elle yapıldığı için ``True``;
        ``semi_auto`` her işlemde onay ister; ``full_auto`` ve ``paper``
        için varsayılan ``False`` (yalnızca risk eşiği aşılırsa onay).
    description_tr:
        Türkçe kullanıcıya gösterilecek açıklama.
    """

    mode: TradingMode
    requires_broker: bool
    automated_execution: bool
    user_confirms_each_trade: bool
    description_tr: str


#: Dört modun davranış matrisi — UI ve AutoGate bu sözlüğü okur.
MODE_REGISTRY: dict[TradingMode, ModeDescription] = {
    TradingMode.MANUAL_PARALLEL: ModeDescription(
        mode=TradingMode.MANUAL_PARALLEL,
        requires_broker=False,
        automated_execution=False,
        user_confirms_each_trade=True,
        description_tr=(
            "Bot öneri üretir, kullanıcı kendi aracı kurum uygulamasında "
            "elle uygular ve işaretler. Bot portföyü ve P&L'i takip eder."
        ),
    ),
    TradingMode.SEMI_AUTO: ModeDescription(
        mode=TradingMode.SEMI_AUTO,
        requires_broker=True,
        automated_execution=False,
        user_confirms_each_trade=True,
        description_tr=(
            "Bot emri hazırlar, kullanıcı tek tıkla onaylar; "
            "emri bot gönderir. (Faz 4 — şu an aktif değil.)"
        ),
    ),
    TradingMode.FULL_AUTO: ModeDescription(
        mode=TradingMode.FULL_AUTO,
        requires_broker=True,
        automated_execution=True,
        user_confirms_each_trade=False,
        description_tr=(
            "Bot kullanıcı onayı olmadan emir gönderir; risk eşiği "
            "aşılırsa otomatik durur ve onay ister. (Faz 4 — şu an aktif değil.)"
        ),
    ),
    TradingMode.PAPER: ModeDescription(
        mode=TradingMode.PAPER,
        requires_broker=False,
        automated_execution=True,
        user_confirms_each_trade=False,
        description_tr=(
            "Sanal para, gerçek fiyat. Hiç gerçek emir gönderilmez; "
            "bot önerilerinin teorik performansı izlenir."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Faz koruması
# ---------------------------------------------------------------------------


def is_active_in_current_phase(mode: TradingMode) -> bool:
    """Bu mod **şu anki** geliştirme fazında aktif mi?

    Faz 4 aktif edilene (gerçek aracı kurum adapter'ı yazılana) kadar
    ``semi_auto`` ve ``full_auto`` modları gerçek emir gönderemediği
    için ``False`` döner. UI bu fonksiyonun çıktısıyla "Yakında"
    rozetini gösterir; ``AutoGate`` aynı kontrolü emir öncesinde yapar.

    Returns
    -------
    bool
        ``True`` ise mod tam işlevsel; ``False`` ise gerçek emir
        gönderme noktasında ``NotImplementedError`` atılır.
    """

    return mode in (TradingMode.MANUAL_PARALLEL, TradingMode.PAPER)


def requires_broker(mode: TradingMode) -> bool:
    """``MODE_REGISTRY`` üzerinden hızlı erişim (UI uyarısı için)."""

    return MODE_REGISTRY[mode].requires_broker


__all__ = [
    "TradingMode",
    "ModeDescription",
    "MODE_REGISTRY",
    "is_active_in_current_phase",
    "requires_broker",
]
