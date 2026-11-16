import { $, h, esc, toast } from "./dom.js";
import { setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { runPanel } from "./mods.js";
import { register } from "./commands.js";
import { registerList } from "./nav.js";
import { contributeStatus } from "./statusbar.js";

let seq = 0;
let current = null;   // {id, mod, ctx} of the panel on screen, so it can be navigated without a reload
let cleanups = [];    // what the panel on screen registered (commands, lists, observers): undone when it is replaced or closed
const unload = () => {
  if (current?.ctx?.dispose) { try { current.ctx.dispose(); } catch {} }
  cleanups.splice(0).forEach((f) => { try { f(); } catch {} });
};
const own = (off) => { cleanups.push(off); return off; };

/** What a panel gets as `ctx` (the contract in docs/panels.md): keep per-panel data on `ctx`, not in module variables. */
export function panelContext(app, body, nav) {
  const instanceId = app.instanceId || (body?.dataset ? (body.dataset.panelInstance ||= `inst_${++seq}`) : app.id);
  const store = `panelstate:${instanceId}`;
  const oldStore = `panelstate:${app.id}`;
  const read = () => {
    try {
      const v = localStorage.getItem(store);
      if (v !== null) return JSON.parse(v) || {};
      const old = localStorage.getItem(oldStore);
      if (old !== null) return JSON.parse(old) || {};
      return {};
    } catch { return {}; }
  };
  const myCleanups = [];
  const myOwn = (off) => { myCleanups.push(off); return off; };
  return {
    api, h, esc, toast,
    reload: () => (app.reload ? app.reload() : load(nav)),
    replaceTab: (newAppId, params = {}) => (app.replaceTab ? app.replaceTab(newAppId, params) : null),
    prompt: (t) => emit("prefill", t),
    id: instanceId,
    app: app.id,
    params: nav || {},
    el: body,
    dispose: () => myCleanups.splice(0).forEach((f) => { try { f(); } catch {} }),
    state: {
      get: (k, d) => (k in read() ? read()[k] : d),
      set: (k, v) => { try { localStorage.setItem(store, JSON.stringify({ ...read(), [k]: v })); } catch {} }
    },
    onResize: (fn) => { const ro = new ResizeObserver(() => fn(body.clientWidth, body.clientHeight)); ro.observe(body); return myOwn(() => ro.disconnect()); },
    command: (def) => myOwn(register({ group: app.name, ...def, id: `${app.id}.${def.id}` })),
    status: (spec) => myOwn(contributeStatus({ id: `${app.id}:${spec.id || "status"}`, ...spec })),
    list: (spec) => myOwn(registerList("panel", spec)),
    // Run `fn` when this panel is replaced or closed: remove document-level listeners, timers and other things the panel started.
    onClose: (fn) => myOwn(fn),
    // Read through an app's read-only tools: ctx.tool("gcal_list_events", {…}) -> the tool's result.
    tool: (name, args = {}) => { const [a, ...t] = String(name).split("_"); return api.post(`/api/panel/tools/${a}/${t.join("_")}`, args).then((r) => r.result); },
    // The user's own tracker apps: ctx.data("exam-tracker").get(), .add(list, {field: value}), .update(id, {field: value}).
    data: (id) => ({ get: () => api.get(`/api/dapps/${id}`), add: (list, data) => api.post(`/api/dapps/${id}/${list}`, { data }),
      update: (item, data) => api.patch(`/api/dapps/${id}/items/${item}`, { data }) }),
  };
}

async function load(nav = null) {
  const app = [...state.apps, ...(state.modApps || [])].find((a) => a.id === state.panelApp);
  if (!app) return;
  const body = $("#panel-body");
  $("#panel-title").textContent = app.name;
  body.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  const my = ++seq;
  unload();
  try {
    const mod = app.mod ? { render: (el, c) => runPanel(app.code, el, c) } : await import(app.module || `/apps/${app.id}.js`);
    if (my !== seq) return;
    body.replaceChildren(); delete body.dataset.dirty; body.classList.toggle("flush", !!mod.flush);
    $("#shell").classList.toggle("panel-wide", !!mod.wide || !!mod.full);
    applyWidth(app.id, mod.width);
    applyFull(app.id, mod.full);
    const pctx = panelContext(app, body, nav);
    await mod.render(body, pctx);
    current = { id: app.id, mod, ctx: pctx };
    if (nav && mod.navigate) await mod.navigate(nav, pctx);
  } catch (e) { body.replaceChildren(h("div", { class: "p-empty" }, `Couldn't load ${app.name}: ${e.message}`)); }
}

export async function openPanel(appId, nav = null) {
  state.panelApp = appId;
  emit("panel");
  const ws = await import("./workspace.js");
  return ws.openPanel(appId, nav || {});
}

export function closePanel() {
  import("./workspace.js").then((ws) => ws.closeActive());
  state.panelApp = null;
  emit("panel");
}

export function initPanel() {
  state.screenContext = screenContext;
}

export const refreshPanel = () => {
  import("./workspace.js").then((ws) => ws.renderWorkspace());
};

/** What the user is looking at, so "summarize this" can mean the open email. */
export function screenContext() {
  try {
    const wsContext = state.getScreenContext?.();
    if (wsContext) return wsContext;
    const activeApp = state.panelApp;
    if (!activeApp) return null;
    const app = [...state.apps, ...(state.modApps || [])].find((a) => a.id === activeApp);
    return { app: activeApp, kind: "app", title: app?.name || activeApp };
  } catch { return null; }
}

export function setFull(on) {}
export function toggleFull() {}
