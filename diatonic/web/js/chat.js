// Chat view. Runs live on the server, so any number of chats can work at once; this module
// renders the one you're looking at and tracks status/notifications for the rest.
import { $, h, toast } from "./dom.js";
import { icon } from "./icons.js";
import { api, streamEvents } from "./api.js";
import { render as md } from "./md.js";
import { state, emit, CLIENT_ID } from "./store.js";
import { speaker } from "./voice.js";
import { highlight } from "./highlight.js";
import { renderHome } from "./home.js";

const msgs = $("#messages");
let view = null;        // live rendering state for the visible conversation: {cid, wrap, seg, ...}
let sending = false;    // a POST /api/chat is in flight
let early = [];         // events that arrived before the POST returned our conversation id
let pending = [];       // approval boxes in the visible conversation: {id, decide}

export const busy = () => sending || !!(state.convId && state.runs[state.convId]);

/* ---------- empty state ---------- */
const SUGGESTIONS = {
  gcal: [["Calendar", "What's on my calendar today?"], ["Calendar", "When am I free tomorrow afternoon for 45 minutes?"]],
  github: [["GitHub", "Show my open issues assigned to me"], ["GitHub", "What pull requests need my attention?"]],
};
const greeting = () => { const hr = new Date().getHours(); return hr < 5 ? "Burning the midnight oil?" : hr < 12 ? "Good morning." : hr < 18 ? "Good afternoon." : "Good evening."; };
function renderEmpty() {
  const apps = state.apps.filter((a) => a.enabled);
  const chips = apps.flatMap((a) => SUGGESTIONS[a.id] || []).slice(0, 4);
  view = null; pending = [];
  const suggestions = () => h("div", { class: "suggestions" }, chips.map(([k, t]) => h("button", { class: "suggestion", onclick: () => send(t) }, h("small", {}, k), t)));
  const home = h("div", { class: "home-wrap" });
  msgs.replaceChildren(h("div", { class: "empty" }, h("h2", {}, greeting()),
    !apps.length && h("p", {}, "Enable an app in Settings to get started."), home));
  renderHome(home, suggestions);   // tiles from your apps, or the suggestions, as chosen under Customize
}

/* ---------- rendering ---------- */
const nearBottom = () => msgs.scrollHeight - msgs.scrollTop - msgs.clientHeight < 140;
const scroll = (force, smooth = false) => {
  if (force || nearBottom()) {
    try { msgs.scrollTo({ top: msgs.scrollHeight, behavior: smooth ? "smooth" : "instant" }); }
    catch { msgs.scrollTop = msgs.scrollHeight; }
  }
};
const appName = (id) => state.apps.find((a) => a.id === id)?.name || id || "Tool";
const MENTION = /(?<![\w/@])@(?:"([^"]+)"|(\S+))/g;
function userBubble(text) {
  const parts = []; let last = 0, m;
  MENTION.lastIndex = 0;
  while ((m = MENTION.exec(text))) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const raw = m[1] || m[2], trail = raw.match(/[.,;:!?)]+$/)?.[0] || "", path = raw.slice(0, raw.length - trail.length).replace(/:\d+(?:-\d+)?$/, "");
    parts.push(h("button", { class: "file-chip", type: "button", title: "Open in Files", onclick: () => { state.openFile = path; emit("open-file", path); } }, "@" + raw.slice(0, raw.length - trail.length)), trail);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return h("div", { class: "msg user" }, h("div", { class: "bubble" }, ...parts));
}

const ANSI = /\x1b\[[0-9;?]*[ -\/]*[@-~]|\x1b\][^\x07]*\x07|\r(?!\n)/g;
/** A shell command's card: the command in the header, its output streaming in like a terminal. */
function shellCard(t, live) {
  const stateEl = h("span", { class: "tool-state" }), meta = h("span", { class: "t-meta" });
  const out = h("pre", { class: "term-out" });
  let streamed = "";
  const fromResult = (preview) => { try { const r = JSON.parse(preview); return r.output ?? preview; } catch { return preview || ""; } };
  const card = h("div", { class: `tool shellcmd ${live ? "open" : ""}`, "data-id": t.id },   // not "shell": that's the app's own grid
    h("div", { class: "tool-head", onclick: () => card.classList.toggle("open") },
      stateEl, h("span", { class: "t-app" }, "Terminal ·"), h("code", { class: "t-cmd" }, t.args?.command || ""), meta),
    h("div", { class: "tool-body" }, t.args?.cwd ? h("div", { class: "lbl" }, t.args.cwd) : null, out));
  if (!live && t.preview) out.textContent = fromResult(t.preview);
  const c = {
    el: card,
    stream(text) { streamed = (streamed + text.replace(ANSI, "")).slice(-200000); out.textContent = streamed; out.scrollTop = out.scrollHeight; },
    set(status, extra = {}) {
      stateEl.innerHTML = status === "running" ? '<span class="spin"></span>'
        : status === "ok" ? `<span class="ok-ic">${icon("check", 16)}</span>`
        : status === "wait" ? `<span class="wait-ic">${icon("shield", 16)}</span>`
        : `<span class="fail-ic">${icon("alert", 16)}</span>`;
      if (extra.preview != null) {
        let r = null; try { r = JSON.parse(extra.preview); } catch {}
        if (!streamed) out.textContent = r ? (r.output ?? "") : extra.preview;
        const bits = [r && r.exit_code != null ? `exit ${r.exit_code}` : null, extra.ms != null ? (extra.ms < 1000 ? `${extra.ms} ms` : `${(extra.ms / 1000).toFixed(1)} s`) : null];
        meta.textContent = bits.filter(Boolean).join(" · ");
        if (r && r.exit_code === 0 && status === "ok") card.classList.remove("open");   // keep failures open
      }
      if (status === "denied") meta.textContent = "denied";
    },
  };
  if (!live) c.set(t.status === "denied" ? "denied" : t.ok === false ? "fail" : "ok", { ms: t.ms, preview: t.preview || "" });   // exit code too
  return c;
}

function toolCard(t, live) {
  if (t.name === "shell_run") return shellCard(t, live);
  const stateEl = h("span", { class: "tool-state" }), meta = h("span", { class: "t-meta" });
  const resPre = h("pre", {}, t.preview || "");
  const card = h("div", { class: "tool", "data-id": t.id },
    h("div", { class: "tool-head", onclick: () => card.classList.toggle("open") },
      stateEl, h("span", { class: "t-app" }, `${appName(t.app)} ·`), h("span", { class: "t-title" }, t.title), meta),
    h("div", { class: "tool-body" }, h("div", { class: "lbl" }, "Arguments"), h("pre", {}, JSON.stringify(t.args || {}, null, 2)),
      h("div", { class: "lbl" }, "Result"), resPre));
  const c = {
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
  if (!live) c.set(t.status === "denied" ? "denied" : t.ok === false ? "fail" : "ok", { ms: t.ms });
  return c;
}

const AsyncFn = Object.getPrototypeOf(async function () {}).constructor;
/** For a card that installs interface code: say so if the code won't even parse, before anyone approves it. */
export function codeProblem(ev) {
  if (ev.name !== "ui_add_script") return null;
  try { new AsyncFn("diatonic", "el", "ctx", ev.args?.code || ""); return null; } catch (e) { return `This code has a syntax error (${e.message}). Deny it and ask the agent to fix it.`; }
}
function approvalBox(ev, v) {
  const bad = codeProblem(ev);
  const decide = (ok, scope) => api.approve(ev.id, ok, scope).catch(() => toast("Couldn't send your decision", "error"));
  const cls = `approval ${ev.risk === "destructive" ? "destructive" : ""} ${ev.share ? "share" : ""}`.trim();
  const box = h("div", { class: cls },
    h("div", { class: "a-top", html: `${icon("shield", 15)} ${ev.share ? "Share private data with a cloud model?" : ev.risk === "destructive" ? "Needs approval · irreversible" : "Needs approval"}` }),
    h("div", { class: "a-what" }, ev.describe || ev.title),
    ev.preview ? h("pre", { class: "diff", html: highlight(ev.preview, "diff") }) : null,
    bad ? h("div", { class: "err" }, bad) : null,
    h("div", { class: "a-btns" },
      h("button", { class: "btn primary", onclick: () => decide(true) }, ev.share ? "Allow once" : "Approve", h("kbd", {}, "⌘↵")),
      ev.share && h("button", { class: "btn", onclick: () => decide(true, "chat") }, "Allow for this chat"),
      ev.job && h("button", { class: "btn", title: "Run it now, and let this scheduled job run this exact command unattended from now on", onclick: () => decide(true, "job") }, "Always allow for this job"),
      h("button", { class: "btn", onclick: () => decide(false) }, "Deny", h("kbd", {}, "Esc"))));
  v.approvals.set(ev.id, box);
  pending.push({ id: ev.id, decide });
  return box;
}
function dropApproval(v, id) {
  v.approvals.get(id)?.remove(); v.approvals.delete(id);
  pending = pending.filter((p) => p.id !== id);
}

addEventListener("keydown", (e) => {
  if (!pending.length || document.activeElement?.closest?.(".xterm")) return;   // Esc in vim must not deny an approval
  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); pending[0].decide(true); }
  else if (e.key === "Escape" && !document.querySelector("dialog[open]")) { e.preventDefault(); pending[0].decide(false); }
});

const k = (n) => (n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n));
function perfLine(p) {
  if (!p?.ms) return null;
  const bits = [p.ms < 950 ? `${p.ms} ms` : `${(p.ms / 1000).toFixed(1)} s`, p.prompt_tokens ? `${k(p.prompt_tokens)} in` : null,
    p.cache_pct != null ? `cache ~${p.cache_pct}%` : null, p.completion_tokens ? `${k(p.completion_tokens)} out` : null,
    p.model_calls > 1 ? `${p.model_calls} model calls` : null].filter(Boolean);
  return h("div", { class: "runmeta", title: `First output ${p.ttft_ms != null ? p.ttft_ms + " ms" : "n/a"} · open the Telemetry panel for details` }, bits.join(" · "));
}

/* ---------- live view ---------- */
function newSeg(v) { v.seg = h("div", { class: "md cursor" }); v.segText = ""; v.wrap.append(v.seg); }
function paint(v) { v.raf = 0; if (v.seg) { v.seg.innerHTML = md(v.segText); scroll(); } }

function newView(cid) {
  const v = { cid, wrap: h("div", { class: "msg assistant" }), seg: null, segText: "", raf: 0, cards: new Map(), approvals: new Map(), lastSeq: 0, live: true, thoughtBox: null, thoughtBody: null, thoughtText: "", thoughtSummary: null };
  msgs.append(v.wrap); newSeg(v);
  view = v; pending = [];
  return v;
}

/** Render one run event into the view. Pure rendering: no speech, no side effects outside the DOM. */
function applyEvent(v, ev) {
  v.lastSeq = Math.max(v.lastSeq, ev.seq || 0);
  queueMicrotask(() => emit("view-event", { ev, v }));   // the bottom dock mirrors what this conversation is saying
  switch (ev.type) {
    case "thought": {
      if (!v.thoughtBox) {
        v.thoughtSummary = h("summary", { class: "thought-summary" }, "Thinking…");
        v.thoughtBody = h("div", { class: "thought-body md" });
        v.thoughtBox = h("details", { class: "thought-box", open: true }, v.thoughtSummary, v.thoughtBody);
        v.wrap.prepend(v.thoughtBox);
      }
      v.thoughtText = (v.thoughtText || "") + ev.text;
      v.thoughtBody.innerHTML = md(v.thoughtText);
      scroll(); break;
    }
    case "title": {
      if (v.cid && ev.title) {
        const titleEl = $("#chat-title");
        if (titleEl) titleEl.textContent = ev.title;
        if (state.runs[v.cid]) state.runs[v.cid].title = ev.title;
        const conv = (state.conversations || []).find((c) => c.id === v.cid);
        if (conv) conv.title = ev.title;
        emit("conversations-changed");
      }
      break;
    }
    case "token":
      if (v.thoughtBox && v.thoughtBox.open) {
        v.thoughtBox.open = false;
        if (v.thoughtSummary) v.thoughtSummary.textContent = "Thought process";
      }
      if (!v.seg) newSeg(v);
      v.segText += ev.text; if (!v.raf) v.raf = requestAnimationFrame(() => paint(v)); break;
    case "rewrite": // the server cut a runaway reply: replace this step's text
      if (!v.seg) newSeg(v);
      cancelAnimationFrame(v.raf); v.segText = ev.text; v.seg.innerHTML = md(v.segText); break;
    case "guard": v.wrap.append(h("div", { class: "guard", html: `${icon("shield", 13)} ` }, ev.message)); scroll(); break;
    case "tool_call": {
      if (v.seg) { v.seg.classList.remove("cursor"); if (!v.segText.trim()) v.seg.remove(); }
      v.seg = null;
      const c = toolCard(ev, true); v.cards.set(ev.id, c); v.wrap.append(c.el);
      if (ev.needs_approval) { c.set("wait"); v.wrap.append(approvalBox(ev, v)); } else c.set("running");
      scroll(); break;
    }
    case "tool_status":
      dropApproval(v, ev.id);
      v.cards.get(ev.id)?.set(ev.status === "denied" ? "denied" : "running"); break;
    case "tool_result":
      dropApproval(v, ev.id);
      v.cards.get(ev.id)?.set(ev.ok ? "ok" : (ev.preview === "Denied by you" ? "denied" : "fail"), { ms: ev.ms, preview: ev.preview }); break;
    case "error": v.wrap.append(h("div", { class: "err" }, ev.message)); scroll(); break;
    case "message":
      if (v.seg) { cancelAnimationFrame(v.raf); v.seg.innerHTML = md(v.segText); v.seg.classList.remove("cursor"); }
      break;
    case "cancelled": {   // a card still waiting for approval is void now: say so instead of leaving it looking open
      const waiting = [...v.approvals.keys()];
      waiting.forEach((id) => { v.cards.get(id)?.set("denied"); dropApproval(v, id); });
      v.wrap.append(h("div", { class: "stopped" }, waiting.length ? "Stopped. Nothing was changed; the pending approval was cancelled." : "Stopped.")); break;
    }
    case "done":
      cancelAnimationFrame(v.raf);
      if (v.thoughtBox && v.thoughtBox.open) {
        v.thoughtBox.open = false;
        if (v.thoughtSummary) v.thoughtSummary.textContent = "Thought process";
      }
      if (v.seg) { v.seg.innerHTML = md(v.segText); v.seg.classList.remove("cursor"); if (!v.segText.trim()) v.seg.remove(); }
      [...v.approvals.keys()].forEach((id) => dropApproval(v, id));
      { const pl = perfLine(ev.perf); if (pl) v.wrap.append(pl); }
      v.live = false; break;
  }
}

function replay(v, events) {
  for (const ev of events) if (ev.seq > v.lastSeq) applyEvent(v, ev);
  if (v.raf) { cancelAnimationFrame(v.raf); paint(v); }
}

/* ---------- sending ---------- */
export async function send(text) {
  text = text.trim();
  if (!text) return;
  if (busy()) return toast("This chat is still working. Stop it, or start a new chat to run things in parallel.");
  if (!state.health?.agent?.ok) toast(state.health?.agent?.error || "The agent isn't reachable.", "error");
  speaker.stop();
  if (!msgs.querySelector(".msg")) msgs.replaceChildren();
  const bubble = userBubble(text); msgs.append(bubble);
  const v = newView(null);
  sending = true; early = []; emit("runs-changed"); scroll(true);
  try {
    const sc = state.screenContext?.(), screen = sc && state.screenOff !== sc.ref ? sc : null;   // what "this" means
    const r = await api.chat({ conversation_id: state.convId, message: text, speak: state.config.voice.speak_summary, screen });
    if (view !== v) return; // you moved to another chat while the request was in flight; the run continues in the background
    state.convId = v.cid = r.conversation_id;
    $("#chat-title").textContent = r.title;
    state.runs[v.cid] = { status: r.run.status, title: r.title };
    if (r.attached?.length) bubble.querySelector(".bubble").append(h("div", { class: "attach-note" }, "Attached: " + r.attached.map((a) => a.path + (a.chars ? ` (${(a.chars / 1000).toFixed(1)}k chars)` : "")).join(", ")));
    replay(v, r.run.events);
    early.forEach((ev) => ev.conversation_id === v.cid && ev.seq > v.lastSeq && applyEvent(v, ev));
  } catch (e) {
    if (view === v) { v.wrap.remove(); bubble.remove(); view = null; if (!msgs.querySelector(".msg")) renderEmpty(); emit("prefill", text); }
    toast(e.message, "error");
  } finally {
    sending = false; early = []; emit("runs-changed"); emit("conversations-changed");
  }
}

export async function stop() {
  const cid = state.convId;
  if (cid && state.runs[cid]) await api.cancel(cid).catch(() => {});
}

/* ---------- global event stream ---------- */
const titleOf = (cid) => state.runs[cid]?.title || state.conversations.find((c) => c.id === cid)?.title || "a chat";
const short = (s) => (s.length > 40 ? s.slice(0, 40) + "…" : s);

function shouldSpeak(kind) {
  const v = state.config.voice;
  if (!v.speak_summary) return false;
  if (kind === "notify" && !v.speak_when.approvals) return false;
  return !(v.speak_when.only_unfocused && document.hasFocus());
}

function onEvent(ev) {
  if (ev.type === "hello") return resync(ev.runs);
  if (ev.type === "tool_output") {   // a running shell command's output, live
    if (view && view.cid === ev.conversation_id) view.cards.get(ev.id)?.stream?.(ev.text);
    return;
  }
  if (ev.type === "ui") return ev.target === CLIENT_ID ? emit("ui", ev) : undefined;   // screen control from a run this window started
  if (["notification", "app_changed", "custom_app_changed", "mods_changed", "keymap_changed", "model_changed", "script_output", "script_done", "perm_ask", "perm_ask_done", "tool_ask", "app_event"].includes(ev.type)) return emit(ev.type, ev); // not tied to a chat
  const cid = ev.conversation_id;
  const title = ev.type === "run_start" ? ev.title : titleOf(cid); // other events' `title` is a tool title

  // status bookkeeping for the sidebar / status bar
  if (ev.type === "done") delete state.runs[cid]; else state.runs[cid] = { status: ev.status, title };
  if (ev.type === "run_start" || ev.type === "done") emit("conversations-changed");
  emit("runs-changed");

  // render into the visible conversation
  const viewed = view && state.convId === cid;
  if (sending && view && !view.cid) early.push(ev);
  else if (viewed && ev.seq > view.lastSeq) applyEvent(view, ev);

  // speech and notifications (live events only; replays never speak)
  const where = viewed ? "" : `In ${short(title)}: `;
  const mine = !ev.speak_for || ev.speak_for === CLIENT_ID;   // several tabs may be open; only one speaks
  if (ev.type === "notify" && mine && shouldSpeak("notify")) speaker.speak(where + ev.text);
  if (ev.type === "summary" && mine && shouldSpeak("summary")) speaker.speak(where + ev.text);
  if (!viewed) {
    if (ev.type === "tool_call" && ev.needs_approval) toast(`“${short(title)}” needs your approval`, "", { label: "Open", run: () => openConversation(cid) });
    if (ev.type === "done") { state.unread.add(cid); emit("runs-changed"); toast(`“${short(title)}” finished`, "", { label: "Open", run: () => openConversation(cid) }); }
  }
}

async function resync(runList) {
  state.runs = Object.fromEntries(runList.map((r) => [r.conversation_id, { status: r.status, title: r.title }]));
  emit("runs-changed");
  const v = view;
  if (!v?.cid || !v.live) return;
  // We may have missed events while disconnected: catch up, or reload if the run ended meanwhile.
  const c = await api.conversation(v.cid).catch(() => null);
  if (!c || view !== v) return;
  if (c.run) replay(v, c.run.events); else openConversation(v.cid);
}

export function startEventStream() {
  (async function loop() {
    let delay = 500;
    for (;;) {
      try { await streamEvents((ev) => { delay = 500; onEvent(ev); }); } catch { /* server down: retry */ }
      await new Promise((r) => setTimeout(r, delay));
      delay = Math.min(delay * 2, 8000);
    }
  })();
}

/* ---------- conversations ---------- */
export function newChat() {
  state.convId = null; $("#chat-title").textContent = "New chat";
  renderEmpty(); emit("runs-changed"); emit("conversations-changed");
  import("./panel.js").then((m) => {
    m.openPanel("chat");
    setTimeout(() => $("#input")?.focus(), 50);
  });
}

export async function openConversation(id) {
  const c = await api.conversation(id);
  state.convId = id; state.unread.delete(id);
  $("#chat-title").textContent = c.title;
  msgs.replaceChildren(); view = null; pending = [];
  for (const m of c.messages) {
    if (m.role === "user") msgs.append(userBubble(m.content));
    else {
      const wrap = h("div", { class: "msg assistant" });
      if (m.meta?.thoughts?.length) {
        const thoughtText = m.meta.thoughts.join("\n\n");
        wrap.append(h("details", { class: "thought-box" },
          h("summary", { class: "thought-summary" }, "Thought process"),
          h("div", { class: "thought-body md", html: md(thoughtText) })));
      }
      (m.meta.tools || []).forEach((t) => wrap.append(toolCard(t, false).el));
      if (m.content) wrap.append(h("div", { class: "md", html: md(m.content) }));
      if (m.meta.error) wrap.append(h("div", { class: "err" }, m.meta.error));
      (m.meta.guards || []).forEach((g) => wrap.append(h("div", { class: "guard", html: `${icon("shield", 13)} ` }, g)));
      if (m.meta.stopped) wrap.append(h("div", { class: "stopped" }, "Stopped."));
      { const pl = perfLine(m.meta.perf); if (pl) wrap.append(pl); }
      msgs.append(wrap);
    }
  }
  if (c.run) { const v = newView(id); replay(v, c.run.events); }
  else if (!c.messages.length) renderEmpty();
  scroll(true); emit("runs-changed"); emit("conversations-changed");
  import("./panel.js").then((m) => {
    m.openPanel("chat");
    setTimeout(() => $("#input")?.focus(), 50);
  });
}

export async function removeConversation(id) {
  await api.deleteConversation(id);
  delete state.runs[id]; state.unread.delete(id);
  if (state.convId === id) newChat(); else emit("conversations-changed");
}

export { renderEmpty };
