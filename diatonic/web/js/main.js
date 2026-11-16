import { $, h, toast } from "./dom.js";
import { icon, setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit, on } from "./store.js";
import * as chat from "./chat.js";
import { initVoiceUI, paintSpeakToggle, setSpeak, updateHint } from "./voiceui.js";
import { initSettings, openSettings } from "./settings.js";
import { initPalette, openPalette } from "./palette.js";
import { registerCore } from "./commands-core.js";
import { initKeymap } from "./keymap.js";
import { initNav } from "./nav.js";
import { initPanel, openPanel, closePanel, refreshPanel, setFull } from "./panel.js";
import { initInbox, loadInbox } from "./inbox.js";
import { applyAppearance, railApps } from "./appearance.js";
import { initActions } from "./actions.js";
import { initMentions } from "./mentions.js";
import { requestEdit } from "./settings-actions.js";
import { initDock, dockVisible, focusDock, setDockInput } from "./dock.js";
import { initMods } from "./mods.js";
import { verify } from "./verify.js";
import { initStatusBar } from "./statusbar.js";
import { loadModels } from "./settings-models.js";
import { initMacros } from "./macros.js";

/* ---------- theme ---------- */
const mq = matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const t = state.config?.appearance.theme || "system";
  document.documentElement.dataset.theme = t === "system" ? (mq.matches ? "light" : "dark") : t;
}
mq.addEventListener("change", applyTheme);

/* ---------- rail ---------- */
let workspace = null;

function renderRail() {
  setIcon($("#rail-chat"), "chat", 20); setIcon($("#rail-settings"), "sliders", 20);
  const isChatActive = state.panelApp === "chat";
  $("#rail-chat").classList.toggle("active", isChatActive);
  const box = $("#rail-apps");
  box.replaceChildren(...railApps(state.apps).map((a) => {
    const b = h("button", { class: `rail-btn ${state.panelApp === a.id ? "active" : ""}`, title: a.name, "aria-label": a.name, "data-app": a.id,
      onclick: () => {
        a.panel ? openPanel(a.id) : openSettings("apps");
      }, html: icon(a.icon, 20) });
    if (a.status && !a.status.connected) b.append(h("i", { class: "badge", title: a.status.detail }));
    return b;
  }));
}

/* ---------- conversations ---------- */
async function loadConvs() { state.conversations = await api.conversations(); renderConvs(); }
function indicator(id) {
  const r = state.runs[id];
  if (r) return h("span", { class: `ind ${r.status === "waiting" ? "wait" : ""}`, title: r.status === "waiting" ? "Needs your approval" : "Working…",
    html: r.status === "waiting" ? icon("shield", 14) : '<span class="spin"></span>' });
  if (state.unread.has(id)) return h("span", { class: "ind unread", title: "New activity" });
  return null;
}
function renderConvs() {
  const list = $("#conv-list");
  if (!state.conversations.length) return list.replaceChildren(h("div", { class: "empty-list" }, "No conversations yet."));
  const startOfDay = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const today = startOfDay(new Date()), groups = { Today: [], Yesterday: [], "Previous 7 days": [], Older: [] };
  for (const c of state.conversations) {
    const age = (today - startOfDay(new Date(c.updated_at * 1000))) / 864e5;
    groups[age < 1 ? "Today" : age < 2 ? "Yesterday" : age < 8 ? "Previous 7 days" : "Older"].push(c);
  }
  list.replaceChildren(...Object.entries(groups).filter(([, v]) => v.length).flatMap(([name, cs]) => [
    h("div", { class: "conv-group" }, name),
    ...cs.map((c) => h("div", { class: `conv ${c.id === state.convId ? "active" : ""}`, role: "listitem", tabindex: 0,
      onclick: () => { chat.openConversation(c.id); $("#sidebar").classList.remove("show"); },
      onkeydown: (e) => { if (e.key === "Enter") chat.openConversation(c.id); } },
      h("span", {}, c.title), indicator(c.id),
      h("button", { class: "del", "aria-label": "Delete conversation", html: icon("trash", 14),
        onclick: (e) => { e.stopPropagation(); chat.removeConversation(c.id); } })))]));
}

/* ---------- composer ---------- */
const input = $("#input");
function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 200) + "px"; }
function paintSend() {
  const b = $("#send"), running = chat.busy();
  b.classList.toggle("stop", running);
  b.disabled = !running && !input.value.trim();
  setIcon(b, running ? "stop" : "send", 18);
  b.setAttribute("aria-label", running ? "Stop" : "Send");
}
function setInput(text) {
  if (dockVisible()) return setDockInput(text);   // an app fills the window: the chat column is hidden, the dock is where you talk
  input.value = input.value.trim() ? `${input.value.trim()} ${text}` : text;
  autosize(); paintSend(); input.focus();
}
function submit() {
  if (chat.busy()) return chat.stop();
  const t = input.value; if (!t.trim()) return;
  input.value = ""; autosize(); paintSend(); chat.send(t);
}
input.addEventListener("input", () => { autosize(); paintSend(); });
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
});
$("#send").addEventListener("click", submit);

/* ---------- what "this" refers to ---------- */
const KIND_ICON = { email: "mail", note: "file-text", file: "file", event: "calendar" };
function paintScreenChip() {
  const sc = state.screenContext?.(), off = sc && state.screenOff === sc.ref;
  document.querySelectorAll(".ctx-chip").forEach((el) => {
    el.hidden = !sc?.ref;   // only a selected item gets a chip; the open app is shared without one
    if (!sc) return el.replaceChildren();
    el.classList.toggle("off", !!off);
    el.title = off ? "Not shared with the agent. Click to share it again." : "The agent knows this is open, so you can say “this” or “it”.";
    el.replaceChildren(h("button", { type: "button", class: "ctx-main", onclick: () => { state.screenOff = off ? null : sc.ref; paintScreenChip(); } },
      h("span", { html: icon(KIND_ICON[sc.kind] || "file", 13) }), h("span", { class: "ctx-t" }, sc.title || sc.ref), h("span", { class: "ctx-x", html: icon(off ? "plus" : "x", 12) })));
  });
}

/* ---------- model switcher (click the model name in the status bar) ---------- */
async function openModelMenu() {
  document.querySelector(".model-menu")?.remove();
  let d;
  try { d = await loadModels(); } catch (e) { return toast(e.message, "error"); }
  const close = () => { menu.remove(); removeEventListener("pointerdown", outside, true); removeEventListener("keydown", esc, true); };
  const outside = (e) => { if (!menu.contains(e.target) && !$("#st-agent").contains(e.target)) close(); };
  const esc = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const pick = async (m) => {
    close();
    if (m.active) return;
    if (m.cloud && !m.key_set) { toast(`${m.label} needs an API key first.`); return openSettings("models"); }
    try { await api.put("/api/models/active", { id: m.id }); toast(`Now using ${m.label}${m.cloud ? " (cloud: private apps will ask before sharing)" : ""}`); }
    catch (e) { toast(e.message, "error"); }
    refreshHealth();
  };
  const menu = h("div", { class: "model-menu", role: "menu" },
    h("div", { class: "mm-head" }, "Model"),
    ...d.models.map((m) => h("button", { type: "button", role: "menuitemradio", "aria-checked": String(m.active), class: m.active ? "on" : "", onclick: () => pick(m) },
      h("span", { class: "mm-tick", html: m.active ? icon("check", 14) : "" }), h("span", { class: "grow" }, m.label, h("small", {}, m.model)),
      h("span", { class: `pill ${m.cloud ? "cloud" : "local"}` }, m.cloud ? "cloud" : "local"))),
    h("button", { type: "button", class: "mm-manage", onclick: () => { close(); openSettings("models"); } }, "Manage models…"));
  document.body.append(menu);
  addEventListener("pointerdown", outside, true); addEventListener("keydown", esc, true);
}

/* ---------- status bar ---------- */
function renderStatus() {
  const g = state.health?.agent, v = state.health?.voice;
  const a = $("#st-agent");
  if (!g) a.replaceChildren(h("i", { class: "dot" }), "Checking…");
  else if (!g.ok) a.replaceChildren(h("i", { class: "dot bad" }), g.error);
  else if (!g.model_present) a.replaceChildren(h("i", { class: "dot warn" }), g.error);
  else a.replaceChildren(h("i", { class: "dot ok" }), g.label || g.model, g.cloud ? h("span", { class: "pill cloud" }, "cloud") : ` · ${g.provider}`);
  a.title = "Switch model";
  const vs = $("#st-voice");
  if (!v) return vs.replaceChildren();
  const ready = v.stt.ready && v.tts.ready;
  vs.replaceChildren(h("i", { class: `dot ${ready ? "ok" : "warn"}` }),
    ready ? "Voice ready" : h("button", { onclick: () => openSettings("voice") }, v.stt.available ? "Download voice models" : "Set up voice"));
}
/* attention indicators: sidebar, status bar, tab title, rail badge */
function paintRuns() {
  paintSend(); renderConvs();
  const all = Object.entries(state.runs), waiting = all.filter(([, r]) => r.status === "waiting");
  const el = $("#st-runs");
  if (!all.length) el.replaceChildren();
  else {
    const first = (waiting[0] || all[0])[0];
    el.replaceChildren(h("i", { class: `dot ${waiting.length ? "warn" : "ok"}` }),
      h("button", { class: "link", onclick: () => chat.openConversation(first) },
        waiting.length ? `${waiting.length} waiting for approval` : `${all.length} chat${all.length > 1 ? "s" : ""} working`));
  }
  document.title = waiting.length ? "⚠ Approval needed · Diatonic" : all.length ? "Working… · Diatonic" : "Diatonic";
  const rb = $("#rail-chat"); rb.querySelector(".badge")?.remove();
  const others = all.filter(([id]) => id !== state.convId);
  if (others.length || state.unread.size) rb.append(h("i", { class: `badge ${others.some(([, r]) => r.status === "waiting") ? "warn" : "busy"}` }));
}
async function refreshHealth() {
  try { state.health = await api.health(); } catch { state.health = { agent: { ok: false, error: "Diatonic server not reachable." } }; }
  renderStatus();
}

/* ---------- commands (the registry lives in commands.js; the built-ins in commands-core.js) ---------- */
function toggleSidebar() { setSidebar(state.config.appearance.sidebar === "hide" ? "show" : "hide"); }
function setSidebar(next) {
  if (matchMedia("(max-width: 820px)").matches) return $("#sidebar").classList.toggle("show", next === "show");
  if (state.config.appearance.sidebar === next) return;
  state.config.appearance.sidebar = next; applyAppearance();
  api.putConfig({ appearance: { sidebar: next } }).then((c) => { state.config = c; }).catch(() => {});
}
async function setTheme(t) { state.config = await api.putConfig({ appearance: { theme: t } }); applyTheme(); }

/* ---------- boot ---------- */
async function boot() {
  const s = await api.state();
  state.config = s.config; state.apps = s.apps;
  applyTheme(); applyAppearance();
  setIcon($("#new-chat"), "plus", 18); setIcon($("#toggle-sidebar"), "menu", 18); setIcon($("#palette-btn"), "command", 18);
  paintSpeakToggle(); paintSend();
  initSettings(); initPalette(); initPanel(); initDock(); initNav();
  initMentions();
  initVoiceUI({ setInput, send: (t) => chat.send(t), openSettings });
  initActions({ newChat: () => chat.newChat(), openSettings, setTheme, setSpeak, openPanel, closePanel, toggleSidebar,
    editAction: (id) => { requestEdit(id); openSettings("actions"); } });
  registerCore({ toggleSidebar, setSidebar, setTheme, setSpeak, openModelMenu, focusComposer: () => (dockVisible() ? focusDock() : input.focus()) });
  initKeymap();
  initMacros();
  $("#new-chat").onclick = () => chat.newChat();
  $("#rail-chat").onclick = () => {
    openPanel("chat");
    setTimeout(() => input.focus(), 50);
  };
  $("#rail-settings").onclick = () => openSettings();
  $("#palette-btn").onclick = () => openPalette();
  $("#toggle-sidebar").onclick = toggleSidebar;
  $("#speak-toggle").onclick = () => setSpeak(!state.config.voice.speak_summary);

  const inbox = initInbox({ onOpenChat: (id) => chat.openConversation(id) });
  window.addEventListener("error", (e) => {
    const fn = e.filename || "";
    const m = fn.match(/\/custom-apps\/([a-z0-9-]+)\//);
    if (m) {
      api.post(`/api/custom-apps/${m[1]}/logs`, {
        error: `${e.message} (${fn}:${e.lineno}:${e.colno})`,
        source: "window_error",
      }).catch(() => {});
    }
  });
  on("notification", inbox.onNotification);
  on("app_changed", (ev) => { if (state.panelApp === ev.app && !$("#panel-body")?.dataset?.dirty) refreshPanel(); });
  on("runs-changed", paintRuns);
  on("conversations-changed", loadConvs);
  on("apps", () => { renderRail(); if (!document.querySelector("#messages .msg")) chat.renderEmpty?.(); });   // never wipe a chat in view
  on("panel", renderRail);
  on("health", refreshHealth);
  on("config", () => { applyTheme(); applyAppearance(); updateHint(); paintSpeakToggle(); renderRail(); if (!document.querySelector("#messages .msg")) chat.renderEmpty(); });
  on("rail-changed", renderRail);
  on("open-file", () => (state.panelApp === "files" ? refreshPanel() : openPanel("files")));
  on("prefill", (t) => { setInput(t); });
  $("#st-agent").addEventListener("click", openModelMenu);
  on("model_changed", async () => { try { state.config = (await api.state()).config; } catch {} refreshHealth(); });
  on("models-changed", refreshHealth);
  on("screen", paintScreenChip); on("panel", () => setTimeout(paintScreenChip));
  on("ui", async (ev) => {   // the agent moving the screen (only the window whose run asked receives these)
    try {
      if (ev.command) {
        const { run } = await import("./commands.js");
        await run(ev.command, ev.args || {});
      } else if (ev.action === "verify") {
        await verify(ev);
      }
    } catch (e) { toast(`Couldn't show that: ${e.message}`, "error"); }
  });

  const reportFocus = () => { if (document.visibilityState === "visible" && document.hasFocus()) api.focus(); };
  addEventListener("focus", reportFocus); document.addEventListener("visibilitychange", reportFocus); setTimeout(reportFocus, 1500);
  initMods(); initStatusBar(); renderRail(); chat.renderEmpty(); updateHint(); loadConvs(); refreshHealth(); loadInbox(); chat.startEventStream();
  setInterval(refreshHealth, 20000);

  const { initWorkspace } = await import("./workspace.js");
  $("#main")?.classList.add("hidden");
  $("#panel")?.classList.add("hidden");
  $("#workspace-host")?.classList.remove("hidden");
  $("#shell")?.classList.add("shell-tree");
  workspace = initWorkspace($("#workspace-host"));

  const q = new URLSearchParams(location.search);
  if (q.get("settings")) { history.replaceState({}, "", "/"); openSettings(q.get("settings")); if (q.get("connected")) toast(`Connected ${q.get("connected")}`); if (q.get("error")) toast(`Google sign-in: ${q.get("error")}`, "error"); }
  input.focus();
}
boot().catch((e) => { document.body.append(h("div", { class: "err", style: "margin:40px auto;max-width:520px" }, `Couldn't start the UI: ${e.message}`)); console.error(e); });
