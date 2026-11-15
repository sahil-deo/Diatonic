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
    body.replaceChildren(); delete body.dataset.dirty; body.classList.toggle("flush", !!mod.flush);
    $("#shell").classList.toggle("panel-wide", !!mod.wide || !!mod.full);
    applyWidth(app.id, mod.width);
    applyFull(app.id, mod.full);
    await mod.render(body, { api, h, esc, toast, reload: load, prompt: (t) => emit("prefill", t) });
  } catch (e) { body.replaceChildren(h("div", { class: "p-empty" }, `Couldn't load ${app.name}: ${e.message}`)); }
}

const SHELL = () => $("#shell");
function applyFull(appId, def) {
  let v = null; try { v = localStorage.getItem(`panelfull:${appId}`); } catch {}
  const on = v === null ? !!def : v === "1";
  SHELL().classList.toggle("panel-full", on);
  setIcon($("#panel-full"), on ? "minimize" : "maximize", 16);
  $("#panel-full").title = on ? "Show the chat beside this" : "Expand over the chat";
}
function toggleFull() {
  const on = !SHELL().classList.contains("panel-full");
  try { localStorage.setItem(`panelfull:${state.panelApp}`, on ? "1" : "0"); } catch {}
  applyFull(state.panelApp, on);
}
function applyWidth(appId, def) {
  let w = null; try { w = localStorage.getItem(`panelw:${appId}`); } catch {}
  const px = Math.min(Number(w) || def || 0, Math.floor(innerWidth * 0.6)) || 0;   // never crush the chat on a small window
  px ? SHELL().style.setProperty("--panel-w", `${px}px`) : SHELL().style.removeProperty("--panel-w");
}
function initResize() {
  const grip = $("#panel-resize");
  grip.addEventListener("mousedown", (e) => {
    e.preventDefault(); document.body.classList.add("resizing");
    const move = (ev) => { const w = Math.max(320, Math.min(innerWidth - 420, innerWidth - ev.clientX)); SHELL().style.setProperty("--panel-w", `${w}px`); };
    const up = () => {
      removeEventListener("mousemove", move); removeEventListener("mouseup", up); document.body.classList.remove("resizing");
      const w = parseInt(getComputedStyle(SHELL()).getPropertyValue("--panel-w")); try { if (w && state.panelApp) localStorage.setItem(`panelw:${state.panelApp}`, String(w)); } catch {}
    };
    addEventListener("mousemove", move); addEventListener("mouseup", up);
  });
}

export function openPanel(appId) {
  state.panelApp = appId;
  $("#shell").classList.add("panel-open"); $("#panel").classList.remove("hidden");
  emit("panel"); load();
}
export function closePanel() {
  state.panelApp = null; seq++;
  $("#shell").classList.remove("panel-open", "panel-wide", "panel-full"); SHELL().style.removeProperty("--panel-w"); $("#panel").classList.add("hidden"); emit("panel");
}
export function initPanel() {
  initResize();
  $("#panel-full").onclick = toggleFull; setIcon($("#panel-full"), "maximize", 16);
  setIcon($("#panel-refresh"), "refresh", 16); setIcon($("#panel-close"), "x", 16);
  $("#panel-refresh").onclick = load; $("#panel-close").onclick = closePanel;
}
export const refreshPanel = load;
