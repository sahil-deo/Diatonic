import { $, h, esc, toast } from "./dom.js";
import { setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";

let seq = 0;

async function load() {
  const app = state.apps.find((a) => a.id === state.panelApp);
  if (!app) return;
  const body = $("#panel-body");
  $("#panel-title").textContent = app.name;
  body.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  const my = ++seq;
  try {
    const mod = await import(`/apps/${app.id}.js`);
    if (my !== seq) return;
    body.replaceChildren();
    await mod.render(body, { api, h, esc, toast, prompt: (t) => emit("prefill", t) });
  } catch (e) { body.replaceChildren(h("div", { class: "p-empty" }, `Couldn't load ${app.name}: ${e.message}`)); }
}

export function openPanel(appId) {
  state.panelApp = appId;
  $("#shell").classList.add("panel-open"); $("#panel").classList.remove("hidden");
  emit("panel"); load();
}
export function closePanel() {
  state.panelApp = null; seq++;
  $("#shell").classList.remove("panel-open"); $("#panel").classList.add("hidden"); emit("panel");
}
export function initPanel() {
  setIcon($("#panel-refresh"), "refresh", 16); setIcon($("#panel-close"), "x", 16);
  $("#panel-refresh").onclick = load; $("#panel-close").onclick = closePanel;
}
export const refreshPanel = load;
