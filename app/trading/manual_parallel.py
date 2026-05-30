"""Manuel-eşli mod (manual_parallel) — Faz 3 Batch 3.

Doküman §7.3 (manuel-eşli mod, başlangıç önceliği) referans alınmıştır.

Akış:
1. Bot piyasa verisiyle ``recommendations`` üretir.
2. Kullanıcı kendi aracı kurum uygulamasında işlemi **elle** yapar.
3. Kullanıcı bota "uyguladım" işaretler → ``mark_applied`` çağrılır.
4. ``portfolio`` tablosuna satır eklenir (``followed_bot=True``);
   ``positions`` modülü varsa açık pozisyon güncellenir.
5. Bot kullanıcının önerileri ne sıklıkla dinlediğini ve "botu
   dinlediğinde vs. dinlemediğinde" teorik getiri farkını raporlar.

Tasarım notları:
- ``ManualParallelService`` async API'ye sahip; ``session_factory``
  sözleşmesi ``AccountService`` / ``WatchlistService`` ile aynı.
- Para alanları ``Decimal``; DB ``Numeric(18, 4)`` kolonlarına
  ``float(decimal)`` cast ile yazılır.
- ``position_service`` opsiyoneldir (Faz 3 Batch sonrası eklendiğinde
  ``apply_buy`` / ``apply_sell`` çağrılır). ``None`` ise yalnızca
  ``portfolio`` satırı eklenir.
- "Bekleyen öneri" tanımı: ``recommendations.generated_at`` sonrasında
  aynı ``instrument_id`` için ``portfolio`` satırı yoksa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Optional

from loguru import logger
from sqlalchemy import and_, func, select


# ---------------------------------------------------------------------------
# Sabitler / etiketler
# ---------------------------------------------------------------------------

#: ``ManualTradeMarker.user_action`` için izinli değerler.
USER_ACTIONS: frozenset[str] = frozenset({"applied", "skipped", "modified"})


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class ManualTradeMarker:
    """Kullanıcının bir öneri için verdiği "uyguladım / atladım" işareti.

    Notes
    -----
    Bu sınıf yalnızca veri taşıyıcı; DB'ye yazımı ``ManualParallelService``
    yapar. ``user_action='modified'`` durumunda kullanıcı önerideki
    fiyat/miktarı değiştirerek uygulamıştır — ``actual_price`` ve
    ``actual_quantity`` bu durumda gerçek değerleri taşır.
    """

    recommendation_id: int
    user_action: str  # 'applied' | 'skipped' | 'modified'
    actual_price: Optional[Decimal] = None
    actual_quantity: Optional[Decimal] = None
    notes: str = ""


@dataclass
class PendingRecommendationView:
    """UI için bekleyen öneri görünümü."""

    recommendation_id: int
    instrument_id: int
    ticker: str
    action: str  # 'BUY' | 'HOLD' | 'SELL'
    timeframe: str
    confidence: float
    target_price: Optional[Decimal]
    generated_at: datetime
    summary: str


@dataclass
class BotFollowRate:
    """Botu ne kadar dinlediğine dair özet."""

    total_recommendations: int
    applied: int
    skipped: int
    modified: int
    follow_rate: float  # 0..1 — applied / (applied+skipped+modified)


@dataclass
class FollowVsSkipPnL:
    """Botu dinleyince vs. dinlemeyince teorik getiri farkı.

    Notes
    -----
    "Dinlemediğinde getiri" hesabı için ``bot_picks`` tablosundaki
    ``return_pct`` ve outcome alanları kullanılır — kullanıcı öneriyi
    atladığında o öneri bot_picks listesindeyse teorik kâr bilinir.
    """

    followed_count: int
    skipped_count: int
    followed_realized_pnl: Decimal
    skipped_theoretical_pnl: Decimal
    delta: Decimal = field(default=Decimal("0"))


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class ManualParallelService:
    """Manuel-eşli mod servisi.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    position_service:
        İsteğe bağlı. Eğer verilirse ``apply_buy`` / ``apply_sell``
        çağrılır. None ise yalnızca ``portfolio`` satırı yazılır.
    """

    def __init__(
        self,
        session_factory: Callable[[], Any],
        position_service: Optional[Any] = None,
    ) -> None:
        self.session_factory = session_factory
        self.position_service = position_service

    # ----------------------------------------------------------- helpers

    def _session(self):
        return self.session_factory()

    @staticmethod
    def _close(session) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    @staticmethod
    def _commit(session) -> None:
        commit = getattr(session, "commit", None)
        if callable(commit):
            commit()

    @staticmethod
    def _to_decimal(value, default: Decimal = Decimal("0")) -> Decimal:
        if value is None:
            return default
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    # ------------------------------------------------------------- read

    async def pending_recommendations(
        self,
        account_id: int,
        since: Optional[datetime] = None,
    ) -> list[PendingRecommendationView]:
        """Henüz işaretlenmemiş önerileri döndür.

        Bir öneri "bekliyor" sayılır eğer ``generated_at`` sonrasında
        aynı ``instrument_id`` için bu hesabın herhangi bir cüzdanında
        ``portfolio`` satırı yoksa.

        Parameters
        ----------
        account_id:
            ``user_account.id``.
        since:
            Sadece bu tarihten sonraki öneriler (None → tüm geçmiş).
        """

        from sqlalchemy import func

        from app.db.models import (  # noqa: WPS433
            Instrument,
            OpenPosition,
            Portfolio,
            Recommendation,
            Wallet,
        )

        session = self._session()
        try:
            wallet_ids_subq = (
                select(Wallet.id).where(Wallet.account_id == account_id).subquery()
            )

            # Açık pozisyon olan instrument_id'leri çek
            open_inst_ids: set[int] = set(
                session.execute(
                    select(OpenPosition.instrument_id)
                    .join(Wallet, Wallet.id == OpenPosition.wallet_id)
                    .where(Wallet.account_id == account_id)
                    .distinct()
                )
                .scalars()
                .all()
            )

            # DISTINCT (instrument, timeframe) en yeni recommendation alt sorgusu
            # — kullanıcının "Reddet" tıkladığı (dismissed_at NOT NULL) kayıtlar
            # zaten listede gözükmemeli, bu yüzden subquery'de de filtrele.
            latest_subq = (
                select(
                    Recommendation.instrument_id.label("inst_id"),
                    Recommendation.timeframe.label("tf"),
                    func.max(Recommendation.generated_at).label("max_at"),
                )
                .where(Recommendation.dismissed_at.is_(None))
                .group_by(Recommendation.instrument_id, Recommendation.timeframe)
                .subquery()
            )

            # Filtre kuralları:
            # - HOLD önerisi listeye DÜŞMEZ
            # - BUY öneri SADECE açık pozisyon YOKSA listede (zaten alınmış hisseye tekrar AL anlamsız)
            # - SELL öneri SADECE açık pozisyon VARSA listede
            # - Her (instrument, timeframe) için EN YENİ öneri (duplicate spam yok)
            stmt = (
                select(Recommendation, Instrument)
                .join(
                    latest_subq,
                    and_(
                        Recommendation.instrument_id == latest_subq.c.inst_id,
                        Recommendation.timeframe == latest_subq.c.tf,
                        Recommendation.generated_at == latest_subq.c.max_at,
                    ),
                )
                .join(Instrument, Instrument.id == Recommendation.instrument_id)
                .where(
                    Recommendation.action != "HOLD",
                    Recommendation.dismissed_at.is_(None),
                    ~select(Portfolio.id)
                    .where(
                        and_(
                            Portfolio.wallet_id.in_(select(wallet_ids_subq.c.id)),
                            Portfolio.instrument_id == Recommendation.instrument_id,
                            Portfolio.transaction_at >= Recommendation.generated_at,
                        )
                    )
                    .exists(),
                )
                .order_by(Recommendation.generated_at.desc())
            )
            if since is not None:
                stmt = stmt.where(Recommendation.generated_at >= since)

            raw_rows = session.execute(stmt).all()
            # Pozisyon-bağımlı filtre:
            # - BUY: pozisyon yoksa göster (zaten almış olduğun hisseye AL gösterme)
            # - SELL: pozisyon varsa göster
            rows = []
            for rec, instr in raw_rows:
                inst_id = int(rec.instrument_id)
                has_position = inst_id in open_inst_ids
                if rec.action == "BUY" and has_position:
                    continue
                if rec.action == "SELL" and not has_position:
                    continue
                rows.append((rec, instr))

            result: list[PendingRecommendationView] = []
            for rec, instr in rows:
                result.append(
                    PendingRecommendationView(
                        recommendation_id=int(rec.id),
                        instrument_id=int(rec.instrument_id),
                        ticker=str(instr.ticker),
                        action=str(rec.action),
                        timeframe=str(rec.timeframe),
                        confidence=float(rec.confidence or 0.0),
                        target_price=self._to_decimal(rec.target_price)
                        if rec.target_price is not None
                        else None,
                        generated_at=rec.generated_at,
                        summary=str(rec.summary or ""),
                    )
                )
        finally:
            self._close(session)

        return result

    # ------------------------------------------------------------- write

    async def mark_applied(
        self,
        wallet_id: int,
        recommendation_id: int,
        actual_price: Decimal,
        actual_quantity: Decimal,
        commission: Optional[Decimal] = None,
        notes: str = "",
    ) -> int:
        """Kullanıcı öneriyi uyguladı: portföye yaz + pozisyon güncelle.

        Returns
        -------
        int
            Yeni ``portfolio.id``.

        Raises
        ------
        LookupError
            Recommendation veya Wallet bulunamadıysa.
        ValueError
            Miktar / fiyat ≤ 0.
        """

        from app.db.models import Portfolio, Recommendation, Wallet  # noqa: WPS433

        price = self._to_decimal(actual_price)
        qty = self._to_decimal(actual_quantity)
        comm = self._to_decimal(commission, Decimal("0"))
        if price <= 0:
            raise ValueError("actual_price > 0 olmalı")
        if qty <= 0:
            raise ValueError("actual_quantity > 0 olmalı")

        session = self._session()
        try:
            rec = session.get(Recommendation, recommendation_id)
            if rec is None:
                raise LookupError(f"Recommendation bulunamadı: id={recommendation_id}")
            wal = session.get(Wallet, wallet_id)
            if wal is None:
                raise LookupError(f"Wallet bulunamadı: id={wallet_id}")

            action = str(rec.action).upper()
            if action == "HOLD":
                logger.info(
                    "HOLD önerisi için mark_applied çağrıldı, portfolio yazılmadı: rec_id={}",
                    recommendation_id,
                )
                return 0
            if action not in ("BUY", "SELL"):
                raise ValueError(f"Geçersiz recommendation.action: {action!r}")

            self._commit(session)
        finally:
            self._close(session)

        # Pozisyon servisine delege et — PositionService portfolio + open_positions
        # + cash_flow + wallet.cash_balance hepsini atomic yazar. Manuel-eşli'de
        # cüzdan boş olabilir; skip_cash_check/skip_position_check ile tracking-only.
        new_id = 0
        rec_notes = notes or f"manual_parallel: rec_id={recommendation_id}"

        def _write_portfolio_fallback(reason: str = "") -> int:
            fb = self._session()
            try:
                fb_row = Portfolio(
                    wallet_id=int(wallet_id),
                    instrument_id=int(rec.instrument_id),
                    action=action,
                    quantity=float(qty),
                    price=float(price),
                    total=float(price * qty),
                    commission=float(comm),
                    realized_pnl=None,
                    hold_days=None,
                    followed_bot=True,
                    transaction_at=datetime.now(timezone.utc),
                    notes=f"{rec_notes}{f' ({reason})' if reason else ''}",
                )
                fb.add(fb_row)
                fb.flush()
                fid = int(fb_row.id)
                self._commit(fb)
                return fid
            finally:
                self._close(fb)

        if self.position_service is not None:
            try:
                if action == "BUY":
                    new_id = await self.position_service.apply_buy(
                        wallet_id=int(wallet_id),
                        instrument_id=int(rec.instrument_id),
                        quantity=qty,
                        price=price,
                        commission=comm,
                        followed_bot=True,
                        notes=rec_notes,
                        skip_cash_check=True,
                    )
                else:  # SELL
                    new_id, _realized = await self.position_service.apply_sell(
                        wallet_id=int(wallet_id),
                        instrument_id=int(rec.instrument_id),
                        quantity=qty,
                        price=price,
                        commission=comm,
                        followed_bot=True,
                        notes=rec_notes,
                        skip_position_check=True,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("PositionService çağrısı başarısız: {}", exc)
                new_id = _write_portfolio_fallback(f"fallback after error: {exc}")
        else:
            # position_service yok (test ortamı / sade kullanım) — sadece audit
            new_id = _write_portfolio_fallback("no position_service")

        logger.info(
            "manual_parallel uygulandı: wallet={}, rec={}, action={}, qty={}, price={}",
            wallet_id,
            recommendation_id,
            action,
            qty,
            price,
        )
        return new_id

    async def mark_skipped(
        self,
        recommendation_id: int,
        notes: str = "",
    ) -> None:
        """Kullanıcı öneriyi atladı/reddetti — ``recommendations.dismissed_at``
        sütununa şu an damgalanır. Pending sorgusu bu kayıtları artık döndürmez.

        Bot bir sonraki tick'te aynı hisseye yeni bir öneri üretirse o
        recommendation taze ``id`` ile listede görünür (kullanıcı kararı
        eski öneriye özgüdür, gelecek önerilere değil).
        """

        from app.db.models import Recommendation  # noqa: WPS433

        session = self._session()
        try:
            rec = session.get(Recommendation, int(recommendation_id))
            if rec is None:
                logger.warning(
                    "mark_skipped: rec_id={} bulunamadı.", recommendation_id
                )
                return
            rec.dismissed_at = datetime.now(tz=timezone.utc)
            session.commit()
            logger.info(
                "manual_parallel dismissed: rec_id={}, notes={!r}",
                recommendation_id,
                notes,
            )
        except Exception as exc:  # noqa: BLE001
            session.rollback()
            logger.error("mark_skipped DB hata: {}", exc)
            raise

    # ------------------------------------------------------------- stats

    async def bot_follow_rate(self, account_id: int) -> BotFollowRate:
        """Kaç öneri uygulandı / atlandı / değiştirildi.

        Heuristik: Hesabın cüzdanlarındaki ``portfolio`` satırlarından
        ``followed_bot IS TRUE`` olanlar "applied", ``followed_bot IS FALSE``
        olanlar "modified" (kullanıcı kendi seçimi olarak işaretlemiş)
        sayılır. "Skipped" = (toplam öneri) - (uygulanan benzersiz
        recommendation sayısı).
        """

        from app.db.models import Portfolio, Recommendation, Wallet  # noqa: WPS433

        session = self._session()
        try:
            wallet_ids = (
                select(Wallet.id).where(Wallet.account_id == account_id).subquery()
            )

            total_recs = session.execute(
                select(func.count(Recommendation.id))
            ).scalar_one() or 0

            # Bu hesabın cüzdanlarında followed_bot=True olan portfolio
            # satırlarına karşılık gelen benzersiz instrument sayısını proxy
            # olarak "applied" kabul ediyoruz.
            applied = session.execute(
                select(func.count(func.distinct(Portfolio.instrument_id)))
                .where(
                    and_(
                        Portfolio.wallet_id.in_(select(wallet_ids.c.id)),
                        Portfolio.followed_bot.is_(True),
                    )
                )
            ).scalar_one() or 0

            modified = session.execute(
                select(func.count(Portfolio.id))
                .where(
                    and_(
                        Portfolio.wallet_id.in_(select(wallet_ids.c.id)),
                        Portfolio.followed_bot.is_(False),
                    )
                )
            ).scalar_one() or 0

            total_recs = int(total_recs)
            applied = int(applied)
            modified = int(modified)
            skipped = max(0, total_recs - applied - modified)
            denom = applied + skipped + modified
            rate = (applied / denom) if denom > 0 else 0.0
        finally:
            self._close(session)

        return BotFollowRate(
            total_recommendations=total_recs,
            applied=applied,
            skipped=skipped,
            modified=modified,
            follow_rate=rate,
        )

    async def follow_vs_skip_pnl(self, account_id: int) -> FollowVsSkipPnL:
        """Botu dinleyince vs. dinlemeyince teorik getiri farkı.

        Notes
        -----
        ``followed_realized_pnl`` = bu hesabın cüzdanlarındaki
        ``followed_bot=True`` satırların ``realized_pnl`` toplamı.

        ``skipped_theoretical_pnl`` = ``bot_picks`` listesindeki kapanmış
        (``is_open=False``) önerilerin ``return_pct`` × ``price_at_pick``
        toplamı — kullanıcı dinlemiş olsaydı potansiyel kazanç.
        Bu yalnızca tahminîdir; tam doğruluk için ``recommendations`` ile
        ``bot_picks`` arasında bağlantı kurulması gerekir.
        """

        from app.db.models import BotPick, Portfolio, Wallet  # noqa: WPS433

        session = self._session()
        try:
            wallet_ids = (
                select(Wallet.id).where(Wallet.account_id == account_id).subquery()
            )

            followed_count = session.execute(
                select(func.count(Portfolio.id)).where(
                    and_(
                        Portfolio.wallet_id.in_(select(wallet_ids.c.id)),
                        Portfolio.followed_bot.is_(True),
                    )
                )
            ).scalar_one() or 0

            followed_pnl = session.execute(
                select(func.coalesce(func.sum(Portfolio.realized_pnl), 0)).where(
                    and_(
                        Portfolio.wallet_id.in_(select(wallet_ids.c.id)),
                        Portfolio.followed_bot.is_(True),
                    )
                )
            ).scalar_one() or 0

            # bot_picks tablosundaki kapanmış önerilerin getirisi
            closed_returns = session.execute(
                select(BotPick.price_at_pick, BotPick.return_pct).where(
                    BotPick.is_open.is_(False)
                )
            ).all()

            skipped_theo = Decimal("0")
            skipped_count = 0
            for price_at_pick, return_pct in closed_returns:
                if price_at_pick is None or return_pct is None:
                    continue
                skipped_count += 1
                p = self._to_decimal(price_at_pick)
                r = self._to_decimal(return_pct) / Decimal("100")
                skipped_theo += p * r

            followed_pnl_d = self._to_decimal(followed_pnl)
        finally:
            self._close(session)

        return FollowVsSkipPnL(
            followed_count=int(followed_count),
            skipped_count=skipped_count,
            followed_realized_pnl=followed_pnl_d,
            skipped_theoretical_pnl=skipped_theo,
            delta=followed_pnl_d - skipped_theo,
        )


__all__ = [
    "USER_ACTIONS",
    "ManualTradeMarker",
    "PendingRecommendationView",
    "BotFollowRate",
    "FollowVsSkipPnL",
    "ManualParallelService",
]
