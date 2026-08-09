"""Central config, all overridable via environment / .env."""
import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "data" / "news_screener.db"
TIER_TABLE_PATH = BASE_DIR / "data" / "tier_table.json"
SP500_TICKERS_PATH = BASE_DIR / "data" / "sp500_tickers.json"

FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "75"))
CLASSIFY_CONFIDENCE_THRESHOLD = float(os.environ.get("CLASSIFY_CONFIDENCE_THRESHOLD", "0.6"))


def _load_default_watchlist():
    try:
        with open(SP500_TICKERS_PATH) as f:
            return json.load(f)["tickers"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "GOOGL", "META", "AMD"]


# Tickers polled per-symbol for company news (Finnhub, Yahoo Finance). SEC
# EDGAR's filing feed already covers every filer regardless of this list.
# Defaults to a static S&P 500-ish snapshot (data/sp500_tickers.json);
# set WATCHLIST in .env to a comma-separated list to override with your own.
_watchlist_env = os.environ.get("WATCHLIST", "").strip()
WATCHLIST = (
    [t.strip().upper() for t in _watchlist_env.split(",") if t.strip()]
    if _watchlist_env
    else _load_default_watchlist()
)

# Per-poll-cycle batch size for per-ticker news calls, rotating through
# WATCHLIST over multiple cycles so a large watchlist doesn't blow past a
# free-tier rate limit in one poll. 40 tickers/cycle at a 75s interval stays
# comfortably under Finnhub's 60 calls/min free-tier limit.
TICKER_BATCH_SIZE = int(os.environ.get("TICKER_BATCH_SIZE", "40"))

TICKER_CACHE_TTL_SECONDS = 24 * 60 * 60

API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))

# SEC requires a descriptive User-Agent identifying the requester (fair access
# policy) on all requests, including the free real-time filings feed.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "market-news-screener/0.1 (contact: jamesbarbati2003@gmail.com)"
)
