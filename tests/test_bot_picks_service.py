"""BotPicksService (app/portfolio/bot_picks.py) birim testleri.

In-memory SQLite üzerinde gerçek INSERT/UPDATE akışı + return_pct hesabı.
``session_factory`` test ortamında ``sessionmaker(bind=sync_engine)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.portfolio.bot_picks import BotPicksService, EXPIRE_DAYS
from app.db.models import BotPick, Instrument


@pytest.fixture
def session_factory(sync_engine):
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(
        bind=sync_engine, autoflush=False, expire_on_commit=False
    )


@pytest.fixture
def aapl_id(sync_engine):
    """AAPL instrument seed et, id döndür."""
    from sqlalchemy.orm import Session

    with Session(sync_engine) as s:
        instr = Instrument(ticker="AAPL", name="Apple")
        s.add(instr)
        s.commit()
        return int(instr.id)


# ---------------------------------------------------------------------------
# add_pick
# ---------------------------------------------------------------------------


class TestAddPick:
    @pytest.mark.asyncio
    async def test_add_pick_inserts_row_with_open_status(self, session_factory, aapl_id):
        from sqlalchemy.orm import Session

        svc = BotPicksService(session_factory)
        pick_id = await svc.add_pick(
            instrument_id=aapl_id,
            timeframe="short",
            action="BUY",
            confidence=75.0,
            current_price=Decimal("100"),
            target_price=Decimal("110"),
        )

        with Session(session_factory.kw["bind"]) as s:
            row = s.get(BotPick, pick_id)
            assert row is not None
            assert row.is_open is True
            assert row.outcome == "open"
            assert row.action == "BUY"
            assert row.timeframe == "short"
            assert row.confidence == pytest.approx(75.0, abs=1e-6)
            assert row.price_at_pick == pytest.approx(100.0, abs=1e-6)
            assert row.target_price == pytest.approx(110.0, abs=1e-6)

    @pytest.mark.asyncio
    async def test_add_pick_validates_action_and_timeframe(self, session_factory, aapl_id):
        svc = BotPicksService(session_factory)
        with pytest.raises(ValueError):
            await svc.add_pick(aapl_id, "weekly", "BUY", 50.0, Decimal("100"), None)
        with pytest.raises(ValueError):
            await svc.add_pick(aapl_id, "short", "BORROW", 50.0, Decimal("100"), None)


# ---------------------------------------------------------------------------
# close_pick + return_pct
# ---------------------------------------------------------------------------


class TestClosePick:
    @pytest.mark.asyncio
    async def test_close_pick_buy_sets_positive_return_for_gain(
        self, session_factory, aapl_id
    ):
        from sqlalchemy.orm import Session

        svc = BotPicksService(session_factory)
        pick_id = await svc.add_pick(
            aapl_id, "short", "BUY", 70.0, Decimal("100"), Decimal("110")
        )
        await svc.close_pick(pick_id, Decimal("110"), "hit_target")

        with Session(session_factory.kw["bind"]) as s:
            row = s.get(BotPick, pick_id)
            assert row.is_open is False
            assert row.closed_at is not None
            assert row.outcome == "hit_target"
            # BUY: (110-100)/100 * 100 = +10
            assert row.return_pct == pytest.approx(10.0, abs=1e-3)

    @pytest.mark.asyncio
    async def test_close_pick_sell_inverts_sign(self, session_factory, aapl_id):
        """SELL action: fiyat düşünce pozitif getiri."""
        from sqlalchemy.orm import Session

        svc = BotPicksService(session_factory)
        pick_id = await svc.add_pick(
            aapl_id, "short", "SELL", 70.0, Decimal("100"), Decimal("90")
        )
        await svc.close_pick(pick_id, Decimal("90"), "hit_target")

        with Session(session_factory.kw["bind"]) as s:
            row = s.get(BotPick, pick_id)
            # base = (90-100)/100*100 = -10; SELL → +10
            assert row.return_pct == pytest.approx(10.0, abs=1e-3)

    @pytest.mark.asyncio
    async def test_close_pick_already_closed_raises(self, session_factory, aapl_id):
        svc = BotPicksService(session_factory)
        pick_id = await svc.add_pick(
            aapl_id, "short", "BUY", 50.0, Decimal("100"), Decimal("110")
        )
        await svc.close_pick(pick_id, Decimal("110"), "hit_target")
        with pytest.raises(ValueError, match="zaten kapalı"):
            await svc.close_pick(pick_id, Decimal("115"), "hit_target")

    @pytest.mark.asyncio
    async def test_close_pick_unknown_id_raises_lookup(self, session_factory):
        svc = BotPicksService(session_factory)
        with pytest.raises(LookupError):
            await svc.close_pick(9999, Decimal("100"), "hit_target")


# ---------------------------------------------------------------------------
# update_open_picks: hit_target + expired
# ---------------------------------------------------------------------------


class TestUpdateOpenPicks:
    @pytest.mark.asyncio
    async def test_hit_target_closes_pick(self, session_factory, aapl_id):
        from sqlalchemy.orm import Session

        svc = BotPicksService(session_factory)
        pick_id = await svc.add_pick(
            aapl_id, "short", "BUY", 75.0, Decimal("100"), Decimal("110")
        )
        # current 115 — target 110'u geçti, hit_target tetiklenmeli.
        closed = await svc.update_open_picks({aapl_id: Decimal("115")})
        assert pick_id in closed

        with Session(session_factory.kw["bind"]) as s:
            row = s.get(BotPick, pick_id)
            assert row.is_open is False
            assert row.outcome == "hit_target"

    @pytest.mark.asyncio
    async def test_expired_no_longer_auto_closes(self, session_factory, aapl_id):
        """Süre-bazlı expire KALDIRILDI (kullanıcı geri bildirimi):
        Pick'ler artık sadece hedef fiyata ulaşınca kapanır, süre dolduğunda değil."""
        from sqlalchemy.orm import Session

        svc = BotPicksService(session_factory)
        old_ts = datetime.now(tz=timezone.utc) - timedelta(days=400)
        with Session(session_factory.kw["bind"]) as s:
            row = BotPick(
                instrument_id=aapl_id,
                timeframe="short",
                action="BUY",
                confidence=50.0,
                price_at_pick=100.0,
                target_price=110.0,
                picked_at=old_ts,
                is_open=True,
                outcome="open",
            )
            s.add(row)
            s.commit()
            pick_id = int(row.id)

        # Current price 105 — target'a ulaşmadı; artık otomatik kapanmamalı.
        closed = await svc.update_open_picks({aapl_id: Decimal("105")})
        assert pick_id not in closed

        with Session(session_factory.kw["bind"]) as s:
            row = s.get(BotPick, pick_id)
            assert row.is_open is True
            assert row.outcome == "open"


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------


class TestGetStats:
    @pytest.mark.asyncio
    async def test_stats_with_no_picks_returns_zero_success_rate(
        self, session_factory, aapl_id
    ):
        svc = BotPicksService(session_factory)
        stats = await svc.get_stats("short")
        # Sıfıra bölme koruması
        assert stats.success_rate == 0.0
        assert stats.total_picks == 0
        assert stats.closed_picks == 0
        assert stats.avg_return_pct is None
        assert stats.avg_hold_days is None

    @pytest.mark.asyncio
    async def test_stats_computes_success_rate_and_avg_return(
        self, session_factory, aapl_id
    ):
        from sqlalchemy.orm import Session

        svc = BotPicksService(session_factory)
        now = datetime.now(tz=timezone.utc)

        with Session(session_factory.kw["bind"]) as s:
            # 2 closed hit_target (+10%, +20%) → success rate = 2/3
            # 1 closed expired (-5%)
            # 1 open
            rows = [
                BotPick(
                    instrument_id=aapl_id, timeframe="short", action="BUY",
                    confidence=80.0, price_at_pick=100.0, target_price=110.0,
                    picked_at=now - timedelta(days=5), is_open=False,
                    closed_at=now - timedelta(days=2),
                    price_at_close=110.0, outcome="hit_target", return_pct=10.0,
                ),
                BotPick(
                    instrument_id=aapl_id, timeframe="short", action="BUY",
                    confidence=80.0, price_at_pick=100.0, target_price=120.0,
                    picked_at=now - timedelta(days=4), is_open=False,
                    closed_at=now - timedelta(days=1),
                    price_at_close=120.0, outcome="hit_target", return_pct=20.0,
                ),
                BotPick(
                    instrument_id=aapl_id, timeframe="short", action="BUY",
                    confidence=80.0, price_at_pick=100.0, target_price=120.0,
                    picked_at=now - timedelta(days=10), is_open=False,
                    closed_at=now - timedelta(days=2),
                    price_at_close=95.0, outcome="expired", return_pct=-5.0,
                ),
                BotPick(
                    instrument_id=aapl_id, timeframe="short", action="BUY",
                    confidence=80.0, price_at_pick=100.0, target_price=120.0,
                    picked_at=now - timedelta(days=1), is_open=True,
                    outcome="open",
                ),
            ]
            s.add_all(rows)
            s.commit()

        stats = await svc.get_stats("short")
        assert stats.total_picks == 4
        assert stats.open_picks == 1
        assert stats.closed_picks == 3
        assert stats.hit_target_count == 2
        assert stats.expired_count == 1
        assert stats.success_rate == pytest.approx(2 / 3, abs=1e-6)
        # ortalama getiri (10+20-5)/3 = 8.33
        assert stats.avg_return_pct is not None
        assert float(stats.avg_return_pct) == pytest.approx(8.3333, abs=1e-3)
        assert stats.avg_hold_days is not None
        assert stats.avg_hold_days > 0
