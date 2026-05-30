"""Kullanıcı takip listesi (watchlist) servisi — Faz 2.

Doküman §6.2 (Kullanıcı Takip Listesi) referans alınmıştır.

Sorumluluk:
- Birden fazla isimlendirilmiş watchlist (örn. "Uzun Vade", "Temettü").
- Her listeye ticker bazlı hisse ekleme / çıkarma (idempotent).
- Listeleme: sade meta veya runtime fiyat/sinyal ile birleşik görünüm.
- Sürükle-bırak için ``sort_order`` güncellemesi (``reorder``).

Tasarım kuralları:
- ``session_factory`` callable; her çağrıda yeni ``Session`` döndürmeli
  (``sessionmaker(bind=engine)`` veya context-manager fabrikası — bkz.
  ``app/analysis/news_aggregator.py`` ile aynı sözleşme).
- API **async** — UI background worker'larında doğal kullanım. DB erişimi
  sync SQLAlchemy session ile yapılır (modeller sync); ``AsyncSession``
  terfisi Faz 3'te değerlendirilebilir.
- Para alanı **YOK** — watchlist sadece izleme listesi; ``Decimal`` yalnız
  runtime fiyat alanlarında (Faz 3'te doldurulacak) tutulur.
- DB CHECK constraint'leri ile uyum: ``watchlist_items`` tablosunda
  ``(watchlist_id, instrument_id)`` UNIQUE; CASCADE delete tetiklenir.

Faz 3'te eklenecekler (yapılmadı, bilinçli):
- ``WatchlistItemView`` içindeki ``current_price``, ``daily_change_pct``,
  ``volume``, ``bot_action``, ``bot_confidence`` alanlarının fiyat /
  öneri tablolarından join'lenerek doldurulması.
- UI tarafında sürükle-bırak ile ``reorder`` çağrısı, hızlı arama,
  filtreleme (ui-developer).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, Optional

from loguru import logger
from sqlalchemy import and_, delete, func, select, update


# ---------------------------------------------------------------------------
# Veri yapıları
# ---------------------------------------------------------------------------


@dataclass
class WatchlistSummary:
    """Watchlist meta + öğe sayısı."""

    id: int
    name: str
    items_count: int
    created_at: datetime


@dataclass
class WatchlistItemView:
    """Watchlist içindeki tek bir hisse satırı.

    Faz 2'de yalnızca statik alanlar (``instrument_id``..``added_at``)
    doldurulur. Fiyat ve sinyal alanları Faz 3'te ``with_quotes=True``
    çağrısında doldurulacak.
    """

    instrument_id: int
    ticker: str
    name: str
    exchange: str
    sort_order: int
    added_at: datetime
    # ---- Faz 3'te doldurulacak runtime alanları ----
    current_price: Optional[Decimal] = None
    daily_change_pct: Optional[Decimal] = None
    volume: Optional[int] = None
    bot_action: Optional[str] = None       # 'BUY' / 'HOLD' / 'SELL'
    bot_confidence: Optional[float] = None


# ---------------------------------------------------------------------------
# Servis
# ---------------------------------------------------------------------------


class WatchlistService:
    """CRUD + öğe yönetimi.

    Parameters
    ----------
    session_factory:
        Çağrıldığında yeni bir SQLAlchemy ``Session`` döndüren callable.
    """

    def __init__(self, session_factory: Callable[[], object]) -> None:
        self.session_factory = session_factory

    # ------------------------------------------------------------- helpers

    def _session(self):
        """Yeni bir session aç. Caller ``close()`` çağırmalı."""
        return self.session_factory()

    @staticmethod
    def _close(session) -> None:
        close = getattr(session, "close", None)
        if callable(close):
            close()

    # ------------------------------------------------------------- CRUD: watchlist

    async def create(self, name: str) -> int:
        """Yeni watchlist oluştur, ``id`` döndür.

        İsim boş veya whitespace olamaz. Aynı isimde başka liste olup
        olmadığı kontrol edilmez (kullanıcı bilinçli olarak duplicate
        isim isteyebilir).
        """

        clean = (name or "").strip()
        if not clean:
            raise ValueError("Watchlist adı boş olamaz.")

        from app.db.models import Watchlist  # noqa: WPS433

        session = self._session()
        try:
            row = Watchlist(name=clean)
            session.add(row)
            session.flush()  # id'yi al
            wl_id = int(row.id)
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info("Watchlist oluşturuldu: id={}, name={!r}", wl_id, clean)
        return wl_id

    async def rename(self, watchlist_id: int, new_name: str) -> None:
        """Watchlist adını güncelle. Yoksa ``LookupError``."""

        clean = (new_name or "").strip()
        if not clean:
            raise ValueError("Watchlist adı boş olamaz.")

        from app.db.models import Watchlist  # noqa: WPS433

        session = self._session()
        try:
            row = session.get(Watchlist, watchlist_id)
            if row is None:
                raise LookupError(f"Watchlist bulunamadı: id={watchlist_id}")
            row.name = clean
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info("Watchlist yeniden adlandırıldı: id={} → {!r}", watchlist_id, clean)

    async def delete(self, watchlist_id: int) -> None:
        """Watchlist'i sil. İlişkili öğeler CASCADE ile düşer."""

        from app.db.models import Watchlist  # noqa: WPS433

        session = self._session()
        try:
            row = session.get(Watchlist, watchlist_id)
            if row is None:
                logger.warning("Silinmeye çalışılan watchlist yok: id={}", watchlist_id)
                return
            session.delete(row)
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info("Watchlist silindi: id={}", watchlist_id)

    async def list_all(self) -> list[WatchlistSummary]:
        """Tüm watchlist'leri öğe sayısı ile döndür.

        Sıralama: ``sort_order ASC``, ardından ``created_at ASC``.
        """

        from app.db.models import Watchlist, WatchlistItem  # noqa: WPS433

        session = self._session()
        try:
            stmt = (
                select(
                    Watchlist.id,
                    Watchlist.name,
                    Watchlist.created_at,
                    func.count(WatchlistItem.id),
                )
                .outerjoin(WatchlistItem, WatchlistItem.watchlist_id == Watchlist.id)
                .group_by(Watchlist.id, Watchlist.name, Watchlist.created_at, Watchlist.sort_order)
                .order_by(Watchlist.sort_order.asc(), Watchlist.created_at.asc())
            )
            rows = session.execute(stmt).all()
        finally:
            self._close(session)

        return [
            WatchlistSummary(
                id=int(r[0]),
                name=str(r[1] or ""),
                items_count=int(r[3] or 0),
                created_at=r[2],
            )
            for r in rows
        ]

    # ------------------------------------------------------------- items

    async def add_item(self, watchlist_id: int, ticker: str) -> int:
        """Ticker'a göre instrument bul ve listeye ekle.

        Returns
        -------
        int
            Eklenen (veya zaten var olan) ``WatchlistItem.id``.

        Raises
        ------
        LookupError
            Watchlist veya instrument bulunamazsa.
        """

        clean_ticker = (ticker or "").strip().upper()
        if not clean_ticker:
            raise ValueError("Ticker boş olamaz.")

        from app.db.models import Instrument, Watchlist, WatchlistItem  # noqa: WPS433

        session = self._session()
        try:
            wl = session.get(Watchlist, watchlist_id)
            if wl is None:
                raise LookupError(f"Watchlist bulunamadı: id={watchlist_id}")

            instr = session.execute(
                select(Instrument).where(Instrument.ticker == clean_ticker)
            ).scalar_one_or_none()
            if instr is None:
                raise LookupError(
                    f"Instrument bulunamadı: ticker={clean_ticker!r}. "
                    "Önce data-collector ile seed edilmiş olmalı."
                )

            # Idempotent: zaten ekliyse mevcut id'yi döndür.
            existing = session.execute(
                select(WatchlistItem).where(
                    and_(
                        WatchlistItem.watchlist_id == watchlist_id,
                        WatchlistItem.instrument_id == instr.id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                logger.debug(
                    "Watchlist item zaten mevcut: wl={}, instr={} → id={}",
                    watchlist_id,
                    instr.id,
                    existing.id,
                )
                return int(existing.id)

            # Yeni satır sona eklensin: mevcut max(sort_order)+1.
            max_order = session.execute(
                select(func.coalesce(func.max(WatchlistItem.sort_order), -1)).where(
                    WatchlistItem.watchlist_id == watchlist_id
                )
            ).scalar_one()
            new_order = int(max_order) + 1

            item = WatchlistItem(
                watchlist_id=watchlist_id,
                instrument_id=int(instr.id),
                sort_order=new_order,
            )
            session.add(item)
            session.flush()
            item_id = int(item.id)

            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Watchlist item eklendi: wl={}, ticker={!r} (instr_id={}), id={}",
            watchlist_id,
            clean_ticker,
            int(instr.id),
            item_id,
        )
        return item_id

    async def remove_item(self, watchlist_id: int, instrument_id: int) -> None:
        """Watchlist'ten bir hisseyi çıkar. Yoksa sessizce geç."""

        from app.db.models import WatchlistItem  # noqa: WPS433

        session = self._session()
        try:
            stmt = delete(WatchlistItem).where(
                and_(
                    WatchlistItem.watchlist_id == watchlist_id,
                    WatchlistItem.instrument_id == instrument_id,
                )
            )
            result = session.execute(stmt)
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
            deleted_count = getattr(result, "rowcount", 0) or 0
        finally:
            self._close(session)

        logger.info(
            "Watchlist item kaldırıldı: wl={}, instr={}, rows={}",
            watchlist_id,
            instrument_id,
            deleted_count,
        )

    async def list_items(
        self, watchlist_id: int, with_quotes: bool = False
    ) -> list[WatchlistItemView]:
        """Liste içeriğini ``sort_order`` sırasıyla döndür.

        Parameters
        ----------
        with_quotes:
            ``True`` ise son ``price_history`` + son ``recommendation`` join'lenir.
            **Faz 2'de bu kısım sadece şema olarak hazır** — runtime
            doldurma Faz 3'te ``data-collector`` ve ``analysis-engine``
            çıktılarıyla yapılacaktır (TODO işaretli).
        """

        from app.db.models import (  # noqa: WPS433
            Instrument,
            PriceHistory,
            Recommendation,
            WatchlistItem,
        )

        session = self._session()
        try:
            stmt = (
                select(
                    WatchlistItem.instrument_id,
                    Instrument.ticker,
                    Instrument.name,
                    Instrument.exchange,
                    WatchlistItem.sort_order,
                    WatchlistItem.added_at,
                )
                .join(Instrument, Instrument.id == WatchlistItem.instrument_id)
                .where(WatchlistItem.watchlist_id == watchlist_id)
                .order_by(WatchlistItem.sort_order.asc(), WatchlistItem.added_at.asc())
            )
            rows = session.execute(stmt).all()

            views: list[WatchlistItemView] = [
                WatchlistItemView(
                    instrument_id=int(r[0]),
                    ticker=str(r[1] or ""),
                    name=str(r[2] or ""),
                    exchange=str(r[3] or ""),
                    sort_order=int(r[4] or 0),
                    added_at=r[5],
                )
                for r in rows
            ]

            if with_quotes and views:
                # Her instrument için son fiyat ve son öneri — N+1 olmaması
                # için tek seferde IN sorgusu ile çekiyoruz; ardından
                # subquery ile "her instrument için en yeni" filtrelemesi.
                instr_ids = [v.instrument_id for v in views]

                # ---- Son fiyat (verified varsa onu kullan) ----
                price_subq = (
                    select(
                        PriceHistory.instrument_id,
                        func.max(PriceHistory.timestamp).label("max_ts"),
                    )
                    .where(PriceHistory.instrument_id.in_(instr_ids))
                    .group_by(PriceHistory.instrument_id)
                    .subquery()
                )
                price_stmt = (
                    select(
                        PriceHistory.instrument_id,
                        PriceHistory.close,
                        PriceHistory.verified_close,
                        PriceHistory.volume,
                    )
                    .join(
                        price_subq,
                        and_(
                            PriceHistory.instrument_id == price_subq.c.instrument_id,
                            PriceHistory.timestamp == price_subq.c.max_ts,
                        ),
                    )
                )
                price_rows = session.execute(price_stmt).all()
                price_map: dict[int, tuple[Optional[Decimal], Optional[int]]] = {}
                for pr in price_rows:
                    chosen = pr[2] if pr[2] is not None else pr[1]
                    price_map[int(pr[0])] = (
                        Decimal(str(chosen)) if chosen is not None else None,
                        int(pr[3]) if pr[3] is not None else None,
                    )

                # ---- Son öneri ----
                rec_subq = (
                    select(
                        Recommendation.instrument_id,
                        func.max(Recommendation.generated_at).label("max_gen"),
                    )
                    .where(Recommendation.instrument_id.in_(instr_ids))
                    .group_by(Recommendation.instrument_id)
                    .subquery()
                )
                rec_stmt = (
                    select(
                        Recommendation.instrument_id,
                        Recommendation.action,
                        Recommendation.confidence,
                    )
                    .join(
                        rec_subq,
                        and_(
                            Recommendation.instrument_id == rec_subq.c.instrument_id,
                            Recommendation.generated_at == rec_subq.c.max_gen,
                        ),
                    )
                )
                rec_rows = session.execute(rec_stmt).all()
                rec_map: dict[int, tuple[Optional[str], Optional[float]]] = {
                    int(r[0]): (
                        str(r[1]) if r[1] is not None else None,
                        float(r[2]) if r[2] is not None else None,
                    )
                    for r in rec_rows
                }

                for v in views:
                    price, vol = price_map.get(v.instrument_id, (None, None))
                    v.current_price = price
                    v.volume = vol
                    action, conf = rec_map.get(v.instrument_id, (None, None))
                    v.bot_action = action
                    v.bot_confidence = conf
                    # daily_change_pct hesabı için "bir önceki günün
                    # close'una" ihtiyaç var — bu Faz 3'te ayrı bir helper
                    # ile (örn. ``positions.py`` veya ``benchmark.py``)
                    # hesaplanacak.
        finally:
            self._close(session)

        return views

    async def reorder(
        self, watchlist_id: int, ordered_instrument_ids: list[int]
    ) -> None:
        """Verilen sıraya göre ``sort_order`` değerlerini günceller.

        ``ordered_instrument_ids[0]`` → ``sort_order=0``,
        ``ordered_instrument_ids[1]`` → ``sort_order=1`` …

        Listede olmayan instrument_id'ler sessizce atlanır (rowcount=0).
        Listede olup da DB'de bulunmayan id'ler hata vermez.
        """

        if not ordered_instrument_ids:
            return

        from app.db.models import WatchlistItem  # noqa: WPS433

        session = self._session()
        try:
            for idx, instr_id in enumerate(ordered_instrument_ids):
                session.execute(
                    update(WatchlistItem)
                    .where(
                        and_(
                            WatchlistItem.watchlist_id == watchlist_id,
                            WatchlistItem.instrument_id == int(instr_id),
                        )
                    )
                    .values(sort_order=idx)
                )
            commit = getattr(session, "commit", None)
            if callable(commit):
                commit()
        finally:
            self._close(session)

        logger.info(
            "Watchlist reorder: wl={}, count={}",
            watchlist_id,
            len(ordered_instrument_ids),
        )


__all__ = [
    "WatchlistSummary",
    "WatchlistItemView",
    "WatchlistService",
]
