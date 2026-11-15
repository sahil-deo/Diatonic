// Forgiving search for the command palette: no DOM, so it can be tested on its own.

const boundary = (t, i) => i === 0 || /[\s\-_:/.]/.test(t[i - 1]);

/** How well `query` matches `text`: {score, hits} (hits = indexes to highlight), or null for no match. Higher is better. */
export function fuzzy(query, text) {
  const q = String(query ?? "").trim().toLowerCase(), t = String(text ?? "").toLowerCase();
  if (!q) return { score: 0, hits: [] };
  const at = t.indexOf(q);
  if (at >= 0) return { score: 100 + (at === 0 ? 50 : boundary(t, at) ? 30 : 0) - t.length * 0.1, hits: Array.from({ length: q.length }, (_, i) => at + i) };
  const words = q.split(/\s+/);
  if (words.length > 1) {   // "open fi" finds "Open Files": every word somewhere, each as a substring
    const hits = [];
    for (const w of words) {
      const i = t.indexOf(w);
      if (i < 0) { hits.length = 0; break; }
      for (let k = 0; k < w.length; k++) hits.push(i + k);
    }
    if (hits.length) return { score: 70 - t.length * 0.1, hits: [...new Set(hits)].sort((a, b) => a - b) };
  }
  const hits = [];   // letters in order ("gcal" -> "Google Calendar"), preferring starts of words and runs
  let from = 0, score = 40, prev = -2;
  for (const ch of q.replace(/\s+/g, "")) {
    let i = t.indexOf(ch, from);
    for (let j = i; j >= 0; j = t.indexOf(ch, j + 1)) { if (boundary(t, j) || j === prev + 1) { i = j; break; } if (j - from > 12) break; }
    if (i < 0) return null;
    score += (i === prev + 1 ? 8 : boundary(t, i) ? 6 : 1) - Math.min(3, (i - from) * 0.1);
    hits.push(i); prev = i; from = i + 1;
  }
  return { score: score - t.length * 0.05, hits };
}

/** Recently and often used things rank higher: use = {id: [count, lastUsedMs]}. */
export function boost(use, id, now = Date.now()) {
  const u = use?.[id];
  if (!u) return 0;
  const days = Math.max(0, (now - u[1]) / 864e5);
  return Math.log2(1 + u[0]) * 6 / (1 + days / 7);
}

/** Rank `items` for a query. With no query: recently used first (as their own group), then the rest by group. */
export function rankItems(items, q, use = {}, now = Date.now(), max = 60) {
  const text = (q || "").trim();
  if (!text) {
    const recent = items.map((it) => [it, boost(use, it.id, now)]).filter(([, b]) => b > 0).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([it]) => ({ ...it, group: "Recent", hits: [] }));
    const seen = new Set(recent.map((r) => r.id));
    return [...recent, ...items.filter((it) => !seen.has(it.id)).map((it) => ({ ...it, hits: [] }))].slice(0, max);
  }
  return items.map((it) => {
    const m = fuzzy(text, it.label), g = m ? null : fuzzy(text, it.group || "");
    const hit = m || (g && { score: g.score * 0.4, hits: [] });
    return hit && { ...it, hits: m ? m.hits : [], score: hit.score + boost(use, it.id, now) };
  }).filter(Boolean).sort((a, b) => b.score - a.score).slice(0, max);
}
