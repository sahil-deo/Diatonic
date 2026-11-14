import { $, h, toast } from "./dom.js";
import { icon, setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit, on } from "./store.js";
import * as chat from "./chat.js";
import { initVoiceUI, paintSpeakToggle, setSpeak, updateHint } from "./voiceui.js";
import { initSettings, openSettings } from "./settings.js";
import { initPalette, openPalette } from "./palette.js";
import { initPanel, openPanel, closePanel, refreshPanel } from "./panel.js";
import { initInbox, loadInbox } from "./inbox.js";
import { applyAppearance, railApps } from "./appearance.js";
import { initActions, invoke as invokeAction } from "./actions.js";
import { requestEdit } from "./settings-actions.js";

/* ---------- theme ---------- */
const mq = matchMedia("(prefers-color-scheme: light)");
function applyTheme() {
  const t = state.config?.appearance.theme || "system";
  document.documentElement.dataset.theme = t === "system" ? (mq.matches ? "light" : "dark") : t;
}
mq.addEventListener("change", applyTheme);

/* ---------- rail ---------- */
function renderRail() {
  setIcon($("#rail-chat"), "chat", 20); setIcon($("#rail-settings"), "sliders", 20);
  $("#rail-chat").classList.toggle("active", !state.panelApp);
  const box = $("#rail-apps");
  box.replaceChildren(...railApps(state.apps).map((a) => {
    const b = h("button", { class: `rail-btn ${state.panelApp === a.id ? "active" : ""}`, title: a.name, "aria-label": a.name,
      onclick: () => (state.panelApp === a.id ? closePanel() : a.panel ? openPanel(a.id) : openSettings("apps")), html: icon(a.icon, 20) });
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

/* ---------- status bar ---------- */
function renderStatus() {
  const g = state.health?.agent, v = state.health?.voice;
  const a = $("#st-agent");
  if (!g) a.replaceChildren(h("i", { class: "dot" }), "Checking…");
  else if (!g.ok) a.replaceChildren(h("i", { class: "dot bad" }), g.error);
  else if (!g.model_present) a.replaceChildren(h("i", { class: "dot warn" }), g.error);
  else a.replaceChildren(h("i", { class: "dot ok" }), `${g.model} · ${g.provider}`);
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

/* ---------- commands ---------- */
const commands = () => [
  { group: "Actions", label: "New chat", icon: "plus", hint: "⌘⇧O", run: chat.newChat },
  { group: "Actions", label: state.config.voice.speak_summary ? "Turn off spoken summaries" : "Turn on spoken summaries", icon: "volume", run: () => setSpeak(!state.config.voice.speak_summary) },
  { group: "Actions", label: "Settings", icon: "sliders", hint: "⌘,", run: () => openSettings() },
  { group: "Actions", label: "Voice settings", icon: "mic", run: () => openSettings("voice") },
  { group: "Actions", label: "Customize the interface", icon: "sliders", run: () => openSettings("interface") },
  { group: "Actions", label: "Edit saved prompts and buttons", icon: "command", run: () => openSettings("actions") },
  { group: "Actions", label: "Toggle sidebar", icon: "menu", hint: "⌘B", run: () => toggleSidebar() },
  ...(state.config.actions || []).map((a) => ({ group: "Your actions", label: a.label, icon: a.kind === "script" ? "command" : "chat", hint: a.name ? "/" + a.name : "", run: () => invokeAction(a) })),
  { group: "Actions", label: "Theme: dark", icon: "chevron", run: () => setTheme("dark") },
  { group: "Actions", label: "Theme: light", icon: "chevron", run: () => setTheme("light") },
  ...state.apps.filter((a) => a.enabled && a.panel).map((a) => ({ group: "Apps", label: `Open ${a.name}`, icon: a.icon, run: () => openPanel(a.id) })),
  ...(state.panelApp ? [{ group: "Apps", label: "Close app panel", icon: "x", run: closePanel }] : []),
  ...state.conversations.slice(0, 15).map((c) => ({ group: "Conversations", label: c.title, icon: "chat", run: () => chat.openConversation(c.id) })),
];
function toggleSidebar() {
  if (matchMedia("(max-width: 820px)").matches) return $("#sidebar").classList.toggle("show");
  const next = state.config.appearance.sidebar === "hide" ? "show" : "hide";
  state.config.appearance.sidebar = next; applyAppearance();
  api.putConfig({ appearance: { sidebar: next } }).then((c) => { state.config = c; }).catch(() => {});
}
async function setTheme(t) { state.config = await api.putConfig({ appearance: { theme: t } }); applyTheme(); }

addEventListener("keydown", (e) => {
  const mod = e.metaKey || e.ctrlKey;
  if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(commands); }
  else if (mod && e.key === ",") { e.preventDefault(); openSettings(); }
  else if (mod && e.key.toLowerCase() === "b" && !e.shiftKey) { e.preventDefault(); toggleSidebar(); }
  else if (mod && e.shiftKey && e.key.toLowerCase() === "o") { e.preventDefault(); chat.newChat(); }
  else if (e.key === "/" && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName) && !document.querySelector("dialog[open]")) { e.preventDefault(); input.focus(); }
});

/* ---------- boot ---------- */
async function boot() {
  const s = await api.state();
  state.config = s.config; state.apps = s.apps;
  applyTheme(); applyAppearance();
  setIcon($("#new-chat"), "plus", 18); setIcon($("#toggle-sidebar"), "menu", 18); setIcon($("#palette-btn"), "command", 18);
  paintSpeakToggle(); paintSend();
  initSettings(); initPalette(); initPanel();
  initVoiceUI({ setInput, send: (t) => chat.send(t), openSettings });
  initActions({ newChat: () => chat.newChat(), openSettings, setTheme, setSpeak, openPanel, closePanel, toggleSidebar,
    editAction: (id) => { requestEdit(id); openSettings("actions"); } });
  $("#new-chat").onclick = () => chat.newChat();
  $("#rail-chat").onclick = () => { closePanel(); input.focus(); };
  $("#rail-settings").onclick = () => openSettings();
  $("#palette-btn").onclick = () => openPalette(commands);
  $("#toggle-sidebar").onclick = toggleSidebar;
  $("#speak-toggle").onclick = () => setSpeak(!state.config.voice.speak_summary);

  const inbox = initInbox({ onOpenChat: (id) => chat.openConversation(id) });
  on("notification", inbox.onNotification);
  on("app_changed", (ev) => { if (state.panelApp === ev.app && !$("#panel-body").dataset.dirty) refreshPanel(); });
  on("runs-changed", paintRuns);
  on("conversations-changed", loadConvs);
  on("apps", () => { renderRail(); chat.renderEmpty?.(); });
  on("panel", renderRail);
  on("health", refreshHealth);
  on("config", () => { applyTheme(); applyAppearance(); updateHint(); paintSpeakToggle(); renderRail(); });
  on("rail-changed", renderRail);
  on("prefill", (t) => { setInput(t); });

  renderRail(); chat.renderEmpty(); updateHint(); loadConvs(); refreshHealth(); loadInbox(); chat.startEventStream();
  setInterval(refreshHealth, 20000);

  const q = new URLSearchParams(location.search);
  if (q.get("settings")) { history.replaceState({}, "", "/"); openSettings(q.get("settings")); if (q.get("connected")) toast("Connected."); }
  input.focus();
}
boot().catch((e) => { document.body.append(h("div", { class: "err", style: "margin:40px auto;max-width:520px" }, `Couldn't start the UI: ${e.message}`)); console.error(e); });
