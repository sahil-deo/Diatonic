// Key handling with no DOM: chords, sequences, `when` conditions and the matcher. keymap.js wires it to the page.
//
// A chord is one key press in a canonical text form: modifiers in the order Ctrl, Alt, Meta, Shift, then the key
// ("Meta+Shift+o", "Ctrl+ArrowDown", "?"). A plain character already says whether Shift was held ("G", "?"), so Shift
// only appears with other modifiers. A sequence is chords separated by spaces ("g g", "<leader> p"). "Mod" in a
// written binding means ⌘ on a Mac and Ctrl elsewhere.

const ORDER = ["Ctrl", "Alt", "Meta", "Shift"];      // how a chord is written in the file
const APPLE = ["Ctrl", "Alt", "Shift", "Meta"];      // how macOS shows it: ⌃⌥⇧⌘
const ALIASES = { cmd: "Meta", command: "Meta", meta: "Meta", super: "Meta", ctrl: "Ctrl", control: "Ctrl", alt: "Alt", option: "Alt", opt: "Alt", shift: "Shift" };
const NAMED = { " ": "Space", space: "Space", esc: "Escape", escape: "Escape", enter: "Enter", return: "Enter", tab: "Tab", backspace: "Backspace", delete: "Delete",
  up: "ArrowUp", down: "ArrowDown", left: "ArrowLeft", right: "ArrowRight", arrowup: "ArrowUp", arrowdown: "ArrowDown", arrowleft: "ArrowLeft", arrowright: "ArrowRight", pageup: "PageUp", pagedown: "PageDown", home: "Home", end: "End" };
export const LEADER = "<leader>";
const SYMBOL = { Ctrl: "⌃", Alt: "⌥", Shift: "⇧", Meta: "⌘" };
const KEY_LABEL = { ArrowUp: "↑", ArrowDown: "↓", ArrowLeft: "←", ArrowRight: "→", Escape: "Esc", Backspace: "⌫", Delete: "⌦", Enter: "↵", Space: "Space", PageUp: "PgUp", PageDown: "PgDn" };

/** Canonical form of one written chord ("cmd+shift+O", "Mod+k", "esc"), or null if it isn't one. */
export function normalizeChord(text, mac = true) {
  const s = String(text ?? "").trim();
  if (!s) return null;
  if (s.toLowerCase() === LEADER) return LEADER;
  const mods = new Set();
  let key = s, m;
  while ((m = /^([A-Za-z]+)\+(.+)$/.exec(key))) {   // peel "Meta+" off the front; "Meta++" leaves the plus key
    const low = m[1].toLowerCase();
    if (low === "mod") mods.add(mac ? "Meta" : "Ctrl");
    else if (ALIASES[low]) mods.add(ALIASES[low]);
    else return null;
    key = m[2];
  }
  const low = key.toLowerCase();
  if (NAMED[low]) key = NAMED[low];
  else if (/^f([1-9]|1[0-2])$/.test(low)) key = low.toUpperCase();
  else if ([...key].length !== 1) return null;
  else if (key !== key.toLowerCase()) {                       // an upper-case letter
    if (mods.size) { mods.add("Shift"); key = key.toLowerCase(); }   // Meta+K means Meta+Shift+k; a bare "K" stays "K"
  } else if (mods.size === 1 && mods.has("Shift") && key !== key.toUpperCase()) { mods.delete("Shift"); key = key.toUpperCase(); }   // Shift+g is "G"
  return [...ORDER.filter((x) => mods.has(x)), key].join("+");
}

/** Canonical form of a written sequence, or null if any chord is bad. */
export function normalizeSequence(text, mac = true) {
  const chords = String(text ?? "").trim().split(/\s+/).map((c) => normalizeChord(c, mac));
  return chords.length && chords.every(Boolean) ? chords.join(" ") : null;
}

/** The chord for a keydown event (anything with key, code and the four modifier flags). Null for a bare modifier press. */
export function eventToChord(e) {
  let key = e.key;
  if (!key || ["Control", "Alt", "Shift", "Meta", "OS", "Dead", "Unidentified", "Process"].includes(key)) return null;
  if (key === " ") key = "Space";
  if (e.altKey && key.length === 1 && e.code) {   // on a Mac ⌥H types "˙": use the physical key so ⌥H stays ⌥H
    const m = /^Key([A-Z])$/.exec(e.code) || /^Digit(\d)$/.exec(e.code);
    if (m) key = m[1].toLowerCase();
  }
  const mods = new Set();
  if (e.ctrlKey) mods.add("Ctrl");
  if (e.altKey) mods.add("Alt");
  if (e.shiftKey) mods.add("Shift");
  if (e.metaKey) mods.add("Meta");
  if (key.length === 1) {
    if (mods.size === 1 && mods.has("Shift")) mods.delete("Shift");     // the character already carries the Shift ("G", "?")
    else if (mods.size) key = key.toLowerCase();                       // ⌘⇧O arrives as "O"; write it as Meta+Shift+o
  }
  return [...ORDER.filter((m) => mods.has(m)), key].join("+");
}

/** Can this chord start a binding while a text field has focus? Only ones that can't be typing: a modifier, Esc, F-keys. */
export const safeInInput = (chord) => /(^|\+)(Ctrl|Alt|Meta)\+/.test(chord) || chord === "Escape" || /^F\d+$/.test(chord) || chord === LEADER;

/** "Meta+Shift+o" -> "⌘⇧O"; a sequence reads with spaces: "g g", "⌘; P". */
export function prettyChord(chord, leader = "") {
  if (chord === LEADER) return leader ? prettyChord(leader) : "Leader";
  const parts = chord.endsWith("++") ? [...chord.slice(0, -2).split("+"), "+"] : chord.split("+");
  const key = parts.pop();
  const mods = APPLE.filter((m) => parts.includes(m)).map((m) => SYMBOL[m]).join("");
  return mods + (KEY_LABEL[key] || (key.length === 1 ? (mods ? key.toUpperCase() : key) : key));
}
export const prettySequence = (seq, leader = "") => seq.split(" ").map((c) => prettyChord(c, leader)).join(" ");

/* ---------- when: a tiny condition language over the names the page says are true ---------- */
const WHEN = /\s*(&&|\|\||!|\(|\)|[A-Za-z_][\w:.-]*)/y;

/** Compile "!input && (panel:files || panel:notes)" into a function of a Set of true names; throws on bad syntax. */
export function compileWhen(src) {
  const text = String(src ?? "").trim();
  if (!text) return () => true;
  const toks = [];
  WHEN.lastIndex = 0;
  let pos = 0;
  while (pos < text.length) {
    WHEN.lastIndex = pos;
    const m = WHEN.exec(text);
    if (!m) { if (/^\s*$/.test(text.slice(pos))) break; throw new Error(`Can't read "${text.slice(pos).trim()}" in "${text}"`); }
    toks.push(m[1]); pos = WHEN.lastIndex;
  }
  let i = 0;
  const peek = () => toks[i], next = () => toks[i++];
  function or() { let l = and(); while (peek() === "||") { next(); const a = l, r = and(); l = (f) => a(f) || r(f); } return l; }
  function and() { let l = not(); while (peek() === "&&") { next(); const a = l, r = not(); l = (f) => a(f) && r(f); } return l; }
  function not() { if (peek() === "!") { next(); const a = not(); return (f) => !a(f); } return atom(); }
  function atom() {
    const t = next();
    if (t === "(") { const e = or(); if (next() !== ")") throw new Error(`Missing ")" in "${text}"`); return e; }
    if (!t || /^(&&|\|\||!|\))$/.test(t)) throw new Error(`Unexpected "${t ?? "end"}" in "${text}"`);
    return (f) => f.has(t);
  }
  const fn = or();
  if (i < toks.length) throw new Error(`Unexpected "${toks[i]}" in "${text}"`);
  return fn;
}

/* ---------- the keymap: defaults and the user's file merged into one list ---------- */
/**
 * defaults: [{key, command, when?, args?}], user: {leader?, vim?, bindings?: [...]}, opts: {mac}.
 * Returns {leader, vim, bindings, errors}. Each binding: {seq: [chords], key, command, args, when, whenFn, user}.
 * Later bindings win, so the user's override the defaults; a null command removes the same key (and `when`).
 */
export function buildKeymap(defaults, user = {}, { mac = true, defaultLeader = "Mod+;" } = {}) {
  const errors = [];
  const leader = normalizeChord(user.leader || defaultLeader, mac) || normalizeChord(defaultLeader, mac);
  if (user.leader && !normalizeChord(user.leader, mac)) errors.push(`Leader "${user.leader}" isn't a key; using ${defaultLeader}.`);
  const out = [];
  const add = (b, isUser) => {
    const seq = normalizeSequence(b.key, mac);
    if (!seq) return errors.push(`"${b.key}" isn't a valid key${b.command ? ` (for ${b.command})` : ""}.`);
    let whenFn;
    try { whenFn = compileWhen(b.when); } catch (e) { return errors.push(`${b.command || b.key}: ${e.message}`); }
    const at = out.findIndex((x) => x.key === seq && (x.when || "") === (b.when || ""));
    if (at >= 0) out.splice(at, 1);
    if (!b.command) return;
    out.push({ key: seq, seq: seq.split(" "), command: b.command, args: b.args || null, when: b.when || "", whenFn, user: isUser });
  };
  defaults.forEach((b) => add(b, false));
  (user.bindings || []).forEach((b) => add(b, true));
  return { leader, vim: !!user.vim, bindings: out, errors };
}

/** Does `a` start with all of `b` (as whole chords)? */
const startsWith = (a, b) => b.length <= a.length && b.every((c, i) => a[i] === c);

/**
 * Problems with the bindings: the same keys for two commands under overlapping conditions, or a key that is also the
 * start of a longer sequence (the longer one could never be typed). Conditions are treated as overlapping unless one is
 * written as the opposite of the other, which is a safe over-report for a settings screen.
 */
export function findConflicts(bindings) {
  const out = [];
  for (let i = 0; i < bindings.length; i++) for (let j = i + 1; j < bindings.length; j++) {
    const a = bindings[i], b = bindings[j];
    if (a.command === b.command && JSON.stringify(a.args) === JSON.stringify(b.args)) continue;
    if (!whenOverlap(a.when, b.when)) continue;
    if (a.key === b.key) out.push({ kind: "same", a, b });
    else if (startsWith(a.seq, b.seq) || startsWith(b.seq, a.seq)) out.push({ kind: "prefix", a, b });
  }
  return out;
}
function whenOverlap(a, b) {
  if (!a || !b) return true;
  const na = a.replace(/\s/g, ""), nb = b.replace(/\s/g, "");
  return na !== `!${nb}` && nb !== `!${na}`;
}

/**
 * Feeds chords and says what to do. `flags` is the Set of names true right now (see compileWhen).
 * feed(chord) -> {type: "none"} | {type: "pending", options: [{key, command}]} | {type: "match", binding}.
 * A match is returned as soon as the typed chords equal a binding, so give a key and its extension different names.
 */
export class Matcher {
  constructor(keymap) { this.keymap = keymap; this.typed = []; }
  reset() { this.typed = []; }
  get pending() { return this.typed.length > 0; }
  feed(chord, flags) {
    const typed = [...this.typed, chord];
    const live = this.keymap.bindings.filter((b) => b.whenFn(flags));
    const match = [...live].reverse().find((b) => b.seq.length === typed.length && b.seq.every((c, i) => c === typed[i]));
    if (match) { this.typed = []; return { type: "match", binding: match }; }
    const more = live.filter((b) => b.seq.length > typed.length && startsWith(b.seq, typed));
    if (more.length) { this.typed = typed; return { type: "pending", options: more.map((b) => ({ key: b.seq[typed.length], rest: b.seq.slice(typed.length + 1), command: b.command, args: b.args })) }; }
    const had = this.typed.length > 0;
    this.typed = [];
    return { type: "none", abandoned: had };
  }
  /** Could this chord start a sequence or fire a binding now? Used to decide whether to leave a key alone. */
  starts(chord, flags) {
    return this.keymap.bindings.some((b) => b.seq[0] === chord && b.whenFn(flags));
  }
}

/** The chord strings a command is bound to, for hints ("⌘K", "g g"). */
export function bindingsOf(keymap, command, args = null) {
  const key = JSON.stringify(args || null);
  return keymap.bindings.filter((b) => b.command === command && JSON.stringify(b.args || null) === key).map((b) => b.key);
}

/** Chords a browser tab can't receive, so a binding to them would silently never fire. */
export const BROWSER_RESERVED = new Set(["Meta+w", "Meta+t", "Meta+n", "Meta+q", "Meta+l", "Meta+r", "Meta+Shift+t", "Meta+Shift+n", "Meta+Tab",
  "Ctrl+w", "Ctrl+t", "Ctrl+n", "Ctrl+Tab", "Ctrl+Shift+t", "Ctrl+Shift+n", "Meta+1", "Meta+2", "Meta+3", "Meta+4", "Meta+5", "Meta+6", "Meta+7", "Meta+8", "Meta+9"]);
