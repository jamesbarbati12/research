import { Fragment, useEffect, useMemo, useState } from "react";
import { applyFilters, describeFilters, interpretQueryLocal, resolveFilters } from "./chat.js";

// True for the GitHub Pages deployment (built with VITE_STATIC_MODE=true):
// there's no live backend, so data comes from a JSON snapshot refreshed by
// a scheduled CI job instead of a server polled every few seconds, and the
// "Ask" bar runs entirely client-side (see chat.js) instead of calling
// POST /api/chat. Local dev (`npm run dev` against `python src/api.py`)
// leaves this false and behaves exactly as before.
const STATIC_MODE = import.meta.env.VITE_STATIC_MODE === "true";

const POLL_MS = 5000;
const STATIC_REFRESH_MS = 60000; // how often to re-fetch the static snapshot

const TIER_LABELS = { 1: "Tier 1 — Big", 2: "Tier 2 — Notable", 3: "Tier 3 — Minor", 4: "Tier 4 — Noise" };

// Raw source identifiers are internal plumbing (which poller found the item)
// - collapse them to the provider name a person actually recognizes.
const SOURCE_LABELS = {
  fmp_news: "FMP",
  fmp_press_release: "FMP",
  finnhub_news: "Finnhub",
  finnhub_company_news: "Finnhub",
  yfinance_news: "Yahoo Finance",
  sec_8k: "SEC (8-K)",
  sec_13d: "SEC (13D)",
  sec_13g: "SEC (13G)",
};

function sourceLabel(source) {
  return SOURCE_LABELS[source] || source;
}

// Example prompts for the "Ask" dropdown - each one is phrasing src/chat.py
// (or its client-side port, chat.js) actually recognizes, so picking one
// always produces a result.
const EXAMPLE_QUESTIONS = [
  "Top 5 biggest moves",
  "Top 10 biggest moves today",
  "Any rumors right now",
  "Rumor denials this week",
  "Earnings beats today",
  "Earnings misses",
  "Analyst downgrades this week",
  "Analyst upgrades",
  "SEC 8-K filings",
  "Activist stakes (13D)",
  "Dividend announcements",
  "Buybacks announced",
  "Only the biggest news (Tier 1)",
  "Show everything",
];

function buildFeedUrl({ ticker, category, tier, limit }) {
  const params = new URLSearchParams();
  if (ticker) params.set("ticker", ticker);
  if (category) params.set("category", category);
  if (tier) params.set("tier", tier);
  if (limit) params.set("limit", limit);
  const qs = params.toString();
  return `/api/feed${qs ? `?${qs}` : ""}`;
}

function formatTime(iso) {
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  } catch {
    return iso;
  }
}

export default function App() {
  const [items, setItems] = useState([]);
  const [allItems, setAllItems] = useState([]); // static mode only: the full snapshot
  const [tickerSet, setTickerSet] = useState(new Set()); // static mode only: for Ask ticker detection
  const [snapshotAt, setSnapshotAt] = useState(null); // static mode only: when the CI job generated feed.json
  const [tierTable, setTierTable] = useState(null);
  const [tickerFilter, setTickerFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [tierFilter, setTierFilter] = useState("2");
  const [limitFilter, setLimitFilter] = useState("200");
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  // "live" = normal polling feed with the dropdown filters above.
  // "chat" = a one-shot snapshot from a typed question; polling pauses
  // until the user goes back to the live feed.
  const [viewMode, setViewMode] = useState("live");
  const [chatQuery, setChatQuery] = useState("");
  const [chatItems, setChatItems] = useState([]);
  const [chatExplanation, setChatExplanation] = useState("");
  const [chatError, setChatError] = useState(null);
  const [chatLoading, setChatLoading] = useState(false);

  useEffect(() => {
    // Relative (no leading slash) in static mode - the built app can be
    // served from a nested path (e.g. /research/screener/), and an
    // absolute "/tier-table.json" would resolve to the domain root instead.
    const tierTableUrl = STATIC_MODE ? "tier-table.json" : "/api/tier-table";
    fetch(tierTableUrl)
      .then((r) => r.json())
      .then(setTierTable)
      .catch(() => setError("Could not load the tier table"));

    if (STATIC_MODE) {
      fetch("tickers.json")
        .then((r) => r.json())
        .then((data) => setTickerSet(new Set(data.tickers || [])))
        .catch(() => {});
    }
  }, []);

  // Live mode: poll the backend with server-side filtering every 5s.
  useEffect(() => {
    if (STATIC_MODE || viewMode !== "live") return;
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(
          buildFeedUrl({ ticker: tickerFilter, category: categoryFilter, tier: tierFilter, limit: limitFilter })
        );
        if (!res.ok) throw new Error(`API returned ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setItems(data.items || []);
          setLastUpdated(new Date());
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [tickerFilter, categoryFilter, tierFilter, limitFilter, viewMode]);

  // Static mode: fetch the whole snapshot occasionally (the underlying file
  // only changes every ~15 min via CI); filtering happens client-side.
  useEffect(() => {
    if (!STATIC_MODE) return;
    let cancelled = false;

    async function loadSnapshot() {
      try {
        const res = await fetch(`feed.json?t=${Date.now()}`);
        if (!res.ok) throw new Error(`Snapshot returned ${res.status}`);
        const data = await res.json();
        if (!cancelled) {
          setAllItems(data.items || []);
          setSnapshotAt(data.generated_at || null);
          setLastUpdated(new Date());
          setError(null);
        }
      } catch (err) {
        if (!cancelled) setError(err.message);
      }
    }

    loadSnapshot();
    const id = setInterval(loadSnapshot, STATIC_REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Static mode: re-derive the displayed live-view items whenever the
  // snapshot or dropdown filters change (client-side equivalent of the
  // live mode's server-side query).
  useEffect(() => {
    if (!STATIC_MODE) return;
    const resolved = {
      ticker: tickerFilter || null,
      category: categoryFilter || null,
      maxTier: tierFilter ? parseInt(tierFilter, 10) : null,
      sortBy: "recency",
      limit: limitFilter ? parseInt(limitFilter, 10) : 200,
      minTimestamp: null,
    };
    setItems(applyFilters(allItems, resolved));
  }, [allItems, tickerFilter, categoryFilter, tierFilter, limitFilter]);

  async function askQuestion(text) {
    if (!text.trim() || chatLoading) return;
    setChatError(null);

    if (STATIC_MODE) {
      const validCategories = new Set(Object.keys(tierTable?.categories || {}));
      const raw = interpretQueryLocal(text, tickerSet);
      const resolved = resolveFilters(raw, validCategories);
      const matched = applyFilters(allItems, resolved);
      setChatItems(matched);
      setChatExplanation(describeFilters(resolved, matched.length, tierTable));
      setViewMode("chat");
      return;
    }

    setChatLoading(true);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: text }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `Server returned ${res.status}`);
      setChatItems(data.items || []);
      setChatExplanation(data.explanation || "");
      setViewMode("chat");
    } catch (err) {
      setChatError(err.message);
    } finally {
      setChatLoading(false);
    }
  }

  function submitChat(e) {
    e.preventDefault();
    askQuestion(chatQuery);
  }

  function pickExample(e) {
    const question = e.target.value;
    e.target.value = ""; // reset to placeholder so it reads as a menu, not a persistent choice
    if (!question) return;
    setChatQuery(question);
    askQuestion(question);
  }

  function backToLiveFeed() {
    setViewMode("live");
    setChatItems([]);
    setChatExplanation("");
    setChatError(null);
    setChatQuery("");
  }

  const displayedItems = viewMode === "chat" ? chatItems : items;

  const categoryOptions = useMemo(() => {
    if (!tierTable) return [];
    return Object.entries(tierTable.categories)
      .map(([slug, info]) => ({ slug, label: info.label || slug }))
      .sort((a, b) => a.label.localeCompare(b.label));
  }, [tierTable]);

  function categoryLabel(slug) {
    return tierTable?.categories?.[slug]?.label || slug;
  }

  function calibrationInfo(slug) {
    const info = tierTable?.categories?.[slug];
    if (!info) return null;
    if (info.placeholder === false && info.calibrated_n) {
      return { calibrated: true, n: info.calibrated_n, at: info.calibrated_at };
    }
    return { calibrated: false };
  }

  return (
    <div className="app">
      <header>
        <div>
          <h1>Market News Screener</h1>
          <p className="subtitle">What's moving stocks right now — including rumors and buildup, not just confirmed news.</p>
        </div>
        <div className="status-line">
          {error ? (
            <span className="error">
              {STATIC_MODE
                ? `Couldn't load the data snapshot (${error})`
                : `Can't reach the server — is \`python src/api.py\` running? (${error})`}
            </span>
          ) : viewMode === "chat" ? (
            <span>{chatExplanation}</span>
          ) : (
            <span>
              {items.length} item{items.length === 1 ? "" : "s"}
              {STATIC_MODE && snapshotAt
                ? ` · snapshot generated ${new Date(snapshotAt).toLocaleString()}`
                : lastUpdated
                ? ` · updated ${lastUpdated.toLocaleTimeString()}`
                : ""}
            </span>
          )}
        </div>
      </header>

      <form className="chat-bar" onSubmit={submitChat}>
        <input
          placeholder='Ask e.g. "top 5 biggest moves today" or "rumors about AAPL"'
          value={chatQuery}
          onChange={(e) => setChatQuery(e.target.value)}
        />
        <select
          className="example-picker"
          defaultValue=""
          onChange={pickExample}
          disabled={chatLoading}
          aria-label="Example questions"
        >
          <option value="" disabled>
            Examples…
          </option>
          {EXAMPLE_QUESTIONS.map((q) => (
            <option key={q} value={q}>
              {q}
            </option>
          ))}
        </select>
        <button type="submit" disabled={chatLoading}>
          {chatLoading ? "Thinking…" : "Ask"}
        </button>
        {viewMode === "chat" && (
          <button type="button" className="secondary" onClick={backToLiveFeed}>
            Back to live feed
          </button>
        )}
      </form>
      {chatError && <div className="chat-error">{chatError}</div>}

      <div className={`filters ${viewMode === "chat" ? "filters-disabled" : ""}`}>
        <label className="filter-field">
          <span>Ticker</span>
          <input
            placeholder="e.g. AAPL"
            value={tickerFilter}
            onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
          />
        </label>
        <label className="filter-field">
          <span>Category</span>
          <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
            <option value="">All categories</option>
            {categoryOptions.map(({ slug, label }) => (
              <option key={slug} value={slug}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="filter-field">
          <span>Importance</span>
          <select value={tierFilter} onChange={(e) => setTierFilter(e.target.value)}>
            <option value="1">Only the biggest (Tier 1)</option>
            <option value="2">Big + notable (Tier 1-2)</option>
            <option value="3">Include minor (Tier 1-3)</option>
            <option value="4">Show everything</option>
          </select>
        </label>
        <label className="filter-field">
          <span>Show</span>
          <select value={limitFilter} onChange={(e) => setLimitFilter(e.target.value)}>
            <option value="50">50 items</option>
            <option value="100">100 items</option>
            <option value="200">200 items</option>
            <option value="500">500 items</option>
            <option value="1000">1000 items</option>
          </select>
        </label>
      </div>

      <div className="tier-legend">
        {[1, 2, 3, 4].map((t) => (
          <span key={t} className={`legend-chip tier-${t}`}>
            {TIER_LABELS[t]}
          </span>
        ))}
      </div>

      <table>
        <thead>
          <tr>
            <th></th>
            <th>Time</th>
            <th>Ticker</th>
            <th>Headline</th>
            <th>Category</th>
            <th>Tier</th>
            <th>Est. move</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {displayedItems.length === 0 && !error && (
            <tr className="empty-row">
              <td colSpan={8}>
                {viewMode === "chat"
                  ? "Nothing matched that question — try rephrasing, or go back to the live feed."
                  : 'No items yet at this filter level — try "Show everything," or give the ingest loop a minute to pull in its first batch.'}
              </td>
            </tr>
          )}
          {displayedItems.map((item) => {
            const isExpanded = expandedId === item.id;
            return (
              <Fragment key={item.id}>
                <tr
                  className={`tier-${item.tier}`}
                  onClick={() => setExpandedId(isExpanded ? null : item.id)}
                >
                  <td className="expand-caret">{isExpanded ? "▾" : "▸"}</td>
                  <td>{formatTime(item.published_at)}</td>
                  <td className="ticker">{item.ticker || "—"}</td>
                  <td className="headline">
                    {item.url ? (
                      <a href={item.url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
                        {item.headline}
                      </a>
                    ) : (
                      item.headline
                    )}
                  </td>
                  <td title={item.category}>{categoryLabel(item.category)}</td>
                  <td>
                    <span className={`tier-badge tier-${item.tier}`}>Tier {item.tier}</span>
                  </td>
                  <td>{item.est_move_pct}%</td>
                  <td>{sourceLabel(item.source)}</td>
                </tr>
                {isExpanded && (() => {
                  const calibration = calibrationInfo(item.category);
                  return (
                  <tr className="detail-row">
                    <td></td>
                    <td colSpan={7}>
                      <div className="detail">
                        <div>
                          <strong>Why this tier:</strong> "{categoryLabel(item.category)}" headlines move a
                          typical stock about <strong>{item.base_avg_move_pct}%</strong> on average, and have
                          historically gone the expected direction {Math.round(item.hit_rate * 100)}% of the
                          time.{" "}
                          {calibration?.calibrated ? (
                            <span className="calibration-badge calibrated">
                              ✓ calibrated from {calibration.n} real historical events
                            </span>
                          ) : (
                            <span className="calibration-badge placeholder">
                              placeholder estimate — not yet calibrated from real data
                            </span>
                          )}
                        </div>
                        <div>
                          <strong>Size adjustment:</strong> this ticker is in the "{item.cap_bucket}"
                          market-cap bucket, which scales the move estimate ×{item.cap_multiplier} (smaller
                          companies tend to move more on the same news).
                        </div>
                        <div>
                          <strong>Estimated move:</strong> {item.base_avg_move_pct}% × {item.cap_multiplier} ={" "}
                          <strong>{item.est_move_pct}%</strong>
                        </div>
                        <div className="muted">
                          Classified by {item.classified_by} (confidence {Math.round(item.confidence * 100)}%
                          {item.needs_llm ? " — flagged for a closer look" : ""}) · raw category:{" "}
                          <code>{item.category}</code>
                        </div>
                        {item.body && <div className="lede">{item.body}</div>}
                      </div>
                    </td>
                  </tr>
                  );
                })()}
              </Fragment>
            );
          })}
        </tbody>
      </table>

      <footer className="disclaimer">
        Aggregates already-public news, press releases, and SEC filings — it does not access or use
        material non-public information. Estimated moves and tier rankings are statistical averages
        derived from historical data, not predictions. Nothing here is investment advice or a
        recommendation to buy or sell any security.
        {STATIC_MODE && " This deployed demo refreshes on a schedule (~every 15 minutes), not continuously."}
      </footer>
    </div>
  );
}
