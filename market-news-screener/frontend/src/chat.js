// Client-side port of src/chat.py's local rule-based parser, for the static
// (GitHub Pages) deployment where there's no backend to call /api/chat.
// Same principle as the Python version: this only ever picks which already-
// loaded, real items to show - it never invents anything. Keep this in sync
// with src/chat.py if the patterns there change.

const CATEGORY_KEYWORDS = [
  [/\brumou?r denials?\b|denied the rumou?r|denies? the rumou?r/i, "rumor_denial"],
  [/\brumou?rs?\b|\bunconfirmed\b|\bspeculation\b|\bsources say\b/i, "rumor_speculation"],
  [/\bearnings beat|beats? estimates\b/i, "earnings_beat"],
  [/\bearnings miss|misses? estimates\b/i, "earnings_miss"],
  [/\bguidance (?:raise|raised|hike)\b|\braised? guidance\b/i, "guidance_raise"],
  [/\bguidance cut\b|\bcut guidance\b|\blowered guidance\b/i, "guidance_cut"],
  [/\bm ?& ?a\b|\bacquisition\b|\bmerger\b|\bbuyout\b/i, "ma_definitive_agreement"],
  [/\bfda\b/i, "fda_decision"],
  [/\bbankrupt/i, "bankruptcy_restructuring"],
  [/\bsettlement\b/i, "legal_settlement"],
  [/\bregulatory\b|\binvestigation\b|\blawsuit\b|\bsubpoena\b/i, "regulatory_legal_action"],
  [/\bceo\b|\bcfo\b|\bexecutive change\b|\bresign/i, "executive_change"],
  [/\banalyst upgrades?\b|\bupgraded\b/i, "analyst_upgrade"],
  [/\banalyst downgrades?\b|\bdowngraded\b/i, "analyst_downgrade"],
  [/\bdividend\b/i, "dividend_change"],
  [/\bbuyback\b|\brepurchase\b/i, "buyback_announced"],
  [/\b8-?k\b/i, "sec_filing_8k_material"],
  [/\b13-?d\b|\bactivist\b/i, "sec_filing_13d_activist"],
  [/\b13-?g\b/i, "sec_filing_13g_passive"],
  [/\binsider (?:trad|buy|sell)|\bform 4\b/i, "insider_form4"],
  [/\bproduct launch\b|\bnew launch\b/i, "product_launch"],
  [/\bpartnership\b|\bjoint venture\b/i, "partnership_announced"],
  [/\bstock split\b|\breverse split\b/i, "stock_split"],
  [/\bsocial media\b|\breddit\b|\bwallstreetbets\b|\btwitter\b|\bsentiment\b/i, "social_sentiment_spike"],
  [/\bbuildup\b|\bahead of\b|\bpreview\b|\banticipation\b/i, "media_anticipation"],
];

const BIGGEST_MOVE_WORDS = /\bbiggest\b|\blargest\b|\btop movers?\b|\bmost impact\b|\bpotential moves?\b|\bmovers\b/i;
const ONLY_BIGGEST_TIER = /\bonly the biggest\b|\bmajor(?:\s+news)? only\b|\btier ?1 only\b|\bbiggest impact only\b/i;
const TOP_N = /\btop\s+(\d+)\b|\b(\d+)\s+(?:biggest|largest|top)\b/i;
const SHOW_N = /\bshow(?:\s+me)?\s+(\d+)\b/i;
const TIER_N = /\btier\s*1?-?(\d)\b/i;
const LAST_N_HOURS = /\blast\s+(\d+)\s*hours?\b|\bpast\s+(\d+)\s*hours?\b/i;
const LAST_N_DAYS = /\blast\s+(\d+)\s*days?\b|\bpast\s+(\d+)\s*days?\b/i;
const TODAY = /\btoday\b|\blast 24 ?h(?:ours?)?\b/i;
const THIS_WEEK = /\bthis week\b|\blast 7 ?days?\b|\bpast week\b/i;
const LAST_HOUR = /\blast hour\b|\bpast hour\b/i;
const EVERYTHING = /\beverything\b|\ball tiers\b|\bshow all\b|\bno filter\b/i;

function findTicker(query, tickerSet) {
  const matches = query.match(/\b[A-Z]{2,5}\b/g) || [];
  for (const token of matches) {
    if (tickerSet.has(token)) return token;
  }
  return null;
}

function findCategory(queryLower) {
  for (const [pattern, slug] of CATEGORY_KEYWORDS) {
    if (pattern.test(queryLower)) return slug;
  }
  return null;
}

function firstMatchedNumber(match) {
  if (!match) return null;
  for (let i = 1; i < match.length; i++) {
    if (match[i]) return parseInt(match[i], 10);
  }
  return null;
}

function findLimit(queryLower) {
  return firstMatchedNumber(TOP_N.exec(queryLower)) ?? firstMatchedNumber(SHOW_N.exec(queryLower));
}

function findMaxTier(queryLower) {
  if (ONLY_BIGGEST_TIER.test(queryLower)) return 1;
  if (EVERYTHING.test(queryLower)) return 4;
  const m = TIER_N.exec(queryLower);
  if (m) {
    const n = parseInt(m[1], 10);
    if (n >= 1 && n <= 4) return n;
  }
  return null;
}

function findSinceHours(queryLower) {
  const hoursMatch = firstMatchedNumber(LAST_N_HOURS.exec(queryLower));
  if (hoursMatch != null) return hoursMatch;
  const daysMatch = firstMatchedNumber(LAST_N_DAYS.exec(queryLower));
  if (daysMatch != null) return daysMatch * 24;
  if (LAST_HOUR.test(queryLower)) return 1;
  if (TODAY.test(queryLower)) return 24;
  if (THIS_WEEK.test(queryLower)) return 24 * 7;
  return null;
}

/** Rule-based parse. Mirrors src/chat.py's interpret_query_local(). */
export function interpretQueryLocal(query, tickerSet) {
  const queryLower = query.toLowerCase();
  return {
    ticker: findTicker(query, tickerSet),
    category: findCategory(queryLower),
    maxTier: findMaxTier(queryLower),
    sortBy: BIGGEST_MOVE_WORDS.test(queryLower) ? "biggest_move" : "recency",
    limit: findLimit(queryLower),
    sinceHours: findSinceHours(queryLower),
  };
}

/** Normalize a raw parse into safe filter params. Mirrors resolve_filters(). */
export function resolveFilters(raw, validCategories) {
  const ticker = raw.ticker || null;
  const category = raw.category && validCategories.has(raw.category) ? raw.category : null;
  const maxTier = [1, 2, 3, 4].includes(raw.maxTier) ? raw.maxTier : null;
  const sortBy = raw.sortBy === "biggest_move" ? "biggest_move" : "recency";

  let limit = Number.isFinite(raw.limit) ? raw.limit : null;
  if (limit == null) {
    limit = sortBy === "biggest_move" ? 20 : 200;
  }
  limit = Math.max(1, Math.min(limit, 500));

  let sinceHours = Number.isFinite(raw.sinceHours) && raw.sinceHours > 0 ? raw.sinceHours : null;
  let minTimestamp = null;
  if (sinceHours) {
    minTimestamp = new Date(Date.now() - sinceHours * 3600 * 1000).toISOString();
  }

  return { ticker, category, maxTier, sortBy, limit, sinceHours, minTimestamp };
}

/** Apply resolved filters to an in-memory items array (client-side
 * equivalent of db.query_feed's SQL WHERE/ORDER BY/LIMIT). */
export function applyFilters(items, filters) {
  let result = items.filter((item) => {
    if (filters.ticker && item.ticker !== filters.ticker) return false;
    if (filters.category && item.category !== filters.category) return false;
    if (filters.maxTier != null && item.tier > filters.maxTier) return false;
    if (filters.minTimestamp && item.published_at < filters.minTimestamp) return false;
    return true;
  });

  result.sort((a, b) => {
    if (filters.sortBy === "biggest_move") {
      if (b.est_move_pct !== a.est_move_pct) return b.est_move_pct - a.est_move_pct;
    } else if (a.tier !== b.tier) {
      return a.tier - b.tier;
    }
    return b.published_at.localeCompare(a.published_at);
  });

  return result.slice(0, filters.limit);
}

/** One-line explanation, mirrors describe_filters(). */
export function describeFilters(filters, count, tierTable) {
  const parts = [`Showing ${count} item${count === 1 ? "" : "s"}`];
  if (filters.ticker) parts.push(`for ${filters.ticker}`);
  if (filters.category) {
    const label = tierTable?.categories?.[filters.category]?.label || filters.category;
    parts.push(`in "${label}"`);
  }
  if (filters.maxTier) parts.push(`(Tier 1-${filters.maxTier})`);
  if (filters.sortBy === "biggest_move") parts.push("sorted by biggest estimated move");
  if (filters.sinceHours) parts.push(`from the last ${filters.sinceHours}h`);
  return parts.join(" ") + ".";
}
