"""Turns a typed question like "show me the 5 biggest potential moves today"
into structured filters over the real, already-collected feed data.

This is a free, local, rule-based parser - no LLM, no API key, no cost. It
never invents tickers/headlines/numbers, it only picks which real rows to
show (same principle an LLM-backed version would need to follow, so this
module's output shape - a plain filter dict - is exactly what a future
Claude-backed interpret_query() could also return, if that's ever added).
"""
import re
from datetime import datetime, timedelta, timezone

from . import classify, config

# Phrase -> category slug. Only unambiguous phrases are mapped; a bare word
# like "earnings" (could mean beat or miss) is deliberately left unmapped.
_CATEGORY_KEYWORDS = [
    (r"\brumou?r denials?\b|denied the rumou?r|denies? the rumou?r", "rumor_denial"),
    (r"\brumou?rs?\b|\bunconfirmed\b|\bspeculation\b|\bsources say\b", "rumor_speculation"),
    (r"\bearnings beat|beats? estimates\b", "earnings_beat"),
    (r"\bearnings miss|misses? estimates\b", "earnings_miss"),
    (r"\bguidance (?:raise|raised|hike)\b|\braised? guidance\b", "guidance_raise"),
    (r"\bguidance cut\b|\bcut guidance\b|\blowered guidance\b", "guidance_cut"),
    (r"\bm ?& ?a\b|\bacquisition\b|\bmerger\b|\bbuyout\b", "ma_definitive_agreement"),
    (r"\bfda\b", "fda_decision"),
    (r"\bbankrupt", "bankruptcy_restructuring"),
    (r"\bsettlement\b", "legal_settlement"),
    (r"\bregulatory\b|\binvestigation\b|\blawsuit\b|\bsubpoena\b", "regulatory_legal_action"),
    (r"\bceo\b|\bcfo\b|\bexecutive change\b|\bresign", "executive_change"),
    (r"\banalyst upgrades?\b|\bupgraded\b", "analyst_upgrade"),
    (r"\banalyst downgrades?\b|\bdowngraded\b", "analyst_downgrade"),
    (r"\bdividend\b", "dividend_change"),
    (r"\bbuyback\b|\brepurchase\b", "buyback_announced"),
    (r"\b8-?k\b", "sec_filing_8k_material"),
    (r"\b13-?d\b|\bactivist\b", "sec_filing_13d_activist"),
    (r"\b13-?g\b", "sec_filing_13g_passive"),
    (r"\binsider (?:trad|buy|sell)|\bform 4\b", "insider_form4"),
    (r"\bproduct launch\b|\bnew launch\b", "product_launch"),
    (r"\bpartnership\b|\bjoint venture\b", "partnership_announced"),
    (r"\bstock split\b|\breverse split\b", "stock_split"),
    (r"\bsocial media\b|\breddit\b|\bwallstreetbets\b|\btwitter\b|\bsentiment\b", "social_sentiment_spike"),
    (r"\bbuildup\b|\bahead of\b|\bpreview\b|\banticipation\b", "media_anticipation"),
]

_BIGGEST_MOVE_WORDS = re.compile(r"\bbiggest\b|\blargest\b|\btop movers?\b|\bmost impact\b|\bpotential moves?\b|\bmovers\b")
_ONLY_BIGGEST_TIER = re.compile(r"\bonly the biggest\b|\bmajor(?:\s+news)? only\b|\btier ?1 only\b|\bbiggest impact only\b")
_TOP_N = re.compile(r"\btop\s+(\d+)\b|\b(\d+)\s+(?:biggest|largest|top)\b")
_SHOW_N = re.compile(r"\bshow(?:\s+me)?\s+(\d+)\b")
_TIER_N = re.compile(r"\btier\s*1?-?(\d)\b")
_LAST_N_HOURS = re.compile(r"\blast\s+(\d+)\s*hours?\b|\bpast\s+(\d+)\s*hours?\b")
_LAST_N_DAYS = re.compile(r"\blast\s+(\d+)\s*days?\b|\bpast\s+(\d+)\s*days?\b")
_TODAY = re.compile(r"\btoday\b|\blast 24 ?h(?:ours?)?\b")
_THIS_WEEK = re.compile(r"\bthis week\b|\blast 7 ?days?\b|\bpast week\b")
_LAST_HOUR = re.compile(r"\blast hour\b|\bpast hour\b")
_EVERYTHING = re.compile(r"\beverything\b|\ball tiers\b|\bshow all\b|\bno filter\b")


def _find_ticker(query: str):
    # Only match tokens typed in caps (how people naturally write tickers),
    # cross-referenced against the real watchlist to avoid false positives
    # like "A" or "IT" matching common words. Won't catch tickers outside
    # WATCHLIST or ones typed in lowercase - acceptable for a free, local pass.
    watchlist = set(config.WATCHLIST)
    for token in re.findall(r"\b[A-Z]{2,5}\b", query):
        if token in watchlist:
            return token
    return None


def _find_category(query_lower: str):
    for pattern, slug in _CATEGORY_KEYWORDS:
        if re.search(pattern, query_lower):
            return slug
    return None


def _find_limit(query_lower: str):
    for pattern in (_TOP_N, _SHOW_N):
        match = pattern.search(query_lower)
        if match:
            for group in match.groups():
                if group:
                    return int(group)
    return None


def _find_max_tier(query_lower: str):
    if _ONLY_BIGGEST_TIER.search(query_lower):
        return 1
    if _EVERYTHING.search(query_lower):
        return 4
    match = _TIER_N.search(query_lower)
    if match:
        n = int(match.group(1))
        if n in (1, 2, 3, 4):
            return n
    return None


def _find_since_hours(query_lower: str):
    for pattern, multiplier in ((_LAST_N_HOURS, 1), (_LAST_N_DAYS, 24)):
        match = pattern.search(query_lower)
        if match:
            for group in match.groups():
                if group:
                    return int(group) * multiplier
    if _LAST_HOUR.search(query_lower):
        return 1
    if _TODAY.search(query_lower):
        return 24
    if _THIS_WEEK.search(query_lower):
        return 24 * 7
    return None


def interpret_query_local(query: str) -> dict:
    """Rule-based parse. Returns a raw filter dict (same shape resolve_filters
    expects): ticker, category, max_tier, sort_by, limit, since_hours - any
    key may be None/absent if the query didn't say anything about it."""
    query_lower = query.lower()

    sort_by = "biggest_move" if _BIGGEST_MOVE_WORDS.search(query_lower) else "recency"

    return {
        "ticker": _find_ticker(query),
        "category": _find_category(query_lower),
        "max_tier": _find_max_tier(query_lower),
        "sort_by": sort_by,
        "limit": _find_limit(query_lower),
        "since_hours": _find_since_hours(query_lower),
    }


def resolve_filters(raw: dict) -> dict:
    """Normalize/validate a raw filter dict into safe db.query_feed kwargs
    (plus since_hours, kept for describe_filters to reference)."""
    tier_table = classify.load_tier_table()
    valid_categories = set(tier_table["categories"].keys())

    ticker = raw.get("ticker")
    ticker = ticker.strip().upper() if isinstance(ticker, str) and ticker.strip() else None

    category = raw.get("category")
    category = category if category in valid_categories else None

    max_tier = raw.get("max_tier")
    max_tier = max_tier if max_tier in (1, 2, 3, 4) else None

    sort_by = raw.get("sort_by")
    sort_by = sort_by if sort_by in ("recency", "biggest_move") else "recency"

    limit = raw.get("limit")
    try:
        limit = int(limit)
        limit = max(1, min(limit, 500))
    except (TypeError, ValueError):
        limit = 20 if sort_by == "biggest_move" else 200

    since_hours = raw.get("since_hours")
    min_timestamp = None
    try:
        since_hours = int(since_hours)
        if since_hours > 0:
            min_timestamp = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        else:
            since_hours = None
    except (TypeError, ValueError):
        since_hours = None

    return {
        "ticker": ticker,
        "category": category,
        "max_tier": max_tier,
        "sort_by": sort_by,
        "limit": limit,
        "since_hours": since_hours,
        "min_timestamp": min_timestamp,
    }


def describe_filters(filters: dict, count: int) -> str:
    """One-line, human-readable restatement of what was actually applied -
    built from the same resolved dict used for the query, so it can't drift
    from the truth the way an LLM's own summary of itself sometimes can."""
    tier_table = classify.load_tier_table()
    parts = [f"Showing {count} item{'s' if count != 1 else ''}"]
    if filters.get("ticker"):
        parts.append(f"for {filters['ticker']}")
    if filters.get("category"):
        label = tier_table["categories"].get(filters["category"], {}).get("label", filters["category"])
        parts.append(f'in "{label}"')
    if filters.get("max_tier"):
        parts.append(f"(Tier 1-{filters['max_tier']})")
    if filters.get("sort_by") == "biggest_move":
        parts.append("sorted by biggest estimated move")
    if filters.get("since_hours"):
        parts.append(f"from the last {filters['since_hours']}h")
    return " ".join(parts) + "."
