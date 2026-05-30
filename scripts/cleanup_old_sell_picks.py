"""Eski SELL bot_picks satırlarını temizler (yeni mantık: SAT sadece açık pozisyonda)."""
import sqlite3
from pathlib import Path

db_path = Path(__file__).resolve().parents[1] / "borsa_bot.db"
db = sqlite3.connect(str(db_path))
c = db.cursor()
c.execute("DELETE FROM bot_picks WHERE action='SELL'")
print(f"eski SAT pick silindi: {c.rowcount}")
db.commit()
for t in ["bot_picks", "recommendations", "price_history", "news_feed", "fx_rates", "open_positions"]:
    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    print(f"  {t}: {n}")
db.close()
