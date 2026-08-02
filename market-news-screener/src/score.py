"""Score each raw item live: classify -> tier table base rate -> market-cap
bucket adjustment -> estimated move. Designed to be called right after
ingest inserts new rows, so items are scored as they arrive.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import classify, config, db

SEC_SOURCE_TO_FORM_TYPE = {
    "sec_8k": "8-K",
    "sec_13d": "SC 13D",
    "sec_13g": "SC 13G",
}

_BUCKET_ORDER = ["mega", "large", "mid", "small", "micro"]


def bucket_for_market_cap(market_cap, tier_table):
    buckets = tier_table["market_cap_buckets"]
    if market_cap is None:
        return "unknown", buckets["unknown"]["multiplier"]
    for name in _BUCKET_ORDER:
        if market_cap >= buckets[name]["min_usd"]:
            return name, buckets[name]["multiplier"]
    return "micro", buckets["micro"]["multiplier"]


async def _fetch_market_cap(client, ticker):
    if not config.FMP_API_KEY:
        return None
    try:
        resp = await client.get(
            "https://financialmodelingprep.com/stable/profile",
            params={"symbol": ticker, "apikey": config.FMP_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list) and data[0].get("marketCap"):
            return float(data[0]["marketCap"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        pass
    return None


def _cache_is_fresh(row):
    if row is None:
        return False
    updated_at = datetime.fromisoformat(row["updated_at"]).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - updated_at < timedelta(seconds=config.TICKER_CACHE_TTL_SECONDS)


async def get_market_cap_bucket(conn, client, ticker, tier_table):
    if not ticker:
        return "unknown", tier_table["market_cap_buckets"]["unknown"]["multiplier"]

    cached = db.get_ticker_cache(conn, ticker)
    if _cache_is_fresh(cached):
        return cached["bucket"], tier_table["market_cap_buckets"][cached["bucket"]]["multiplier"]

    market_cap = await _fetch_market_cap(client, ticker)
    bucket, multiplier = bucket_for_market_cap(market_cap, tier_table)
    db.upsert_ticker_cache(conn, ticker=ticker, market_cap=market_cap, bucket=bucket)
    return bucket, multiplier


def score_category(tier_table, category):
    info = tier_table["categories"].get(category, tier_table["categories"]["unclassified"])
    return info["tier"], info["avg_move_pct"], info["hit_rate"]


async def score_item(conn, client, tier_table, raw_item):
    form_type = SEC_SOURCE_TO_FORM_TYPE.get(raw_item["source"])
    lede = (raw_item["body"] or "")[:500]

    result = classify.classify_item(raw_item["headline"], lede=lede, form_type=form_type)

    if result["needs_llm"]:
        llm_result = classify.classify_with_llm_fallback(
            raw_item["headline"], lede=lede, candidates=result["candidates"]
        )
        if llm_result is not None:
            result = llm_result

    tier, base_avg_move_pct, hit_rate = score_category(tier_table, result["category"])
    cap_bucket, cap_multiplier = await get_market_cap_bucket(conn, client, raw_item["ticker"], tier_table)
    est_move_pct = round(base_avg_move_pct * cap_multiplier, 3)

    db.insert_scored_item(
        conn,
        item_id=raw_item["id"],
        category=result["category"],
        confidence=result["confidence"],
        needs_llm=result["needs_llm"],
        classified_by=result["classified_by"],
        tier=tier,
        base_avg_move_pct=base_avg_move_pct,
        hit_rate=hit_rate,
        cap_bucket=cap_bucket,
        cap_multiplier=cap_multiplier,
        est_move_pct=est_move_pct,
    )


async def score_all_pending(conn):
    """Classify + score every raw_item that doesn't have a scored_items row yet."""
    tier_table = classify.load_tier_table()
    pending = db.get_unscored_items(conn)
    if not pending:
        return 0

    async with httpx.AsyncClient() as client:
        for raw_item in pending:
            await score_item(conn, client, tier_table, raw_item)
    conn.commit()
    return len(pending)


async def _run_loop():
    db.init_db()
    while True:
        with db.connection() as conn:
            n = await score_all_pending(conn)
        if n:
            print(f"[score] scored {n} pending item(s)")
        await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(_run_loop())
