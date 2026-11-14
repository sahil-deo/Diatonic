import { $, h } from "./dom.js";
import { icon } from "./icons.js";
import { state } from "./store.js";

let items = [], sel = 0, shown = [];
const dlg = () => $("#palette");

export function openPalette(commands) {
  items = commands();
  const d = dlg();
  const input = h("input", { placeholder: "Type a command, app or conversation…", "aria-label": "Command" });
  const list = h("div", { class: "pal-list", role: "listbox" });
  const run = (it) => { d.close(); it.run(); };
  const paint = () => {
    const q = input.value.trim().toLowerCase();
    shown = items.filter((i) => !q || i.label.toLowerCase().includes(q) || (i.group || "").toLowerCase().includes(q));
    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let last = null; const rows = [];
    shown.forEach((it, idx) => {
      if (it.group !== last) { rows.push(h("div", { class: "pal-group" }, it.group)); last = it.group; }
      rows.push(h("div", { class: `pal-item ${idx === sel ? "sel" : ""}`, role: "option", onclick: () => run(it), onmousemove: () => { if (sel !== idx) { sel = idx; paint(); } } },
        h("span", { html: icon(it.icon || "command", 16) }), it.label, it.hint && h("span", { class: "k" }, it.hint)));
    });
    list.replaceChildren(...(rows.length ? rows : [h("div", { class: "p-empty" }, "No matches")]));
    list.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
  };
  input.addEventListener("input", () => { sel = 0; paint(); });
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); sel = Math.min(shown.length - 1, sel + 1); paint(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); sel = Math.max(0, sel - 1); paint(); }
    else if (e.key === "Enter" && shown[sel]) { e.preventDefault(); run(shown[sel]); }
  });
  d.replaceChildren(input, list);
  sel = 0; paint();
  if (!d.open) d.showModal();
  input.focus();
}
export function initPalette() { dlg().addEventListener("click", (e) => { if (e.target === dlg()) dlg().close(); }); }
