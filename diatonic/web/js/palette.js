// The command palette (⌘K): everything in the command registry plus apps, saved actions and conversations,
// searched forgivingly and ordered by how well it matches and how often you use it.
import { $, h } from "./dom.js";
import { icon } from "./icons.js";
import { list, provided, labelOf } from "./commands.js";
import { hintFor } from "./keymap.js";
import { rankItems } from "./fuzzy.js";

const USE_KEY = "diatonic.palette.use";
let sel = 0, shown = [];
const dlg = () => $("#palette");

const usage = () => { try { return JSON.parse(localStorage.getItem(USE_KEY)) || {}; } catch { return {}; } };
function remember(id) {
  const u = usage(), cur = u[id] || [0, 0];
  u[id] = [cur[0] + 1, Date.now()];
  const keep = Object.entries(u).sort((a, b) => b[1][1] - a[1][1]).slice(0, 200);   // forget the oldest
  try { localStorage.setItem(USE_KEY, JSON.stringify(Object.fromEntries(keep))); } catch {}
}

/** Everything that can be picked right now. */
export function paletteItems() {
  const cmds = list().map((c) => ({ id: c.id, group: c.group, label: labelOf(c), icon: c.icon, hint: hintFor(c.id), run: () => c.run({}) }));
  return [...cmds, ...provided()];
}

const mark = (label, hits) => {
  if (!hits.length) return label;
  const on = new Set(hits);
  return [...label].map((ch, i) => (on.has(i) ? h("b", {}, ch) : ch));
};

export function openPalette() {
  const items = paletteItems(), d = dlg();
  const input = h("input", { placeholder: "Type a command, app or conversation…", "aria-label": "Command" });
  const list_ = h("div", { class: "pal-list", role: "listbox" });
  const run = (it) => { d.close(); remember(it.id); try { Promise.resolve(it.run()).catch(() => {}); } catch {} };
  const paint = () => {
    const q = input.value;
    shown = rankItems(items, q, usage());
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let last = null; const rows = [];
    shown.forEach((it, idx) => {
      if (!q.trim() && it.group !== last) { rows.push(h("div", { class: "pal-group" }, it.group)); last = it.group; }
      rows.push(h("div", { class: `pal-item ${idx === sel ? "sel" : ""}`, role: "option", "aria-selected": String(idx === sel), onclick: () => run(it), onmousemove: () => { if (sel !== idx) { sel = idx; paint(); } } },
        h("span", { html: icon(it.icon || "command", 16) }), h("span", { class: "pal-label" }, mark(it.label, it.hits)),
        q.trim() && it.group && h("span", { class: "pal-tag" }, it.group), it.hint && h("span", { class: "k" }, it.hint)));
    });
    list_.replaceChildren(...(rows.length ? rows : [h("div", { class: "p-empty" }, "No matches")]));
    list_.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
  };
  input.addEventListener("input", () => { sel = 0; paint(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); paint(); }
    else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === "Enter" && shown[sel]) { e.preventDefault(); run(shown[sel]); }
  });
  d.replaceChildren(input, list_);
  sel = 0; paint();
  if (!d.open) d.showModal();
  input.focus();
}
export function initPalette() { dlg().addEventListener("click", (e) => { if (e.target === dlg()) dlg().close(); }); }
