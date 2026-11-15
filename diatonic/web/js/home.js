// The home screen: live tiles from your apps (to-dos, mail, GitHub, calendar, your own apps...) in a layout you choose.
// Each app that offers a snapshot can fill a slot; clicking an item opens it in its app.
import { h, toast } from "./dom.js";
import { icon } from "./icons.js";
import { api } from "./api.js";
import { state, emit, on } from "./store.js";

export const LAYOUTS = [["grid4", "4 small", 4], ["two_rows", "2 stacked", 2], ["two_cols", "2 side by side", 2], ["one", "1 big", 1], ["suggestions", "Suggestions", 0]];
const ITEMS = { grid4: 3, two_rows: 4, two_cols: 7, one: 10 };
const PREFERRED = ["todos", "gcal", "gmail", "scheduler", "github", "telemetry", "notes"];
let catalog = null, editing = false, timer = 0;
const tiles = new Map();     // app id -> repaint function, for live refresh

on("apps", () => { catalog = null; });
on("app_changed", (ev) => tiles.get(ev.app)?.(true));

const cfg = () => state.config?.appearance?.home || { layout: "grid4", slots: [] };
const count = (layout) => LAYOUTS.find((l) => l[0] === layout)?.[2] ?? 4;

/** Slots as configured, with empty ones filled by apps you're likely to want (and never the same app twice). */
function slots(layout, chosen) {
  const ids = catalog.map((c) => c.id), out = [];
  for (let i = 0; i < count(layout); i++) {
    const want = chosen[i];
    if (want && ids.includes(want) && !out.includes(want)) out.push(want);
    else out.push([...PREFERRED, ...ids].find((x) => ids.includes(x) && !out.includes(x) && !chosen.includes(x)) || null);
  }
  return out;
}

async function save(home) {
  try { state.config = await api.putConfig({ appearance: { home } }); emit("config"); }
  catch (e) { toast(e.message, "error"); }
}

/** Fills `box` with the home screen. `suggestions` builds the old suggestion chips, still offered as a layout. */
export async function renderHome(box, suggestions) {
  clearInterval(timer); tiles.clear();
  if (!catalog) { try { catalog = await api.get("/api/home"); } catch { catalog = []; } }
  const { layout, slots: chosen } = cfg();
  const ids = layout === "suggestions" ? [] : slots(layout, chosen);
  const bar = h("div", { class: "home-bar" },
    editing && h("div", { class: "seg", role: "group", "aria-label": "Layout" }, ...LAYOUTS.map(([v, label]) =>
      h("button", { type: "button", class: v === layout ? "on" : "", title: label, onclick: () => save({ layout: v, slots: ids.length ? ids : chosen }) }, layoutIcon(v), h("span", {}, label)))),
    h("button", { type: "button", class: "btn sm home-edit", onclick: () => { editing = !editing; renderHome(box, suggestions); } }, editing ? "Done" : "Customize"));
  const body = layout === "suggestions" ? suggestions()
    : h("div", { class: `home home-${layout}` }, ...ids.map((id, i) => tile(id, i, layout, ids)));
  box.replaceChildren(...[body, bar].filter(Boolean));
  timer = setInterval(() => { if (!box.isConnected) return clearInterval(timer); tiles.forEach((paint) => paint(false)); }, 120000);
}

function tile(id, i, layout, ids) {
  const app = catalog.find((c) => c.id === id);
  const content = h("div", { class: "tile-body" });
  const pick = h("select", { "aria-label": "App in this tile", onchange: (e) => { const next = [...ids]; next[i] = e.target.value; save({ layout, slots: next }); } },
    ...catalog.map((c) => h("option", { value: c.id, selected: c.id === id }, c.name)));
  const el = h("section", { class: "tile" },
    h("header", {}, h("span", { class: "tile-ic", html: icon(app?.icon || "box", 15) }),
      editing ? pick : h("button", { type: "button", class: "tile-name", title: `Open ${app?.name || ""}`, onclick: () => app && emit("ui", { action: "open", app: id }) }, app?.name || "Empty"),
      h("div", { class: "grow" }), !editing && app && h("button", { type: "button", class: "icon-btn sm", title: "Refresh", "aria-label": "Refresh", html: icon("refresh", 13), onclick: () => paint(true) })),
    content);
  if (!app) { content.replaceChildren(h("div", { class: "p-s" }, editing ? "Pick an app above." : "No app here. Use Customize.")); return el; }
  const paint = async (fresh) => {
    if (!content.childElementCount) content.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
    let d;
    try { d = await api.get(`/api/home/${encodeURIComponent(id)}?n=${ITEMS[layout] || 4}${fresh ? "&fresh=1" : ""}`); }
    catch (e) { d = { error: e.message }; }
    content.replaceChildren(...body(id, d).filter(Boolean));
  };
  tiles.set(id, paint); paint(false);
  return el;
}

function body(id, d) {
  if (d.error) return [h("div", { class: "tile-err" }, d.error)];
  const open = (it) => it.url ? window.open(it.url, "_blank", "noopener") : emit("ui", { action: "open", app: id, params: it.open || null });
  return [
    d.stats?.length ? h("div", { class: "tile-stats" }, ...d.stats.map((s) => h("div", {}, h("b", {}, String(s.value)), h("span", {}, s.label)))) : null,
    ...(d.items || []).map((it) => h("button", { type: "button", class: `tile-row ${it.tone || ""} ${it.done ? "done" : ""}`, onclick: () => open(it) },
      h("i", { class: "tile-dot" }), h("span", { class: "tile-t" }, it.text), it.badge ? h("span", { class: "tile-badge" }, it.badge) : null,
      it.sub ? h("span", { class: "tile-sub" }, it.sub) : null)),
    !(d.items || []).length ? h("div", { class: "p-s tile-empty" }, d.empty || "Nothing here.") : null,
    d.more ? h("div", { class: "p-s tile-more" }, d.more) : null,
  ];
}

function layoutIcon(v) {
  const cells = { grid4: [[0, 0, 1, 1], [1, 0, 1, 1], [0, 1, 1, 1], [1, 1, 1, 1]], two_rows: [[0, 0, 2, 1], [0, 1, 2, 1]], two_cols: [[0, 0, 1, 2], [1, 0, 1, 2]],
    one: [[0, 0, 2, 2]], suggestions: [[0, 0, 2, 0.6], [0, 0.9, 0.9, 0.5], [1.1, 0.9, 0.9, 0.5]] }[v];
  return h("span", { class: "lay-ic", html: `<svg viewBox="0 0 22 16" width="22" height="16">${cells.map(([x, y, w, hh]) => `<rect x="${x * 11 + 1}" y="${y * 8 + 1}" width="${w * 11 - 2}" height="${hh * 8 - 2}" rx="1.5"/>`).join("")}</svg>` });
}
