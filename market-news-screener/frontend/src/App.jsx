import { Fragment, useEffect, useMemo, useState } from "react";

const POLL_MS = 5000;

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

function buildFeedUrl({ ticker, category, tier }) {
  const params = new URLSearchParams();
  if (ticker) params.set("ticker", ticker);
  if (category) params.set("category", category);
  if (tier) params.set("tier", tier);
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
  const [tierTable, setTierTable] = useState(null);
  const [tickerFilter, setTickerFilter] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [tierFilter, setTierFilter] = useState("2");
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  // "live" = normal polling feed with the dropdown filters above.
  // "chat" = a one-shot snapshot returned by /api/chat for a typed question;
  // polling pauses until the user goes back to the live feed.
  const [viewMode, setViewMode] = useState("live");
  const [chatQuery, setChatQuery] = useState("");
  const [chatItems, setChatItems] = useState([]);
  const [chatExplanation, setChatExplanation] = useState("");
  const [chatError, setChatError] = useState(null);
  const [chatLoading, setChatLoading] = useState(false);

  useEffect(() => {
    fetch("/api/tier-table")
      .then((r) => r.json())
      .then(setTierTable)
      .catch(() => setError("Could not load tier table from API"));
  }, []);

  useEffect(() => {
    if (viewMode !== "live") return;
    let cancelled = false;

    async function poll() {
      try {
        const res = await fetch(buildFeedUrl({ ticker: tickerFilter, category: categoryFilter, tier: tierFilter }));
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
  }, [tickerFilter, categoryFilter, tierFilter, viewMode]);

  async function submitChat(e) {
    e.preventDefault();
    if (!chatQuery.trim() || chatLoading) return;
    setChatLoading(true);
    setChatError(null);
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: chatQuery }),
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

  return (
    <div className="app">
      <header>
        <div>
          <h1>Market News Screener</h1>
          <p className="subtitle">What's moving stocks right now — including rumors and buildup, not just confirmed news.</p>
        </div>
        <div className="status-line">
          {error ? (
            <span className="error">Can't reach the server — is `python src/api.py` running? ({error})</span>
          ) : viewMode === "chat" ? (
            <span>{chatExplanation}</span>
          ) : (
            <span>
              {items.length} item{items.length === 1 ? "" : "s"}
              {lastUpdated ? ` · updated ${lastUpdated.toLocaleTimeString()}` : ""}
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
                {isExpanded && (
                  <tr className="detail-row">
                    <td></td>
                    <td colSpan={7}>
                      <div className="detail">
                        <div>
                          <strong>Why this tier:</strong> "{categoryLabel(item.category)}" headlines move a
                          typical stock about <strong>{item.base_avg_move_pct}%</strong> on average, and have
                          historically gone the expected direction {Math.round(item.hit_rate * 100)}% of the
                          time.
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
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
