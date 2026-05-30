"""Investing.com veri kaynağı (teknik özet + analist notları).

# ============================================================
# KAYNAK: investing.com
# YASAL:  Investing.com ToS gereği veri ÇİZGİSEL/MANUEL kullanım
#         içindir; scraping ile toplu, otomatik veya ticari kullanım
#         AÇIKÇA YASAKLANMIŞTIR. Bu modül KİŞİSEL KULLANIM (tek
#         kullanıcı, kendi kararı için bilgilendirme) varsayımı
#         altında SINIRLI scrape yapar. Üçüncü kişiye yayma /
#         ticari ürün haline getirme YASAK.
# RATE:   ÇOK SIKI — 3 req / dk (20 sn min interval), tek paralel.
#         Engellenme riski yüksek; Playwright varsa headless tarayıcı
#         tercih edilir (Cloudflare/JS challenge bypass için).
# robots.txt: /charts/, /technical/, /equities/ gibi yollar çoğu
#             zaman crawl'a izinli görünür; yine de ToS'un üstünde
#             olduğu varsayılarak ÇOK az istek atılır.
# ============================================================

Mimari:
- Playwright async API mevcutsa headless Chromium ile sayfa render edilir,
  DOM seçicileri (text/CSS) ile özet (al/sat/nötr) ve analist görüşleri
  çıkarılır.
- Playwright yoksa ``httpx`` fallback ile sayfa indirilir;
  BeautifulSoup parse edilir (Cloudflare bloğu zaman zaman dönebilir;
  bu durumda ``SourceUnavailableError``).
- ``fetch_ohlcv``/``fetch_quote`` ToS gereği desteklenmez — başka
  birincil kaynaklar (yfinance/stooq/isyatirim) yeterli.
"""

from __future__ import annotations

import asyncio
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from app.data.base_source import (
    BaseSource,
    OHLCVBar,
    Quote,
    SourceError,
    SourceUnavailableError,
)


# ---------------------------------------------------------------------------
# Lazy import: Playwright (opsiyonel — tercih edilen)
# ---------------------------------------------------------------------------

try:  # pragma: no cover - import guard
    from playwright.async_api import async_playwright  # type: ignore
    PW_AVAILABLE = True
except Exception:  # pragma: no cover
    async_playwright = None  # type: ignore
    PW_AVAILABLE = False


# Fallback: httpx + bs4
try:  # pragma: no cover
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

try:  # pragma: no cover
    from bs4 import BeautifulSoup  # type: ignore
except Exception:  # pragma: no cover
    BeautifulSoup = None  # type: ignore


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
class InvSummary:
    """Investing.com teknik özet — TVSummary ile birebir paralel şema."""

    recommendation: str  # STRONG_BUY | BUY | NEUTRAL | SELL | STRONG_SELL
    oscillators_summary: str
    ma_summary: str
    support_levels: tuple[float, ...] = field(default_factory=tuple)
    resistance_levels: tuple[float, ...] = field(default_factory=tuple)


_REC_VOCAB = {
    "STRONG_BUY": "STRONG_BUY",
    "GÜÇLÜ_AL": "STRONG_BUY",
    "GÜÇLÜ AL": "STRONG_BUY",
    "BUY": "BUY",
    "AL": "BUY",
    "NEUTRAL": "NEUTRAL",
    "NÖTR": "NEUTRAL",
    "TARAFSIZ": "NEUTRAL",
    "SELL": "SELL",
    "SAT": "SELL",
    "STRONG_SELL": "STRONG_SELL",
    "GÜÇLÜ_SAT": "STRONG_SELL",
    "GÜÇLÜ SAT": "STRONG_SELL",
}


def _normalize_rec(raw: str | None) -> str:
    if not raw:
        return "NEUTRAL"
    s = str(raw).strip().upper().replace(" ", "_")
    return _REC_VOCAB.get(s, _REC_VOCAB.get(s.replace("_", " "), "NEUTRAL"))


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------


class InvestingSource(BaseSource):
    name = "investing"

    # 3 req/dk -> 20s min interval
    _MIN_INTERVAL_S = 20.0
    _MAX_RETRY = 2
    _BACKOFF_BASE = 5.0
    _semaphore = asyncio.Semaphore(1)

    _BASE_URL = "https://www.investing.com"

    def __init__(self) -> None:
        super().__init__()
        self._last_call_ts: float = 0.0
        if not PW_AVAILABLE and httpx is None:
            logger.warning(
                "investing: ne playwright ne httpx kurulu — kaynak pasif"
            )

    # ---- BaseSource: fiyat desteklenmez ------------------------------------

    async def fetch_ohlcv(
        self,
        ticker: str,
        start: datetime,
        end: datetime,
        interval: str = "1d",
    ) -> list[OHLCVBar]:
        raise SourceError(
            "investing: fiyat verisi ToS gereği scrape edilmez — "
            "yfinance/stooq/isyatirim kullan"
        )

    async def fetch_quote(self, ticker: str) -> Quote:
        raise SourceError(
            "investing: quote ToS gereği scrape edilmez"
        )

    async def is_alive(self) -> bool:
        # Çok sıkı rate-limit'te is_alive maliyetli; basit "True if any client
        # available" testi yeterli.
        return PW_AVAILABLE or (httpx is not None)

    # ---- Public: teknik özet ------------------------------------------------

    async def fetch_technical_summary(
        self, ticker: str, exchange: str
    ) -> InvSummary:
        """Investing.com "Teknikal" sekmesinden özet kararı çıkar.

        Sayfa URL şeması Investing tarafından sürekli değişebilir — bu modül
        sembol -> equities yolunu otomatik tahmin etmez; ``ticker`` parametresi
        Investing slug'ı olabilir (örn. ``thyao``) veya ``BIST:THYAO`` formatı.
        Bilinmeyen sembol için ``SourceError``.
        """
        slug = self._guess_slug(ticker, exchange)
        url = f"{self._BASE_URL}/equities/{slug}-technical"

        html = await self._fetch_html(url)
        if not html:
            raise SourceUnavailableError(f"investing: {url} html alınamadı")

        return self._parse_technical(html)

    # ---- Public: analist görüşleri -----------------------------------------

    async def fetch_analyst_ratings(self, ticker: str) -> dict[str, Any]:
        """Investing.com "Analist Tahminleri" sayfasından özet metrikler.

        Dönüş anahtarları (bulunabilenler):
            - target_high, target_low, target_mean
            - analyst_count
            - recommendation_avg (str: STRONG_BUY/BUY/HOLD/SELL/STRONG_SELL)

        Bulunamadığında boş dict döner — SourceError fırlatmaz (Faz 2'de
        sentiment / recommender bu eksikliği tolere eder).
        """
        slug = self._guess_slug(ticker, "")
        url = f"{self._BASE_URL}/equities/{slug}-analyst-estimates"
        html = await self._fetch_html(url)
        if not html:
            return {}

        out: dict[str, Any] = {}
        # Hedef fiyat aralığı — Investing sayfası farklı dillerde gelir.
        for label, key in (
            (r"target\s*high", "target_high"),
            (r"target\s*low", "target_low"),
            (r"target\s*mean|average\s*target", "target_mean"),
            (r"number\s*of\s*analysts|analysts?\s*count", "analyst_count"),
        ):
            m = re.search(
                rf"{label}[^\d\-]{{0,60}}([\-]?\d[\d,\.]*)",
                html,
                flags=re.IGNORECASE,
            )
            if m:
                try:
                    val = float(m.group(1).replace(",", ""))
                    out[key] = val
                except ValueError:
                    pass

        # Recommendation avg
        rec_m = re.search(
            r"(STRONG[_ ]BUY|BUY|HOLD|NEUTRAL|SELL|STRONG[_ ]SELL)",
            html,
            flags=re.IGNORECASE,
        )
        if rec_m:
            out["recommendation_avg"] = _normalize_rec(rec_m.group(1))

        return out

    # ---- Internal ----------------------------------------------------------

    @staticmethod
    def _guess_slug(ticker: str, exchange: str) -> str:
        """Investing slug heuristic.

        - ``BIST:THYAO`` -> ``thyao``
        - ``THYAO.IS`` -> ``thyao``
        - sade ``AAPL``    -> ``apple-computer-inc`` gibi slug'ları
          önceden BİLEMEYİZ — bu durumda ham ticker döner ve URL büyük
          olasılıkla 404; çağıran SourceUnavailableError yakalar.
        """
        t = ticker.strip().lower()
        if ":" in t:
            t = t.split(":", 1)[1]
        if t.endswith(".is"):
            t = t[:-3]
        return t

    async def _fetch_html(self, url: str) -> str:
        await self._respect_rate()
        if PW_AVAILABLE:
            try:
                return await self._fetch_with_playwright(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("investing: playwright hata — httpx fallback: {}", exc)
        if httpx is None:
            raise SourceUnavailableError("investing: ne playwright ne httpx çalışıyor")
        return await self._fetch_with_httpx(url)

    async def _respect_rate(self) -> None:
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            elapsed = now - self._last_call_ts
            if elapsed < self._MIN_INTERVAL_S:
                await asyncio.sleep(self._MIN_INTERVAL_S - elapsed)
            self._last_call_ts = asyncio.get_event_loop().time()

    async def _fetch_with_playwright(self, url: str) -> str:  # pragma: no cover - external IO
        last_exc: Exception | None = None
        for attempt in range(self._MAX_RETRY):
            try:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    context = await browser.new_context(user_agent=_pick_ua())
                    page = await context.new_page()
                    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                    # Cloudflare/JS render için kısa bekle
                    await page.wait_for_timeout(1500)
                    html = await page.content()
                    await browser.close()
                return html
            except Exception as exc:
                last_exc = exc
                wait = self._BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "investing pw deneme {}/{} başarısız: {} — {}s sonra",
                    attempt + 1,
                    self._MAX_RETRY,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
        raise SourceUnavailableError(f"investing pw retry tükendi: {last_exc}")

    async def _fetch_with_httpx(self, url: str) -> str:  # pragma: no cover - external IO
        last_exc: Exception | None = None
        headers = {
            "User-Agent": _pick_ua(),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,tr;q=0.8",
        }
        for attempt in range(self._MAX_RETRY):
            try:
                async with httpx.AsyncClient(
                    timeout=30.0, headers=headers, follow_redirects=True
                ) as client:
                    resp = await client.get(url)
                if resp.status_code in (403, 503):
                    raise SourceUnavailableError(
                        f"investing: HTTP {resp.status_code} (büyük olasılıkla Cloudflare)"
                    )
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                last_exc = exc
                wait = self._BACKOFF_BASE * (2**attempt)
                logger.warning(
                    "investing httpx deneme {}/{} başarısız: {} — {}s sonra",
                    attempt + 1,
                    self._MAX_RETRY,
                    exc,
                    wait,
                )
                await asyncio.sleep(wait)
        raise SourceUnavailableError(f"investing httpx retry tükendi: {last_exc}")

    @staticmethod
    def _parse_technical(html: str) -> InvSummary:
        # BeautifulSoup varsa daha güvenli; yoksa regex fallback.
        rec_text = osc_text = ma_text = None
        supports: list[float] = []
        resistances: list[float] = []

        if BeautifulSoup is not None:
            soup = BeautifulSoup(html, "html.parser")
            # Investing teknik sayfasında "Summary" / "Özet" başlığı altında
            # span class="techSummaryTbl" benzeri elementler vardır. Sınıf
            # adları değişebileceği için text-based fallback.
            text = soup.get_text("\n", strip=True)
        else:
            text = re.sub(r"<[^>]+>", "\n", html)

        # Heuristic: ilk eşleşen Summary kararı
        for label_re, target in (
            (r"(?:Summary|Özet)[^A-Za-z]{0,20}", "rec"),
            (r"Moving\s*Averages|Hareketli\s*Ortalamalar", "ma"),
            (r"Oscillators|Osilatörler", "osc"),
        ):
            m = re.search(
                label_re
                + r"[^A-Za-z]{0,40}(STRONG[_ ]BUY|GÜÇLÜ[_ ]AL|BUY|AL|NEUTRAL|NÖTR|TARAFSIZ|SELL|SAT|STRONG[_ ]SELL|GÜÇLÜ[_ ]SAT)",
                text,
                flags=re.IGNORECASE,
            )
            if m:
                val = m.group(1)
                if target == "rec":
                    rec_text = val
                elif target == "ma":
                    ma_text = val
                elif target == "osc":
                    osc_text = val

        # Pivot / destek-direnç sayıları — basit regex
        for m in re.finditer(r"S[123][^\d\-]{0,8}([\-]?\d[\d,\.]*)", text):
            try:
                supports.append(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
        for m in re.finditer(r"R[123][^\d\-]{0,8}([\-]?\d[\d,\.]*)", text):
            try:
                resistances.append(float(m.group(1).replace(",", "")))
            except ValueError:
                pass

        return InvSummary(
            recommendation=_normalize_rec(rec_text),
            oscillators_summary=_normalize_rec(osc_text),
            ma_summary=_normalize_rec(ma_text),
            support_levels=tuple(sorted(set(supports))),
            resistance_levels=tuple(sorted(set(resistances))),
        )


__all__ = ["InvestingSource", "InvSummary"]
