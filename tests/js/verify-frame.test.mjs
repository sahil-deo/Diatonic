// The sandbox page in js/verify.js is a string, so `node --check` can't see inside it: compile it here.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(new URL("../../diatonic/web/js/verify.js", import.meta.url), "utf8");
const inner = src.slice(src.indexOf("<script>") + 8, src.indexOf("</script>"));

test("the sandbox script is valid JavaScript once its placeholders are filled", () => {
  const filled = inner.replaceAll("__KIND__", '"panel"').replaceAll("__TOKEN__", '"t"').replaceAll("__CODE__", '"x"').replaceAll("__TOOLS__", "[]").replaceAll("__TRACKERS__", "[]");
  assert.doesNotThrow(() => new vm.Script(filled));
});

test("every placeholder in the page is one the loader fills", () => {
  const used = new Set(inner.match(/__[A-Z]+__/g));
  for (const p of used) assert.ok(src.includes(`.replace("${p}"`), `${p} is never replaced`);
});

test("the sandbox page does not use template literals (it lives inside one)", () => {
  assert.ok(!inner.includes("`"));
});
