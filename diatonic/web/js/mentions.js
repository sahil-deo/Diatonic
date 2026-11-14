// "@" file picker in the message box: type @ + part of a file name, Tab/Enter to insert.
import { $, h } from "./dom.js";
import { icon } from "./icons.js";
import { api } from "./api.js";
import { state } from "./store.js";

let items = [], sel = 0, token = null, seq = 0, timer;
const menu = () => $("#mention");
const input = () => $("#input");

const current = () => {
  const el = input(), before = el.value.slice(0, el.selectionStart);
  const m = before.match(/(^|\s)@("[^"]*|[^\s@"]*)$/);
  return m ? { start: before.length - m[2].length - 1, query: m[2].replace(/^"/, "") } : null;
};

function close() { menu().classList.add("hidden"); items = []; token = null; }

function paint() {
  const el = menu();
  if (!items.length) return close();
  el.replaceChildren(...items.map((it, i) => h("div", { class: `sl-item ${i === sel ? "sel" : ""}`, role: "option",
    onmousedown: (e) => { e.preventDefault(); pick(it); }, onmousemove: () => { if (sel !== i) { sel = i; paint(); } } },
    h("span", { html: icon(it.dir ? "folder" : "file", 15) }), h("span", { class: "sl-name", style: "font-family:var(--font);color:var(--text)" }, it.rel), h("span", { class: "sl-desc" }, ""))));
  el.classList.remove("hidden");
  el.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}

function pick(it) {
  const el = input(), t = token || current(); if (!t) return;
  const text = /\s/.test(it.rel) ? `@"${it.rel}"` : `@${it.rel}`;
  const after = el.value.slice(el.selectionStart);
  el.value = el.value.slice(0, t.start) + text + (it.dir ? "/" : " ") + after.replace(/^\S*/, "");
  const pos = t.start + text.length + 1; el.setSelectionRange(pos, pos); el.dispatchEvent(new Event("input", { bubbles: true })); close(); el.focus();
}

async function update() {
  if (!$("#slash").classList.contains("hidden")) return close();   // "/" commands own the box
  const t = current();
  if (!t || !state.apps.some((a) => a.id === "files" && a.enabled)) return close();
  token = t;
  clearTimeout(timer);
  timer = setTimeout(async () => {
    const my = ++seq;
    const r = await api.get(`/api/apps/files/find?q=${encodeURIComponent(t.query)}&limit=8`).catch(() => []);
    if (my !== seq || !current()) return;
    items = r; sel = 0; paint();
  }, 120);
}

export function initMentions() {
  input().addEventListener("input", update);
  input().addEventListener("click", update);
  input().addEventListener("blur", () => setTimeout(close, 120));
  input().addEventListener("keydown", (e) => {
    if (menu().classList.contains("hidden") || e.isComposing) return;
    const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
    if (e.key === "ArrowDown") { stop(); sel = (sel + 1) % items.length; paint(); }
    else if (e.key === "ArrowUp") { stop(); sel = (sel - 1 + items.length) % items.length; paint(); }
    else if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) { stop(); pick(items[sel]); }
    else if (e.key === "Escape") { stop(); close(); }
  }, true);
}
