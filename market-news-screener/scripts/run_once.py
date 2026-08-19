"""Single ingest+classify+score pass, for use in a scheduled CI job instead
of the long-running server loop in src/api.py.

Deliberately does NOT persist the SQLite DB across runs - each invocation
starts from an empty database. This is fine (and actually correct) for a
"live snapshot" deployment: the sources this project polls (Finnhub's
latest-news endpoints, SEC EDGAR's "current events" feed) are themselves
already snapshots of what's currently happening, not a historical archive
you need to accumulate over time. Historical accumulation for backtesting
is a local-dev-machine concern (see scripts/backtest.py); this script is
only for producing "what's happening right now" for the public deployment.

Usage:
    python scripts/run_once.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, ingest  # noqa: E402


async def main():
    db.init_db()
    with db.connection() as conn:
        inserted, scored = await ingest.poll_once(conn)
    print(f"[run_once] inserted {inserted} new item(s), scored {scored}")


if __name__ == "__main__":
    asyncio.run(main())
