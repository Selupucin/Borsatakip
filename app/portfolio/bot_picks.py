"""Bot öneri (pick) takip servisi — Faz 2.

Doküman §6.3 (Bot Öneri Listesi) referans alınmıştır.

Sorumluluk:
- Recommender çıktısını ``bot_picks`` tablosuna kaydet (``add_pick``).
- Açık pick'leri tarayıp hedefe ulaşan veya süresi geçenleri kapat
  (``update_open_picks``) — günlük arka plan görevi çağıracak.
- Manuel kapatma (``close_pick``) için return_pct hesabı.
- Vade bazlı başarı oranı + ortalama getiri + ortalama tutma süresi
  istatistikleri (``get_stats``) — UI "Botu dinleseydin nerede olurdun"
  panelinin temeli.

Tasarım kuralları:
- ``session_factory`` callable sözleşmesi ``WatchlistService`` ile aynı.
- API **async**; DB erişimi sync SQLAlchemy 2.0 ile.
- Para alanları ``Decimal``; skorlar ``float``. DB ``Numeric(18,4)`` ve
  ``Numeric(8,4)`` kolonlarına float olarak yazılır (mevcut model).
- ``timeframe ∈ {'short','mid','long'}`` ve ``action ∈ {'BUY','HOLD','SELL'}``
  — recommendations CHECK constraint'leriyle aynı kümeyi kullanır.
- Açık pick "expire" eşikleri (doküman §5.2 vade süreleri):
  - short: 7 gün
  - mid:   90 gün
  - long:  365 gün

Return formülü (kritik):
    base = (close - pick) / pick * 100
    return_pct = base                 if action == 'BUY'
    return_pct = -base                if action == 'SELL'   # short pozisyon mantığı
    return_pct = base                 if action == 'HOLD'   # bilgi amaçlı

Faz 3'te eklenecekler (yapılmadı, bilinçli):
- Stop-loss tetiklemesi (``outcome='stopped'``) — risk modülü stop fiyatı
  üretmeye başladığında.
- Pick'ten gerçek pozisyon açma (open_positions / cash_flows) — paper
  trading ve portföy mantığı Faz 3.
- "Botu dinleseydin" sanal portföy simülasyonu (paper_trading.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Optional, Callable

from loguru import logger
from sqlalchemy import and_, func, select


# ---------------------------------------------------------------------------
# Sabitler
# ---------------------------------------------------------------------------

# Süre sınırı KULLANIM DIŞI (kullanıcı geri bildirimi): bot pick'leri sadece
# hedef fiyata ulaştığında kapanır (hit_target). Time-based expire UI'da
# kafa karıştırıcıydı. Sembolik olarak sabit tutuluyor (vade validasyonu için).
EXPIRE_DAYS: dict[str, int] = {
    "short": 36500,  # pratik olarak sınırsız (~100 yıl)
    "mid": 36500,
    "long": 36500,
}

_VALID_TIMEFRAMES = frozenset(EXPIRE_DAYS.keys())
_VALID_ACTIONS = frozenset({"BUY", "HOLD", "SELL"})
_VALID_OUTCOMES = frozenset({"hit_target", "stopped", "expired", "open"})


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class BotPickView:
    """Tek bir pick'in UI/API görünümü."""

    id: int
    instrument_id: int
    ticker: str
    timeframe: str             # 'short' | 'mid' | 'long'
    action: str
    confidence: float
    price_at_pick: Decimal
    target_price: Optional[Decimal]
    picked_at: datetime
    is_open: bool
    closed_at: Optional[datetime]
    price_at_close: Optional[Decimal]
    outcome: Optional[str]     # 'hit_target' | 'stopped' | 'expired' | 'open'
    return_pct: Optional[Decimal]


@dataclass
class BotPicksStats:
    """Vade bazlı başarı oranı istatistiği.

    success_rate hesabında payda **kapatılmış (closed) pick sayısıdır**;
    açık pick'ler hesaba alınmaz (henüz sonuçlanmadıkları için).
    """

    timeframe: str
    total_picks: int
    open_picks: int
    closed_picks: int
    hit_target_count: int
    stopped_count: int
    expired_count: int
    success_rate: float        # hit_target / closed (0..1)
    avg_return_pct: Optional[Decimal]
    avg_hold_days: Optional[float]


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class BotPicksService:
    """Bot öneri (pick) yaşam döngüsü ve istatistikleri.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    """

    def __init__(self, session_factory: Callable[[], object]) -> None:
        self.session_factory = session_factory

    # ------------------------------------------------------------- helpers

    def _session(self):
        return self.session_factory()

    @staticmethod
    def _close(session) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _validate_timeframe(timeframe: str) -> str:
        tf = (timeframe or "").lower().strip()
        if tf not in _VALID_TIMEFRAMES:
            raise ValueError(
                f"Geçersiz timeframe: {timeframe!r}. "
                f"Beklenen: {sorted(_VALID_TIMEFRAMES)}"
            )
        return tf

    @staticmethod
    def _validate_action(action: str) -> str:
        act = (action or "").upper().strip()
        if act not in _VALID_ACTIONS:
            raise ValueError(
                f"Geçersiz action: {action!r}. "
                f"Beklenen: {sorted(_VALID_ACTIONS)}"
            )
        return act

    @staticmethod
    def _validate_outcome(outcome: str) -> str:
        out = (outcome or "").lower().strip()
        if out not in _VALID_OUTCOMES:
            raise ValueError(
                f"Geçersiz outcome: {outcome!r}. "
                f"Beklenen: {sorted(_VALID_OUTCOMES)}"
            )
        return out

    @staticmethod
    def _compute_return_pct(
        action: str, pick_price: Decimal, close_price: Decimal
    ) -> Decimal:
        """``return_pct = (close - pick) / pick * 100`` — SELL'de işaret ters.

        - BUY  → fiyat yükselince pozitif (long).
        - SELL → fiyat düşünce pozitif (short pozisyon mantığı); işaret ters.
        - HOLD → bilgi amaçlı, BUY ile aynı yönde gösterilir.
        """

        if pick_price is None or pick_price == 0:
            raise ValueError("pick_price 0 veya None olamaz; return hesaplanamaz.")
        base = (close_price - pick_price) / pick_price * Decimal("100")
        if action == "SELL":
            base = -base
        # 4 ondalık (DB Numeric(8,4))
        return base.quantize(Decimal("0.0001"))

    # ------------------------------------------------------------- create

    async def add_pick(
        self,
        instrument_id: int,
        timeframe: str,
        action: str,
        confidence: float,
        current_price: Decimal,
        target_price: Optional[Decimal],
    ) -> int:
        """Recommender çıktısını bot_picks tablosuna kaydet.

        Returns
        -------
        int
            Yeni ``BotPick.id``.
        """

        tf = self._validate_timeframe(timeframe)
        act = self._validate_action(action)
        if current_price is None or current_price <= 0:
            raise ValueError("current_price pozitif olmalı.")

        from app.db.models import BotPick  # noqa: WPS433

        session = self._session()
        try:
            row = BotPick(
                instrument_id=int(instrument_id),
                timeframe=tf,
                action=act,
                confidence=float(confidence),
                price_at_pick=float(current_price),
                target_price=(
                    float(target_price) if target_price is not None else None
                ),
                picked_at=datetime.now(tz=timezone.utc),
                is_open=True,
                outcome="open",
            )
            session.add(row)
            session.flush()
            pick_id = int(row.id)
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Bot pick eklendi: id={}, instr={}, tf={}, action={}, "
            "conf={:.1f}, price={}, target={}",
            pick_id,
            instrument_id,
            tf,
            act,
            float(confidence),
            current_price,
            target_price,
        )
        return pick_id

    # ------------------------------------------------------------- close

    async def close_pick(
        self, pick_id: int, close_price: Decimal, outcome: str
    ) -> None:
        """Açık bir pick'i kapat ve ``return_pct`` hesapla.

        Raises
        ------
        LookupError
            Pick bulunamazsa.
        ValueError
            Pick zaten kapalıysa veya outcome geçersizse.
        """

        out = self._validate_outcome(outcome)
        if out == "open":
            raise ValueError("close_pick için outcome 'open' olamaz.")

        from app.db.models import BotPick  # noqa: WPS433

        session = self._session()
        try:
            row = session.get(BotPick, pick_id)
            if row is None:
                raise LookupError(f"BotPick bulunamadı: id={pick_id}")
            if not row.is_open:
                raise ValueError(f"BotPick zaten kapalı: id={pick_id}")
            if row.price_at_pick is None:
                raise ValueError(
                    f"BotPick.price_at_pick NULL: id={pick_id} — return hesaplanamaz."
                )

            pick_price = Decimal(str(row.price_at_pick))
            ret = self._compute_return_pct(
                str(row.action), pick_price, Decimal(str(close_price))
            )

            row.is_open = False
            row.closed_at = datetime.now(tz=timezone.utc)
            row.price_at_close = float(close_price)
            row.outcome = out
            row.return_pct = float(ret)

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Bot pick kapatıldı: id={}, outcome={}, close={}, return%={}",
            pick_id,
            out,
            close_price,
            ret,
        )

    # ------------------------------------------------------------- batch update

    async def update_open_picks(
        self, current_prices: dict[int, Decimal]
    ) -> list[int]:
        """Açık pick'leri tara; hedefe ulaşmış veya süresi geçmiş olanları kapat.

        Parameters
        ----------
        current_prices:
            ``{instrument_id: current_price}`` — collector'dan gelen güncel
            doğrulanmış fiyatlar. Pick'in instrument_id'si haritada yoksa
            yalnızca süre (expire) kontrolü yapılır.

        Returns
        -------
        list[int]
            Kapatılan pick id'lerinin listesi.

        Kapatma kuralları:
        - **hit_target**: action=BUY ve current_price >= target_price;
          veya action=SELL ve current_price <= target_price.
        - **expired**: picked_at + EXPIRE_DAYS[timeframe] < now ve hedefe
          ulaşılmadıysa.
        - Stop-loss tetiklemesi Faz 3 (risk modülü stop fiyatı ürettikten
          sonra ``stopped`` outcome'u burada eklenecek).
        """

        from app.db.models import BotPick  # noqa: WPS433

        now = datetime.now(tz=timezone.utc)
        closed_ids: list[int] = []

        session = self._session()
        try:
            stmt = select(BotPick).where(BotPick.is_open.is_(True))
            rows = list(session.execute(stmt).scalars().all())

            for row in rows:
                pick_id = int(row.id)
                tf = str(row.timeframe or "").lower()
                action = str(row.action or "").upper()
                if row.price_at_pick is None:
                    continue
                pick_price = Decimal(str(row.price_at_pick))
                target = (
                    Decimal(str(row.target_price))
                    if row.target_price is not None
                    else None
                )

                current = current_prices.get(int(row.instrument_id))
                if current is not None and not isinstance(current, Decimal):
                    current = Decimal(str(current))

                outcome: Optional[str] = None
                close_price: Optional[Decimal] = None

                # 1) Target check
                if current is not None and target is not None:
                    if action == "BUY" and current >= target:
                        outcome = "hit_target"
                        close_price = current
                    elif action == "SELL" and current <= target:
                        outcome = "hit_target"
                        close_price = current

                # 2) Expire check DEVRE DIŞI — kullanıcı kararı:
                # Pick'ler sadece hedef fiyata ulaştığında veya bot yeni
                # analizinde ters yönde sinyal verdiğinde kapanır.

                if outcome is None or close_price is None:
                    continue

                ret = self._compute_return_pct(action, pick_price, close_price)
                row.is_open = False
                row.closed_at = now
                row.price_at_close = float(close_price)
                row.outcome = outcome
                row.return_pct = float(ret)
                closed_ids.append(pick_id)

                logger.info(
                    "update_open_picks: pick {} kapatıldı (outcome={}, "
                    "close={}, return%={})",
                    pick_id,
                    outcome,
                    close_price,
                    ret,
                )

            if closed_ids:
                commit = getattr(session, "commit", None)
                if callable(commit):
                    commit()
        finally:
            self._close(session)

        return closed_ids

    # ------------------------------------------------------------- read

    async def list_picks(
        self,
        timeframe: Optional[str] = None,
        only_open: bool = False,
        limit: int = 50,
    ) -> list[BotPickView]:
        """Pick'leri ``picked_at DESC`` sırasıyla listele.

        Şeffaflık ilkesi (doküman §6.3): "her pick için tüm tarihçe
        görünebilir olsun" — bu yüzden hem açık hem kapalı pick'ler
        varsayılan olarak dahildir.
        """

        from app.db.models import BotPick, Instrument  # noqa: WPS433

        tf: Optional[str] = None
        if timeframe is not None:
            tf = self._validate_timeframe(timeframe)

        session = self._session()
        try:
            stmt = (
                select(
                    BotPick.id,
                    BotPick.instrument_id,
                    Instrument.ticker,
                    BotPick.timeframe,
                    BotPick.action,
                    BotPick.confidence,
                    BotPick.price_at_pick,
                    BotPick.target_price,
                    BotPick.picked_at,
                    BotPick.is_open,
                    BotPick.closed_at,
                    BotPick.price_at_close,
                    BotPick.outcome,
                    BotPick.return_pct,
                )
                .join(Instrument, Instrument.id == BotPick.instrument_id)
                .order_by(BotPick.picked_at.desc())
                .limit(int(limit))
            )
            if tf is not None:
                stmt = stmt.where(BotPick.timeframe == tf)
            if only_open:
                stmt = stmt.where(BotPick.is_open.is_(True))

            rows = session.execute(stmt).all()
        finally:
            self._close(session)

        def _dec(v) -> Optional[Decimal]:
            return Decimal(str(v)) if v is not None else None

        return [
            BotPickView(
                id=int(r[0]),
                instrument_id=int(r[1]),
                ticker=str(r[2] or ""),
                timeframe=str(r[3] or ""),
                action=str(r[4] or ""),
                confidence=float(r[5]) if r[5] is not None else 0.0,
                price_at_pick=_dec(r[6]) or Decimal("0"),
                target_price=_dec(r[7]),
                picked_at=r[8],
                is_open=bool(r[9]),
                closed_at=r[10],
                price_at_close=_dec(r[11]),
                outcome=str(r[12]) if r[12] is not None else None,
                return_pct=_dec(r[13]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------- stats

    async def get_stats(self, timeframe: str) -> BotPicksStats:
        """Vade bazlı başarı oranı + ortalama getiri + ortalama tutma süresi.

        Formüller:
        - success_rate = hit_target_count / closed_picks
          (closed=0 ise 0.0; payda 0 koruması)
        - avg_return_pct = AVG(return_pct) closed pick'ler üzerinden;
          hiç closed yoksa None.
        - avg_hold_days = AVG(closed_at - picked_at) gün cinsinden;
          hiç closed yoksa None.
        """

        tf = self._validate_timeframe(timeframe)

        from app.db.models import BotPick  # noqa: WPS433

        session = self._session()
        try:
            # Toplam pick
            total_stmt = select(func.count(BotPick.id)).where(BotPick.timeframe == tf)
            total = int(session.execute(total_stmt).scalar_one() or 0)

            # Açık pick
            open_stmt = select(func.count(BotPick.id)).where(
                and_(BotPick.timeframe == tf, BotPick.is_open.is_(True))
            )
            open_count = int(session.execute(open_stmt).scalar_one() or 0)

            closed_count = total - open_count

            # Outcome dağılımı (sadece kapalı pick'ler)
            outcome_stmt = (
                select(BotPick.outcome, func.count(BotPick.id))
                .where(
                    and_(
                        BotPick.timeframe == tf,
                        BotPick.is_open.is_(False),
                    )
                )
                .group_by(BotPick.outcome)
            )
            outcome_rows = session.execute(outcome_stmt).all()
            outcome_map: dict[str, int] = {
                str(r[0] or ""): int(r[1] or 0) for r in outcome_rows
            }
            hit = outcome_map.get("hit_target", 0)
            stopped = outcome_map.get("stopped", 0)
            expired = outcome_map.get("expired", 0)

            # Ortalama getiri (kapalı pick'ler)
            ret_stmt = select(func.avg(BotPick.return_pct)).where(
                and_(
                    BotPick.timeframe == tf,
                    BotPick.is_open.is_(False),
                    BotPick.return_pct.isnot(None),
                )
            )
            avg_ret = session.execute(ret_stmt).scalar_one()
            avg_return: Optional[Decimal]
            if avg_ret is None:
                avg_return = None
            else:
                avg_return = Decimal(str(avg_ret)).quantize(Decimal("0.0001"))

            # Ortalama tutma süresi (gün) — DB-agnostic için Python tarafında hesapla
            hold_stmt = select(BotPick.picked_at, BotPick.closed_at).where(
                and_(
                    BotPick.timeframe == tf,
                    BotPick.is_open.is_(False),
                    BotPick.closed_at.isnot(None),
                    BotPick.picked_at.isnot(None),
                )
            )
            hold_rows = session.execute(hold_stmt).all()
        finally:
            self._close(session)

        if hold_rows:
            total_days = 0.0
            for picked, closed in hold_rows:
                # naive → aware koruması (her ikisi de varsa fark zaten timedelta)
                if picked is not None and closed is not None:
                    diff = closed - picked
                    total_days += diff.total_seconds() / 86400.0
            avg_hold: Optional[float] = total_days / len(hold_rows)
        else:
            avg_hold = None

        success_rate = (hit / closed_count) if closed_count > 0 else 0.0

        return BotPicksStats(
            timeframe=tf,
            total_picks=total,
            open_picks=open_count,
            closed_picks=closed_count,
            hit_target_count=hit,
            stopped_count=stopped,
            expired_count=expired,
            success_rate=float(success_rate),
            avg_return_pct=avg_return,
            avg_hold_days=avg_hold,
        )


__all__ = [
    "EXPIRE_DAYS",
    "BotPickView",
    "BotPicksStats",
    "BotPicksService",
]
