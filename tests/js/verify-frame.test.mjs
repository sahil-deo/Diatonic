// The sandbox page in js/verify.js is a string, so `node --check` can't see inside it: compile it here.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import vm from "node:vm";

const src = fs.readFileSync(new URL("../../diatonic/web/js/verify.js", import.meta.url), "utf8");
// What the browser is sent is the template string *evaluated* (a `\b` in it is a backspace), not the text of this file.
const tpl = src.slice(src.indexOf("const FRAME = `") + 15, src.indexOf("`;", src.indexOf("const FRAME = `")));
const page = new Function("return `" + tpl + "`")();
const inner = page.slice(page.indexOf("<script>") + 8, page.indexOf("</script>"));

test("the sandbox script is valid JavaScript once its placeholders are filled", () => {
  const filled = inner.replaceAll("__KIND__", '"panel"').replaceAll("__TOKEN__", '"t"').replaceAll("__CODE__", '"x"').replaceAll("__TOOLS__", "[]").replaceAll("__TRACKERS__", "[]");
  assert.doesNotThrow(() => new vm.Script(filled));
});

test("the sandbox script accepts and executes ES module exports", () => {
  const code = JSON.stringify("export async function render(el, ctx) { el.append(diatonic.h('div', {}, 'Hello')); }");
  const filled = inner.replaceAll("__KIND__", '"panel"').replaceAll("__TOKEN__", '"t"').replaceAll("__CODE__", code).replaceAll("__TOOLS__", "[]").replaceAll("__TRACKERS__", "[]");
  assert.doesNotThrow(() => new vm.Script(filled));
});

test("every placeholder in the page is one the loader fills", () => {
  const used = new Set(inner.match(/__[A-Z]+__/g));
  for (const p of used) assert.ok(src.includes(`.replace("${p}"`), `${p} is never replaced`);
});

test("the sandbox page does not use template literals (it lives inside one)", () => {
  assert.ok(!inner.includes("`"));
});
