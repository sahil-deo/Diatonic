import { $, h, esc, toast } from "./dom.js";
import { icon, setIcon } from "./icons.js";
import { api, streamChat } from "./api.js";
import { render as md } from "./md.js";
import { state, emit } from "./store.js";
import { speaker } from "./voice.js";

const msgs = $("#messages");
let abort = null;
const pending = []; // approvals awaiting a decision: {id, resolve}

/* ---------- empty state ---------- */
const SUGGESTIONS = {
  gcal: [["Calendar", "What's on my calendar today?"], ["Calendar", "When am I free tomorrow afternoon for 45 minutes?"]],
  github: [["GitHub", "Show my open issues assigned to me"], ["GitHub", "What pull requests need my attention?"]],
};
function renderEmpty() {
  const apps = state.apps.filter((a) => a.enabled);
  const chips = apps.flatMap((a) => SUGGESTIONS[a.id] || []).slice(0, 4);
  msgs.replaceChildren(h("div", { class: "empty" },
    h("h2", {}, greeting()),
    h("p", {}, apps.length ? `Connected: ${apps.map((a) => a.name).join(", ")}. Ask in text or hold the mic.` : "Enable an app in Settings to get started."),
    h("div", { class: "suggestions" }, chips.map(([k, t]) => h("button", { class: "suggestion", onclick: () => send(t) }, h("small", {}, k), t)))));
}
const greeting = () => { const hr = new Date().getHours(); return hr < 5 ? "Burning the midnight oil?" : hr < 12 ? "Good morning." : hr < 18 ? "Good afternoon." : "Good evening."; };

/* ---------- message building ---------- */
const nearBottom = () => msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 140;
const scroll = (force) => { if (force || nearBottom()) msgs.scrollTo({ top: msgs.scrollHeight }); };

function userBubble(text) {
  return h("div", { class: "msg user" }, h("div", { class: "bubble" }, text));
}

function toolCard(t, live) {
  const stateEl = h("span", { class: "tool-state" });
  const meta = h("span", { class: "t-meta" });
  const argsPre = h("pre", {}, JSON.stringify(t.args || {}, null, 2));
  const resPre = h("pre", {}, t.preview || "");
  const resBox = h("div", {}, h("div", { class: "lbl" }, "Result"), resPre);
  const card = h("div", { class: "tool", "data-id": t.id },
    h("div", { class: "tool-head", onclick: () => card.classList.toggle("open") },
      stateEl, h("span", { class: "t-app" }, `${appName(t.app)} ·`), h("span", { class: "t-title" }, t.title), meta),
    h("div", { class: "tool-body" }, h("div", { class: "lbl" }, "Arguments"), argsPre, resBox));
  const api_ = {
    el: card,
    set(status, extra = {}) {
      stateEl.innerHTML = status === "running" ? '<span class="spin"></span>'
        : status === "ok" ? `<span class="ok-ic">${icon("check", 16)}</span>`
        : status === "wait" ? `<span class="wait-ic">${icon("shield", 16)}</span>`
        : `<span class="fail-ic">${icon("alert", 16)}</span>`;
      if (extra.ms != null) meta.textContent = extra.ms < 1000 ? `${extra.ms} ms` : `${(extra.ms / 1000).toFixed(1)} s`;
      if (extra.preview != null) resPre.textContent = extra.preview;
      if (status === "denied") meta.textContent = "denied";
    },
  };
  if (!live) api_.set(t.status === "denied" ? "denied" : t.ok === false ? "fail" : "ok", { ms: t.ms });
  return api_;
}
const appName = (id) => state.apps.find((a) => a.id === id)?.name || id || "Tool";

function approvalBox(ev, card, onDone) {
  const box = h("div", { class: `approval ${ev.risk === "destructive" ? "destructive" : ""}` });
  const decide = (ok) => {
    const i = pending.findIndex((p) => p.id === ev.id); if (i >= 0) pending.splice(i, 1);
    box.remove();
    api.approve(ev.id, ok).catch(() => toast("Couldn't send your decision", "error"));
    onDone(ok);
  };
  box.append(
    h("div", { class: "a-top", html: `${icon("shield", 15)} ${ev.risk === "destructive" ? "Needs approval · irreversible" : "Needs approval"}` }),
    h("div", { class: "a-what" }, ev.describe || ev.title),
    h("div", { class: "a-btns" },
      h("button", { class: "btn primary", onclick: () => decide(true) }, "Approve", h("kbd", {}, "⌘↵")),
      h("button", { class: "btn", onclick: () => decide(false) }, "Deny", h("kbd", {}, "Esc"))));
  pending.push({ id: ev.id, decide });
  return box;
}

addEventListener("keydown", (e) => {
  if (!pending.length) return;
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); pending[0].decide(true); }
  else if (e.key === "Escape") { e.preventDefault(); pending[0].decide(false); }
});

/* ---------- sending ---------- */
export async function send(text) {
  text = text.trim();
  if (!text || state.running) return;
  if (!state.health?.agent?.ok) toast(state.health?.agent?.error || "The agent isn't reachable.", "error");
  speaker.stop();
  if (!msgs.querySelector(".msg")) msgs.replaceChildren();
  msgs.append(userBubble(text));
  const body = h("div", { class: "body md streaming" });
  const wrap = h("div", { class: "msg assistant" }, body);
  msgs.append(wrap); scroll(true);

  let seg = null, segText = "", raf = 0;
  const cards = new Map();
  const paint = () => { raf = 0; seg.innerHTML = md(segText); scroll(); };
  const newSeg = () => { seg = h("div", { class: "md cursor" }); segText = ""; wrap.append(seg); };
  body.remove(); newSeg(); // first segment replaces the placeholder body

  state.running = true; emit("running", true);
  abort = new AbortController();
  const speakOn = () => state.config.voice.speak_summary;
  const rules = () => state.config.voice.speak_when;

  try {
    await streamChat({ conversation_id: state.convId, message: text, speak: speakOn() }, (ev) => {
      switch (ev.type) {
        case "conversation":
          state.convId = ev.id; $("#chat-title").textContent = ev.title; emit("conversations-changed"); break;
        case "token":
          if (!seg) newSeg();
          segText += ev.text; if (!raf) raf = requestAnimationFrame(paint); break;
        case "tool_call": {
          if (seg) { seg.classList.remove("cursor"); if (!segText.trim()) seg.remove(); }
          seg = null;
          const c = toolCard(ev, true); cards.set(ev.id, c); wrap.append(c.el);
          if (ev.needs_approval) {
            c.set("wait");
            wrap.append(approvalBox(ev, c, (ok) => { if (!ok) c.set("denied"); else c.set("running"); }));
          } else c.set("running");
          scroll(); break;
        }
        case "tool_result": {
          const c = cards.get(ev.id);
          c?.set(ev.ok ? "ok" : (ev.preview === "Denied by you" ? "denied" : "fail"), { ms: ev.ms, preview: ev.preview }); break;
        }
        case "notify":
          if (speakOn() && rules().approvals && !(rules().only_unfocused && document.hasFocus())) speaker.speak(ev.text);
          break;
        case "summary":
          if (speakOn() && !(rules().only_unfocused && document.hasFocus())) speaker.speak(ev.text);
          break;
        case "error":
          wrap.append(h("div", { class: "err" }, ev.message)); scroll(); break;
        case "message":
          if (seg) { cancelAnimationFrame(raf); seg.innerHTML = md(segText); seg.classList.remove("cursor"); }
          break;
      }
    }, abort.signal);
  } catch (e) {
    if (e.name !== "AbortError") wrap.append(h("div", { class: "err" }, e.message));
  } finally {
    seg?.classList.remove("cursor");
    pending.splice(0).forEach((p) => api.approve(p.id, false).catch(() => {}));
    wrap.querySelectorAll(".approval").forEach((a) => a.remove());
    state.running = false; abort = null; emit("running", false); emit("conversations-changed");
  }
}

export function stop() { abort?.abort(); }

/* ---------- conversations ---------- */
export async function newChat() {
  if (state.running) stop();
  state.convId = null; $("#chat-title").textContent = "New chat"; renderEmpty(); emit("conversations-changed"); $("#input").focus();
}

export async function openConversation(id) {
  if (state.running) stop();
  const c = await api.conversation(id);
  state.convId = id; $("#chat-title").textContent = c.title;
  msgs.replaceChildren();
  for (const m of c.messages) {
    if (m.role === "user") msgs.append(userBubble(m.content));
    else {
      const wrap = h("div", { class: "msg assistant" });
      (m.meta.tools || []).forEach((t) => wrap.append(toolCard(t, false).el));
      if (m.content) wrap.append(h("div", { class: "md", html: md(m.content) }));
      if (m.meta.error) wrap.append(h("div", { class: "err" }, m.meta.error));
      msgs.append(wrap);
    }
  }
  if (!c.messages.length) renderEmpty();
  scroll(true); emit("conversations-changed");
}

export async function removeConversation(id) {
  await api.deleteConversation(id);
  if (state.convId === id) await newChat(); else emit("conversations-changed");
}

export { renderEmpty };
