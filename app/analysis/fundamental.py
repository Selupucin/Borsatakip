"""Temel analiz (fundamental) verileri — F/K, PD/DD, büyüme, sektör.

Doküman §3.4 (veri kaynakları: Finviz, İş Yatırım, KAP), §5.1 (girdi
sinyalleri — temel analiz %20 ağırlığı kısa vadede; §5.2'de orta vadede
%35, uzun vadede %50) ve agent kuralları referans alınarak yazılmıştır.

Tasarım kuralları:
- ``FundamentalSnapshot`` paralel kaynaklardan toplanan tek hisse "anlık
  görüntüsü". Sources opsiyonel — collector tarafı bir veya birden çok
  kaynak (Finviz, İş Yatırım) verisini birleştirip bu snapshot'ı üretir.
- ``score(snapshot, sector_avg=None)`` 0..100 fundamental skor —
  ``MidTermRecommender`` ve ``LongTermRecommender`` tarafından
  kullanılır. Sektör ortalaması varsa F/K karşılaştırması daha sağlam.
- Para alanı yok; tüm oranlar/yüzdeler float (DB'ye yazılırken
  ``Numeric(5,2)`` kolonu kullanılır).
- ``fetch_snapshot`` paralel async (``asyncio.gather``); kaynak yoksa
  yalnızca verilenleri çağırır.
- Bu modül data-collector ile birlikte çalışır: gerçek kaynak adapter'leri
  (``finviz_source``, ``isyatirim_source``) ``data-collector`` agent'a
  aittir; burada yalnızca **birleştirme + skorlama** mantığı vardır.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class FundamentalSnapshot:
    """Tek hissenin temel analiz anlık görüntüsü.

    Alanların çoğu opsiyonel — kaynak veriyi sağlamadıysa ``None`` kalır
    ve ``score()`` o bileşeni atlar.

    Attributes
    ----------
    instrument_id:
        DB ``instruments.id``.
    ticker:
        Sembol (örn. "THYAO", "AAPL").
    pe_ratio:
        Fiyat / Kazanç (F/K) — Price to Earnings.
    pb_ratio:
        Piyasa Değeri / Defter Değeri (PD/DD) — Price to Book.
    market_cap:
        Piyasa değeri (para birimine göre — TRY / USD).
    eps:
        Hisse başına kâr (Earnings per Share).
    dividend_yield:
        Temettü verimi (örn. 0.03 = %3).
    beta:
        Piyasa duyarlılığı katsayısı (1.0 = piyasa ile aynı, >1 daha
        oynak).
    target_price:
        Analist hedef fiyat (Finviz / TradingView konsensüs).
    sector:
        Sektör (örn. "Banking", "Technology").
    industry:
        Alt sektör (Finviz endüstrisi).
    growth_yoy:
        Yıllık (Year-over-Year) gelir büyümesi oranı (0.10 = %10).
    debt_to_equity:
        Borç / Özsermaye oranı.
    roe:
        Özsermaye Kârlılığı (Return on Equity) — 0.15 = %15.
    fetched_at:
        Bu snapshot'ın oluşturulduğu UTC zamanı.
    sources:
        Bu snapshot'a katkı yapan kaynakların listesi (örn.
        ``["finviz", "isyatirim"]``).
    """

    instrument_id: int
    ticker: str
    pe_ratio: Optional[float] = None
    pb_ratio: Optional[float] = None
    market_cap: Optional[float] = None
    eps: Optional[float] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    target_price: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    growth_yoy: Optional[float] = None
    debt_to_equity: Optional[float] = None
    roe: Optional[float] = None
    fetched_at: datetime = field(
        default_factory=lambda: datetime.now(tz=timezone.utc)
    )
    sources: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Yardımcılar
# ---------------------------------------------------------------------------


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, float(value)))


def _coerce_float(value: Any) -> Optional[float]:
    """``None`` / NaN toleranslı float dönüşümü."""

    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _merge_value(existing: Optional[float], new: Optional[float]) -> Optional[float]:
    """Birden çok kaynaktan gelen aynı alan için birleştirme stratejisi.

    Şu an basit: var olan ``None`` ise yeniyi al, değilse aritmetik
    ortalama (her iki kaynak da geçerli sayı). İleride güvenilirlik
    ağırlıklı ortalama kullanılabilir.
    """

    if existing is None:
        return new
    if new is None:
        return existing
    return (existing + new) / 2.0


# ---------------------------------------------------------------------------
# Ana sınıf
# ---------------------------------------------------------------------------


class FundamentalAnalyzer:
    """Temel analiz veri birleştirme + skorlama motoru.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni ``Session`` döndüren callable. ``sector_average``
        DB sorgusu için kullanılır. Test ortamında bağlamsız ``lambda:
        session`` da geçilebilir.
    sources:
        Kaynak adı → ``async (ticker: str, exchange: str | None) -> dict``
        callable eşlemesi. ``data-collector`` agent'ı somut adapter'leri
        (``finviz_fetch``, ``isyatirim_fetch``) sağlar; burada arayüz
        olarak callable bekleriz. ``None`` ise ``fetch_snapshot`` yalnızca
        boş snapshot döndürür.

    Example
    -------
    >>> async def finviz_fetch(ticker, exchange):
    ...     return {"pe_ratio": 12.4, "pb_ratio": 1.8, "sector": "Tech"}
    >>> analyzer = FundamentalAnalyzer(session_factory=Session,
    ...     sources={"finviz": finviz_fetch})
    >>> snap = await analyzer.fetch_snapshot(1, "AAPL", "NASDAQ")
    >>> analyzer.score(snap)
    60.0
    """

    # Skor parametreleri (sabit; gelecekte config'e taşınabilir).
    BASELINE = 50.0
    PE_LOW_BONUS = 10.0
    PE_HIGH_PENALTY = -10.0
    PB_LOW_BONUS = 10.0  # PD/DD < 1.5
    PB_HIGH_PENALTY = -10.0  # PD/DD > 3.0
    GROWTH_HIGH_BONUS = 15.0  # growth > 10%
    GROWTH_NEG_PENALTY = -15.0  # growth < 0
    DIVIDEND_BONUS = 5.0  # dividend_yield > 3%
    DEBT_LOW_BONUS = 5.0  # D/E < 1.0
    DEBT_HIGH_PENALTY = -10.0  # D/E > 2.0
    ROE_HIGH_BONUS = 10.0  # ROE > 15%

    PB_LOW_THRESHOLD = 1.5
    PB_HIGH_THRESHOLD = 3.0
    GROWTH_HIGH_THRESHOLD = 0.10
    GROWTH_NEG_THRESHOLD = 0.0
    DIVIDEND_THRESHOLD = 0.03
    DEBT_LOW_THRESHOLD = 1.0
    DEBT_HIGH_THRESHOLD = 2.0
    ROE_HIGH_THRESHOLD = 0.15

    def __init__(
        self,
        session_factory: Optional[Callable[[], object]] = None,
        sources: Optional[dict[str, Callable[..., Any]]] = None,
    ) -> None:
        self.session_factory = session_factory
        self.sources = dict(sources) if sources else {}

    # --------------------------------------------------------------- fetch

    async def fetch_snapshot(
        self,
        instrument_id: int,
        ticker: str,
        exchange: Optional[str] = None,
    ) -> FundamentalSnapshot:
        """Yapılandırılmış tüm kaynaklardan **paralel** veri çeker ve birleştirir.

        Her kaynak ``async fn(ticker, exchange) -> dict`` arayüzünü
        uygulamalıdır. Bir kaynak hata verirse loglanır ve atlanır;
        diğer kaynaklar etkilenmez. Hiç kaynak verisi gelmezse boş ama
        ``ticker``/``instrument_id`` dolu bir snapshot döner.

        Returns
        -------
        FundamentalSnapshot
            Birleştirilmiş veri; ``sources`` listesinde katkıda bulunan
            kaynaklar gösterilir.
        """

        snapshot = FundamentalSnapshot(
            instrument_id=instrument_id, ticker=ticker
        )

        if not self.sources:
            logger.debug(
                "FundamentalAnalyzer.fetch_snapshot: hiç kaynak yapılandırılmamış; "
                "boş snapshot döndürülüyor (ticker={}).",
                ticker,
            )
            return snapshot

        async def _run(name: str, fn: Callable[..., Any]):
            try:
                result = await fn(ticker, exchange)
                return name, result
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Fundamental kaynağı {!r} {} için başarısız: {}",
                    name,
                    ticker,
                    exc,
                )
                return name, None

        results = await asyncio.gather(
            *[_run(name, fn) for name, fn in self.sources.items()],
            return_exceptions=False,
        )

        for name, payload in results:
            if not payload or not isinstance(payload, dict):
                continue
            snapshot.sources.append(name)
            self._merge_into(snapshot, payload)

        snapshot.fetched_at = datetime.now(tz=timezone.utc)
        return snapshot

    @staticmethod
    def _merge_into(target: FundamentalSnapshot, payload: dict) -> None:
        """``payload`` dict'inden gelen alanları snapshot'a aktarır.

        Numeric alanlar için ``_merge_value`` (mevcutsa ortalama). Kategorik
        alanlar (sector/industry) için var olan boşsa atanır, doluysa
        dokunulmaz (ilk kaynak baskın).
        """

        numeric_fields = (
            "pe_ratio",
            "pb_ratio",
            "market_cap",
            "eps",
            "dividend_yield",
            "beta",
            "target_price",
            "growth_yoy",
            "debt_to_equity",
            "roe",
        )
        for key in numeric_fields:
            if key in payload:
                new = _coerce_float(payload.get(key))
                setattr(target, key, _merge_value(getattr(target, key), new))

        for key in ("sector", "industry"):
            if key in payload and getattr(target, key) is None:
                val = payload.get(key)
                if val is not None:
                    setattr(target, key, str(val))

    # --------------------------------------------------------------- skor

    def score(
        self,
        snapshot: FundamentalSnapshot,
        sector_avg: Optional[dict[str, float]] = None,
    ) -> float:
        """Snapshot'ı 0..100 fundamental skora çevirir.

        Bileşenler (Doküman §5.1 ruhu — sektör karşılaştırması + büyüme +
        verim + risk):

        - F/K: ``sector_avg["pe_ratio"]`` varsa altındaysa +10, üstündeyse
          −10. Yoksa mutlak eşik (PE<10 → +10, PE>25 → −10).
        - PD/DD: <1.5 → +10; >3.0 → −10.
        - Yıllık büyüme: >10% → +15; <0 → −15.
        - Temettü verimi: >3% → +5.
        - Borç/Özsermaye: <1.0 → +5; >2.0 → −10.
        - ROE: >15% → +10.

        Baseline 50. Sonuç ``[0, 100]`` aralığında clamp.
        """

        score = self.BASELINE

        # F/K — sektör ortalaması varsa kıyas, yoksa mutlak.
        pe = _coerce_float(snapshot.pe_ratio)
        if pe is not None and pe > 0:
            avg_pe = (sector_avg or {}).get("pe_ratio")
            avg_pe = _coerce_float(avg_pe)
            if avg_pe is not None and avg_pe > 0:
                if pe < avg_pe:
                    score += self.PE_LOW_BONUS
                elif pe > avg_pe:
                    score += self.PE_HIGH_PENALTY
            else:
                if pe < 10:
                    score += self.PE_LOW_BONUS
                elif pe > 25:
                    score += self.PE_HIGH_PENALTY

        # PD/DD
        pb = _coerce_float(snapshot.pb_ratio)
        if pb is not None and pb > 0:
            if pb < self.PB_LOW_THRESHOLD:
                score += self.PB_LOW_BONUS
            elif pb > self.PB_HIGH_THRESHOLD:
                score += self.PB_HIGH_PENALTY

        # Yıllık büyüme
        growth = _coerce_float(snapshot.growth_yoy)
        if growth is not None:
            if growth > self.GROWTH_HIGH_THRESHOLD:
                score += self.GROWTH_HIGH_BONUS
            elif growth < self.GROWTH_NEG_THRESHOLD:
                score += self.GROWTH_NEG_PENALTY

        # Temettü verimi
        div = _coerce_float(snapshot.dividend_yield)
        if div is not None and div > self.DIVIDEND_THRESHOLD:
            score += self.DIVIDEND_BONUS

        # Borç / Özsermaye
        de = _coerce_float(snapshot.debt_to_equity)
        if de is not None and de >= 0:
            if de < self.DEBT_LOW_THRESHOLD:
                score += self.DEBT_LOW_BONUS
            elif de > self.DEBT_HIGH_THRESHOLD:
                score += self.DEBT_HIGH_PENALTY

        # ROE
        roe = _coerce_float(snapshot.roe)
        if roe is not None and roe > self.ROE_HIGH_THRESHOLD:
            score += self.ROE_HIGH_BONUS

        return _clamp(score)

    # --------------------------------------------------------------- sektör ort.

    def sector_average(
        self, sector: str, fields: Optional[tuple[str, ...]] = None
    ) -> dict[str, float]:
        """Aynı sektördeki diğer hisselerin temel oran ortalamalarını döndürür.

        **Placeholder uygulaması:** Faz 3'te ``fundamental_snapshots``
        gibi bir DB tablosu olmadığı için bu metod boş dict döndürür ve
        ``score(snapshot, sector_avg=None)`` mutlak eşik kuralına düşer.
        İleride (Faz 4) sektör F/K ortalaması için ayrı tablo veya
        cache'lenmiş hesap eklenebilir.

        Parameters
        ----------
        sector:
            ``instruments.sector`` ile aynı isimlendirme.
        fields:
            Hangi alanların ortalaması istendiği (örn. ``("pe_ratio",
            "pb_ratio")``).

        Returns
        -------
        dict
            Şimdilik daima boş ``{}``.
        """

        # Şema eklenmediği için NOOP. İmza ileride genişlemek üzere korunur.
        del sector, fields
        if self.session_factory is None:
            return {}
        return {}


__all__ = [
    "FundamentalSnapshot",
    "FundamentalAnalyzer",
]
