"""Veri toplama katmanı — async paralel çekim ve karşılaştırma.

Public API:
    BaseSource, OHLCVBar, Quote — soyut arayüz ve veri yapıları
    SourceError / SourceUnavailableError / RateLimitError — hata sınıfları
    DataCollector — async paralel toplayıcı
    PriceComparator, DiscrepancyRecord — karşılaştırma motoru
"""

from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    RateLimitError,
    SourceError,
    SourceUnavailableError,
)
from app.data.collector import DataCollector
from app.data.comparator import DiscrepancyRecord, PriceComparator

__all__ = [
    "BaseSource",
    "OHLCVBar",
    "Quote",
    "SourceError",
    "SourceUnavailableError",
    "RateLimitError",
    "DataCollector",
    "PriceComparator",
    "DiscrepancyRecord",
]
