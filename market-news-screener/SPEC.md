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
this phase, not calibrated output. See "Backtesting" below for how
placeholders get replaced with real numbers.

## Backtesting (`scripts/backtest.py`)

Placeholders are meant to be temporary. The backtest script measures what
each category *actually* did to price, using free daily history from
`yfinance`:

1. For each ingested item with a ticker, compute "day 0" — the trading day
   the market first had a chance to react (same day if published before
   4pm ET, otherwise the next trading day; weekends/holidays fall out
   naturally since yfinance only returns real trading days).
2. Move = % change from the close before day 0 to the close on/after day 0.
3. Normalize that move by the ticker's market-cap bucket multiplier (reusing
   `score.bucket_for_market_cap` and the `ticker_cache` table), so results
   are comparable to the tier table's documented "mid-cap-equivalent"
   `avg_move_pct` regardless of whether the sample happened to be a mega-cap
   or a microcap.
4. Aggregate per category: sample size, mean normalized move, and a
   `hit_rate` (fraction of *raw* moves at or above `--noise-threshold`,
   default 1.0%, i.e. "big enough to not just be noise").
5. A category is only trusted (`"calibrated": true`) once it has
   `--min-samples` (default 5) real examples. Below that, the script leaves
   the existing placeholder untouched rather than overwriting it with an
   estimate from 1-2 data points.

Dry run by default (`python scripts/backtest.py`); `--apply` is required to
actually write into `tier_table.json`, and it only touches categories that
cleared the sample-size bar — every other category's placeholder is left
exactly as-is. Full results (including uncalibrated categories and their
current sample counts) are always written to `data/backtest_results.json`.

### Tier re-ranking

The original Tier 1-4 assignments in `tier_table.json` were hand-picked
guesses (e.g. `rumor_speculation` was assumed Tier 1 because unconfirmed
reports were expected to carry the biggest surprise). Once a category
calibrates, `--apply` also re-derives its tier from the *measured*
`avg_move_pct`, using the same thresholds `tier_table.json`'s own
`_meta.tier_scale` already documents (`>5%` → Tier 1, `2-5%` → Tier 2,
`1-2%` → Tier 3, `<1%` → Tier 4) — not a separate, newly-invented rule.
`unclassified` is excluded on purpose: it's a catch-all for whatever the
classifier couldn't place, not a real signal category, so its measured
average is noise from an unrelated grab-bag of headlines and shouldn't
drive a tier.

When a tier changes, the category gains a `tier_reassigned_from` field
recording the old value, and the console report shows the transition
(e.g. `2->1`). This is deliberately real: a first production run surfaced
`sec_filing_8k_material` moving from Tier 2 to Tier 1 (its real average
move, 9.81%, was larger than `rumor_speculation`'s, 1.68%, which dropped
from Tier 1 to Tier 3) — i.e. reality contradicted the original hand-picked
ranking, and the tier table now reflects reality instead of the guess.

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

`POST /chat` — body `{"query": "<free text>"}`. Runs the free, local
rule-based parser in `src/chat.py` (`interpret_query_local`) to turn the
text into the same filter shape `/feed` uses, plus `sort_by` ("recency" or
"biggest_move") and `limit`, applies it via `db.query_feed`, and returns
`{items, filters_applied, explanation}`. `explanation` is built from the
resolved filters in code (not a second model call), so it can't drift from
what was actually queried. No LLM involved - see "Ask" in README.md for why
and how a Claude-backed version could slot in later without changing this
endpoint's contract.

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
