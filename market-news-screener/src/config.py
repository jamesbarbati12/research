"""Central config, all overridable via environment / .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DB_PATH = BASE_DIR / "data" / "news_screener.db"
TIER_TABLE_PATH = BASE_DIR / "data" / "tier_table.json"

FMP_API_KEY = os.environ.get("FMP_API_KEY", "").strip()
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "75"))
CLASSIFY_CONFIDENCE_THRESHOLD = float(os.environ.get("CLASSIFY_CONFIDENCE_THRESHOLD", "0.6"))

# Comma-separated tickers FMP press-releases are polled for (FMP's press
# release endpoint is per-symbol, unlike its general news endpoint).
WATCHLIST = [
    t.strip().upper()
    for t in os.environ.get("WATCHLIST", "AAPL,MSFT,NVDA,TSLA,AMZN,GOOGL,META,AMD").split(",")
    if t.strip()
]

TICKER_CACHE_TTL_SECONDS = 24 * 60 * 60

API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))

# SEC requires a descriptive User-Agent identifying the requester (fair access
# policy) on all requests, including the free real-time filings feed.
SEC_USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "market-news-screener/0.1 (contact: jamesbarbati2003@gmail.com)"
)
