"""FundamentalAnalyzer (app/analysis/fundamental.py) birim testleri.

Doğrulanan davranışlar:
1. ``FundamentalSnapshot`` dataclass alanları doğru initialize edilir.
2. ``FundamentalAnalyzer.score`` bonus/penalty kuralları:
   - F/K < 10 → +10; F/K > 25 → -10 (sektör avg yoksa).
   - PD/DD < 1.5 → +10; PD/DD > 3 → -10.
   - growth > %10 → +15; growth < 0 → -15.
   - D/E > 2 → -10.
   - Baseline 50; clamp [0, 100].
3. ``fetch_snapshot`` mock async kaynak ile alanları birleştirir.
"""

from __future__ import annotations

import pytest

from app.analysis.fundamental import FundamentalAnalyzer, FundamentalSnapshot


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


class TestFundamentalSnapshot:
    def test_required_fields_only(self):
        snap = FundamentalSnapshot(instrument_id=1, ticker="AAPL")
        assert snap.instrument_id == 1
        assert snap.ticker == "AAPL"
        # tüm opsiyonel alanlar None ile başlamalı
        assert snap.pe_ratio is None
        assert snap.pb_ratio is None
        assert snap.growth_yoy is None
        assert snap.debt_to_equity is None
        assert snap.roe is None
        assert snap.sources == []
        assert snap.fetched_at is not None

    def test_full_initialization(self):
        snap = FundamentalSnapshot(
            instrument_id=2,
            ticker="THYAO",
            pe_ratio=8.5,
            pb_ratio=1.2,
            growth_yoy=0.18,
            debt_to_equity=0.6,
            roe=0.22,
            sector="Aviation",
        )
        assert snap.pe_ratio == 8.5
        assert snap.pb_ratio == 1.2
        assert snap.growth_yoy == 0.18
        assert snap.sector == "Aviation"


# ---------------------------------------------------------------------------
# score()
# ---------------------------------------------------------------------------


class TestScore:
    def test_baseline_with_no_data(self):
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X")
        assert analyzer.score(snap) == pytest.approx(50.0)

    def test_low_pe_bonus(self):
        """F/K < 10 → +10 (sektör avg yok)."""
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", pe_ratio=8.0)
        assert analyzer.score(snap) == pytest.approx(60.0)

    def test_high_pe_penalty(self):
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", pe_ratio=30.0)
        assert analyzer.score(snap) == pytest.approx(40.0)

    def test_pb_low_bonus(self):
        """PD/DD < 1.5 → +10."""
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", pb_ratio=1.2)
        assert analyzer.score(snap) == pytest.approx(60.0)

    def test_pb_high_penalty(self):
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", pb_ratio=4.0)
        assert analyzer.score(snap) == pytest.approx(40.0)

    def test_growth_high_bonus(self):
        """growth > %10 → +15."""
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", growth_yoy=0.15)
        assert analyzer.score(snap) == pytest.approx(65.0)

    def test_growth_negative_penalty(self):
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", growth_yoy=-0.05)
        assert analyzer.score(snap) == pytest.approx(35.0)

    def test_debt_high_penalty(self):
        """D/E > 2 → -10."""
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", debt_to_equity=2.5
        )
        assert analyzer.score(snap) == pytest.approx(40.0)

    def test_debt_low_bonus(self):
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(
            instrument_id=1, ticker="X", debt_to_equity=0.5
        )
        assert analyzer.score(snap) == pytest.approx(55.0)

    def test_clamp_upper_bound(self):
        """Tüm bonuslar birleştiğinde dahi 100 üst sınırı aşılamaz."""
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(
            instrument_id=1,
            ticker="X",
            pe_ratio=5.0,       # +10
            pb_ratio=1.0,       # +10
            growth_yoy=0.40,    # +15
            dividend_yield=0.05,  # +5
            debt_to_equity=0.3,  # +5
            roe=0.30,           # +10
        )
        # 50 + 10 + 10 + 15 + 5 + 5 + 10 = 105 → clamp 100
        assert analyzer.score(snap) == pytest.approx(100.0)

    def test_clamp_lower_bound(self):
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(
            instrument_id=1,
            ticker="X",
            pe_ratio=50.0,      # -10
            pb_ratio=5.0,       # -10
            growth_yoy=-0.20,   # -15
            debt_to_equity=4.0,  # -10
        )
        # 50 - 10 - 10 - 15 - 10 = 5 (yine > 0)
        result = analyzer.score(snap)
        assert 0.0 <= result <= 100.0

    def test_sector_avg_below_yields_bonus(self):
        """Sektör ortalaması verildiğinde mutlak eşik atlanır."""
        analyzer = FundamentalAnalyzer()
        snap = FundamentalSnapshot(instrument_id=1, ticker="X", pe_ratio=15.0)
        # mutlak eşikle 15 nötr; sektör ort 20 ise altta → +10
        score = analyzer.score(snap, sector_avg={"pe_ratio": 20.0})
        assert score == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# fetch_snapshot
# ---------------------------------------------------------------------------


class TestFetchSnapshot:
    @pytest.mark.asyncio
    async def test_no_sources_returns_empty_snapshot(self):
        analyzer = FundamentalAnalyzer()
        snap = await analyzer.fetch_snapshot(1, "AAPL", "NASDAQ")
        assert snap.instrument_id == 1
        assert snap.ticker == "AAPL"
        assert snap.sources == []
        assert snap.pe_ratio is None

    @pytest.mark.asyncio
    async def test_single_source_merges_payload(self):
        async def finviz_fetch(ticker, exchange):
            return {
                "pe_ratio": 12.4,
                "pb_ratio": 1.8,
                "sector": "Tech",
                "growth_yoy": 0.12,
            }

        analyzer = FundamentalAnalyzer(sources={"finviz": finviz_fetch})
        snap = await analyzer.fetch_snapshot(1, "AAPL", "NASDAQ")
        assert snap.pe_ratio == 12.4
        assert snap.pb_ratio == 1.8
        assert snap.sector == "Tech"
        assert "finviz" in snap.sources

    @pytest.mark.asyncio
    async def test_failing_source_does_not_break_others(self):
        async def broken(ticker, exchange):
            raise RuntimeError("source down")

        async def ok_source(ticker, exchange):
            return {"pe_ratio": 10.0}

        analyzer = FundamentalAnalyzer(
            sources={"broken": broken, "ok": ok_source}
        )
        snap = await analyzer.fetch_snapshot(1, "X", None)
        # broken failed silently; ok worked
        assert snap.pe_ratio == 10.0
        assert "ok" in snap.sources
        assert "broken" not in snap.sources
