"""SQLite storage: raw ingested items, scored items, ticker market-cap cache."""
import sqlite3
from contextlib import contextmanager

from src import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    ticker TEXT,
    headline TEXT NOT NULL,
    body TEXT,
    url TEXT,
    published_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(source, source_id)
);
CREATE INDEX IF NOT EXISTS idx_raw_items_published ON raw_items(published_at);
CREATE INDEX IF NOT EXISTS idx_raw_items_ticker ON raw_items(ticker);

CREATE TABLE IF NOT EXISTS scored_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES raw_items(id),
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    needs_llm INTEGER NOT NULL DEFAULT 0,
    classified_by TEXT NOT NULL DEFAULT 'regex',
    tier INTEGER NOT NULL,
    base_avg_move_pct REAL NOT NULL,
    hit_rate REAL NOT NULL,
    cap_bucket TEXT NOT NULL,
    cap_multiplier REAL NOT NULL,
    est_move_pct REAL NOT NULL,
    scored_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(item_id)
);
CREATE INDEX IF NOT EXISTS idx_scored_items_tier ON scored_items(tier);

CREATE TABLE IF NOT EXISTS ticker_cache (
    ticker TEXT PRIMARY KEY,
    market_cap REAL,
    bucket TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def connection():
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def insert_raw_item(conn, *, source, source_id, ticker, headline, body, url, published_at):
    """Insert a raw item; returns its row id, or None if it's a duplicate."""
    try:
        cur = conn.execute(
            """
            INSERT INTO raw_items (source, source_id, ticker, headline, body, url, published_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (source, source_id, ticker, headline, body, url, published_at),
        )
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None


def get_unscored_items(conn):
    return conn.execute(
        """
        SELECT r.* FROM raw_items r
        LEFT JOIN scored_items s ON s.item_id = r.id
        WHERE s.id IS NULL
        ORDER BY r.id ASC
        """
    ).fetchall()


def insert_scored_item(conn, *, item_id, category, confidence, needs_llm, classified_by,
                        tier, base_avg_move_pct, hit_rate, cap_bucket, cap_multiplier, est_move_pct):
    conn.execute(
        """
        INSERT INTO scored_items
            (item_id, category, confidence, needs_llm, classified_by, tier,
             base_avg_move_pct, hit_rate, cap_bucket, cap_multiplier, est_move_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(item_id) DO UPDATE SET
            category=excluded.category,
            confidence=excluded.confidence,
            needs_llm=excluded.needs_llm,
            classified_by=excluded.classified_by,
            tier=excluded.tier,
            base_avg_move_pct=excluded.base_avg_move_pct,
            hit_rate=excluded.hit_rate,
            cap_bucket=excluded.cap_bucket,
            cap_multiplier=excluded.cap_multiplier,
            est_move_pct=excluded.est_move_pct,
            scored_at=datetime('now')
        """,
        (item_id, category, confidence, int(needs_llm), classified_by, tier,
         base_avg_move_pct, hit_rate, cap_bucket, cap_multiplier, est_move_pct),
    )


def get_ticker_cache(conn, ticker):
    return conn.execute("SELECT * FROM ticker_cache WHERE ticker = ?", (ticker,)).fetchone()


def upsert_ticker_cache(conn, *, ticker, market_cap, bucket):
    conn.execute(
        """
        INSERT INTO ticker_cache (ticker, market_cap, bucket, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(ticker) DO UPDATE SET
            market_cap=excluded.market_cap,
            bucket=excluded.bucket,
            updated_at=datetime('now')
        """,
        (ticker, market_cap, bucket),
    )


def query_feed(conn, *, ticker=None, category=None, max_tier=None, min_timestamp=None, limit=200, sort_by="recency"):
    clauses = []
    params = []
    if ticker:
        clauses.append("r.ticker = ?")
        params.append(ticker.upper())
    if category:
        clauses.append("s.category = ?")
        params.append(category)
    if max_tier is not None:
        clauses.append("s.tier <= ?")
        params.append(max_tier)
    if min_timestamp:
        clauses.append("r.published_at >= ?")
        params.append(min_timestamp)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    order_clause = (
        "s.est_move_pct DESC, r.published_at DESC"
        if sort_by == "biggest_move"
        else "s.tier ASC, r.published_at DESC"
    )
    sql = f"""
        SELECT r.id, r.source, r.ticker, r.headline, r.body, r.url,
               r.published_at, r.ingested_at,
               s.category, s.confidence, s.needs_llm, s.classified_by,
               s.tier, s.base_avg_move_pct, s.hit_rate,
               s.cap_bucket, s.cap_multiplier, s.est_move_pct
        FROM raw_items r
        JOIN scored_items s ON s.item_id = r.id
        {where}
        ORDER BY {order_clause}
        LIMIT ?
    """
    params.append(limit)
    return conn.execute(sql, params).fetchall()
