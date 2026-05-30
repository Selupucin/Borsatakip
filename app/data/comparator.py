"""Veri karşılaştırma motoru.

Doküman §4.2 (Veri Karşılaştırma Motoru) referans:
1. **Tutarsızlık tespiti:** çiftli karşılaştırma — fark %0.5'i geçerse
   ``discrepancies`` tablosuna log; %2'yi geçerse ``alert_sent=True`` ile
   alarm tetikleme bayrağı set edilir.
2. **Güvenilirlik ağırlıklandırması:** her kaynağın
   ``data_sources.reliability_score`` (0–100) puanı ağırlık olur;
   ağırlıklı ortalama = ``verified_close``.
3. **Reliability güncellemesi:** başarılı çağrı +0.1 (max 100),
   başarısız çağrı −1.0 (min 0). ``DataCollector`` her fetch sonrasında
   ``update_reliability()`` çağırmalıdır.

Eşikler ``.env``'den okunur (``DISCREPANCY_THRESHOLD_PCT=0.5``,
``ALERT_DISCREPANCY_PCT=2.0``).

``analysis-engine`` için not: ``PriceHistory.verified_close`` bu modülün
yazdığı değerdir. Teknik indikatörler (RSI/MACD/EMA) bu sütun üzerinden
hesaplanmalıdır — tek kaynağın hatası bütün sinyali bozmasın.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable, Sequence

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.data.base_source import Quote
from app.db.models import DataSource, Discrepancy, PriceHistory


@dataclass(frozen=True, slots=True)
class DiscrepancyRecord:
    """Çiftli kaynak karşılaştırması — bir satır."""

    instrument_id: int
    timestamp: datetime
    source_a: str
    source_b: str
    price_a: Decimal
    price_b: Decimal
    diff_pct: Decimal  # |a-b| / min(a,b) * 100
    is_alert: bool  # ALERT_DISCREPANCY_PCT eşiğini aştı mı


class PriceComparator:
    """Quote'ları kıyaslar, verified_close ve discrepancy üretir."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._warn_pct = Decimal(str(settings.discrepancy_threshold_pct))
        self._alert_pct = Decimal(str(settings.alert_discrepancy_pct))
        # Reliability cache (DB ana kaynak, bu hızlı erişim için)
        self._reliability: dict[str, Decimal] = {}

    # ---- Public API ---------------------------------------------------------

    async def load_reliability(self) -> None:
        """DB'den tüm reliability skorlarını cache'e yükle. Başlangıçta çağır."""
        async with self._session_factory() as session:
            rows = (await session.execute(select(DataSource))).scalars().all()
            self._reliability = {
                ds.name: Decimal(str(ds.reliability_score or 100.0)) for ds in rows
            }

    def compare_quotes(
        self,
        instrument_id: int,
        quotes: Sequence[Quote],
    ) -> tuple[Decimal | None, list[DiscrepancyRecord]]:
        """Quote listesinden ``verified_close`` + discrepancy listesi üret.

        Returns:
            (verified_close, discrepancies). Quote sayısı < 2 ise tek fiyat
            verified olarak döner ve discrepancy listesi boştur.
        """
        if not quotes:
            return None, []

        # Tek kaynak varsa karşılaştırma yapılamaz
        if len(quotes) == 1:
            return quotes[0].price, []

        # Ağırlıklı ortalama
        verified = self._weighted_average(quotes)

        # Çiftli karşılaştırma
        discrepancies: list[DiscrepancyRecord] = []
        ts = max(q.timestamp for q in quotes)

        for i in range(len(quotes)):
            for j in range(i + 1, len(quotes)):
                qa, qb = quotes[i], quotes[j]
                if qa.price <= 0 or qb.price <= 0:
                    continue
                diff_abs = abs(qa.price - qb.price)
                denom = min(qa.price, qb.price)
                diff_pct = (diff_abs / denom) * Decimal(100)

                if diff_pct >= self._warn_pct:
                    discrepancies.append(
                        DiscrepancyRecord(
                            instrument_id=instrument_id,
                            timestamp=ts,
                            source_a=qa.source,
                            source_b=qb.source,
                            price_a=qa.price,
                            price_b=qb.price,
                            diff_pct=diff_pct.quantize(Decimal("0.0001")),
                            is_alert=diff_pct >= self._alert_pct,
                        )
                    )

        return verified, discrepancies

    async def persist(
        self,
        instrument_id: int,
        quotes: Sequence[Quote],
        verified: Decimal | None,
        discrepancies: Iterable[DiscrepancyRecord],
    ) -> None:
        """Verified fiyatı PriceHistory'ye, discrepancy'leri tabloya yaz."""
        async with self._session_factory() as session:
            # 1) verified_close her kaynağın satırına işaretle (aynı timestamp)
            if verified is not None and quotes:
                ts = max(q.timestamp for q in quotes)
                await session.execute(
                    update(PriceHistory)
                    .where(
                        PriceHistory.instrument_id == instrument_id,
                        PriceHistory.timestamp == ts,
                    )
                    .values(verified_close=verified, is_verified=True)
                )

            # 2) discrepancy satırları
            disc_rows = [
                {
                    "instrument_id": d.instrument_id,
                    "timestamp": d.timestamp,
                    "source_a": d.source_a,
                    "source_b": d.source_b,
                    "price_a": d.price_a,
                    "price_b": d.price_b,
                    "diff_pct": d.diff_pct,
                    "alert_sent": d.is_alert,
                }
                for d in discrepancies
            ]
            if disc_rows:
                await session.execute(pg_insert(Discrepancy).values(disc_rows))
                logger.info(
                    "instrument_id={}: {} discrepancy kaydedildi", instrument_id, len(disc_rows)
                )

            await session.commit()

    async def update_reliability(self, source_name: str, success: bool) -> None:
        """Kaynak güvenilirlik skorunu güncelle (DB + cache).

        Formül:
            success  -> score += 0.1, max 100.0
            failure  -> score -= 1.0, min 0.0

        Başarısızlık 10 kat daha sert cezalandırılır — uzun vadede stabil
        kaynaklar yüksek puan birikir, kararsız kaynaklar hızlı düşer.
        """
        delta = Decimal("0.1") if success else Decimal("-1.0")

        async with self._session_factory() as session:
            ds = (
                await session.execute(
                    select(DataSource).where(DataSource.name == source_name)
                )
            ).scalar_one_or_none()
            if ds is None:
                ds = DataSource(name=source_name)
                session.add(ds)
                await session.flush()

            current = Decimal(str(ds.reliability_score or 100.0))
            new = max(Decimal(0), min(Decimal(100), current + delta))
            ds.reliability_score = new
            await session.commit()
            self._reliability[source_name] = new

    # ---- Internal -----------------------------------------------------------

    def _weighted_average(self, quotes: Sequence[Quote]) -> Decimal:
        """Reliability ağırlığıyla ortalama fiyat."""
        weights: list[Decimal] = []
        for q in quotes:
            w = self._reliability.get(q.source, Decimal(100))
            weights.append(w)

        total_w = sum(weights, Decimal(0))
        if total_w <= 0:
            # Tüm kaynaklar 0 puan — eşit ağırlıkla ortalama al
            n = Decimal(len(quotes))
            return sum((q.price for q in quotes), Decimal(0)) / n

        weighted = sum((q.price * w for q, w in zip(quotes, weights)), Decimal(0))
        return weighted / total_w


__all__ = ["PriceComparator", "DiscrepancyRecord"]
