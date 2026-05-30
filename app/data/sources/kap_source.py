"""KAP (Kamuyu Aydınlatma Platformu) veri kaynağı.

# ============================================================
# KAYNAK: kap
# YASAL:  KAP verisi kamuya açık olarak sunulan resmi BIST bildirim
#         platformudur. Kişisel inceleme/karar destek için kullanım
#         serbesttir; ToS gereği ticari yeniden dağıtım, otomatik
#         toplu indirme veya ücretli ürün haline getirme YASAK.
# RATE:   ÇOK SIKI — 1 req / sn. KAP scraping engellemesi yok ama
#         agresif istek atmamak hem etik hem dayanıklılık açısından
#         hayati.
# robots.txt: https://www.kap.org.tr/robots.txt — public yollar açık;
#             yine de bekleme süresi uygulanır.
# ============================================================

KAP'ın public REST endpoint'leri (web UI'nin kullandığı JSON çağrıları)
``httpx`` ile async olarak çağrılır. KAP zaman zaman endpoint şemasını
değiştirebileceği için cevap çözme esnek tutuldu (anahtar isimleri için
çoklu aday).

``BaseSource`` sözleşmesi quote/ohlcv zorunlu kıldığı için bu metodlar
``SourceError`` ile reddeder — KAP fiyat kaynağı değildir; bildirim ve
finansal tablo kaynağıdır.

Public API'leri:
- ``fetch_disclosures(ticker=None, since=None)`` — son özel durum
  bildirimleri (KAP açıklamaları).
- ``fetch_company_financials(ticker)``           — şirket için en son
  finansal özet (bilanço/gelir tablosu anahtar metrikleri).
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from loguru import logger

from app.data.base_source import BaseSource, OHLCVBar, Quote, SourceError, SourceUnavailableError


try:  # pragma: no cover - import guard
    import httpx  # type: ignore
except Exception as exc:  # pragma: no cover
    httpx = None  # type: ignore
    _IMPORT_ERROR: Exception | None = exc
else:
    _IMPORT_ERROR = None


# ---------------------------------------------------------------------------
# Endpoint sabitleri
# ---------------------------------------------------------------------------

_BASE_URL = "https://www.kap.org.tr"
# KAP'ın web UI'nin kullandığı public arama endpoint'i. Şema değişebilir.
_DISCLOSURES_PATH = "/tr/api/disclosures"
# Şirket finansal özet (mevcut endpoint isimleri stabil değil — try chain).
_FINANCIALS_PATHS = [
    "/tr/api/company/financials/{ticker}",
    "/tr/api/financial-tables/{ticker}",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]


def _pick_ua() -> str:
    return random.choice(_USER_AGENTS)


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class KapDisclosure:
    """KAP özel durum açıklaması (tek satır)."""

    ticker: str  # bildirimin ait olduğu hisse kodu (boş olabilirse "" — şirket bildirimi)
    title: str
    url: str
    published_at: datetime
    subject: Optional[str] = None  # bildirim özeti / başlık altı
    category: Optional[str] = None  # KAP kategorisi (ÖZE, MKK, vb.)


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class KapSource(BaseSource):
    name = "kap"

    # 1 req/sn — ToS uyumlu sıkı limit
    _MIN_INTERVAL_S = 1.0
    _MAX_RETRY = 2
    _BACKOFF_BASE = 2.0
    _semaphore = asyncio.Semaphore(1)

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        if httpx is None:
            logger.warning(
                "httpx import edilemedi: {}. KapSource çağrıları "
                "SourceUnavailableError fırlatacak.",
                _IMPORT_ERROR,
            )

    # ---- BaseSource sözleşmesi (KAP fiyat değil) ---------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        raise SourceError("KAP fiyat kaynağı değil — bildirim ve finansal tablo için")

    async def fetch_quote(self, ticker: str) -> Quote:
        raise SourceError("KAP fiyat kaynağı değil — bildirim ve finansal tablo için")

    async def is_alive(self) -> bool:
        """Public disclosures endpoint'ine 1 küçük çağrı."""
        if httpx is None:
            return False
        try:
            await self._call(_DISCLOSURES_PATH, params={"limit": 1})
            return True
        except Exception:
            return False

    # ---- Public: disclosures -----------------------------------------------

    async def fetch_disclosures(
        self, ticker: str | None = None, since: datetime | None = None
    ) -> list[KapDisclosure]:
        """KAP özel durum açıklamalarını çek.

        Parametreler:
            ticker: belirtilirse sadece o hisseye ait bildirimler
                    (KAP API ``ticker`` parametresini destekler;
                    desteklemediği sürümlerde client-side filtre yapılır).
            since:  ``published_at >= since`` filtresi.
        """
        if httpx is None:
            raise SourceUnavailableError("httpx kurulu değil")

        params: dict[str, Any] = {"limit": 200}
        if ticker:
            params["ticker"] = ticker.strip().upper()
        if since:
            params["fromDate"] = since.strftime("%Y-%m-%d")

        try:
            data = await self._call(_DISCLOSURES_PATH, params=params)
        except SourceUnavailableError:
            raise
        except SourceError:
            raise

        raw_items = self._extract_list(data)

        out: list[KapDisclosure] = []
        for raw in raw_items:
            try:
                item = self._raw_to_disclosure(raw)
            except Exception as exc:  # noqa: BLE001
                logger.debug("kap: bildirim atlandı: {}", exc)
                continue

            if ticker and item.ticker and item.ticker.upper() != ticker.strip().upper():
                continue
            if since and item.published_at < since:
                continue
            out.append(item)

        out.sort(key=lambda d: d.published_at, reverse=True)
        return out

    # ---- Public: financials ------------------------------------------------

    async def fetch_company_financials(self, ticker: str) -> dict | None:
        """Şirket finansal özet metriklerini döndür.

        Dönüş: raw dict (KAP yapısı). Endpoint mevcut değilse / 404 olursa
        ``None`` döner — çağıran fallback yapabilir.

        Faz 2 minimum: net satış, brüt kâr, net dönem kârı, özkaynaklar,
        toplam aktifler gibi anahtarlar fundamental.py tarafında parse edilir.
        Burada yalnızca veriyi ileri taşırız.
        """
        if httpx is None:
            raise SourceUnavailableError("httpx kurulu değil")

        sym = ticker.strip().upper()
        last_exc: Exception | None = None
        for path in _FINANCIALS_PATHS:
            url_path = path.format(ticker=sym)
            try:
                return await self._call(url_path, params={})
            except SourceError as exc:
                last_exc = exc
                continue
            except SourceUnavailableError as exc:
                last_exc = exc
                continue
        logger.warning(
            "kap: {} için finansal endpoint bulunamadı (son hata: {})", sym, last_exc
        )
        return None

    # ---- Internal ----------------------------------------------------------

    @staticmethod
    def _extract_list(data: Any) -> list[dict]:
        """KAP cevabı bazen ``{"items":[...]}``, bazen düz liste döner."""
        if isinstance(data, list):
            return [d for d in data if isinstance(d, dict)]
        if isinstance(data, dict):
            for key in ("items", "data", "result", "disclosures"):
                v = data.get(key)
                if isinstance(v, list):
                    return [d for d in v if isinstance(d, dict)]
        return []

    @staticmethod
    def _raw_to_disclosure(raw: dict) -> KapDisclosure:
        # Anahtar adları KAP sürümleri arasında değişebilir — çoklu aday.
        title = (
            raw.get("title")
            or raw.get("subject")
            or raw.get("baslik")
            or ""
        )
        title = str(title).strip()
        if not title:
            raise ValueError("title boş")

        link = (
            raw.get("url")
            or raw.get("link")
            or raw.get("disclosureUrl")
        )
        if link and not str(link).startswith("http"):
            link = f"{_BASE_URL}{link if str(link).startswith('/') else '/' + link}"
        if not link:
            # fallback — bildirim id'si varsa kanonik link kur
            did = raw.get("disclosureIndex") or raw.get("id")
            link = f"{_BASE_URL}/tr/Bildirim/{did}" if did else ""
        if not link:
            raise ValueError("url üretilemedi")

        published_at = KapSource._parse_ts(
            raw.get("publishDate")
            or raw.get("published_at")
            or raw.get("date")
            or raw.get("kapPublishDate")
        )

        ticker = (
            raw.get("ticker")
            or raw.get("stockCode")
            or raw.get("companyCode")
            or ""
        )
        ticker = str(ticker).upper().strip()

        subject = raw.get("summary") or raw.get("subject") or raw.get("ozet")
        if subject:
            subject = str(subject).strip()

        category = raw.get("category") or raw.get("disclosureClass") or raw.get("type")
        if category:
            category = str(category).strip()

        return KapDisclosure(
            ticker=ticker,
            title=title,
            url=str(link),
            published_at=published_at,
            subject=subject,
            category=category,
        )

    @staticmethod
    def _parse_ts(raw) -> datetime:
        if raw is None:
            return datetime.now(tz=timezone.utc)
        if isinstance(raw, (int, float)):
            # ms ya da s — basit heuristik
            ts = float(raw) / 1000.0 if raw > 10_000_000_000 else float(raw)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        s = str(raw).strip()
        # Yaygın KAP formatları: "2026-05-27 14:32:00", "27.05.2026 14:32", ISO
        for fmt in (
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d.%m.%Y %H:%M:%S",
            "%d.%m.%Y %H:%M",
            "%d.%m.%Y",
            "%d-%m-%Y %H:%M",
            "%d-%m-%Y",
        ):
            try:
                return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return datetime.now(tz=timezone.utc)

    async def _call(self, path: str, params: dict[str, Any]) -> Any:
        if httpx is None:
            raise SourceUnavailableError("httpx kurulu değil")

        url = f"{_BASE_URL}{path}"
        headers = {
            "User-Agent": _pick_ua(),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "tr-TR,tr;q=0.9,en;q=0.8",
        }

        last_exc: Exception | None = None
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)

            for attempt in range(self._MAX_RETRY):
                try:
                    async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                        resp = await client.get(url, params=params)
                    self._last_call_ts = asyncio.get_event_loop().time()
                    if resp.status_code == 404:
                        raise SourceError(f"kap: 404 ({path})")
                    resp.raise_for_status()
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise SourceError(f"kap: JSON parse hatası ({path}): {exc}") from exc
                except SourceError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    wait = self._BACKOFF_BASE * (2**attempt)
                    logger.warning(
                        "kap deneme {}/{} başarısız ({}): {} — {}s sonra",
                        attempt + 1,
                        self._MAX_RETRY,
                        path,
                        exc,
                        wait,
                    )
                    await asyncio.sleep(wait)
                    self._last_call_ts = asyncio.get_event_loop().time()
        raise SourceUnavailableError(f"kap retry tükendi ({path}): {last_exc}")


__all__ = ["KapSource", "KapDisclosure"]
