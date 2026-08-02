"""Seed the DB with realistic sample headlines for a local demo.

This is NOT part of the ingest pipeline - it exists because this dev
session's network egress policy blocks both sec.gov and
financialmodelingprep.com, so the real ingest.py pollers can't reach the
internet from inside this sandbox. It inserts rows directly into raw_items
(same shape ingest.py produces) and runs them through the real
classify/score pipeline, so you can see the full system working end-to-end
before FMP/SEC access is available.

Usage: python scripts/seed_demo_data.py
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import db, score  # noqa: E402

NOW = datetime.now(timezone.utc)


def minutes_ago(n):
    return (NOW - timedelta(minutes=n)).isoformat()


ITEMS = [
    dict(source="fmp_news", source_id="demo-1", ticker="ACME",
         headline="Acme Corp is reportedly in talks to acquire a mid-size rival, sources say",
         body="People familiar with the matter say the deal could be announced within weeks.",
         url="https://example.com/demo-1", published_at=minutes_ago(2)),
    dict(source="fmp_news", source_id="demo-2", ticker="NEXA",
         headline="Nexa Health denies rumors of a takeover bid",
         body="A company spokesperson said there is no truth to the report.",
         url="https://example.com/demo-2", published_at=minutes_ago(4)),
    dict(source="fmp_press_release", source_id="demo-3", ticker="TRDX",
         headline="What to expect ahead of TrendX's Q3 earnings report next week",
         body="Analysts are watching margin trends heading into the print.",
         url="https://example.com/demo-3", published_at=minutes_ago(6)),
    dict(source="fmp_news", source_id="demo-4", ticker="ZYNP",
         headline="ZynPharma shares trending on r/wallstreetbets amid unusual options activity",
         body="Mentions of the ticker spiked overnight on retail trading forums.",
         url="https://example.com/demo-4", published_at=minutes_ago(8)),
    dict(source="fmp_news", source_id="demo-5", ticker="ACME",
         headline="Acme Corp beats Q2 estimates, raises full-year guidance",
         body="EPS of $1.42 topped consensus estimates of $1.20.",
         url="https://example.com/demo-5", published_at=minutes_ago(10)),
    dict(source="fmp_press_release", source_id="demo-6", ticker="BIOX",
         headline="BioX Therapeutics announces FDA approval for its lead drug candidate",
         body="The approval clears the way for a commercial launch in Q1.",
         url="https://example.com/demo-6", published_at=minutes_ago(12)),
    dict(source="sec_8k", source_id="demo-7", ticker="MEGN",
         headline="8-K - Megan Industries Inc. (0000123456) (Filer)",
         body=None, url="https://example.com/demo-7", published_at=minutes_ago(14)),
    dict(source="sec_13d", source_id="demo-8", ticker="VLTR",
         headline="SC 13D - Voltar Energy Corp. (0000654321) (Subject)",
         body=None, url="https://example.com/demo-8", published_at=minutes_ago(16)),
    dict(source="fmp_news", source_id="demo-9", ticker="QUON",
         headline="Analyst downgrades Quon Semiconductor to underweight, cuts price target",
         body="The firm cited softening demand in the current quarter.",
         url="https://example.com/demo-9", published_at=minutes_ago(18)),
    dict(source="fmp_news", source_id="demo-10", ticker="DRFT",
         headline="Drift Robotics considering a sale of its logistics unit, sources say",
         body="The company is exploring strategic alternatives for the division.",
         url="https://example.com/demo-10", published_at=minutes_ago(20)),
    dict(source="fmp_news", source_id="demo-11", ticker="HLNX",
         headline="Helion Materials cuts full-year guidance, withdraws prior forecast",
         body="Weak end-market demand drove the revision.",
         url="https://example.com/demo-11", published_at=minutes_ago(22)),
    dict(source="fmp_press_release", source_id="demo-12", ticker="TRDX",
         headline="TrendX Inc. declares quarterly dividend of $0.25 per share",
         body="The dividend is payable next month to shareholders of record.",
         url="https://example.com/demo-12", published_at=minutes_ago(24)),
    dict(source="fmp_news", source_id="demo-13", ticker="NEXA",
         headline="Nexa Health unveils new diagnostic device at industry conference",
         body="The device is expected to launch commercially next year.",
         url="https://example.com/demo-13", published_at=minutes_ago(26)),
    dict(source="fmp_news", source_id="demo-14", ticker="ACME",
         headline="Acme Corp files for Chapter 11 bankruptcy protection",
         body="The company cited unsustainable debt levels in its filing.",
         url="https://example.com/demo-14", published_at=minutes_ago(28)),
]


async def main():
    db.init_db()
    with db.connection() as conn:
        inserted = 0
        for item in ITEMS:
            if db.insert_raw_item(conn, **item) is not None:
                inserted += 1
        conn.commit()
        scored = await score.score_all_pending(conn)
    print(f"seeded {inserted} new raw item(s), scored {scored}")


if __name__ == "__main__":
    asyncio.run(main())
