"""Tekil veri kaynakları (yfinance, Stooq, Alpha Vantage, İş Yatırım, TCMB, ...).

Faz 1 — birincil fiyat kaynakları:
    - YFinanceSource     (yfinance)     : NYSE/NASDAQ + BIST OHLCV/quote
    - StooqSource        (stooq)        : NYSE/NASDAQ + BIST yedek
    - AlphaVantageSource (alphavantage) : doğrulama (günde 25 limit)
    - IsYatirimSource    (isyatirim)    : BIST birincil
    - TCMBSource         (tcmb)         : SADECE FX (USD/TRY vb.)

Faz 2 — haber, scraping ve sosyal kaynaklar:
    - RSSSource          (rss)          : Reuters / Bloomberg TR / Mynet / Dünya / KAP RSS
    - KapSource          (kap)          : KAP özel durum bildirimleri + finansal tablo
    - TradingViewSource  (tradingview)  : tvdatafeed + teknik analiz özeti
    - InvestingSource    (investing)    : Playwright/httpx scrape — teknik & analist
    - FinvizSource       (finviz)       : finvizfinance — ABD temel veriler
    - RedditSource       (reddit)       : praw — subreddit gönderileri
"""

from app.data.sources.alphavantage_source import AlphaVantageSource
from app.data.sources.finviz_source import FinvizSource, FundamentalData
from app.data.sources.investing_source import InvestingSource, InvSummary
from app.data.sources.isyatirim_source import IsYatirimSource
from app.data.sources.kap_source import KapDisclosure, KapSource
from app.data.sources.reddit_source import RedditPost, RedditSource
from app.data.sources.rss_source import NewsItem, RSSSource
from app.data.sources.stooq_source import StooqSource
from app.data.sources.tcmb_source import TCMBSource
from app.data.sources.tradingview_source import TradingViewSource, TVSummary
from app.data.sources.yfinance_source import YFinanceSource

__all__ = [
    # Faz 1
    "YFinanceSource",
    "StooqSource",
    "AlphaVantageSource",
    "IsYatirimSource",
    "TCMBSource",
    # Faz 2
    "RSSSource",
    "NewsItem",
    "KapSource",
    "KapDisclosure",
    "TradingViewSource",
    "TVSummary",
    "InvestingSource",
    "InvSummary",
    "FinvizSource",
    "FundamentalData",
    "RedditSource",
    "RedditPost",
]
