"""Temel hisse listesini ``instruments`` tablosuna yükler (idempotent).

Kapsam
------
- BIST 30 (yaklaşık) — 30 hisse, ``currency='TRY'``, ``exchange='BIST'``.
- ABD majör 20 hisse — ``currency='USD'``, ``exchange in {'NYSE','NASDAQ'}``.

Idempotent: Aynı ticker varsa yeniden eklenmez (UNIQUE constraint zaten korur,
ama biz önceden kontrol ederek temiz log üretiriz).

Kullanım::

    python scripts/seed_instruments.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import select

from app.db.models import Instrument
from app.db.session import SessionLocal


# ---------------------------------------------------------------------------
# Veri tanımları
# ---------------------------------------------------------------------------


# BIST hisseleri — ticker, ad, sektör. (BIST 30 yaklaşık kapsama.)
# Not: doküman §3.4 BIST için ``isyatirim`` birincil kaynak; sektörler makul
# tahminle. analysis-engine bu listeyi sonradan güncelleyebilir.
# Endeks instrumentları — benchmark hesabı için zorunlu.
# yfinance sembolleri: XU100.IS (BIST 100), ^GSPC (S&P 500)
INDEX_INSTRUMENTS: list[tuple[str, str, str, str, str]] = [
    # (ticker, ad, borsa, currency, sektör)
    ("XU100", "BIST 100 Endeksi", "BIST", "TRY", "Endeks"),
    ("^GSPC", "S&P 500 Endeksi", "NYSE", "USD", "Endeks"),
]


BIST_STOCKS: list[tuple[str, str, str]] = [
    ("THYAO", "Türk Hava Yolları", "Havacılık"),
    ("AKBNK", "Akbank", "Bankacılık"),
    ("GARAN", "Garanti BBVA", "Bankacılık"),
    ("ISCTR", "Türkiye İş Bankası", "Bankacılık"),
    ("YKBNK", "Yapı ve Kredi Bankası", "Bankacılık"),
    ("ASELS", "Aselsan", "Savunma"),
    ("EREGL", "Ereğli Demir Çelik", "Demir Çelik"),
    ("KCHOL", "Koç Holding", "Holding"),
    ("SAHOL", "Sabancı Holding", "Holding"),
    ("BIMAS", "BİM Birleşik Mağazalar", "Perakende"),
    ("FROTO", "Ford Otosan", "Otomotiv"),
    ("TUPRS", "Tüpraş", "Enerji / Rafineri"),
    ("SISE", "Şişe Cam", "Cam"),
    ("KOZAL", "Koza Altın", "Madencilik"),
    ("KOZAA", "Koza Anadolu Metal", "Madencilik"),
    ("PGSUS", "Pegasus", "Havacılık"),
    ("TKFEN", "Tekfen Holding", "İnşaat / Holding"),
    ("TAVHL", "TAV Havalimanları", "Havacılık"),
    ("ARCLK", "Arçelik", "Beyaz Eşya"),
    ("VESTL", "Vestel", "Elektronik"),
    ("HALKB", "Halkbank", "Bankacılık"),
    ("VAKBN", "VakıfBank", "Bankacılık"),
    ("MGROS", "Migros", "Perakende"),
    ("TCELL", "Turkcell", "Telekomünikasyon"),
    ("TTKOM", "Türk Telekom", "Telekomünikasyon"),
    ("EKGYO", "Emlak Konut GYO", "Gayrimenkul"),
    ("ENKAI", "Enka İnşaat", "İnşaat"),
    ("PETKM", "Petkim", "Petrokimya"),
    ("KRDMD", "Kardemir (D)", "Demir Çelik"),
    ("HEKTS", "Hektaş", "Kimya / Tarım"),
]


# ABD hisseleri — ticker, ad, borsa, sektör.
US_STOCKS: list[tuple[str, str, str, str]] = [
    ("AAPL", "Apple Inc.", "NASDAQ", "Teknoloji"),
    ("MSFT", "Microsoft Corp.", "NASDAQ", "Teknoloji"),
    ("GOOGL", "Alphabet Inc. (Class A)", "NASDAQ", "Teknoloji"),
    ("AMZN", "Amazon.com Inc.", "NASDAQ", "E-Ticaret / Bulut"),
    ("META", "Meta Platforms Inc.", "NASDAQ", "Teknoloji / Sosyal Medya"),
    ("NVDA", "NVIDIA Corp.", "NASDAQ", "Yarı İletken"),
    ("TSLA", "Tesla Inc.", "NASDAQ", "Otomotiv / EV"),
    ("AMD", "Advanced Micro Devices", "NASDAQ", "Yarı İletken"),
    ("JPM", "JPMorgan Chase & Co.", "NYSE", "Bankacılık"),
    ("BAC", "Bank of America", "NYSE", "Bankacılık"),
    ("V", "Visa Inc.", "NYSE", "Ödeme Sistemleri"),
    ("MA", "Mastercard Inc.", "NYSE", "Ödeme Sistemleri"),
    ("JNJ", "Johnson & Johnson", "NYSE", "Sağlık"),
    ("PFE", "Pfizer Inc.", "NYSE", "İlaç"),
    ("KO", "The Coca-Cola Company", "NYSE", "İçecek"),
    ("PEP", "PepsiCo Inc.", "NASDAQ", "İçecek / Gıda"),
    ("WMT", "Walmart Inc.", "NYSE", "Perakende"),
    ("DIS", "The Walt Disney Company", "NYSE", "Medya / Eğlence"),
    ("NFLX", "Netflix Inc.", "NASDAQ", "Medya / Streaming"),
    ("INTC", "Intel Corp.", "NASDAQ", "Yarı İletken"),
]


# ---------------------------------------------------------------------------
# Seed mantığı
# ---------------------------------------------------------------------------


def _seed_bist(session) -> int:
    existing = {
        row[0]
        for row in session.execute(
            select(Instrument.ticker).where(Instrument.exchange == "BIST")
        ).all()
    }
    added = 0
    for ticker, name, sector in BIST_STOCKS:
        if ticker in existing:
            continue
        session.add(
            Instrument(
                ticker=ticker,
                name=name,
                exchange="BIST",
                sector=sector,
                currency="TRY",
            )
        )
        added += 1
    return added


def _seed_us(session) -> int:
    existing = {
        row[0]
        for row in session.execute(
            select(Instrument.ticker).where(
                Instrument.exchange.in_(("NYSE", "NASDAQ"))
            )
        ).all()
    }
    added = 0
    for ticker, name, exchange, sector in US_STOCKS:
        if ticker in existing:
            continue
        session.add(
            Instrument(
                ticker=ticker,
                name=name,
                exchange=exchange,
                sector=sector,
                currency="USD",
            )
        )
        added += 1
    return added


def _seed_indices(session) -> int:
    existing = {
        row[0]
        for row in session.execute(
            select(Instrument.ticker).where(Instrument.sector == "Endeks")
        ).all()
    }
    added = 0
    for ticker, name, exchange, currency, sector in INDEX_INSTRUMENTS:
        if ticker in existing:
            continue
        session.add(
            Instrument(
                ticker=ticker,
                name=name,
                exchange=exchange,
                sector=sector,
                currency=currency,
            )
        )
        added += 1
    return added


def run() -> tuple[int, int, int]:
    """BIST + ABD + endeks hisselerini seed et."""

    with SessionLocal() as session:
        bist_added = _seed_bist(session)
        us_added = _seed_us(session)
        idx_added = _seed_indices(session)
        session.commit()

    print(
        f"[seed_instruments] BIST: {bist_added} yeni / {len(BIST_STOCKS)} toplam; "
        f"ABD: {us_added} yeni / {len(US_STOCKS)} toplam; "
        f"Endeks: {idx_added} yeni / {len(INDEX_INSTRUMENTS)} toplam."
    )
    return bist_added, us_added, idx_added


if __name__ == "__main__":
    run()
