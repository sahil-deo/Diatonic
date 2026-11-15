// Moving around by keyboard: which side of the screen has focus (the chat or the app panel), scrolling it, and
// stepping through a list the panel offers. The commands registered here are what the keymap's j/k, arrows and
// Page keys run. Phase 2 will replace the two fixed "regions" with any number of panels; the commands stay the same.
import { $, toast } from "./dom.js";
import { state } from "./store.js";
import { register } from "./commands.js";

let region = "chat", lastPointer = null;
const lists = new Map();   // region -> {items: () => Element[], open?: (el) => void, at: Element|null}

const regionEl = (r) => (r === "panel" ? $("#panel") : $(".main"));
export const currentRegion = () => (region === "panel" && state.panelApp ? "panel" : "chat");

function paintRegion() {
  const cur = currentRegion();
  for (const r of ["chat", "panel"]) regionEl(r)?.classList.toggle("region-focus", r === cur);
}
export function setRegion(r) { region = r; paintRegion(); }
/** Show the focus outline: it only appears once the keyboard is being used, and goes away on the next click. */
export function markKeyboard() { document.body.classList.add("keyboard"); paintRegion(); }

const regionOf = (n) => (n?.closest?.("#panel") ? "panel" : n?.closest?.(".main, #dock") ? "chat" : null);
export function initNav() {
  addEventListener("focusin", (e) => { const r = regionOf(e.target); if (r) setRegion(r); });
  addEventListener("pointerdown", (e) => {
    document.body.classList.remove("keyboard");
    lastPointer = e.target;
    const r = regionOf(e.target); if (r) setRegion(r);
  }, true);
  paintRegion();
}

/* ---------- scrolling ---------- */
function scrollable(el) {
  if (!el || el.nodeType !== 1) return false;
  return /(auto|scroll)/.test(getComputedStyle(el).overflowY) && el.scrollHeight > el.clientHeight + 1;
}
/** The thing to scroll: the scroller around what you last clicked or focused, else the region's main scroller. */
export function scrollTarget() {
  const cur = currentRegion(), root = regionEl(cur);
  const active = document.activeElement;
  const start = active && active !== document.body && root.contains(active) ? active : lastPointer && root.contains(lastPointer) ? lastPointer : null;
  for (let n = start; n && n !== root.parentElement; n = n.parentElement) if (scrollable(n)) return n;
  const def = cur === "chat" ? $("#messages") : $("#panel-body");
  if (scrollable(def)) return def;
  return [...root.querySelectorAll("*")].filter(scrollable).sort((a, b) => b.clientHeight * b.clientWidth - a.clientHeight * a.clientWidth)[0] || def;
}
function scrollBy(f) {
  markKeyboard();
  const el = scrollTarget();
  if (!el) return false;
  el.scrollBy({ top: f(el) });
}
const LINE = 56;

/* ---------- lists a panel offers ---------- */
/** Let ↑/↓ (or j/k), Enter and Home/End step through a panel's rows: spec = {items: () => [elements], open?: (element) => void}. Returns an unregister function. */
export function registerList(r, spec) {
  const entry = { at: null, ...spec };
  lists.set(r, entry);
  return () => { if (lists.get(r) === entry) { entry.at?.classList.remove("kb-sel"); lists.delete(r); } };
}
export const hasList = () => lists.has(currentRegion());
const listHere = () => lists.get(currentRegion());
function select(l, el) {
  l.at?.classList.remove("kb-sel");
  l.at = el; el.classList.add("kb-sel");
  el.scrollIntoView({ block: "nearest" });
}
function step(delta) {
  const l = listHere(); if (!l) return null;
  const items = l.items().filter((el) => el.isConnected);
  if (!items.length) return false;
  markKeyboard();
  const i = items.indexOf(l.at);
  select(l, items[i < 0 ? (delta > 0 ? 0 : items.length - 1) : Math.max(0, Math.min(items.length - 1, i + delta))]);
}
function edge(last) {
  const l = listHere(); if (!l) return null;
  const items = l.items().filter((el) => el.isConnected);
  if (!items.length) return false;
  markKeyboard(); select(l, items[last ? items.length - 1 : 0]);
}

/* ---------- commands ---------- */
export function registerNav() {
  const g = "Navigate";
  const idle = () => true;
  const move = (delta, lines) => () => step(delta) ?? scrollBy(() => delta * lines * LINE);
  const page = (sign, frac) => () => scrollBy((el) => sign * Math.max(LINE, el.clientHeight * frac));
  [
    { id: "nav.down", title: "Down", run: move(1, 1) },
    { id: "nav.up", title: "Up", run: move(-1, 1) },
    { id: "nav.pageDown", title: "Page down", run: page(1, 0.9) },
    { id: "nav.pageUp", title: "Page up", run: page(-1, 0.9) },
    { id: "nav.halfDown", title: "Half page down", run: page(1, 0.5) },
    { id: "nav.halfUp", title: "Half page up", run: page(-1, 0.5) },
    { id: "nav.top", title: "Top", run: () => edge(false) ?? scrollBy((el) => -el.scrollTop) },
    { id: "nav.bottom", title: "Bottom", run: () => edge(true) ?? scrollBy((el) => el.scrollHeight) },
    { id: "nav.open", title: "Open the selected item", run: () => {
      const l = listHere();
      if (!l?.at?.isConnected) return false;       // nothing selected: leave Enter alone
      l.open ? l.open(l.at) : l.at.click();
    } },
  ].forEach((c) => register({ group: g, when: idle, hidden: true, ...c }));

  register({ id: "focus.left", title: "Focus the chat", group: g, run: () => { if (currentRegion() === "chat") return false; focusRegion("chat"); } });
  register({ id: "focus.right", title: "Focus the app panel", group: g, run: () => {
    if (!state.panelApp) { toast("No app panel is open."); return false; }
    if (currentRegion() === "panel") return false;
    focusRegion("panel");
  } });
  register({ id: "focus.release", title: "Leave the text box", group: g, hidden: true, run: () => {
    const a = document.activeElement;
    if (!a || a === document.body) return false;
    a.blur(); markKeyboard();
  } });
}
function focusRegion(r) {
  document.activeElement?.blur?.();
  setRegion(r); markKeyboard();
}
