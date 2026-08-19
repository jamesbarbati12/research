"""Re-score every already-ingested item against the CURRENT tier_table.json.

scored_items.tier / base_avg_move_pct / hit_rate / est_move_pct are computed
once, at ingestion time, and stored - they do NOT automatically update when
tier_table.json changes later (e.g. after a backtest --apply recalibrates a
category, or a manual edit to the tier table). Restarting the API only
changes how NEW items get scored going forward; everything already in the
database stays frozen at whatever the tier table said when it was ingested.

Run this after any backtest --apply (or manual tier_table.json edit) to
bring every existing item in line with the current tier table, so the feed
doesn't show a confusing mix of old and new tier assignments for the same
category. Ticker-specific fields (cap_bucket, cap_multiplier, category,
classification) are left untouched - only the tier-table-derived fields are
recomputed.

Usage:
    python scripts/rescore.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import classify, db  # noqa: E402


def rescore_all(conn):
    tier_table = classify.load_tier_table()
    fallback = tier_table["categories"]["unclassified"]
    rows = conn.execute("SELECT item_id, category, cap_multiplier FROM scored_items").fetchall()

    changed = 0
    for row in rows:
        info = tier_table["categories"].get(row["category"], fallback)
        new_tier = info["tier"]
        new_avg_move = info["avg_move_pct"]
        new_hit_rate = info["hit_rate"]
        new_est_move = round(new_avg_move * row["cap_multiplier"], 3)

        cur = conn.execute(
            """
            UPDATE scored_items
            SET tier = ?, base_avg_move_pct = ?, hit_rate = ?, est_move_pct = ?
            WHERE item_id = ?
              AND (tier != ? OR base_avg_move_pct != ? OR hit_rate != ? OR est_move_pct != ?)
            """,
            (new_tier, new_avg_move, new_hit_rate, new_est_move, row["item_id"],
             new_tier, new_avg_move, new_hit_rate, new_est_move),
        )
        changed += cur.rowcount

    conn.commit()
    return len(rows), changed


def main():
    db.init_db()
    with db.connection() as conn:
        total, changed = rescore_all(conn)
    print(f"[rescore] checked {total} scored item(s), updated {changed} to match the current tier_table.json")


if __name__ == "__main__":
    main()
