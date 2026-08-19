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

Not sure what to type? The "Examples…" dropdown next to the input lists
sample questions covering the patterns the parser understands — picking one
fills the input and runs it immediately.

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

## Backtesting the tier table against real prices

Every value in `data/tier_table.json` starts as an admitted placeholder
(`"placeholder": true`). `scripts/backtest.py` replaces that guesswork with
real measurement:

```bash
python scripts/backtest.py              # dry run - just prints a report
python scripts/backtest.py --apply      # also updates tier_table.json
```

For every ingested item with a ticker, it pulls free daily price history
(`yfinance`, no key needed) and measures the actual price move from the
close before the headline to the close on/after the day the market could
react to it — then normalizes that by the ticker's market-cap bucket so a
big move on a microcap doesn't skew a category's "mid-cap-equivalent" base
rate. A category is only marked `"calibrated": true` (and gets its
`avg_move_pct`/`hit_rate` updated) once it has at least `--min-samples`
(default 5) real examples — everything else stays an honest placeholder
rather than getting overwritten with a noisy estimate from 1-2 data points.

This is naturally most useful the longer the ingest loop has been running
(more real historical items = more calibrated categories) — re-run it
periodically as data accumulates. Results are also written to
`data/backtest_results.json` for a full per-category breakdown, including
categories that don't have enough samples yet.

When a category calibrates, `--apply` also re-derives its **Tier** from the
measured `avg_move_pct` (using the thresholds `tier_table.json`'s own
`_meta.tier_scale` already documents), since the original tiers were
hand-picked guesses. This can genuinely move a category — a real run moved
`sec_filing_8k_material` from Tier 2 to Tier 1 (measured 9.81% average move)
and `rumor_speculation` from Tier 1 down to Tier 3 (measured 1.68%), because
the data contradicted the original assumption. `unclassified` never gets
re-tiered — it's a catch-all bucket, not a real category.

### Important: re-score existing items after calibrating

`backtest.py --apply` only changes `tier_table.json` — it does **not**
retroactively update items already sitting in your database. Each item's
`tier`/`est_move_pct` gets computed once, at ingestion time, and stored;
restarting the API only affects items ingested *after* the restart. If you
skip this step, you'll see a confusing mix — some SEC 8-K filings showing
Tier 1, others still showing the old Tier 2, depending on when each one was
originally scored. Fix that by re-scoring everything against whatever the
tier table currently says:

```bash
python scripts/rescore.py
```

This is fast (no network calls — it just reapplies the already-known
category and cap-bucket for each item against the current tier table) and
safe to run anytime. Make it part of your routine: `backtest.py --apply`,
then `rescore.py`, then restart the API.

## Deployment (GitHub Pages, no server to host)

Everything above assumes a long-running local process (`python src/api.py`).
`.github/workflows/deploy.yml` runs a serverless variant instead, for a
public demo link: on a schedule (~every 15 min) and on every push to `main`,
it runs one ingest pass (`scripts/run_once.py`), exports a JSON snapshot
(`scripts/export_static.py`), builds the frontend in **static mode**
(`VITE_STATIC_MODE=true` — fetches the JSON snapshot instead of polling a
live API, and runs the "Ask" bar's filtering client-side via
`frontend/src/chat.js`, a JS port of `src/chat.py`), and publishes the
result to a dedicated `gh-pages` branch alongside the rest of the static
site (kept separate from `main` so the automated commits don't clutter your
actual portfolio history).

Because each scheduled run starts from an empty database, this is a **live
snapshot** (what's happening right now, refreshed every ~15 min) rather
than the deep historical archive your local instance builds up over days —
that's expected, not a bug. It uses whatever `tier_table.json` is currently
committed, so calibration work you do locally (`backtest.py --apply` +
`rescore.py`, then a normal `git commit`/`push`) carries over automatically
to the next deploy.

**One-time setup** (does not repeat for future changes):

1. **Add a repo secret**: on GitHub.com, go to Settings → Secrets and
   variables → Actions → New repository secret. Name it `FINNHUB_API_KEY`,
   paste your key. (Optional: `FMP_API_KEY` too, if you're on a paid plan —
   omit it entirely and that leg just gets skipped, same as local.)
2. **Trigger the workflow once** — push any commit to `main`, or run it
   manually from the repo's Actions tab (`Deploy site + market news
   screener` → Run workflow). This creates the `gh-pages` branch for the
   first time.
3. **Point Pages at the new branch**: Settings → Pages → under "Build and
   deployment," set Source to "Deploy from a branch," branch `gh-pages`,
   folder `/ (root)`.

After that, the resume site lives at the usual URL and the screener at
`.../screener/` under it — both redeploy automatically on the same
schedule/push triggers, no further manual steps.

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
scripts/
  seed_demo_data.py         local demo data for testing without live network access
  backtest.py               calibrates tier_table.json against real price history
  rescore.py                reapplies the current tier_table.json to already-ingested items
frontend/                  Vite + React polling table (plain-language labels, Tier 1-2 by default, Ask bar)
```
