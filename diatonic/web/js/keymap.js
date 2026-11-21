// Key bindings on the page: one keydown listener turns key presses into commands (commands.js) using the keymap
// (keys.js + keymap-defaults.js + the user's keymap.json). Also the "which key" popup for sequences, and the
// hints other parts of the UI show ("⌘K").
import { $, h, toast } from "./dom.js";
import { api } from "./api.js";
import { state, emit, on } from "./store.js";
import { get as getCommand, available, labelOf } from "./commands.js";
import { hasList } from "./nav.js";
import { buildKeymap, Matcher, eventToChord, safeInInput, bindingsOf, prettySequence, findConflicts, LEADER } from "./keys.js";
import { DEFAULTS, DEFAULT_LEADER } from "./keymap-defaults.js";
import { RESERVED } from "./form.js";

const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || "");
const PENDING_MS = 3000;
let user = { leader: "", vim: false, bindings: [] }, km = buildKeymap(DEFAULTS, user, { mac: MAC, defaultLeader: DEFAULT_LEADER });
let matcher = new Matcher(km), timer = null, popup = null, filePath = "";

export const mac = MAC;
export const keymap = () => km;
export const userKeymap = () => user;
export const keymapPath = () => filePath;
export const defaults = DEFAULTS;
export const conflicts = () => findConflicts(km.bindings);

/* ---------- what is true right now (the names `when` conditions use) ---------- */
const TEXT_INPUT = /^(text|search|email|url|tel|password|number|date|datetime-local|time|month|week)$/;
function isTyping(el) {
  if (!el || el === document.body) return false;
  if (el.isContentEditable || el.tagName === "TEXTAREA" || el.tagName === "SELECT") return true;
  return el.tagName === "INPUT" && TEXT_INPUT.test(el.type || "text");
}
const isInteractive = (el) => !!el && el !== document.body && !!el.closest?.("button, a, summary, [role=button], [role=link], [role=menuitem], [role=tab], input, textarea, select");

export function flags() {
  const a = document.activeElement, f = new Set();
  const term = !!a?.closest?.(".xterm");
  const dialog = !!document.querySelector("dialog[open]");
  if (a?.matches?.("#input, .dock textarea, .dock input")) f.add("composer");   // the chat box: Esc leaves it (other boxes keep their own Esc)
  if (term) f.add("terminal");
  else if (isTyping(a)) f.add("input");
  if (dialog) f.add("dialog");
  if (!term && !f.has("input") && !dialog) { f.add("idle"); if (!isInteractive(a)) f.add("plain"); }
  if (MAC) f.add("mac");
  if (km.vim) f.add("vim");
  f.add(state.panelApp ? "panel" : "no-panel");
  if (state.panelApp) f.add(`panel:${state.panelApp}`);
  f.add("app:chat");
  for (const x of state.apps) if (x.enabled !== false) f.add(`app:${x.id}`);
  f.add(document.body.querySelector(".region-focus")?.id === "panel" ? "focus:panel" : "focus:chat");
  if (hasList()) f.add("list");
  return f;
}

/* ---------- running a binding ---------- */
/** Run a command from a key. Returns false if it didn't apply, so the key keeps its normal meaning. */
function fire(binding) {
  const c = getCommand(binding.command);
  if (!c) { toast(`"${binding.command}" isn't a command. Check keymap.json.`, "error"); return true; }
  if (!available(c)) return false;
  try {
    const r = c.run(binding.args || {});
    if (r === false) return false;
    if (r?.then) r.catch((e) => toast(e.message, "error"));
  } catch (e) { toast(e.message, "error"); }
  return true;
}

function cancel() {
  matcher.reset(); clearTimeout(timer); timer = null;
  popup?.remove(); popup = null;
}
function showPending(options) {
  const typed = matcher.typed.map((c) => (c === LEADER ? km.leader : c)).join(" ");
  const rows = new Map();
  for (const o of options) {
    const row = rows.get(o.key) || { key: o.key, more: 0, title: "" };
    if (o.rest.length) row.more++; else row.title = titleOf(o);
    rows.set(o.key, row);
  }
  const items = [...rows.values()].sort((a, b) => a.key.localeCompare(b.key));
  popup?.remove();
  popup = h("div", { class: "whichkey", role: "status" },
    h("div", { class: "wk-head" }, prettySequence(matcher.typed.join(" "), km.leader) || typed, h("span", { class: "wk-esc" }, "Esc cancels")),
    h("div", { class: "wk-grid" }, items.map((r) => h("div", { class: "wk-row" }, h("kbd", {}, prettySequence(r.key, km.leader)), h("span", {}, r.more ? `${r.title || ""} ${r.title ? "·" : ""} ${r.more} more…`.trim() : r.title)))));
  document.body.append(popup);
  clearTimeout(timer); timer = setTimeout(cancel, PENDING_MS);
}
function titleOf(o) {
  const c = getCommand(o.command);
  if (!c) return o.command;
  return c.titleFor ? c.titleFor(o.args || {}) : labelOf(c);
}

function onKeydown(e) {
  if (e.defaultPrevented || e.isComposing) return;
  let chord = eventToChord(e);
  if (!chord) return;
  if (chord === km.leader) chord = LEADER;
  const f = flags();
  if (!matcher.pending) {
    if (f.has("terminal") && chord !== LEADER) return;               // the terminal gets every key except the leader
    if (f.has("input") && !safeInInput(chord)) return;               // typing: only keys that can't be typing
  } else if (chord === "Escape") { e.preventDefault(); return cancel(); }
  const r = matcher.feed(chord, f);
  if (r.type === "none") { if (r.abandoned) { e.preventDefault(); cancel(); } return; }
  if (r.type === "pending") { e.preventDefault(); return showPending(r.options); }
  cancel();
  if (fire(r.binding)) e.preventDefault();
}

/* ---------- hints ---------- */
/** "⌘K" for a command (its first key), or "". args picks one binding of a command that several keys share (app.open). */
export function hintFor(command, args = null) {
  const keys = bindingsOf(km, command, args);
  return keys.length ? prettySequence(keys[0], km.leader) : "";
}
export const hintsFor = (command, args = null) => bindingsOf(km, command, args).map((k) => prettySequence(k, km.leader));
export const leaderLabel = () => prettySequence(LEADER, km.leader);

function paintHints() {
  document.querySelectorAll("[data-cmd]").forEach((el) => {
    const hint = hintFor(el.dataset.cmd);
    el.title = hint ? `${el.dataset.title} (${hint})` : el.dataset.title;
  });
  const st = $("#st-hint");
  if (st) {
    const parts = [[hintFor("palette.open"), "commands"], [hintFor("settings.open"), "settings"], [leaderLabel(), "leader"]];
    st.textContent = parts.filter(([k]) => k).map(([k, what]) => `${k} ${what}`).join(" · ");
  }
  // Keys already taken, so a custom push-to-talk or action shortcut can't shadow them (written the way voice.js does: Meta+Shift+O).
  for (const k of Object.keys(RESERVED)) delete RESERVED[k];
  for (const b of km.bindings) {
    if (b.seq.length !== 1 || b.seq[0] === LEADER || !/(Ctrl|Alt|Meta)\+/.test(b.key)) continue;
    const c = getCommand(b.command);
    RESERVED[asHotkey(b.key)] = c ? labelOf(c).toLowerCase() : b.command;
  }
}
const CODE = { ",": "Comma", ".": "Period", ";": "Semicolon", "/": "Slash", "'": "Quote", "[": "BracketLeft", "]": "BracketRight", "\\": "Backslash", "-": "Minus", "=": "Equal", "`": "Backquote" };
function asHotkey(chord) {
  const parts = chord.split("+"), key = parts.pop() || "+", mods = new Set(parts);
  const order = [["Ctrl", "Control"], ["Alt", "Alt"], ["Shift", "Shift"], ["Meta", "Meta"]];
  return [...order.filter(([m]) => mods.has(m)).map(([, n]) => n), CODE[key] || (key.length === 1 ? key.toUpperCase() : key)].join("+");
}

/* ---------- loading and saving the user's keymap ---------- */
function rebuild() {
  km = buildKeymap(DEFAULTS, user, { mac: MAC, defaultLeader: DEFAULT_LEADER });
  matcher = new Matcher(km); cancel();
  paintHints(); emit("keymap");
}
export async function loadKeymap() {
  try {
    const d = await api.get("/api/keymap");
    filePath = d.path;
    if (JSON.stringify(d.keymap) !== JSON.stringify(user)) { user = d.keymap; rebuild(); }
  } catch { /* the built-in keys keep working */ }
}
/** Save the user's keymap (the whole file) and apply it. */
export async function saveKeymap(next) {
  const d = await api.put("/api/keymap", next);
  filePath = d.path; user = d.keymap; rebuild();
}

export function initKeymap() {
  addEventListener("keydown", onKeydown);
  addEventListener("blur", cancel);
  addEventListener("focus", loadKeymap);
  on("keymap_changed", loadKeymap);
  paintHints();
  return loadKeymap().then(() => { if (km.errors.length) toast(`keymap.json: ${km.errors[0]}${km.errors.length > 1 ? ` (+${km.errors.length - 1} more)` : ""}`, "error"); });
}
