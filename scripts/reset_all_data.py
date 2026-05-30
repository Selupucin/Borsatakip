"""Tüm uygulama verisini sıfırla — sadece kuru şema + seed (instruments + data_sources).

Sileceği:
- price_history, technical_signals, news_feed, discrepancies, fx_rates
- recommendations, backtest_results, bot_picks
- portfolio, open_positions, cash_flows, watchlist_items, watchlists, alerts

Sıfırlayacağı:
- wallets.allocated/cash_balance = 0
- user_account.cash_balance/initial_balance = 0, trading_mode='manual_parallel', risk_threshold=50

Koruyacağı:
- instruments (BIST + ABD + endeks listesi)
- data_sources (kaynaklar)
- settings (tema vb.)
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import delete, update

from app.db.models import (
    Alert,
    BacktestResult,
    BotPick,
    CashFlow,
    Discrepancy,
    FxRate,
    NewsFeed,
    OpenPosition,
    Portfolio,
    PriceHistory,
    Recommendation,
    TechnicalSignal,
    UserAccount,
    Wallet,
    Watchlist,
    WatchlistItem,
)
from app.db.session import SessionLocal


def reset_all() -> dict:
    deleted: dict[str, int] = {}
    with SessionLocal() as session:
        # Bağımlı tablolar önce
        deleted["alerts"] = session.execute(delete(Alert)).rowcount or 0
        deleted["watchlist_items"] = session.execute(delete(WatchlistItem)).rowcount or 0
        deleted["watchlists"] = session.execute(delete(Watchlist)).rowcount or 0
        deleted["technical_signals"] = session.execute(delete(TechnicalSignal)).rowcount or 0
        deleted["news_feed"] = session.execute(delete(NewsFeed)).rowcount or 0
        deleted["recommendations"] = session.execute(delete(Recommendation)).rowcount or 0
        deleted["bot_picks"] = session.execute(delete(BotPick)).rowcount or 0
        deleted["backtest_results"] = session.execute(delete(BacktestResult)).rowcount or 0
        deleted["discrepancies"] = session.execute(delete(Discrepancy)).rowcount or 0
        deleted["price_history"] = session.execute(delete(PriceHistory)).rowcount or 0
        deleted["fx_rates"] = session.execute(delete(FxRate)).rowcount or 0
        deleted["open_positions"] = session.execute(delete(OpenPosition)).rowcount or 0
        deleted["portfolio"] = session.execute(delete(Portfolio)).rowcount or 0
        deleted["cash_flows"] = session.execute(delete(CashFlow)).rowcount or 0

        # Cüzdanları sıfırla
        wallets_reset = session.execute(
            update(Wallet).values(allocated=0.0, cash_balance=0.0)
        ).rowcount or 0
        deleted["wallets_reset"] = wallets_reset

        # Hesabı sıfırla
        acc_reset = session.execute(
            update(UserAccount).values(
                cash_balance=0.0,
                initial_balance=0.0,
                trading_mode="manual_parallel",
                risk_threshold=50.0,
            )
        ).rowcount or 0
        deleted["accounts_reset"] = acc_reset

        session.commit()
    return deleted


if __name__ == "__main__":
    print("[reset_all_data] TÜM uygulama verisini sıfırlıyorum...")
    result = reset_all()
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("[reset_all_data] Tamam. Instruments + data_sources korundu.")
