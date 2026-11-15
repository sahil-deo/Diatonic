// Settings → Keyboard: every command with its keys. Add a key by pressing it (a sequence like "g g" works too),
// remove one with ×. Changes go to keymap.json; defaults you remove are recorded there as "unbound".
import { h, toast } from "./dom.js";
import { list, get, labelOf } from "./commands.js";
import { keymap, userKeymap, saveKeymap, keymapPath, defaults, mac } from "./keymap.js";
import { eventToChord, normalizeSequence, normalizeChord, prettySequence, findConflicts, buildKeymap, safeInInput, BROWSER_RESERVED, LEADER } from "./keys.js";
import { DEFAULT_LEADER } from "./keymap-defaults.js";
import { field, toggle } from "./form.js";
import { rerenderSettings } from "./settings.js";

const same = (a, b) => JSON.stringify(a || null) === JSON.stringify(b || null);
const GROUPS = ["Actions", "Apps", "Navigate"];

function titleOf(command, args) {
  const c = get(command);
  return c ? (c.titleFor ? c.titleFor(args || {}) : labelOf(c)) : command;
}

/** One row per command (and per app for "Open an app"): what to show and which bindings it has. */
function rows() {
  const km = keymap(), map = new Map();
  const row = (command, args) => {
    const id = `${command}|${JSON.stringify(args || null)}`;
    if (!map.has(id)) map.set(id, { command, args: args || null, title: titleOf(command, args), group: get(command)?.group || "Commands", bindings: [] });
    return map.get(id);
  };
  for (const c of list({ all: true })) if (!c.hidden) row(c.id, null);
  for (const b of km.bindings) row(b.command, b.args).bindings.push(b);
  return [...map.values()].filter((r) => r.bindings.length || !get(r.command)?.hidden).sort((a, b) => {
    const g = (x) => { const i = GROUPS.indexOf(x.group); return i < 0 ? GROUPS.length : i; };
    return g(a) - g(b) || a.group.localeCompare(b.group) || 0;
  });
}

/** Take a key (or a few in a row) from the keyboard; resolves to the written sequence, or null if cancelled. */
function capture(target, { single = false } = {}) {
  return new Promise((resolve) => {
    const chords = [];
    let timer = null;
    const show = () => { target.textContent = chords.length ? prettySequence(chords.join(" "), keymap().leader) : "Press the keys… (Esc cancels)"; };
    const done = (v) => { clearTimeout(timer); target.removeEventListener("keydown", onKey); target.removeEventListener("blur", onBlur); resolve(v); };
    const onBlur = () => done(null);
    const onKey = (e) => {
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") return done(null);
      let c = eventToChord(e); if (!c) return;
      if (c === keymap().leader && !single) c = LEADER;
      chords.push(c); show();
      clearTimeout(timer);
      timer = setTimeout(() => done(chords.join(" ")), single ? 250 : 900);
    };
    target.addEventListener("keydown", onKey); target.addEventListener("blur", onBlur);
    show(); target.focus();
  });
}

async function addKey(r, btn) {
  const seq = normalizeSequence(await capture(btn) || "", mac);
  if (!seq) return rerenderSettings();
  const first = seq.split(" ")[0];
  if (BROWSER_RESERVED.has(first)) toast(`${prettySequence(first)} is taken by the browser, so this key may never reach Diatonic.`);
  const when = r.bindings[0]?.when || (safeInInput(first) ? "" : "idle");
  const fresh = { key: seq, command: r.command, ...(r.args ? { args: r.args } : {}), ...(when ? { when } : {}) };
  const user = userKeymap(), trial = buildKeymap(defaults, { ...user, bindings: [...user.bindings, fresh] }, { mac, defaultLeader: DEFAULT_LEADER });
  const clash = findConflicts(trial.bindings).filter((c) => c.a.key === seq && c.a.user || c.b.key === seq && c.b.user);
  const hides = clash.find((c) => c.kind === "prefix");
  if (hides) return toast(`${prettySequence(seq, keymap().leader)} clashes with ${prettySequence((hides.a.key === seq ? hides.b : hides.a).key, keymap().leader)}: one starts the other, so the longer one could never be typed.`, "error");
  const unbind = [];
  for (const c of clash) {
    const other = c.a.key === seq && c.a.user ? c.b : c.a;
    if (!confirm(`${prettySequence(seq, keymap().leader)} is already “${titleOf(other.command, other.args)}”. Use it for “${r.title}” instead?`)) return rerenderSettings();
    unbind.push({ key: other.key, command: null, ...(other.when ? { when: other.when } : {}) });
  }
  await save({ ...user, bindings: [...user.bindings, ...unbind, fresh] });
}

async function removeKey(b) {
  const user = userKeymap();
  const own = user.bindings.findIndex((u) => normalizeSequence(u.key, mac) === b.key && (u.when || "") === b.when && u.command === b.command && same(u.args, b.args));
  const next = own >= 0 ? user.bindings.filter((_, i) => i !== own) : [...user.bindings, { key: b.key, command: null, ...(b.when ? { when: b.when } : {}) }];
  await save({ ...user, bindings: next });
}

async function save(next) {
  try { await saveKeymap(next); } catch (e) { toast(e.message, "error"); }
  rerenderSettings();
}

function leaderField() {
  const km = keymap();
  const btn = h("button", { type: "button", class: "btn hotkey", style: "min-width:120px" }, prettySequence(LEADER, km.leader));
  btn.onclick = async () => {
    const seq = await capture(btn, { single: true });
    const chord = seq && normalizeChord(seq, mac);
    if (!chord) return rerenderSettings();
    if (!safeInInput(chord)) { toast("The leader key must include ⌘, ⌃ or ⌥ so it doesn't fire while typing.", "error"); return rerenderSettings(); }
    await save({ ...userKeymap(), leader: chord });
  };
  return field("Leader key", h("div", { class: "row", style: "align-items:center" }, btn,
    h("button", { type: "button", class: "btn sm", onclick: () => save({ ...userKeymap(), leader: "" }) }, "Reset")),
    "Press it, then another key: the leader popup lists what each key does. It also works inside the terminal and text boxes.");
}

export function keyboardPane() {
  const km = keymap(), user = userKeymap();
  const out = [
    leaderField(),
    toggle("Vim-style keys", "j and k move, Ctrl-D and Ctrl-U scroll half a page, g g and G jump to the top and bottom, i writes a message. Only when you're not typing.", km.vim,
      (v) => save({ ...user, vim: v })),
    h("div", { class: "help", style: "margin:10px 0 4px" }, "Arrow keys, Page Up/Down, Home, End and Space scroll the side you last clicked (when you're not typing). Esc leaves a text box."),
  ];
  if (km.errors.length) out.push(h("div", { class: "notice warn" }, km.errors.map((e) => h("div", {}, e))));
  let group = null;
  for (const r of rows()) {
    if (r.group !== group) { out.push(h("div", { class: "p-section", style: "margin-top:16px" }, r.group)); group = r.group; }
    const add = h("button", { type: "button", class: "kb-add", title: "Add a key", "aria-label": `Add a key for ${r.title}` }, "+");
    add.onclick = () => addKey(r, add);
    out.push(h("div", { class: "kb-row" }, h("div", { class: "kb-title" }, r.title),
      h("div", { class: "kb-keys" }, r.bindings.map((b) => h("span", { class: `kb-key ${b.user ? "mine" : ""}`, title: b.when ? `when ${b.when}` : "" }, prettySequence(b.key, km.leader),
        h("button", { type: "button", "aria-label": `Remove ${prettySequence(b.key, km.leader)}`, onclick: () => removeKey(b) }, "×"))), add)));
  }
  out.push(h("div", { class: "row", style: "margin-top:20px; align-items:center" },
    h("button", { type: "button", class: "btn", onclick: () => { if (confirm("Put every key back to its default?")) save({ leader: "", vim: user.vim, bindings: [] }); } }, "Reset all keys")),
    h("div", { class: "help" }, "Saved in ", h("code", {}, keymapPath() || "keymap.json"), ". You can edit that file by hand: it is read again when you come back to this window."));
  return out;
}
