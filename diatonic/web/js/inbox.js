// Notification inbox: the bell, the popover, live toasts, and the optional spoken / browser channels.
import { $, h, toast, relTime } from "./dom.js";
import { icon, setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit, CLIENT_ID } from "./store.js";
import { speaker } from "./voice.js";

let openChat = () => {};
const pop = () => $("#inbox");

export function buildFaviconSvg(count = 0) {
  const base = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='8' fill='#7c9cff'/><path d='M8 12h16M8 16h16M8 20h16' stroke='#0e0f13' stroke-width='2' stroke-linecap='round'/><circle cx='20' cy='16' r='3' fill='#0e0f13'/>";
  if (!count || count <= 0) return `${base}</svg>`;
  const text = count > 9 ? "9+" : String(count);
  const badge = text.length > 1
    ? `<rect x='13' y='1.5' width='18' height='15' rx='7.5' fill='#ef4444' stroke='#ffffff' stroke-width='1.5'/><text x='22' y='12' font-family='system-ui, -apple-system, sans-serif' font-size='9' font-weight='bold' fill='#ffffff' text-anchor='middle'>${text}</text>`
    : `<circle cx='23' cy='9' r='7.5' fill='#ef4444' stroke='#ffffff' stroke-width='1.5'/><text x='23' y='12.5' font-family='system-ui, -apple-system, sans-serif' font-size='10' font-weight='bold' fill='#ffffff' text-anchor='middle'>${text}</text>`;
  return `${base}${badge}</svg>`;
}

export function updateFavicon(count = 0) {
  if (typeof document === "undefined") return;
  const svg = buildFaviconSvg(count);
  const url = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  let link = document.querySelector("link[rel~='icon']");
  if (!link) {
    link = document.createElement("link");
    link.rel = "icon";
    document.head.appendChild(link);
  }
  if (link.getAttribute("href") === url) return;
  link.href = url;
  link.remove();
  document.head.appendChild(link);
}

function paintBell() {
  const b = $("#bell-badge");
  b.hidden = !state.unreadCount;
  b.textContent = state.unreadCount > 9 ? "9+" : state.unreadCount || "";
  updateFavicon(state.unreadCount);
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
  if (n.kind === "approval" && n.data?.approval_id) {
    const decide = async (ok) => {
      try {
        const res = await api.approve(n.data.approval_id, ok);
        if (res.ok) {
          toast(ok ? "Approved" : "Rejected");
          await api.del(`/api/notifications/${n.id}`);
        } else {
          toast("This approval is no longer pending", "warn");
        }
      } catch {
        toast("Couldn't send your decision", "error");
      }
      await loadInbox();
    };
    acts.push(
      h("button", { class: "btn sm primary", onclick: (e) => { e.stopPropagation(); decide(true); } }, "Approve"),
      h("button", { class: "btn sm", onclick: (e) => { e.stopPropagation(); decide(false); } }, "Reject"));
  }
  if (n.data?.conversation_id) acts.push(h("button", { class: "btn sm", onclick: () => { pop().classList.add("hidden"); openChat(n.data.conversation_id); } }, "Open chat"));
  acts.push(h("button", { class: "btn sm", onclick: async () => { await api.del(`/api/notifications/${n.id}`); await loadInbox(); } }, n.kind === "reminder" ? "Done" : "Dismiss"));
  return h("div", { class: `inbox-item ${n.read ? "" : "unread"}`, onclick: async () => { if (!n.read) { await api.post(`/api/notifications/${n.id}/read`); n.read = 1; await loadInbox(); } } },
    h("div", { class: "i-top" }, h("span", { class: `i-kind ${n.kind}`, html: icon(n.kind === "reminder" ? "bell" : n.kind === "job" ? "clock" : n.kind === "approval" ? "shield" : "check", 14) }),
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
  document.addEventListener("click", (e) => {
    const p = pop(), b = $("#bell");
    if (p && b && !p.contains(e.target) && !b.contains(e.target)) p.classList.add("hidden");
  });
  addEventListener("keydown", (e) => { if (e.key === "Escape") pop().classList.add("hidden"); });
  return { onNotification };
}

export async function askBrowserPermission() {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission === "default" ? Notification.requestPermission() : Notification.permission;
}
