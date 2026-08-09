# Market News Screener — Phase 1

Real-time screener that tracks anything that could move a stock/ETF price —
confirmed events (earnings, M&A, FDA decisions, filings) as well as rumor,
anticipation, and social sentiment. See `SPEC.md` for the full architecture
and `data/tier_table.json` for the category tier table.

## Local dev setup

### Backend

```bash
cd market-news-screener
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # optionally add FINNHUB_API_KEY / FMP_API_KEY / ANTHROPIC_API_KEY
python src/api.py
```

This starts the API on `http://localhost:8000` **and** runs the ingest poll
loop as a background task inside the same process — no separate process
needed. It works out of the box with no keys: the SEC EDGAR real-time
8-K/13D/13G feed needs no auth. Finnhub and FMP news are skipped (with a log
line) until their keys are set in `.env`.

Check it's alive:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/feed
```

### Frontend

```bash
cd market-news-screener/frontend
npm install
npm run dev
```

Open `http://localhost:5173`. It polls `/api/feed` (proxied to the backend
on :8000) every 5 seconds.

## Enabling Finnhub (free news source)

FMP retired its free news access in August 2025 — the free tier no longer
includes any news endpoint (legacy or current), only a paid plan does.
Finnhub is the free replacement used instead:

1. Sign up at https://finnhub.io/register — free, no card required.
2. Put the key in `market-news-screener/.env` as `FINNHUB_API_KEY=...`.
3. Restart `python src/api.py`.

This polls Finnhub's general market news plus per-ticker company news for a
rotating batch of `WATCHLIST` (see "Ticker coverage" below), well within the
free tier's 60 calls/min limit even at the default 75s poll interval.

## Ticker coverage

`WATCHLIST` defaults to a static snapshot of ~450 large/mid-large-cap US
tickers (`data/sp500_tickers.json`) rather than a small hardcoded handful.
Per-ticker sources (Finnhub company-news, Yahoo Finance) can't query the
whole list every 75s without blowing past free-tier rate limits, so
`ingest.py` rotates through it in batches of `TICKER_BATCH_SIZE` (default 40)
— a full sweep of the default list takes about 15 minutes. SEC EDGAR's
filing feed isn't watchlist-limited at all; it already covers every filer.

Set `WATCHLIST=AAPL,MSFT,...` in `.env` to use your own list instead of the
default snapshot, and `TICKER_BATCH_SIZE` to tune the rotation speed vs.
rate-limit headroom.

## Yahoo Finance (yfinance) — supplementary, no key needed

Per-ticker news via the unofficial `yfinance` library, for the same rotating
batch used for Finnhub. It scrapes an undocumented Yahoo endpoint (no
official API, no key), so treat it as best-effort: per-ticker failures are
logged and skipped rather than blocking the rest of the pipeline, and Yahoo
can change the response shape or rate-limit without notice.

## Enabling FMP (requires a paid plan)

FMP still provides company profile / market-cap data on some tiers, and its
news endpoints are usable again if you're on a paid plan. Put the key in
`.env` as `FMP_API_KEY=...` and restart. No paid tier is enabled by default —
ask before upgrading to one.

## Enabling the LLM classification fallback

Set `ANTHROPIC_API_KEY` in `.env`. Without it, low-confidence regex matches
are stored as `category="unclassified"` with `needs_llm=1` for a later batch
pass instead of blocking the pipeline.

## Ask (chat-style search)

The input bar above the table ("Ask e.g. ...") lets you type things like
*"top 5 biggest moves today"* or *"rumors about AAPL"* instead of setting
the dropdown filters by hand. This is a **free, local, rule-based parser**
(`src/chat.py`) — no API key, no cost, no LLM call. It recognizes patterns
like "top N" / "N biggest", ticker symbols typed in caps (checked against
`WATCHLIST`), category keywords ("rumors", "earnings beat", "SEC filing"
sub-types, etc.), and time windows ("today", "this week", "last N hours").
It only ever picks which real, already-collected rows to show — it can't
invent headlines or numbers. Phrasing it doesn't recognize just falls back
to showing everything, most-recent-first.

Asking a question is a one-shot snapshot (not live-polled) — click "Back to
live feed" to resume the normal auto-refreshing view with your dropdown
filters.

This intentionally isn't Claude/LLM-backed — that was considered (and would
understand more flexible phrasing) but requires an `ANTHROPIC_API_KEY` with
billing attached, so it was skipped in favor of the free option. The parser
in `src/chat.py` returns a plain filter dict (`ticker`, `category`,
`max_tier`, `sort_by`, `limit`, `since_hours`), so if an LLM-backed version
is ever added later, it just needs to produce that same shape - no rewrite
of `api.py` or the frontend required.

## Project layout

```
SPEC.md                    architecture/design notes
data/tier_table.json       category tiers, avg move %, hit rate, cap buckets, display labels
data/sp500_tickers.json    default watchlist snapshot for per-ticker news polling
src/
  config.py                env vars / paths / watchlist loading
  db.py                     SQLite schema + query helpers
  ingest.py                 FMP + Finnhub + Yahoo Finance + SEC EDGAR pollers
  classify.py               regex classifier + Claude fallback
  score.py                  tier table + market-cap scoring
  chat.py                   free local NL-query parser for the Ask bar
  api.py                    FastAPI app (serves /feed, /chat, runs the ingest loop)
frontend/                  Vite + React polling table (plain-language labels, Tier 1-2 by default, Ask bar)
```
