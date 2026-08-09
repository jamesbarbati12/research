"""Poll FMP news + press releases, Finnhub news, and SEC EDGAR's real-time
filing feed, store raw items, then trigger live scoring.

- FMP (requires a paid plan as of FMP's Aug 2025 API changes - the free tier
  no longer includes any news endpoint, legacy or current): general news +
  press releases. Skipped with a warning if no key is configured or the key
  isn't entitled to these endpoints - nothing paid is enabled by default.
- Finnhub (free-tier FINNHUB_API_KEY, 60 calls/min): general market news +
  per-watchlist-ticker company news. This is the primary free news leg.
- Yahoo Finance (via the unofficial `yfinance` library, no key): per-ticker
  news as a supplementary source. Scrapes an undocumented Yahoo endpoint, so
  it's best-effort - failures are logged and skipped rather than blocking.
- SEC EDGAR "current events" feed (no key needed): real-time 8-K / SC 13D /
  SC 13G filings, via the public Atom feed. Tickers are resolved from CIK
  using SEC's public company_tickers.json mapping.

Per-ticker sources (Finnhub company-news, Yahoo Finance) rotate through
config.WATCHLIST in batches of TICKER_BATCH_SIZE each poll cycle rather than
querying the whole list every time, to stay under free-tier rate limits with
a large watchlist.
"""
import asyncio
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config, db, score

FMP_STOCK_NEWS_URL = "https://financialmodelingprep.com/stable/news/stock-latest"
FMP_PRESS_RELEASES_URL = "https://financialmodelingprep.com/stable/news/press-releases-latest"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/news"
FINNHUB_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"
SEC_CURRENT_EVENTS_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

SEC_FILING_TYPES = ["8-K", "SC 13D", "SC 13G"]
SEC_SOURCE_MAP = {"8-K": "sec_8k", "SC 13D": "sec_13d", "SC 13G": "sec_13g"}

ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

_cik_ticker_map = {}
_cik_ticker_map_fetched_at = None


def _to_iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _parse_fmp_date(raw: str) -> str:
    dt = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    return _to_iso_utc(dt)


async def _refresh_cik_ticker_map(client):
    global _cik_ticker_map, _cik_ticker_map_fetched_at
    now = datetime.now(timezone.utc)
    if _cik_ticker_map_fetched_at and now - _cik_ticker_map_fetched_at < timedelta(hours=24):
        return
    try:
        resp = await client.get(
            SEC_COMPANY_TICKERS_URL,
            headers={"User-Agent": config.SEC_USER_AGENT},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        _cik_ticker_map = {int(row["cik_str"]): row["ticker"] for row in data.values()}
        _cik_ticker_map_fetched_at = now
        print(f"[ingest] loaded {len(_cik_ticker_map)} CIK->ticker mappings from SEC")
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        print(f"[ingest] warning: failed to refresh SEC CIK->ticker map: {exc}")


async def poll_fmp_news(client):
    if not config.FMP_API_KEY:
        return []
    try:
        resp = await client.get(FMP_STOCK_NEWS_URL, params={"limit": 100, "apikey": config.FMP_API_KEY}, timeout=15)
        resp.raise_for_status()
        items = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[ingest] warning: FMP stock news poll failed: {exc}")
        return []

    out = []
    for item in items or []:
        url = item.get("url")
        if not url:
            continue
        out.append({
            "source": "fmp_news",
            "source_id": url,
            "ticker": (item.get("symbol") or "").upper() or None,
            "headline": item.get("title", "").strip(),
            "body": item.get("text"),
            "url": url,
            "published_at": _parse_fmp_date(item["publishedDate"]) if item.get("publishedDate") else _to_iso_utc(datetime.now(timezone.utc)),
        })
    return out


async def poll_fmp_press_releases(client):
    if not config.FMP_API_KEY:
        return []
    try:
        resp = await client.get(
            FMP_PRESS_RELEASES_URL, params={"limit": 100, "apikey": config.FMP_API_KEY}, timeout=15
        )
        resp.raise_for_status()
        items = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[ingest] warning: FMP press releases poll failed: {exc}")
        return []

    out = []
    for item in items or []:
        symbol = (item.get("symbol") or "").upper()
        title = item.get("title", "").strip()
        source_id = item.get("url") or f"{symbol}:{title}:{item.get('date') or item.get('publishedDate')}"
        published_raw = item.get("date") or item.get("publishedDate")
        out.append({
            "source": "fmp_press_release",
            "source_id": source_id,
            "ticker": symbol or None,
            "headline": title,
            "body": item.get("text"),
            "url": item.get("url"),
            "published_at": _parse_fmp_date(published_raw) if published_raw else _to_iso_utc(datetime.now(timezone.utc)),
        })
    return out


def _parse_finnhub_datetime(epoch_seconds) -> str:
    return _to_iso_utc(datetime.fromtimestamp(epoch_seconds, tz=timezone.utc))


async def poll_finnhub_general_news(client):
    if not config.FINNHUB_API_KEY:
        return []
    try:
        resp = await client.get(
            FINNHUB_NEWS_URL,
            params={"category": "general", "token": config.FINNHUB_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[ingest] warning: Finnhub general news poll failed: {exc}")
        return []

    out = []
    for item in items or []:
        item_id = item.get("id")
        if item_id is None:
            continue
        related = (item.get("related") or "").strip().upper()
        out.append({
            "source": "finnhub_news",
            "source_id": str(item_id),
            "ticker": related.split(",")[0] if related else None,
            "headline": (item.get("headline") or "").strip(),
            "body": item.get("summary"),
            "url": item.get("url"),
            "published_at": _parse_finnhub_datetime(item["datetime"]) if item.get("datetime") else _to_iso_utc(datetime.now(timezone.utc)),
        })
    return out


async def poll_finnhub_company_news(client, ticker, date_from, date_to):
    if not config.FINNHUB_API_KEY:
        return []
    try:
        resp = await client.get(
            FINNHUB_COMPANY_NEWS_URL,
            params={"symbol": ticker, "from": date_from, "to": date_to, "token": config.FINNHUB_API_KEY},
            timeout=15,
        )
        resp.raise_for_status()
        items = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"[ingest] warning: Finnhub company news poll failed for {ticker}: {exc}")
        return []

    out = []
    for item in items or []:
        item_id = item.get("id")
        if item_id is None:
            continue
        out.append({
            "source": "finnhub_company_news",
            "source_id": str(item_id),
            "ticker": ticker,
            "headline": (item.get("headline") or "").strip(),
            "body": item.get("summary"),
            "url": item.get("url"),
            "published_at": _parse_finnhub_datetime(item["datetime"]) if item.get("datetime") else _to_iso_utc(datetime.now(timezone.utc)),
        })
    return out


async def poll_finnhub_watchlist(client, ticker_batch):
    if not config.FINNHUB_API_KEY or not ticker_batch:
        return []
    today = datetime.now(timezone.utc).date()
    date_from = (today - timedelta(days=1)).isoformat()
    date_to = today.isoformat()
    results = await asyncio.gather(
        *(poll_finnhub_company_news(client, ticker, date_from, date_to) for ticker in ticker_batch)
    )
    return [item for batch in results for item in batch]


def _yfinance_news_for_ticker(ticker):
    """Blocking call (yfinance wraps `requests`) - run via asyncio.to_thread."""
    import yfinance as yf

    raw_items = yf.Ticker(ticker).news or []
    out = []
    for raw in raw_items:
        # yfinance's news schema has changed across versions; newer releases
        # nest fields under "content", older ones are flat. Handle both.
        content = raw.get("content", raw)
        item_id = raw.get("id") or content.get("uuid") or content.get("id")
        if item_id is None:
            continue
        headline = (content.get("title") or "").strip()
        if not headline:
            continue
        url = None
        canonical = content.get("canonicalUrl") or content.get("clickThroughUrl")
        if isinstance(canonical, dict):
            url = canonical.get("url")
        url = url or content.get("link")
        pub_date = content.get("pubDate") or content.get("providerPublishTime")
        if isinstance(pub_date, (int, float)):
            published_at = _to_iso_utc(datetime.fromtimestamp(pub_date, tz=timezone.utc))
        elif isinstance(pub_date, str):
            try:
                published_at = _to_iso_utc(datetime.fromisoformat(pub_date.replace("Z", "+00:00")))
            except ValueError:
                published_at = _to_iso_utc(datetime.now(timezone.utc))
        else:
            published_at = _to_iso_utc(datetime.now(timezone.utc))
        out.append({
            "source": "yfinance_news",
            "source_id": str(item_id),
            "ticker": ticker,
            "headline": headline,
            "body": content.get("summary"),
            "url": url,
            "published_at": published_at,
        })
    return out


async def poll_yfinance_watchlist(ticker_batch):
    """Yahoo Finance news via the unofficial yfinance library - free, no key,
    but scrapes an undocumented Yahoo endpoint that can change or rate-limit
    without notice. Best-effort: per-ticker failures are logged and skipped."""
    if not ticker_batch:
        return []

    async def _one(ticker):
        try:
            return await asyncio.to_thread(_yfinance_news_for_ticker, ticker)
        except Exception as exc:  # yfinance can raise a variety of error types
            print(f"[ingest] warning: yfinance news poll failed for {ticker}: {exc}")
            return []

    results = await asyncio.gather(*(_one(t) for t in ticker_batch))
    return [item for batch in results for item in batch]


_ticker_rotation_index = 0


def _next_ticker_batch():
    """Rotate through config.WATCHLIST in fixed-size batches across poll
    cycles, so a large watchlist doesn't exceed per-minute rate limits on
    any single poll."""
    global _ticker_rotation_index
    watchlist = config.WATCHLIST
    if not watchlist:
        return []
    batch_size = min(config.TICKER_BATCH_SIZE, len(watchlist))
    start = _ticker_rotation_index % len(watchlist)
    end = start + batch_size
    if end <= len(watchlist):
        batch = watchlist[start:end]
    else:
        batch = watchlist[start:] + watchlist[: end - len(watchlist)]
    _ticker_rotation_index = end % len(watchlist)
    return batch


def _extract_cik(title: str):
    match = re.search(r"\((\d{4,10})\)", title)
    if not match:
        return None
    return int(match.group(1))


async def poll_sec_filings(client):
    await _refresh_cik_ticker_map(client)

    out = []
    for filing_type in SEC_FILING_TYPES:
        try:
            resp = await client.get(
                SEC_CURRENT_EVENTS_URL,
                params={
                    "action": "getcurrent",
                    "type": filing_type,
                    "company": "",
                    "dateb": "",
                    "owner": "include",
                    "count": 100,
                    "output": "atom",
                },
                headers={"User-Agent": config.SEC_USER_AGENT},
                timeout=15,
            )
            resp.raise_for_status()
            root = ET.fromstring(resp.content)
        except (httpx.HTTPError, ET.ParseError) as exc:
            print(f"[ingest] warning: SEC EDGAR poll failed for {filing_type}: {exc}")
            continue

        for entry in root.findall("atom:entry", ATOM_NS):
            title_el = entry.find("atom:title", ATOM_NS)
            id_el = entry.find("atom:id", ATOM_NS)
            updated_el = entry.find("atom:updated", ATOM_NS)
            link_el = entry.find("atom:link", ATOM_NS)

            title = (title_el.text or "").strip() if title_el is not None else ""
            entry_id = (id_el.text or "").strip() if id_el is not None else None
            if not entry_id:
                continue

            cik = _extract_cik(title)
            ticker = _cik_ticker_map.get(cik) if cik else None

            published_at = _to_iso_utc(datetime.now(timezone.utc))
            if updated_el is not None and updated_el.text:
                try:
                    published_at = _to_iso_utc(datetime.fromisoformat(updated_el.text.strip()))
                except ValueError:
                    pass

            out.append({
                "source": SEC_SOURCE_MAP[filing_type],
                "source_id": entry_id,
                "ticker": ticker,
                "headline": title,
                "body": None,
                "url": link_el.get("href") if link_el is not None else None,
                "published_at": published_at,
            })
    return out


async def poll_once(conn):
    ticker_batch = _next_ticker_batch()
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            poll_fmp_news(client),
            poll_fmp_press_releases(client),
            poll_finnhub_general_news(client),
            poll_finnhub_watchlist(client, ticker_batch),
            poll_yfinance_watchlist(ticker_batch),
            poll_sec_filings(client),
        )

    inserted = 0
    for batch in results:
        for item in batch:
            row_id = db.insert_raw_item(conn, **item)
            if row_id is not None:
                inserted += 1
    conn.commit()

    scored = await score.score_all_pending(conn)
    return inserted, scored


async def run_loop():
    db.init_db()
    if not config.FMP_API_KEY:
        print("[ingest] FMP_API_KEY not set - skipping FMP news/press releases. "
              "Set FMP_API_KEY in .env to enable FMP (requires a paid FMP plan).")
    if not config.FINNHUB_API_KEY:
        print("[ingest] FINNHUB_API_KEY not set - skipping Finnhub news. "
              "Set FINNHUB_API_KEY in .env to enable it (free tier, no card needed).")

    while True:
        with db.connection() as conn:
            inserted, scored = await poll_once(conn)
        print(f"[ingest] poll complete: {inserted} new item(s), {scored} scored")
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_loop())
