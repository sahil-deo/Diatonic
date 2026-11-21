// The UI kit an app builds from (ctx.ui): each component is a pure function of its props that returns an element, styled only from the
// :root tokens. What each takes is declared in components.json, which the catalog, the gallery and the tests read.
import { h } from "../dom.js";
import { icon } from "../icons.js";

const many = (v) => (v == null || v === false ? [] : Array.isArray(v) ? v : [v]);
const btn = (p) => button(p);

export function button({ label, onclick, kind, size, disabled, title, icon: ic } = {}) {
  const cls = ["btn", kind === "primary" ? "primary" : kind === "danger" ? "danger" : "", size === "sm" ? "sm" : ""].filter(Boolean).join(" ");
  return h("button", { class: cls, type: "button", onclick, disabled, title }, ic && h("span", { html: icon(ic, 14) }), label);
}

export const badge = ({ text, tone = "muted" } = {}) => h("span", { class: `ui-badge ui-tone-${tone}` }, text);

const head = (title, subtitle, actions) => (title || actions?.length) && h("div", { class: "ui-head" },
  h("div", { class: "ui-head-text" }, title && h("div", { class: "ui-title" }, title), subtitle && h("div", { class: "ui-sub" }, subtitle)),
  many(actions).length ? h("div", { class: "ui-actions" }, many(actions).map(btn)) : null);

export const card = ({ title, subtitle, body, footer, actions } = {}) => h("div", { class: "ui-card" },
  head(title, subtitle, actions), many(body).length ? h("div", { class: "ui-body" }, many(body)) : null, many(footer).length ? h("div", { class: "ui-foot" }, many(footer)) : null);

export const section = ({ title, body, actions } = {}) => h("section", { class: "ui-section" }, head(title, null, actions), h("div", { class: "ui-body" }, many(body)));

export function row({ title, subtitle, right, mark, onclick, selected } = {}) {
  const el = h("div", { class: `ui-row${onclick ? " clickable" : ""}${selected ? " selected" : ""}`, tabindex: onclick ? 0 : null, role: onclick ? "button" : null,
    onclick: onclick && ((e) => { if (!e.target.closest("input, button")) onclick(e); }),
    onkeydown: onclick && ((e) => { if (e.key === "Enter" && e.target === el) onclick(e); }) },
  mark && h("input", { type: "checkbox", checked: !!mark.checked, "aria-label": "Mark " + title, onchange: (e) => mark.onchange?.(e.target.checked) }),
  h("div", { class: "ui-row-main" }, h("div", { class: "ui-row-title" }, title), subtitle && h("div", { class: "ui-sub" }, subtitle)),
  right != null && h("div", { class: "ui-row-right" }, right));
  return el;
}

export const empty = ({ title, text, action } = {}) => h("div", { class: "ui-empty" }, h("div", { class: "ui-empty-title" }, title), text && h("div", { class: "ui-sub" }, text), action && btn(action));

export const list = ({ items = [], empty: none = "Nothing here yet." } = {}) => (items.length ? h("div", { class: "ui-list" }, items.map(row)) : empty({ title: none }));

export function table({ columns = [], rows = [], empty: none = "Nothing here yet.", onRowClick } = {}) {
  if (!rows.length) return empty({ title: none });
  const cell = (c, r) => h("td", { class: c.align === "right" ? "r" : "" }, c.render ? c.render(r) : r[c.key] ?? "");
  return h("div", { class: "ui-table-wrap" }, h("table", { class: "ui-table" },
    h("thead", {}, h("tr", {}, columns.map((c) => h("th", { class: c.align === "right" ? "r" : "" }, c.label ?? c.key)))),
    h("tbody", {}, rows.map((r) => h("tr", { class: onRowClick ? "clickable" : "", onclick: onRowClick && (() => onRowClick(r)) }, columns.map((c) => cell(c, r)))))));
}

export function tabs({ tabs: items = [], active, onChange } = {}) {
  let cur = items.some((t) => t.id === active) ? active : items[0]?.id;
  const bar = h("div", { class: "ui-tabbar", role: "tablist" }), body = h("div", { class: "ui-tabbody" });
  const paint = () => {
    bar.replaceChildren(...items.map((t) => h("button", { type: "button", role: "tab", class: t.id === cur ? "on" : "", "aria-selected": t.id === cur,
      onclick: () => { cur = t.id; paint(); onChange?.(cur); } }, t.label)));
    body.replaceChildren(...many(items.find((t) => t.id === cur)?.content));
  };
  paint();
  return h("div", { class: "ui-tabs" }, bar, body);
}

export function field({ label, type = "text", name, value, options, placeholder, help, input } = {}) {
  let control = input;
  if (!control) {
    if (type === "textarea") control = h("textarea", { placeholder, rows: 3 }, value ?? "");
    else if (type === "select") control = h("select", {}, many(options).map((o) => { const [v, l] = Array.isArray(o) ? o : [o, o]; return h("option", { value: v, selected: v === value }, l); }));
    else if (type === "checkbox") control = h("input", { type: "checkbox", checked: !!value });
    else control = h("input", { type, value: value ?? "", placeholder });
  }
  if (name) control.name = name;
  const inline = control.type === "checkbox";
  const el = h("div", { class: `ui-field${inline ? " inline" : ""}` }, h("label", {}, inline ? [control, " ", label] : [label]), inline ? null : control, help && h("div", { class: "ui-sub" }, help));
  el.control = control;
  return el;
}

const valueOf = (c) => (c.type === "checkbox" ? c.checked : c.type === "number" ? (c.value === "" ? null : Number(c.value)) : c.value);

export function form({ fields = [], submit = "Save", onSubmit } = {}) {
  const made = fields.map(field), go = button({ label: submit, kind: "primary" });
  go.type = "submit";
  const el = h("form", { class: "ui-form", novalidate: true, onsubmit: async (e) => {
    e.preventDefault();
    const values = Object.fromEntries(made.map((f) => [f.control.name, valueOf(f.control)]));
    go.disabled = true;
    try { await onSubmit?.(values, el); } finally { go.disabled = false; }
  } }, made, go);
  return el;
}

export const kv = ({ items = [] } = {}) => h("dl", { class: "ui-kv" }, items.map((i) => [h("dt", {}, i.label), h("dd", {}, i.value ?? "")]));

export const stat = ({ label, value, hint, tone } = {}) => h("div", { class: "ui-stat" }, h("div", { class: "ui-sub" }, label), h("div", { class: `ui-stat-value${tone ? " ui-tone-" + tone : ""}` }, value), hint && h("div", { class: "ui-sub" }, hint));

export function modal({ title, body, actions, onClose } = {}) {
  const el = h("div", { class: "ui-modal", tabindex: -1, onkeydown: (e) => { if (e.key === "Escape") { e.stopPropagation(); el.close(); } }, onclick: (e) => { if (e.target === el) el.close(); } },
    h("div", { class: "ui-dialog", role: "dialog", "aria-label": title }, head(title), h("div", { class: "ui-body" }, many(body)),
      many(actions).length ? h("div", { class: "ui-foot" }, many(actions).map((a) => btn({ ...a, onclick: (e) => { a.onclick?.(e); if (!a.keep) el.close(); } }))) : null));
  el.close = () => { if (el.isConnected) { el.remove(); onClose?.(); } };
  queueMicrotask(() => el.isConnected && el.focus());
  return el;
}

const NS = "http://www.w3.org/2000/svg";
export function chart({ data = [], type = "bar", height = 140, unit = "" } = {}) {
  const W = 320, top = 14, bottom = 20, H = Math.max(60, Number(height) || 140), max = Math.max(1, ...data.map((d) => Number(d.value) || 0));
  const n = Math.max(1, data.length), step = W / n, y = (v) => top + (H - top - bottom) * (1 - (Number(v) || 0) / max);
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`); svg.setAttribute("class", "ui-chart"); svg.setAttribute("role", "img");
  const add = (tag, attrs, text) => { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text != null) e.textContent = text; svg.append(e); return e; };
  add("line", { x1: 0, x2: W, y1: H - bottom, y2: H - bottom, class: "ui-axis" });
  if (type === "line" && data.length) add("polyline", { class: "ui-line", points: data.map((d, i) => `${(i + 0.5) * step},${y(d.value)}`).join(" ") });
  data.forEach((d, i) => {
    const cx = (i + 0.5) * step;
    if (type === "line") add("circle", { cx, cy: y(d.value), r: 3, class: "ui-dot" });
    else add("rect", { x: i * step + step * 0.15, y: y(d.value), width: step * 0.7, height: Math.max(0, H - bottom - y(d.value)), rx: 3, class: "ui-bar" });
    add("text", { x: cx, y: type === "line" ? y(d.value) - 7 : y(d.value) - 4, class: "ui-val", "text-anchor": "middle" }, `${d.value}${unit}`);
    add("text", { x: cx, y: H - 6, class: "ui-lab", "text-anchor": "middle" }, d.label);
  });
  return h("div", { class: "ui-chart-wrap" }, svg);
}

export const components = { button, badge, card, section, row, list, table, tabs, field, form, kv, stat, empty, modal, chart };

/** Run an action the way every app should: the button is disabled while it runs, a returned string is shown as the result, and a failure is shown and goes to the app's log. */
export function runner(ctx) {
  return async (label, fn, btnEl) => {
    if (btnEl) btnEl.disabled = true;
    try {
      const out = await fn();
      if (typeof out === "string" && out) ctx.toast(out);
      return out;
    } catch (e) {
      const msg = `${label}: ${e?.message || e}`;
      ctx.toast(msg, "error");
      ctx.api.post(`/api/custom-apps/${encodeURIComponent(ctx.app)}/logs`, { error: msg, source: "action" }).catch(() => {});
    } finally { if (btnEl) btnEl.disabled = false; }
  };
}

/**
 * A whole list view: search, filters, rows (or a table when `columns` is given), check marks, a detail pane, and the same actions on the row and
 * in the detail. Arrow keys and Enter step through the rows (ctx.list). Returns an element with set(rows) and selected().
 */
export function listApp(ctx, spec = {}) {
  const run = runner(ctx);
  let rows = spec.rows || [], query = "", filter = spec.filters?.[0]?.id ?? null, sel = null;
  const search = spec.search ? h("input", { type: "search", placeholder: "Search", "aria-label": "Search", oninput: (e) => { query = e.target.value.toLowerCase(); paint(); } }) : null;
  const seg = spec.filters?.length ? h("div", { class: "seg", role: "group" }) : null;
  const listEl = h("div", { class: "ui-la-list" }), detailEl = h("div", { class: "ui-la-detail" });
  const visible = () => rows.filter((r) => (!filter || spec.filters.find((f) => f.id === filter)?.test(r) !== false)
    && (!query || Object.values(r).some((v) => typeof v !== "object" && String(v).toLowerCase().includes(query))));
  const actionsFor = (r, small) => many(spec.actions).filter((a) => !a.when || a.when(r)).map((a) => button({ label: a.label, kind: a.kind, size: small ? "sm" : undefined, onclick: (e) => { e.stopPropagation(); run(a.label, () => a.run(r), e.currentTarget); } }));
  const pick = (r) => { sel = r.id; paint(); spec.onSelect?.(r); };
  const mark = (r) => spec.marks && { checked: !!r[spec.marks.key], onchange: (v) => run(spec.marks.label || "Mark", () => spec.marks.onChange(r, v)) };
  function paint() {
    const vis = visible();
    if (seg) seg.replaceChildren(...spec.filters.map((f) => h("button", { type: "button", class: f.id === filter ? "on" : "", onclick: () => { filter = f.id; paint(); } }, f.label)));
    if (spec.columns) {
      const cols = [...(spec.marks ? [{ key: "_mark", label: "", render: (r) => h("input", { type: "checkbox", checked: !!r[spec.marks.key], "aria-label": "Mark", onclick: (e) => e.stopPropagation(), onchange: (e) => run(spec.marks.label || "Mark", () => spec.marks.onChange(r, e.target.checked)) }) }] : []),
        ...spec.columns, ...(spec.actions?.length ? [{ key: "_actions", label: "", render: (r) => h("div", { class: "ui-actions" }, actionsFor(r, true)) }] : [])];
      listEl.replaceChildren(table({ columns: cols, rows: vis, empty: spec.empty, onRowClick: pick }));
      listEl.querySelectorAll("tbody tr").forEach((tr, i) => tr.classList.toggle("selected", vis[i]?.id === sel));
    } else {
      listEl.replaceChildren(vis.length ? h("div", { class: "ui-list" }, vis.map((r) => row({ title: r.title ?? r.name, subtitle: r.subtitle, mark: mark(r), selected: r.id === sel, onclick: () => pick(r),
        right: [r.badge ? badge(typeof r.badge === "string" ? { text: r.badge } : r.badge) : r.right, spec.actions?.length ? h("span", { class: "ui-row-actions" }, actionsFor(r, true)) : null] }))) : empty({ title: spec.empty || "Nothing here yet." }));
    }
    const cur = rows.find((r) => r.id === sel);
    detailEl.replaceChildren(...(spec.detail && cur ? [head(cur.title ?? cur.name, null, null), h("div", { class: "ui-actions ui-detail-actions" }, actionsFor(cur, false)), h("div", { class: "ui-body" }, many(spec.detail(cur)))] : []));
    detailEl.hidden = !(spec.detail && cur);
  }
  const el = h("div", { class: `ui-listapp${spec.detail ? " has-detail" : ""}` }, (search || seg) && h("div", { class: "ui-la-bar" }, search, seg), h("div", { class: "ui-la-body" }, listEl, spec.detail && detailEl));
  ctx.list?.({ items: () => [...listEl.querySelectorAll(".ui-row, tbody tr")], open: (e) => e.click() });
  paint();
  el.set = (next) => { rows = next || []; if (!rows.some((r) => r.id === sel)) sel = null; paint(); };
  el.selected = () => rows.find((r) => r.id === sel) || null;
  return el;
}

/** What `ctx.ui` is: the components, plus the pieces that need the panel (listApp, run). */
export const createUI = (ctx) => ({ ...components, listApp: (spec) => listApp(ctx, spec), run: runner(ctx) });
export const names = () => [...Object.keys(components), "listApp", "run"];
