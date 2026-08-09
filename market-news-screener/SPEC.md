# Market News Screener — SPEC

## Goal

Surface anything that could move a stock/ETF price in real time — not just
confirmed events (earnings, M&A, FDA decisions), but the anticipation, rumor,
and social chatter that often moves price *more* than the eventual
confirmation, because the confirmation is priced in and the surprise already
happened.

This document is the working spec for Phase 1. It didn't exist before this
build — it's written alongside the code so the architecture and the
tier table stay in sync.

## Phase 1 scope

1. **Ingest** raw news/filings from free/already-available sources.
2. **Classify** each item into a category (rule-based regex first pass,
   Claude API fallback for low-confidence matches).
3. **Score** each item using a tier table (base rate per category) adjusted
   for the ticker's market-cap bucket (smaller caps move more per unit of
   "surprise").
4. **Serve** a live, filterable feed over HTTP.
5. **Display** the feed in a polling React table, color-coded by tier, with
   an expandable "why this tier" breakdown.

## Data sources

| Source | What | Auth | Poll interval |
|---|---|---|---|
| Finnhub | `/news` (general market news) + `/company-news` (per-ticker, rotating batch) | Free-tier API key (`FINNHUB_API_KEY`), 60 calls/min, no card required | 60–90s (`POLL_INTERVAL_SECONDS`, default 75) |
| Yahoo Finance (via `yfinance`) | Per-ticker news (same rotating batch as Finnhub) | None — no key, but unofficial/scraped, best-effort | Same loop, 60–90s |
| Financial Modeling Prep (FMP) | `/stable/news/stock-latest` (general news) + `/stable/news/press-releases-latest` | Requires a **paid** FMP plan — FMP retired free-tier news access (legacy and current) in August 2025 | Same loop, 60–90s |
| SEC EDGAR "current events" feed | Real-time 8-K / SC 13D / SC 13G filings, Atom/RSS | None — public, no key | Same loop, 60–90s |

Finnhub is the primary free news leg (FMP's free tier stopped including any
news endpoint partway through this project — confirmed via a live `402`/
legacy-endpoint error against a real free-tier key). Yahoo Finance
(`yfinance`) supplements it for broader per-ticker coverage at no cost, at
the price of being an unofficial, undocumented integration that can break
without notice — failures there are logged and skipped, never fatal. Nothing
paid is enabled by default — `ingest.py` runs the SEC EDGAR leg
unconditionally (no key needed), Finnhub and Yahoo Finance for whichever
tickers are in the current rotation batch, and skips FMP with a warning
unless `FMP_API_KEY` is set to a key on a paid plan. Per the task
instructions, no paid tier/API should be added without asking first.

### Ticker coverage

Per-ticker sources can't afford to query the entire watchlist every poll
without exceeding free-tier rate limits, so `config.WATCHLIST` (default: a
~450-ticker large/mid-large-cap snapshot, `data/sp500_tickers.json`) is
consumed in rotating batches of `TICKER_BATCH_SIZE` (default 40) —
`ingest._next_ticker_batch()` advances a fixed-size window through the list
each poll cycle, wrapping around, so the full list gets swept roughly every
`ceil(len(WATCHLIST) / TICKER_BATCH_SIZE)` cycles (~15 minutes at defaults).
SEC EDGAR's filing feed is unaffected by this — it already covers every
filer regardless of watchlist.

## Categories & tiers

See `data/tier_table.json` for the full table (base `avg_move_pct`,
`hit_rate`, tier 1–4, per category) and market-cap bucket multipliers.

Four categories are the focus of this phase — they track *unconfirmed* or
*ambient* signals rather than confirmed events, because in practice the
unconfirmed leg of a story often carries the bigger, more tradeable surprise:

- **`rumor_speculation`** — "sources say", "in talks to", "considering",
  "exploring a sale" — unconfirmed reports of M&A, restructuring, executive
  moves, etc. Tier 1: the eventual confirmation is usually already priced in,
  so the rumor itself is where the surprise (and the move) lives.
- **`media_anticipation`** — buildup coverage ahead of a *known, scheduled*
  event: pre-earnings previews, "what to expect from the Fed", analyst
  previews ahead of a product launch. Tier 2: directional and can front-run
  the event, but less explosive than a true rumor since the event's timing
  isn't a surprise.
- **`social_sentiment_spike`** — unusual mention-volume or sentiment shift on
  financial social media (X/Twitter, Reddit/WSB) without a matching formal
  news item. Tier 3 pending calibration: real but noisier and harder to
  attribute to a single cause.
- **`rumor_denial`** — a company or official denial of a prior rumor. Tracked
  *separately* from the original rumor because denials move price too, often
  reversing a large fraction of the rumor's move. Tier 2.

All four are seeded with **placeholder** `avg_move_pct` / `hit_rate` values
(marked `"placeholder": true` in the tier table) — they need to be backfilled
with real historical price-reaction data once the pipeline has logged enough
of them. Everything else in the table (earnings, guidance, M&A, FDA,
analyst actions, filings, etc.) is also a reasonable-default placeholder for
this phase, not calibrated output.

## Market-cap bucket adjustment

The same headline moves a $500M microcap much more than a $500B megacap.
`score.py` multiplies each category's base `avg_move_pct` by a bucket
multiplier looked up from the ticker's market cap:

| Bucket | Market cap | Multiplier |
|---|---|---|
| mega | ≥ $200B | 0.6 |
| large | $10B–$200B | 0.85 |
| mid | $2B–$10B | 1.1 |
| small | $300M–$2B | 1.4 |
| micro | < $300M | 1.8 |
| unknown | cap not yet resolved | 1.0 |

Market caps are fetched lazily from FMP's profile endpoint on first sighting
of a ticker and cached in SQLite (`ticker_cache`, refreshed after 24h). If
`FMP_API_KEY` isn't set, every ticker falls back to the `unknown` bucket
(multiplier 1.0) rather than blocking scoring.

## Storage

SQLite (`data/news_screener.db`), three tables:

- `raw_items` — one row per ingested item (source, source_id, ticker,
  headline, body, url, published_at, ingested_at). Unique on
  `(source, source_id)` for dedup.
- `scored_items` — one row per classified+scored item, FK to `raw_items`
  (category, confidence, needs_llm, tier, base_avg_move_pct, hit_rate,
  cap_bucket, cap_multiplier, est_move_pct, scored_at).
- `ticker_cache` — market cap + bucket per ticker, with a refresh timestamp.

## Classification

`classify.py` runs a regex/keyword first pass against `headline + lede` for
every category in the tier table (including the 4 new ones). Each match
produces a confidence score; matches below `CLASSIFY_CONFIDENCE_THRESHOLD`
(default 0.6) are flagged `needs_llm=True` and, if `ANTHROPIC_API_KEY` is
set, re-classified with a Claude call constrained to the tier table's
category list. Without a key, low-confidence items are stored as
`category="unclassified"` pending a fallback pass rather than blocking the
pipeline.

## API

`GET /feed` (FastAPI) — live feed, most important first: ordered by tier
ascending (Tier 1 = biggest expected move, shown first) then `published_at`
descending within a tier. Filters: `ticker`, `category`, `tier` (max tier to
include, e.g. `tier=2` returns tiers 1–2), `min_timestamp` (ISO 8601 or
epoch seconds).

## Frontend

React table (Vite dev server) polling `/feed` every 5s. Columns: Time,
Ticker, Headline, Category, Tier, Est. move, Source. Row background
color-coded by tier (Tier 1 red → Tier 4 gray). Clicking a row expands it to
show the scoring breakdown in plain language: category base rate (avg move /
hit rate) × market-cap multiplier = estimated move, with the raw category
slug kept as small reference text rather than the primary label.

Internal identifiers are translated to plain language for display rather
than shown raw: category slugs (`rumor_speculation`) get a human label
("Rumor / Unconfirmed Report") from `label` fields in `tier_table.json`, and
ingest source names (`finnhub_company_news`, `sec_8k`) collapse to a
provider name ("Finnhub", "SEC (8-K)") via a small map in `App.jsx`. The
importance filter defaults to "Tier 1-2" rather than "everything," since an
unfiltered feed across ~450 tickers is mostly noise for a first look — a
"Show everything" option is still one click away.

## Explicitly out of scope for Phase 1

- Backfilling/calibrating real `avg_move_pct` / `hit_rate` numbers from
  historical price data.
- Any paid data source (real-time social listening APIs, premium FMP tiers,
  Bloomberg/Refinitiv, etc.) — ask before adding.
- Auth, multi-user, alerting/notifications, backtesting UI.
- Websocket push to the frontend (polling is fine for Phase 1).
