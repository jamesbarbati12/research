"""Backtest the tier table against real historical price data.

For every ingested news item with a ticker, this pulls free daily price
history (yfinance - no key needed) and measures the ACTUAL price move
around that headline: close of the last trading day before publication vs.
close of the trading day the market had to react to it (same day if
published before 4pm ET, otherwise the next trading day; weekends/holidays
are skipped automatically since yfinance only returns real trading days).

Each move is normalized by the ticker's market-cap bucket multiplier, so a
big move on a microcap doesn't inflate a category's "mid-cap-equivalent"
base rate (data/tier_table.json defines avg_move_pct that way already).

Categories only get marked "calibrated" once they have enough real samples
(MIN_SAMPLES). Nothing is silently overwritten: this script always prints a
report, and only touches tier_table.json when run with --apply.

Usage:
    python scripts/backtest.py                # dry run, prints a report
    python scripts/backtest.py --apply         # also updates tier_table.json
    python scripts/backtest.py --min-samples 10 --noise-threshold 1.5
"""
import argparse
import logging
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import classify, config, db, score  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_NOISE_THRESHOLD_PCT = 1.0
DEFAULT_MIN_SAMPLES = 5

_history_cache = {}


def _log(msg):
    print(f"[backtest] {msg}")


def trading_day_for(published_at_iso: str) -> date:
    """The trading day the market first had a chance to react to this item -
    same day if published before 4pm ET, otherwise the next calendar day
    (weekends are pushed to Monday; actual holiday handling falls out of the
    'nearest date present in yfinance's data' lookup below, since yfinance
    only returns real trading days)."""
    dt = datetime.fromisoformat(published_at_iso).astimezone(ET)
    day = dt.date()
    if dt.hour >= 16:
        day += timedelta(days=1)
    while day.weekday() >= 5:  # Saturday=5, Sunday=6
        day += timedelta(days=1)
    return day


def get_daily_history(ticker: str, start: date):
    """One yfinance call per ticker (cached), not per item."""
    if ticker in _history_cache:
        return _history_cache[ticker]
    try:
        import yfinance as yf

        df = yf.Ticker(ticker).history(
            start=start.isoformat(),
            end=(date.today() + timedelta(days=1)).isoformat(),
            interval="1d",
        )
        df = df if not df.empty else None
    except Exception as exc:  # yfinance can raise assorted error types
        _log(f"warning: price history fetch failed for {ticker}: {exc}")
        df = None
    _history_cache[ticker] = df
    time.sleep(0.2)  # be polite to an unofficial, undocumented endpoint
    return df


def close_before(df, target_day: date):
    for ts, row in reversed(list(df.iterrows())):
        if ts.date() < target_day:
            return float(row["Close"])
    return None


def close_on_or_after(df, target_day: date):
    for ts, row in df.iterrows():
        if ts.date() >= target_day:
            return float(row["Close"])
    return None


def get_market_cap_bucket_sync(conn, ticker, tier_table):
    """Sync, backtest-only variant of score.get_market_cap_bucket: check the
    ticker_cache table first (populated by the live app), else look up via
    yfinance and cache the result for next time."""
    cached = db.get_ticker_cache(conn, ticker)
    if cached is not None:
        return cached["bucket"], tier_table["market_cap_buckets"][cached["bucket"]]["multiplier"]

    market_cap = None
    try:
        import yfinance as yf

        info = yf.Ticker(ticker).fast_info
        market_cap = getattr(info, "market_cap", None) or info.get("marketCap")
    except Exception:
        market_cap = None

    bucket, multiplier = score.bucket_for_market_cap(market_cap, tier_table)
    db.upsert_ticker_cache(conn, ticker=ticker, market_cap=market_cap, bucket=bucket)
    return bucket, multiplier


def run_backtest(noise_threshold_pct, min_samples):
    tier_table = classify.load_tier_table()

    with db.connection() as conn:
        rows = conn.execute(
            """
            SELECT r.ticker, r.published_at, s.category
            FROM raw_items r
            JOIN scored_items s ON s.item_id = r.id
            WHERE r.ticker IS NOT NULL AND r.ticker != ''
            """
        ).fetchall()

        by_category = defaultdict(list)  # category -> list of (raw_abs_move, normalized_abs_move)
        considered = 0
        skipped_no_price_data = 0

        for row in rows:
            considered += 1
            ticker, published_at, category = row["ticker"], row["published_at"], row["category"]
            try:
                day0 = trading_day_for(published_at)
            except ValueError:
                skipped_no_price_data += 1
                continue

            df = get_daily_history(ticker, day0 - timedelta(days=10))
            if df is None:
                skipped_no_price_data += 1
                continue

            baseline = close_before(df, day0)
            reaction = close_on_or_after(df, day0)
            if baseline is None or reaction is None or baseline == 0:
                skipped_no_price_data += 1
                continue

            raw_abs_move = abs((reaction - baseline) / baseline * 100)
            _, multiplier = get_market_cap_bucket_sync(conn, ticker, tier_table)
            normalized_abs_move = raw_abs_move / multiplier if multiplier else raw_abs_move

            by_category[category].append((raw_abs_move, normalized_abs_move))

    results = {}
    for category, moves in sorted(by_category.items()):
        n = len(moves)
        raw_moves = [m[0] for m in moves]
        normalized_moves = [m[1] for m in moves]
        mean_normalized = sum(normalized_moves) / n
        hits = sum(1 for m in raw_moves if m >= noise_threshold_pct)
        results[category] = {
            "n": n,
            "mean_abs_move_pct": round(mean_normalized, 2),
            "hit_rate": round(hits / n, 2),
            "calibrated": n >= min_samples,
        }

    meta = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "items_considered": considered,
        "items_skipped_no_price_data": skipped_no_price_data,
        "noise_threshold_pct": noise_threshold_pct,
        "min_samples_to_calibrate": min_samples,
        "methodology": (
            "Daily close-to-close move from the last trading day before publication to the "
            "trading day the market could first react (same day if published before 4pm ET, "
            "else next trading day). Normalized by the ticker's market-cap bucket multiplier "
            "so results are comparable to tier_table.json's mid-cap-equivalent avg_move_pct. "
            "hit_rate uses the RAW (non-normalized) move against noise_threshold_pct."
        ),
    }
    return meta, results


def print_report(meta, results, tier_table):
    _log(f"considered {meta['items_considered']} items, skipped {meta['items_skipped_no_price_data']} "
         f"(no price data available)")
    print()
    header = f"{'category':<28} {'n':>4} {'old avg%':>9} {'new avg%':>9} {'old hit%':>9} {'new hit%':>9}  status"
    print(header)
    print("-" * len(header))
    for category, info in results.items():
        old = tier_table["categories"].get(category, {})
        status = "CALIBRATED" if info["calibrated"] else f"needs {meta['min_samples_to_calibrate']}+ (has {info['n']})"
        print(
            f"{category:<28} {info['n']:>4} {old.get('avg_move_pct', '-'):>9} {info['mean_abs_move_pct']:>9} "
            f"{round(old.get('hit_rate', 0) * 100):>8}% {round(info['hit_rate'] * 100):>8}%  {status}"
        )
    uncovered = set(tier_table["categories"]) - set(results)
    if uncovered:
        print()
        _log(f"no real samples at all yet for: {', '.join(sorted(uncovered))}")


def apply_results(results, tier_table_path, run_at):
    import json

    with open(tier_table_path) as f:
        tier_table = json.load(f)

    updated = []
    for category, info in results.items():
        if not info["calibrated"]:
            continue
        entry = tier_table["categories"].get(category)
        if entry is None:
            continue
        entry["avg_move_pct"] = info["mean_abs_move_pct"]
        entry["hit_rate"] = info["hit_rate"]
        entry["placeholder"] = False
        entry["calibrated_n"] = info["n"]
        entry["calibrated_at"] = run_at
        updated.append(category)

    with open(tier_table_path, "w") as f:
        json.dump(tier_table, f, indent=2)
        f.write("\n")

    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Write calibrated values into tier_table.json")
    parser.add_argument("--noise-threshold", type=float, default=DEFAULT_NOISE_THRESHOLD_PCT,
                         help=f"Min abs price move %% to count as a 'hit' (default {DEFAULT_NOISE_THRESHOLD_PCT})")
    parser.add_argument("--min-samples", type=int, default=DEFAULT_MIN_SAMPLES,
                         help=f"Min real samples before a category is trusted (default {DEFAULT_MIN_SAMPLES})")
    args = parser.parse_args()

    db.init_db()
    tier_table = classify.load_tier_table()

    meta, results = run_backtest(args.noise_threshold, args.min_samples)
    print_report(meta, results, tier_table)

    import json

    report_path = config.BASE_DIR / "data" / "backtest_results.json"
    with open(report_path, "w") as f:
        json.dump({"_meta": meta, "categories": results}, f, indent=2)
        f.write("\n")
    print()
    _log(f"full report written to {report_path}")

    if args.apply:
        updated = apply_results(results, config.TIER_TABLE_PATH, meta["run_at"])
        if updated:
            _log(f"applied calibrated values to tier_table.json for: {', '.join(updated)}")
        else:
            _log("no categories had enough samples to calibrate - tier_table.json unchanged")
    else:
        calibrated = [c for c, r in results.items() if r["calibrated"]]
        if calibrated:
            _log(f"dry run only - re-run with --apply to update tier_table.json for: {', '.join(calibrated)}")


if __name__ == "__main__":
    main()
