// The UI kit: every declared component exists, is documented, and renders its own example with no app state (a tiny fake DOM stands in for the browser).
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

class Node_ {
  constructor(tag) { this.tag = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.nodeType = 1; this.classList = { toggle() {}, add() {}, remove() {} }; }
  append(...k) { this.children.push(...k); } replaceChildren(...k) { this.children = k; } remove() {}
  setAttribute(k, v) { this.attrs[k] = v; } addEventListener(t, f) { (this.listeners[t] ||= []).push(f); }
  querySelectorAll() { return []; } querySelector() { return null; } get isConnected() { return false; } focus() {}
  set innerHTML(v) { this.html = v; } set textContent(v) { this.text = v; } set className(v) { this.attrs.class = v; } get className() { return this.attrs.class; }
}
globalThis.document = { createElement: (t) => new Node_(t), createElementNS: (_, t) => new Node_(t), createTextNode: (t) => ({ nodeType: 3, text: String(t) }) };
globalThis.queueMicrotask ||= (f) => f();

const { components, names } = await import("../../diatonic/web/js/ui/index.js");
const decl = JSON.parse(fs.readFileSync(new URL("../../diatonic/web/js/ui/components.json", import.meta.url), "utf8"));

test("every declared component is implemented, and every implemented one is declared", () => {
  assert.deepEqual(Object.keys(decl).sort(), Object.keys(components).sort());
  assert.ok(names().includes("listApp") && names().includes("run"));
});

test("every declaration has a doc, documented props with a type, and a plain-data example", () => {
  for (const [name, d] of Object.entries(decl)) {
    assert.ok(d.doc.length > 20, `${name}: doc`);
    assert.ok(d.props.length, `${name}: props`);
    for (const p of d.props) assert.ok(p.name && p.type && p.doc, `${name}.${p.name}: needs name, type and doc`);
    assert.deepEqual(JSON.parse(JSON.stringify(d.example)), d.example, `${name}: example must be plain JSON`);
    for (const k of Object.keys(d.example)) assert.ok(d.props.some((p) => p.name === k), `${name}: example uses ${k}, which is not a declared prop`);
    for (const p of d.props.filter((p) => p.required)) assert.ok(p.name in d.example, `${name}: example is missing the required prop ${p.name}`);
  }
});

test("every example renders to an element with no app state", () => {
  for (const [name, d] of Object.entries(decl)) {
    const el = components[name](d.example);
    assert.ok(el && el.nodeType === 1, `${name} did not return an element`);
  }
});

test("components survive being given nothing", () => {
  for (const [name, fn] of Object.entries(components)) assert.doesNotThrow(() => fn(), `${name}()`);
});

test("the kit uses no colour of its own: the CSS for ui- classes only takes values from the :root tokens", () => {
  const css = fs.readFileSync(new URL("../../diatonic/web/css/app.css", import.meta.url), "utf8");
  const kit = css.slice(css.indexOf("/* UI kit (js/ui/)"), css.indexOf(".comp-live"));
  assert.ok(kit.length > 1000);
  assert.deepEqual(kit.match(/#[0-9a-fA-F]{3,8}\b/g) || [], [], "a hex colour in the kit CSS");
  assert.deepEqual((kit.match(/rgba?\([^)]*\)/g) || []).filter((c) => !/^rgba\(0, 0, 0/.test(c)), [], "a colour that is not a token");
});
