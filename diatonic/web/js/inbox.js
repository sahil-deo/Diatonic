// Notification inbox: the bell, the popover, live toasts, and the optional spoken / browser channels.
import { $, h, toast, relTime } from "./dom.js";
import { icon, setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit, CLIENT_ID } from "./store.js";
import { speaker } from "./voice.js";

let openChat = () => {};
const pop = () => $("#inbox");

function paintBell() {
  const b = $("#bell-badge");
  b.hidden = !state.unreadCount;
  b.textContent = state.unreadCount > 9 ? "9+" : state.unreadCount || "";
  if (!pop().classList.contains("hidden")) renderList();
}

export async function loadInbox() {
  try { const d = await api.get("/api/notifications"); state.inbox = d.items; state.unreadCount = d.unread; } catch { /* offline */ }
  paintBell();
}

const ago = (t) => relTime(new Date(t * 1000).toISOString());

function renderList() {
  const el = pop();
  const items = state.inbox;
  el.replaceChildren(
    h("div", { class: "inbox-head" }, h("strong", {}, "Notifications"), h("div", { class: "grow" }),
      h("button", { class: "btn sm", onclick: async () => { await api.post("/api/notifications/read-all"); await loadInbox(); } }, "Mark all read"),
      h("button", { class: "btn sm", onclick: async () => { await api.post("/api/notifications/clear"); await loadInbox(); } }, "Clear")),
    h("div", { class: "inbox-list" }, items.length ? items.map(row) : h("div", { class: "p-empty" }, "Nothing yet. Reminders and finished jobs show up here.")));
}

function row(n) {
  const snooze = async (m) => { await api.post(`/api/notifications/${n.id}/snooze`, { minutes: m }); toast(`Snoozed for ${m >= 60 ? m / 60 + " h" : m + " min"}`); await loadInbox(); };
  const tomorrow = () => { const d = new Date(); d.setDate(d.getDate() + 1); d.setHours(9, 0, 0, 0); return Math.round((d - Date.now()) / 60000); };
  const acts = [];
  if (n.kind === "reminder") acts.push(
    h("button", { class: "btn sm", onclick: () => snooze(10) }, "10 min"), h("button", { class: "btn sm", onclick: () => snooze(60) }, "1 hour"),
    h("button", { class: "btn sm", onclick: () => snooze(tomorrow()) }, "Tomorrow 9am"));
  if (n.data?.conversation_id) acts.push(h("button", { class: "btn sm", onclick: () => { pop().classList.add("hidden"); openChat(n.data.conversation_id); } }, "Open chat"));
  acts.push(h("button", { class: "btn sm", onclick: async () => { await api.del(`/api/notifications/${n.id}`); await loadInbox(); } }, n.kind === "reminder" ? "Done" : "Dismiss"));
  return h("div", { class: `inbox-item ${n.read ? "" : "unread"}`, onclick: async () => { if (!n.read) { await api.post(`/api/notifications/${n.id}/read`); n.read = 1; await loadInbox(); } } },
    h("div", { class: "i-top" }, h("span", { class: `i-kind ${n.kind}`, html: icon(n.kind === "reminder" ? "bell" : n.kind === "job" ? "clock" : "check", 14) }),
      h("span", { class: "i-title" }, n.title), h("span", { class: "i-time" }, ago(n.created_at))),
    n.body && h("div", { class: "i-body" }, n.body), h("div", { class: "i-acts" }, acts));
}

/** A notification arrived live over the event stream. */
function onNotification(ev) {
  state.inbox.unshift({ id: ev.id, title: ev.title, body: ev.body, kind: ev.kind, data: ev.data, created_at: ev.created_at, read: 0 });
  state.unreadCount = ev.unread; paintBell();
  const cfg = state.config.notifications;
  toast(ev.body ? `${ev.title}: ${ev.body}` : ev.title, "", ev.kind === "reminder"
    ? { label: "Snooze 10 min", run: () => api.post(`/api/notifications/${ev.id}/snooze`, { minutes: 10 }).then(loadInbox) } : null);
  if (ev.quiet) return;
  if (ev.speak_text && cfg.speak && state.config.voice.speak_summary && (!ev.speak_for || ev.speak_for === CLIENT_ID)) speaker.speak(ev.speak_text);
  if (cfg.browser && "Notification" in window && Notification.permission === "granted" && document.hidden) {
    const n = new Notification(ev.title, { body: ev.body, tag: `diatonic-${ev.id}` });
    n.onclick = () => { window.focus(); n.close(); };
  }
}

export function initInbox({ onOpenChat }) {
  openChat = onOpenChat;
  setIcon($("#bell-icon"), "bell", 18);
  $("#bell").addEventListener("click", (e) => {
    e.stopPropagation();
    const p = pop(); p.classList.toggle("hidden");
    if (!p.classList.contains("hidden")) { renderList(); loadInbox(); }
  });
  document.addEventListener("click", (e) => { if (!pop().contains(e.target) && !$("#bell").contains(e.target)) pop().classList.add("hidden"); });
  addEventListener("keydown", (e) => { if (e.key === "Escape") pop().classList.add("hidden"); });
  return { onNotification };
}

export async function askBrowserPermission() {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission === "default" ? Notification.requestPermission() : Notification.permission;
}
