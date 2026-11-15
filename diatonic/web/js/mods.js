// Interface mods the agent made on request: CSS, buttons, scripts and whole panels. They load after the built-in UI.
// Each script gets its own `diatonic` object that remembers what the script added (buttons, listeners, timers), so a
// changed or removed script is cleaned up and re-run in place, with no page reload. `?safe=1` skips every mod.
import { $, h, esc, toast } from "./dom.js";
import { icon } from "./icons.js";
import { api } from "./api.js";
import { state, on, emit, CLIENT_ID } from "./store.js";
import * as chat from "./chat.js";
import { applyAppearance } from "./appearance.js";

export const SAFE = new URLSearchParams(location.search).has("safe");
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const live = new Map();   // mod name -> {version, cleanup} for scripts and buttons running in this page

/** Compile without running: null if fine, else the syntax error. Used for approval cards too. */
export function syntaxError(code) {
  try { new AsyncFunction("diatonic", "el", "ctx", code); return null; } catch (e) { return e.message; }
}

/** What a script or panel can use. Small on purpose: a 4B model has to write against it. */
export function makeApi() {
  const undo = [];
  const keep = (off) => { undo.push(off); return off; };
  const toggle = (app) => (state.panelApp === app ? emit("ui", { action: "close" }) : emit("ui", { action: "open", app }));
  const api_ = {
    h, esc, api, toast, icon,
    agent: { send: (t) => chat.send(String(t)) },
    on: (name, fn) => keep(on(name, fn)),
    open: (app, params) => emit("ui", { action: "open", app, params }),
    close: () => emit("ui", { action: "close" }),
    toggle, isOpen: (app) => state.panelApp === app,
    every(ms, fn) { const t = setInterval(fn, Math.max(250, ms)); keep(() => clearInterval(t)); return t; },
    addButton({ label, title, onclick, icon: ic, app }) {
      // One copy in the top bar and one in the agent bar, so it can be reached while an app fills the window.
      const make = () => h("button", { class: "chip-btn mod-btn", type: "button", title: title || label, "data-mod": "1",
        onclick: onclick || (app ? () => toggle(app) : null) }, ic ? h("span", { html: icon(ic, 15) }) : null, h("span", {}, label));
      const copies = [topbarHost(), $("#dock .dock-mods")].filter(Boolean).map((host) => { const b = make(); host.append(b); keep(() => b.remove()); return b; });
      if (app) {   // a toggle shows whether its app is open
        const paint = () => copies.forEach((b) => b.classList.toggle("on", state.panelApp === app));
        paint(); keep(on("panel", paint));
      }
      return copies[0];
    },
  };
  return { api: api_, cleanup: () => undo.splice(0).reverse().forEach((f) => { try { f(); } catch {} }) };
}

function topbarHost() {
  let bar = $(".topbar .mod-bar");
  if (!bar) { bar = h("div", { class: "mod-bar" }); $(".topbar").insertBefore(bar, $("#speak-toggle")); }
  return bar;
}

/** Run panel code: the body of an async function (diatonic, el, ctx). */
export async function runPanel(code, el, ctx) {
  const { api: d } = makeApi();
  const r = await new AsyncFunction("diatonic", "el", "ctx", code)(d, el, ctx);
  if (r instanceof Node) el.append(r);   // small models often build an element and return it
}

function paintStyles(mods) {
  const want = new Map(mods.filter((m) => m.kind === "style" && m.enabled).map((m) => [m.name, m]));
  document.querySelectorAll("style[data-mod]").forEach((s) => { const m = want.get(s.dataset.mod); if (!m || s.dataset.v !== String(m.version)) s.remove(); });
  for (const m of want.values()) {
    if (document.querySelector(`style[data-mod="${CSS.escape(m.name)}"]`)) continue;
    const s = document.createElement("style"); s.dataset.mod = m.name; s.dataset.v = m.version; s.textContent = m.code; document.head.append(s);
  }
}

async function start(m) {
  const { api: d, cleanup } = makeApi();
  live.set(m.name, { version: m.version, cleanup });
  try {
    if (m.kind === "button") { const b = JSON.parse(m.code); d.addButton({ label: b.label, app: b.app, onclick: b.prompt ? () => chat.send(b.prompt) : null }); }
    else await new AsyncFunction("diatonic", "el", "ctx", m.code)(d, null, null);
  } catch (e) {
    cleanup(); live.delete(m.name);
    toast(`“${m.title}” failed and was switched off: ${e.message}`, "error");
    api.put(`/api/mods/${encodeURIComponent(m.name)}`, { enabled: false }).catch(() => {});
  }
}

async function runScripts(mods) {
  const want = new Map(mods.filter((m) => (m.kind === "script" || m.kind === "button") && m.enabled).map((m) => [m.name, m]));
  for (const [name, r] of [...live]) {   // removed, switched off or changed: take away what it added
    if (want.get(name)?.version !== r.version) { r.cleanup(); live.delete(name); }
  }
  for (const m of want.values()) if (!live.has(m.name)) await start(m);
}

function panelApps(mods) {
  state.modApps = mods.filter((m) => m.kind === "panel" && m.enabled)
    .map((m) => ({ id: `mod-${m.name}`, name: m.title, icon: "box", panel: true, enabled: true, mod: true, code: m.code, version: m.version }));
  emit("rail-changed");
  const open = state.modApps.find((a) => a.id === state.panelApp);
  if (state.panelApp?.startsWith("mod-")) emit("ui", open ? { action: "open", app: open.id, reload: true } : { action: "close" });   // re-render or close an edited panel
}

export async function loadMods() {
  if (SAFE) { state.modApps = []; state.mods = []; return []; }
  let mods = [];
  try { mods = await api.get("/api/mods"); } catch { return []; }
  state.mods = mods;
  paintStyles(mods); panelApps(mods); await runScripts(mods);
  return mods;
}

export function initMods() {
  if (SAFE) toast("Safe mode: interface changes made by the agent are switched off.");
  on("mods_changed", async (ev) => {
    try { const s = await api.state(); state.config = s.config; state.apps = s.apps; applyAppearance(); emit("config"); emit("apps"); } catch {}   // theme changes, and API apps the agent built
    await loadMods();
    if (ev.by === CLIENT_ID && ev.summary && !/^undid/.test(ev.summary))
      toast(`Changed the interface: ${ev.summary}`, "", { label: "Undo", run: () => api.post("/api/mods/revert").catch((e) => toast(e.message, "error")) });
  });
  return loadMods();
}
