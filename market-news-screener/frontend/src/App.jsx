import { Fragment, useEffect, useMemo, useState } from "react";

const POLL_MS = 5000;

const TIER_LABELS = { 1: "Tier 1", 2: "Tier 2", 3: "Tier 3", 4: "Tier 4" };

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
  const [tierFilter, setTierFilter] = useState("");
  const [expandedId, setExpandedId] = useState(null);
  const [error, setError] = useState(null);
  const [lastUpdated, setLastUpdated] = useState(null);

  useEffect(() => {
    fetch("/api/tier-table")
      .then((r) => r.json())
      .then(setTierTable)
      .catch(() => setError("Could not load tier table from API"));
  }, []);

  useEffect(() => {
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
  }, [tickerFilter, categoryFilter, tierFilter]);

  const categories = useMemo(
    () => (tierTable ? Object.keys(tierTable.categories).sort() : []),
    [tierTable]
  );

  return (
    <div className="app">
      <header>
        <h1>Market News Screener</h1>
        <div className="status-line">
          {error ? (
            <span className="error">{error}</span>
          ) : (
            <span>
              {items.length} item(s){lastUpdated ? ` · updated ${lastUpdated.toLocaleTimeString()}` : ""}
            </span>
          )}
        </div>
      </header>

      <div className="filters">
        <input
          placeholder="Ticker (e.g. AAPL)"
          value={tickerFilter}
          onChange={(e) => setTickerFilter(e.target.value.toUpperCase())}
        />
        <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select value={tierFilter} onChange={(e) => setTierFilter(e.target.value)}>
          <option value="">All tiers</option>
          <option value="1">Tier 1 only</option>
          <option value="2">Tier 1-2</option>
          <option value="3">Tier 1-3</option>
          <option value="4">Tier 1-4</option>
        </select>
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
          {items.map((item) => {
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
                  <td>{item.category}</td>
                  <td>
                    <span className={`tier-badge tier-${item.tier}`}>{TIER_LABELS[item.tier]}</span>
                  </td>
                  <td>{item.est_move_pct}%</td>
                  <td>{item.source}</td>
                </tr>
                {isExpanded && (
                  <tr className="detail-row">
                    <td></td>
                    <td colSpan={7}>
                      <div className="detail">
                        <div>
                          <strong>Category base rate</strong>: {item.category} — avg move{" "}
                          {item.base_avg_move_pct}%, hit rate {Math.round(item.hit_rate * 100)}%
                        </div>
                        <div>
                          <strong>Market cap adjustment</strong>: {item.cap_bucket} bucket × {item.cap_multiplier}
                        </div>
                        <div>
                          <strong>Estimated move</strong>: {item.base_avg_move_pct}% × {item.cap_multiplier} ={" "}
                          <strong>{item.est_move_pct}%</strong>
                        </div>
                        <div>
                          <strong>Classification</strong>: {item.classified_by} (confidence{" "}
                          {Math.round(item.confidence * 100)}%
                          {item.needs_llm ? ", flagged for LLM review" : ""})
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
