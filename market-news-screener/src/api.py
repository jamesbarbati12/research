"""FastAPI app serving the live, filterable news feed.

Runs the ingest poll loop as a background asyncio task on startup, so
`python src/api.py` alone gets you both the API and live data flowing in
(no separate process needed for Phase 1).
"""
import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import classify, config, db, ingest  # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    ingest_task = asyncio.create_task(ingest.run_loop())
    try:
        yield
    finally:
        ingest_task.cancel()


app = FastAPI(title="Market News Screener API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _normalize_min_timestamp(min_timestamp: Optional[str]) -> Optional[str]:
    if not min_timestamp:
        return None
    # Accept epoch seconds or an ISO 8601 string; normalize to the same
    # ISO-UTC format raw_items.published_at is stored in.
    try:
        epoch = float(min_timestamp)
        return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(min_timestamp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return min_timestamp


@app.get("/feed")
def get_feed(
    ticker: Optional[str] = Query(None, description="Filter to a single ticker, e.g. AAPL"),
    category: Optional[str] = Query(None, description="Filter to one category from tier_table.json"),
    tier: Optional[int] = Query(None, ge=1, le=4, description="Max tier to include (e.g. tier=2 returns tiers 1-2)"),
    min_timestamp: Optional[str] = Query(None, description="ISO 8601 timestamp or epoch seconds"),
    limit: int = Query(200, ge=1, le=1000),
):
    with db.connection() as conn:
        rows = db.query_feed(
            conn,
            ticker=ticker,
            category=category,
            max_tier=tier,
            min_timestamp=_normalize_min_timestamp(min_timestamp),
            limit=limit,
        )
    return {"count": len(rows), "items": [dict(row) for row in rows]}


@app.get("/tier-table")
def get_tier_table():
    """Full tier table (categories + market-cap buckets) so the frontend can
    build filter dropdowns and render the 'why this tier' breakdown."""
    return classify.load_tier_table()


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run("src.api:app", host=config.API_HOST, port=config.API_PORT, reload=True)
