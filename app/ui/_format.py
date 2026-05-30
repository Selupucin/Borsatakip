"""Türkçe sayı/para formatlama yardımcıları.

Tüm UI'da para birimleri ve oranlar `1.000.000,00` formatında gösterilmelidir
(nokta = binlik ayracı, virgül = ondalık ayracı). Python'un f-string `{:,.2f}`
sadece Amerikan formatı (virgül binlik, nokta ondalık) üretir; bu modül onu
TR locale'e çevirir.

Kullanım::

    from app.ui._format import fmt_money, fmt_pct, fmt_int

    fmt_money(15000.5)            # '15.000,50'
    fmt_money(1234567.89, "TRY")  # '1.234.567,89 ₺'
    fmt_money(98.5, "USD")        # '$98,50'
    fmt_pct(12.345)               # '%12,35'
    fmt_int(1234567)              # '1.234.567'
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional, Union

Number = Union[int, float, Decimal, None]

CURRENCY_SYMBOLS: dict[str, tuple[str, str]] = {
    # currency_code -> (prefix, suffix)
    "TRY": ("", " ₺"),
    "USD": ("$", ""),
    "EUR": ("€", ""),
    "GBP": ("£", ""),
}


def _to_float(value: Number) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        try:
            return float(value)
        except (ValueError, OverflowError):
            return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _us_to_tr(us_formatted: str) -> str:
    """`1,234,567.89` → `1.234.567,89` (US → TR locale)."""
    # Önce comma'ları placeholder'a çevir, sonra noktayı virgüle, sonra placeholder'ı noktaya
    return us_formatted.replace(",", "").replace(".", ",").replace("", ".")


def fmt_money(value: Number, currency: Optional[str] = None, decimals: int = 2) -> str:
    """Para birimi formatla. None → '—'.

    Args:
        value: Sayı (Decimal/float/int kabul edilir)
        currency: 'TRY'/'USD'/'EUR'/... Verilirse sembol eklenir
        decimals: Ondalık basamak sayısı (default 2)
    """
    v = _to_float(value)
    if v is None:
        return "—"
    formatted = _us_to_tr(f"{v:,.{decimals}f}")
    if currency:
        prefix, suffix = CURRENCY_SYMBOLS.get(currency.upper(), ("", f" {currency}"))
        return f"{prefix}{formatted}{suffix}"
    return formatted


def fmt_pct(value: Number, decimals: int = 2, with_sign: bool = False) -> str:
    """Yüzde formatla: `12.345` → `%12,35`. None → '—'.

    with_sign=True ise pozitif değerler önüne '+' koyar.
    """
    v = _to_float(value)
    if v is None:
        return "—"
    sign = "+" if with_sign and v > 0 else ""
    formatted = _us_to_tr(f"{abs(v):,.{decimals}f}")
    if v < 0:
        sign = "-"
    return f"%{sign}{formatted}" if not (with_sign and v > 0) else f"{sign}%{formatted}"


def fmt_int(value: Number) -> str:
    """Tam sayı formatla: `1234567` → `1.234.567`. None → '—'."""
    v = _to_float(value)
    if v is None:
        return "—"
    return _us_to_tr(f"{int(v):,d}")


def fmt_qty(value: Number, max_decimals: int = 4) -> str:
    """Adet formatla — tam sayıysa ondalık göstermez. None → '—'.

    Examples: 100 → '100', 12.5 → '12,50', 0.0001 → '0,0001'.
    """
    v = _to_float(value)
    if v is None:
        return "—"
    # Tam sayı kontrolü
    if v == int(v):
        return _us_to_tr(f"{int(v):,d}")
    return _us_to_tr(f"{v:,.{max_decimals}f}".rstrip("0").rstrip(","))


def fmt_volume(value: Number) -> str:
    """Hacim formatla — büyük sayıları K/M/B kısaltır.

    Examples: 1500 → '1,5K', 1_500_000 → '1,5M', 1_500_000_000 → '1,5B'.
    """
    v = _to_float(value)
    if v is None:
        return "—"
    abs_v = abs(v)
    if abs_v >= 1_000_000_000:
        return _us_to_tr(f"{v / 1_000_000_000:.1f}") + "B"
    if abs_v >= 1_000_000:
        return _us_to_tr(f"{v / 1_000_000:.1f}") + "M"
    if abs_v >= 1_000:
        return _us_to_tr(f"{v / 1_000:.1f}") + "K"
    return fmt_int(v)


def apply_tr_locale(spinbox) -> None:
    """``QDoubleSpinBox`` / ``QSpinBox`` widget'ına TR locale uygula.

    Qt'nin built-in lokal sistemini kullanarak 1.000.000,50 formatında
    otomatik gruplama (nokta = binlik) ve virgül ondalık ayracı sağlar.

    PySide6 lazy import — bu modül tek başına test edilebilsin (Qt yokken).
    """
    try:
        from PySide6.QtCore import QLocale  # noqa: WPS433
    except ImportError:
        return

    tr_locale = QLocale(QLocale.Language.Turkish, QLocale.Country.Turkey)
    try:
        spinbox.setLocale(tr_locale)
    except Exception:  # noqa: BLE001
        return
    # Binlik ayracını göster (varsayılan kapalı)
    try:
        spinbox.setGroupSeparatorShown(True)
    except Exception:  # noqa: BLE001
        pass


__all__ = [
    "fmt_money",
    "fmt_pct",
    "fmt_int",
    "fmt_qty",
    "fmt_volume",
    "apply_tr_locale",
    "CURRENCY_SYMBOLS",
]
