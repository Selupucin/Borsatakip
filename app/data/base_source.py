"""Soyut veri kaynağı arayüzü ve ortak veri yapıları.

Tüm veri kaynakları (``yfinance``, ``stooq``, ``alpha_vantage``,
``isyatirim``, ``tcmb`` vb.) bu modüldeki ``BaseSource`` sınıfından türer.
Böylece ``collector.py`` kaynakları homojen biçimde paralel sorgulayabilir.

Tasarım kuralları (data-collector agent §32):
- **Async-first:** Tüm ağ çağrıları ``async def``. Senkron lib'ler
  ``asyncio.to_thread`` ile sarmalanır.
- **Veri yapıları immutable:** ``OHLCVBar`` ve ``Quote`` ``frozen=True``
  dataclass; collector birden çok kaynaktan gelen sonuçları güvenle
  paralel toplar.
- **Para alanları ``Decimal``:** Float ile fiyat saklanmaz (doğruluk kaybı).
- **Hata hiyerarşisi:** ``SourceError`` taban; alt sınıflar
  (``SourceUnavailableError``, ``RateLimitError``) collector tarafında
  farklı politika ile ele alınır.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


# ---------------------------------------------------------------------------
# Hata sınıfları
# ---------------------------------------------------------------------------


class SourceError(Exception):
    """Veri kaynağı hatalarının tabanı.

    ``collector.py`` bu istisnayı yakalar, ``failed_requests`` sayacını
    artırır ve ``SOURCE_FAILURE_LIMIT`` (vars. 5) art arda yaşandığında
    kaynağı devre dışı bırakır.
    """


class SourceUnavailableError(SourceError):
    """Kaynak geçici olarak erişilemez (HTTP 5xx, timeout, DNS hatası).

    Retry + exponential back-off için uygun adaydır.
    """


class RateLimitError(SourceError):
    """Kaynak rate-limit döndü (HTTP 429 veya provider'a özel uyarı).

    Collector bu istisnayı gördüğünde aynı saniye içinde tekrar denemez;
    ``Retry-After`` veya kaynak içi semaforu beklemeyi tercih eder.
    """


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OHLCVBar:
    """Tek bir zaman dilimi için açılış/yüksek/düşük/kapanış/hacim."""

    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int


@dataclass(frozen=True, slots=True)
class Quote:
    """Anlık fiyat snapshot'ı.

    Birden çok kaynaktan toplanan ``Quote``'lar ``PriceComparator`` tarafından
    karşılaştırılır ve ağırlıklı ortalama ile ``verified_close`` üretilir.
    """

    ticker: str
    price: Decimal
    timestamp: datetime
    source: str
    volume: Optional[int] = None  # opsiyonel — bazı kaynaklar quote ile hacmi vermez


# ---------------------------------------------------------------------------
# BaseSource
# ---------------------------------------------------------------------------


class BaseSource(ABC):
    """Tüm veri kaynaklarının türediği soyut sınıf.

    Alt sınıflar şu sözleşmeyi tamamlar:
    - ``name``: kaynak tanımlayıcısı (``data_sources.name`` ile birebir
      eşleşmelidir — örn. ``"yfinance"``, ``"stooq"``, ``"isyatirim"``).
    - ``fetch_ohlcv``: belirli ticker için verilen aralıkta mum verisi.
    - ``fetch_quote``: anlık fiyat (canlı veya en son kapanış).
    - ``is_alive``: kaynak sağlık testi (varsayılan basit fetch denemesi).
    """

    name: str = ""  # alt sınıf override eder; boşsa ``__init__`` patlatır.

    def __init__(self) -> None:
        if not self.name:
            raise ValueError(
                f"{self.__class__.__name__}.name tanımlanmalı — "
                "data_sources tablosundaki adla eşleşmelidir."
            )

    # ---- Soyut metodlar -----------------------------------------------------

    @abstractmethod
    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        """``ticker`` için ``[start, end]`` aralığında ``interval`` mumları döndür.

        Interval konvansiyonu: ``1m``, ``5m``, ``15m``, ``1h``, ``1d``, ``1wk``,
        ``1mo``. Her kaynak desteklemediği interval için
        ``SourceError`` (NotImplemented mesajıyla) fırlatabilir.
        """

    @abstractmethod
    async def fetch_quote(self, ticker: str) -> Quote:
        """``ticker`` için anlık fiyat snapshot'ı döndür."""

    # ---- Yardımcı metodlar --------------------------------------------------

    async def is_alive(self) -> bool:
        """Kaynak sağlık testi.

        Varsayılan implementasyon iyi bilinen bir ticker (``AAPL``) ile
        ``fetch_quote`` dener. BIST-only kaynaklar (örn. İş Yatırım) bu metodu
        ``THYAO`` gibi bir tickerla override etmelidir.
        """
        try:
            await self.fetch_quote(self._health_check_ticker())
            return True
        except Exception:
            return False

    def _health_check_ticker(self) -> str:
        """is_alive() için kullanılacak ticker. Alt sınıflar override edebilir."""
        return "AAPL"


__all__ = [
    "BaseSource",
    "OHLCVBar",
    "Quote",
    "SourceError",
    "SourceUnavailableError",
    "RateLimitError",
]
