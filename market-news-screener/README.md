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

This polls Finnhub's general market news plus per-ticker company news for
everything in `WATCHLIST`, well within the free tier's 60 calls/min limit
even at the default 75s poll interval.

## Enabling FMP (requires a paid plan)

FMP still provides company profile / market-cap data on some tiers, and its
news endpoints are usable again if you're on a paid plan. Put the key in
`.env` as `FMP_API_KEY=...` and restart. No paid tier is enabled by default —
ask before upgrading to one.

## Enabling the LLM classification fallback

Set `ANTHROPIC_API_KEY` in `.env`. Without it, low-confidence regex matches
are stored as `category="unclassified"` with `needs_llm=1` for a later batch
pass instead of blocking the pipeline.

## Project layout

```
SPEC.md                    architecture/design notes
data/tier_table.json       category tiers, avg move %, hit rate, cap buckets
src/
  config.py                env vars / paths
  db.py                     SQLite schema + query helpers
  ingest.py                 FMP + Finnhub + SEC EDGAR pollers
  classify.py               regex classifier + Claude fallback
  score.py                  tier table + market-cap scoring
  api.py                    FastAPI app (serves /feed, runs the ingest loop)
frontend/                  Vite + React polling table
```
