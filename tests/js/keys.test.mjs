import test from "node:test";
import assert from "node:assert/strict";
import { DEFAULTS as BUILTIN } from "../../diatonic/web/js/keymap-defaults.js";
import { normalizeChord as nc, normalizeSequence as ns, eventToChord as ev, compileWhen, buildKeymap, findConflicts, Matcher, bindingsOf,
  prettyChord, prettySequence, safeInInput } from "../../diatonic/web/js/keys.js";

const e = (key, o = {}) => ({ key, code: o.code || "", ctrlKey: !!o.ctrl, altKey: !!o.alt, shiftKey: !!o.shift, metaKey: !!o.meta });

test("written chords are normalised", () => {
  assert.equal(nc("cmd+shift+O"), "Meta+Shift+o");
  assert.equal(nc("Mod+k"), "Meta+k");
  assert.equal(nc("Mod+k", false), "Ctrl+k");
  assert.equal(nc("Meta+K"), "Meta+Shift+k");
  assert.equal(nc("shift+Meta+,"), "Meta+Shift+,");
  assert.equal(nc("esc"), "Escape");
  assert.equal(nc("Ctrl+Down"), "Ctrl+ArrowDown");
  assert.equal(nc("G"), "G");
  assert.equal(nc("Shift+g"), "G");
  assert.equal(nc("Meta++"), "Meta++");
  assert.equal(nc("<LEADER>"), "<leader>");
  assert.equal(nc("f5"), "F5");
  assert.equal(nc("Foo+k"), null);
  assert.equal(nc("ab"), null);
  assert.equal(nc(""), null);
});

test("modifier order is fixed", () => {
  assert.equal(nc("Shift+Meta+Alt+Ctrl+x"), "Ctrl+Alt+Meta+Shift+x");
  assert.equal(ns("g  g"), "g g");
  assert.equal(ns("<leader> a f"), "<leader> a f");
  assert.equal(ns("g nope+"), null);
});

test("events become chords", () => {
  assert.equal(ev(e("k", { meta: true })), "Meta+k");
  assert.equal(ev(e("O", { meta: true, shift: true })), "Meta+Shift+o");
  assert.equal(ev(e("G", { shift: true })), "G");
  assert.equal(ev(e("?", { shift: true })), "?");
  assert.equal(ev(e("j")), "j");
  assert.equal(ev(e(" ")), "Space");
  assert.equal(ev(e("˙", { alt: true, code: "KeyH" })), "Alt+h");
  assert.equal(ev(e("ArrowDown", { ctrl: true })), "Ctrl+ArrowDown");
  assert.equal(ev(e("ArrowDown", { shift: true })), "Shift+ArrowDown");
  assert.equal(ev(e("Shift", { shift: true })), null);
  assert.equal(ev(e("Meta", { meta: true })), null);
  assert.equal(ev(e(";", { meta: true })), "Meta+;");
});

test("an event and its written form agree", () => {
  for (const [event, written] of [[e("k", { meta: true }), "Mod+k"], [e("G", { shift: true }), "Shift+g"], [e("d", { ctrl: true }), "Ctrl+d"], [e("Escape"), "esc"]])
    assert.equal(ev(event), nc(written));
});

test("pretty printing", () => {
  assert.equal(prettyChord("Meta+Shift+o"), "⇧⌘O");
  assert.equal(prettyChord("Meta+,"), "⌘,");
  assert.equal(prettyChord("g"), "g");
  assert.equal(prettyChord("ArrowDown"), "↓");
  assert.equal(prettySequence("<leader> a f", "Meta+;"), "⌘; a f");
  assert.equal(prettySequence("g g"), "g g");
});

test("when conditions", () => {
  const f = (...names) => new Set(names);
  assert.equal(compileWhen("")(f()), true);
  assert.equal(compileWhen("!input")(f()), true);
  assert.equal(compileWhen("!input")(f("input")), false);
  assert.equal(compileWhen("a && b")(f("a", "b")), true);
  assert.equal(compileWhen("a && b")(f("a")), false);
  assert.equal(compileWhen("a || b && c")(f("a")), true);
  assert.equal(compileWhen("(a || b) && c")(f("a")), false);
  assert.equal(compileWhen("!(a || b)")(f()), true);
  assert.equal(compileWhen("panel:files && !app:x-y")(f("panel:files")), true);
  for (const bad of ["a &&", "&& a", "(a", "a b", "a $ b", "!"]) assert.throws(() => compileWhen(bad), Error, bad);
});

const DEFAULTS = [
  { key: "Mod+k", command: "palette.open" },
  { key: "j", command: "nav.down", when: "!input" },
  { key: "g g", command: "nav.top", when: "!input" },
  { key: "<leader> n", command: "chat.new" },
  { key: "<leader> a f", command: "app.open", args: { id: "files" } },
];

test("the user's file overrides defaults and can remove keys", () => {
  const km = buildKeymap(DEFAULTS, { leader: "Ctrl+Space", bindings: [
    { key: "Mod+k", command: "my.palette" }, { key: "j", command: null, when: "!input" }, { key: "x", command: "x.run" }] });
  assert.equal(km.leader, "Ctrl+Space");
  const by = Object.fromEntries(km.bindings.map((b) => [b.key, b]));
  assert.equal(by["Meta+k"].command, "my.palette");
  assert.equal(by["Meta+k"].user, true);
  assert.equal(by.j, undefined);
  assert.equal(by.x.command, "x.run");
  assert.equal(km.errors.length, 0);
});

test("bad entries are reported and skipped, the rest still works", () => {
  const km = buildKeymap(DEFAULTS, { leader: "nonsense+", bindings: [{ key: "Foo+q", command: "a" }, { key: "q", command: "b", when: "(" }, { key: "w", command: "ok" }] });
  assert.equal(km.leader, "Meta+;");
  assert.equal(km.errors.length, 3);
  assert.ok(km.bindings.some((b) => b.command === "ok"));
});

test("sequences: pending, match, abandon", () => {
  const km = buildKeymap(DEFAULTS);
  const m = new Matcher(km), flags = new Set();
  assert.deepEqual(m.feed("g", flags).type, "pending");
  assert.equal(m.pending, true);
  const r = m.feed("g", flags);
  assert.equal(r.type, "match"); assert.equal(r.binding.command, "nav.top");
  assert.equal(m.pending, false);
  assert.equal(m.feed("g", flags).type, "pending");
  const x = m.feed("z", flags);
  assert.equal(x.type, "none"); assert.equal(x.abandoned, true);
  assert.equal(m.feed("Meta+k", flags).binding.command, "palette.open");
});

test("the leader starts a sequence and lists what can follow", () => {
  const km = buildKeymap(DEFAULTS), m = new Matcher(km);
  const r = m.feed("<leader>", new Set());
  assert.equal(r.type, "pending");
  assert.deepEqual(r.options.map((o) => o.key).sort(), ["a", "n"]);
  const a = m.feed("a", new Set());
  assert.deepEqual(a.options.map((o) => o.key), ["f"]);
  const f = m.feed("f", new Set());
  assert.equal(f.binding.command, "app.open"); assert.deepEqual(f.binding.args, { id: "files" });
});

test("when decides which bindings are live", () => {
  const m = new Matcher(buildKeymap(DEFAULTS));
  assert.equal(m.feed("j", new Set(["input"])).type, "none");
  assert.equal(m.feed("j", new Set()).type, "match");
});

test("when the same keys are bound twice, the later one wins", () => {
  const km = buildKeymap([{ key: "q", command: "a", when: "x" }, { key: "q", command: "b", when: "y" }]);
  const m = new Matcher(km);
  assert.equal(m.feed("q", new Set(["x", "y"])).binding.command, "b");
  assert.equal(m.feed("q", new Set(["x"])).binding.command, "a");
});

test("conflicts: same keys, and a key that hides a longer sequence", () => {
  const km = buildKeymap([{ key: "a", command: "one" }, { key: "a", command: "two", when: "!input" }, { key: "g", command: "three" }, { key: "g g", command: "four" },
    { key: "b", command: "five", when: "input" }, { key: "b", command: "six", when: "!input" }]);
  const c = findConflicts(km.bindings);
  assert.deepEqual(c.map((x) => `${x.kind}:${x.a.command}/${x.b.command}`).sort(), ["prefix:three/four", "same:one/two"]);
});

test("hints list every key a command has", () => {
  const km = buildKeymap([{ key: "Mod+k", command: "p" }, { key: "<leader> <leader>", command: "p" }, { key: "x", command: "q" }]);
  assert.deepEqual(bindingsOf(km, "p"), ["Meta+k", "<leader> <leader>"]);
});

test("keys that are safe to use while typing", () => {
  assert.equal(safeInInput("Meta+k"), true);
  assert.equal(safeInInput("Escape"), true);
  assert.equal(safeInInput("<leader>"), true);
  assert.equal(safeInInput("j"), false);
  assert.equal(safeInInput("ArrowDown"), false);
  assert.equal(safeInInput("Shift+ArrowDown"), false);
});

test("the built-in keymap is valid and has no conflicts", () => {
  const km = buildKeymap(BUILTIN);
  assert.deepEqual(km.errors, []);
  assert.equal(km.bindings.length, BUILTIN.length);
  assert.deepEqual(findConflicts(km.bindings).map((c) => `${c.kind}: ${c.a.key} ${c.a.command} / ${c.b.key} ${c.b.command}`), []);
});

test("every written named key is understood", () => {
  for (const k of ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "PageUp", "PageDown", "Home", "End", "Enter", "Escape", "Space", "Tab", "Backspace", "Delete", "F1", "F12"])
    assert.equal(nc(k), k, k);
});
