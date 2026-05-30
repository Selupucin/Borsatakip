"""Hesap + cüzdan + işlem geçmişini SIFIRLA — yeni baştan başlamak için.

Sileceği veri:
- cash_flows (tüm para hareketleri)
- portfolio (tüm işlem geçmişi)
- open_positions (tüm açık pozisyonlar)
- bot_picks (tüm bot önerileri)
- recommendations (tüm geçmiş öneriler — opsiyonel, --keep-recs ile koru)

Sıfırlayacağı:
- wallets.allocated = 0
- wallets.cash_balance = 0
- user_account.cash_balance = 0
- user_account.initial_balance = 0
- user_account.trading_mode = 'manual_parallel'

DİKKAT: instruments, watchlists, watchlist_items, price_history, technical_signals,
news_feed, data_sources, fx_rates, settings — KORUNUR.

Kullanım:
    python scripts/reset_account.py [--keep-recs]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from sqlalchemy import delete, update

from app.db.models import (
    BotPick,
    CashFlow,
    OpenPosition,
    Portfolio,
    Recommendation,
    UserAccount,
    Wallet,
)
from app.db.session import SessionLocal


def reset(keep_recs: bool = False) -> dict:
    deleted = {}
    with SessionLocal() as session:
        # Sırasını koru: önce çocuk tablolar, sonra parent'lar
        deleted["cash_flows"] = session.execute(delete(CashFlow)).rowcount or 0
        deleted["portfolio"] = session.execute(delete(Portfolio)).rowcount or 0
        deleted["open_positions"] = session.execute(delete(OpenPosition)).rowcount or 0
        deleted["bot_picks"] = session.execute(delete(BotPick)).rowcount or 0
        if not keep_recs:
            deleted["recommendations"] = session.execute(delete(Recommendation)).rowcount or 0

        # Cüzdanları sıfırla
        wallet_count = session.execute(
            update(Wallet).values(allocated=0.0, cash_balance=0.0)
        ).rowcount or 0
        deleted["wallets_reset"] = wallet_count

        # Hesabı sıfırla (silme, sadece reset)
        acc_count = session.execute(
            update(UserAccount).values(
                cash_balance=0.0,
                initial_balance=0.0,
                trading_mode="manual_parallel",
                risk_threshold=50.0,
            )
        ).rowcount or 0
        deleted["accounts_reset"] = acc_count

        session.commit()
    return deleted


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--keep-recs", action="store_true", help="recommendations tablosunu koru")
    args = p.parse_args()

    print("[reset_account] Hesap + cüzdan + işlem geçmişi sıfırlanıyor...")
    result = reset(keep_recs=args.keep_recs)
    for k, v in result.items():
        print(f"  {k}: {v}")
    print("[reset_account] Tamam.")
