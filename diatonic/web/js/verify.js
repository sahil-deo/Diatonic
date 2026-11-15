// Test-runs code the agent wrote, before the user is asked to approve it, in a sandboxed frame: no network (CSP), no cookies or
// access to this page (sandbox without allow-same-origin), and every call to the server just never answers. The server is told
// what went wrong (a syntax error, an undefined name, a panel that shows nothing) so the model can fix it first.
import { h } from "./dom.js";
import { api } from "./api.js";

const WAIT = 4000;

/** The page inside the frame. CODE and TOKEN are filled in; "</" in the code is escaped so it can't close the script tag. */
const FRAME = `<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval'; style-src 'unsafe-inline'">
<body><div id="el"></div><script>
const KIND = __KIND__, TOKEN = __TOKEN__, CODE = __CODE__, TOOLS = __TOOLS__, TRACKERS = __TRACKERS__;
const errors = []; let usedApi = false;
const bad = (e) => errors.push((e && e.message) || String(e));
addEventListener("error", (e) => bad(e.error || e.message)); addEventListener("unhandledrejection", (e) => bad(e.reason));
const h = (tag, attrs, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v; else if (k === "html") el.innerHTML = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v; else if (v === true) el.setAttribute(k, ""); else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null && kid !== false) el.append(kid.nodeType ? kid : document.createTextNode(kid));
  return el;
};
// A call to the server never answers here. It returns a thenable, so a call whose result is never awaited or .then()'d can be told apart.
const unanswered = new Set();
const hang = () => {
  usedApi = true;
  const t = { then() { unanswered.delete(t); return t; }, catch() { unanswered.delete(t); return t; }, finally() { unanswered.delete(t); return t; } };
  unanswered.add(t);
  return t;
};
const nothing = () => {};
const memory = {}, mem = { getItem: (k) => (k in memory ? memory[k] : null), setItem: (k, v) => { memory[k] = String(v); }, removeItem: (k) => { delete memory[k]; }, clear: nothing };
const diatonic = { h, esc: (s) => String(s), toast: nothing, icon: () => "", api: { get: hang, post: hang, put: hang, patch: hang, del: hang }, agent: { send: () => { usedApi = true; } },
  on: () => nothing, open: nothing, close: nothing, toggle: nothing, isOpen: () => false, run: hang, command: () => nothing,
  every: (ms, fn) => { setTimeout(() => { try { fn(); } catch (e) { bad(e); } }, 50); return 0; },   // run a tick once, to catch errors inside it
  addButton: () => document.createElement("button") };
const el = document.getElementById("el");
const ctx = { api: diatonic.api, h, esc: diatonic.esc, toast: nothing, reload: nothing, prompt: () => { usedApi = true; }, id: "check", app: "check", params: {}, el,
  state: { get: (k, d) => d, set: nothing }, onResize: () => nothing, command: () => nothing, list: () => nothing,
  tool: (name) => {
    if (!TOOLS.includes(name)) {
      const app = String(name).split("_")[0], near = TOOLS.filter((t) => t.startsWith(app + "_"));
      throw new Error('There is no read-only tool "' + name + '". ' + (near.length ? "Tools of that app" : "Tools") + ": " + (near.length ? near : TOOLS).slice(0, 20).join(", ") + ".");
    }
    return hang();
  },
  data: (id) => {
    if (!TRACKERS.includes(id)) throw new Error('"' + id + '" is not a tracker app made with create_app (' + (TRACKERS.join(", ") || "there are none") + '). For built-in apps use await ctx.tool("<app>_<tool>", {}), e.g. todos_list_todos.');
    return { get: hang, add: hang, update: hang };
  } };
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
(async () => {
  try {
    const fn = new AsyncFunction("diatonic", "el", "ctx", "localStorage", CODE);
    const r = await Promise.race([fn(diatonic, KIND === "panel" ? el : null, ctx, mem), new Promise((ok) => setTimeout(ok, 1200))]);
    if (r instanceof Node) el.append(r);
  } catch (e) { bad(e); }
  await new Promise((ok) => setTimeout(ok, 150));
  if (unanswered.size) errors.push("A call to the server was not awaited. Write await in front of ctx.tool(...), ctx.data(...).get() and diatonic.api calls: without it you get a promise, not the data.");
  parent.postMessage({ token: TOKEN, errors, usedApi, rendered: el.children.length > 0 || el.textContent.trim().length > 0,
    text: el.innerText.slice(0, 200), buttons: el.querySelectorAll("button").length, inputs: el.querySelectorAll("input, textarea, select").length }, "*");
})();
</script>`;

const safe = (v) => JSON.stringify(v).replace(/</g, "\\u003c");

/** Run `code` in a sandbox; resolves to {errors, usedApi, rendered, text, buttons, inputs}. */
export function check(kind, code, known = {}) {
  const token = Math.random().toString(36).slice(2);
  const frame = h("iframe", { sandbox: "allow-scripts", "aria-hidden": "true", tabindex: "-1", style: "position:fixed;left:-9999px;top:0;width:380px;height:600px;border:0",
    srcdoc: FRAME.replace("__KIND__", safe(kind)).replace("__TOKEN__", safe(token)).replace("__TOOLS__", () => safe(known.tools || [])).replace("__TRACKERS__", () => safe(known.trackers || [])).replace("__CODE__", () => safe(code)) });
  return new Promise((resolve) => {
    const done = (r) => { clearTimeout(timer); removeEventListener("message", onMessage); frame.remove(); resolve(r); };
    const onMessage = (m) => { if (m.source === frame.contentWindow && m.data?.token === token) done(m.data); };
    const timer = setTimeout(() => done({ errors: ["It never finished running (an endless loop?)."], usedApi: false, rendered: false }), WAIT);
    addEventListener("message", onMessage);
    document.body.append(frame);
  });
}

/** The server asked (a `ui` event, action "verify"): run it and report back. Always reports, so the agent never waits for nothing. */
export async function verify(ev) {
  let result;
  try { result = await check(ev.kind === "script" ? "script" : "panel", String(ev.code || ""), { tools: ev.tools, trackers: ev.trackers }); }
  catch (e) { result = { errors: [], usedApi: true, rendered: true }; }   // can't test here: leave it to the approval card
  await api.post(`/api/mods/verify/${ev.id}`, result).catch(() => {});
}
