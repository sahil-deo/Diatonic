import test from "node:test";
import assert from "node:assert/strict";
import { fuzzy, boost } from "../../diatonic/web/js/fuzzy.js";

const best = (q, items) => items.map((t) => [t, fuzzy(q, t)]).filter(([, m]) => m).sort((a, b) => b[1].score - a[1].score).map(([t]) => t);

test("no query matches everything, no letters match nothing", () => {
  assert.deepEqual(fuzzy("", "Anything"), { score: 0, hits: [] });
  assert.equal(fuzzy("zzz", "New chat"), null);
});
test("a prefix beats a word start beats the middle", () => {
  assert.deepEqual(best("set", ["Reset layout", "Open settings", "Settings"]), ["Settings", "Open settings", "Reset layout"]);
});
test("letters in order find initials", () => {
  assert.ok(fuzzy("gcal", "Google Calendar"));
  assert.deepEqual(fuzzy("nc", "New chat").hits, [0, 4]);
  assert.equal(fuzzy("ncx", "New chat"), null);
});
test("several words match anywhere", () => {
  assert.ok(fuzzy("open fi", "Open Files"));
  assert.equal(fuzzy("open xx", "Open Files"), null);
});
test("hits point at the matched letters", () => {
  assert.deepEqual(fuzzy("chat", "New chat").hits, [4, 5, 6, 7]);
});
test("use pushes things up, and fades with time", () => {
  const now = Date.now();
  assert.equal(boost({}, "a", now), 0);
  assert.ok(boost({ a: [5, now] }, "a", now) > boost({ a: [1, now] }, "a", now));
  assert.ok(boost({ a: [5, now] }, "a", now) > boost({ a: [5, now - 30 * 864e5] }, "a", now));
});
