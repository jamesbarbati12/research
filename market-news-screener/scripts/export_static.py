"""Export the current feed + tier table as static JSON files the built
frontend can fetch directly, for a serverless (GitHub Pages) deployment.

Run this right after scripts/run_once.py, before `npm run build` - Vite
copies everything in frontend/public/ verbatim into frontend/dist/, so
these files end up served alongside the built app with no backend needed.

Usage:
    python scripts/export_static.py
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import classify, config, db  # noqa: E402

OUT_DIR = config.BASE_DIR / "frontend" / "public"
FEED_LIMIT = 500


def main():
    db.init_db()
    with db.connection() as conn:
        rows = db.query_feed(conn, limit=FEED_LIMIT)
    items = [dict(row) for row in rows]

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    feed_payload = {
        "count": len(items),
        "items": items,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(OUT_DIR / "feed.json", "w") as f:
        json.dump(feed_payload, f, indent=2)
        f.write("\n")

    with open(OUT_DIR / "tier-table.json", "w") as f:
        json.dump(classify.load_tier_table(), f, indent=2)
        f.write("\n")

    with open(config.SP500_TICKERS_PATH) as src_f:
        tickers_payload = json.load(src_f)
    with open(OUT_DIR / "tickers.json", "w") as f:
        json.dump(tickers_payload, f, indent=2)
        f.write("\n")

    print(f"[export_static] wrote {len(items)} item(s) to {OUT_DIR}/feed.json")


if __name__ == "__main__":
    main()
