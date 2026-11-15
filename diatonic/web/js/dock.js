// The always-there agent bar. When an app fills the window the chat column is hidden, so this bar (bottom of the window)
// carries the conversation: type or speak, see the latest reply, and approve or deny without leaving the app.
import { $, h } from "./dom.js";
import { icon, setIcon } from "./icons.js";
import { api } from "./api.js";
import { render as md } from "./md.js";
import { state, on } from "./store.js";
import * as chat from "./chat.js";
import { codeProblem } from "./chat.js";

let input, send, dot, reply, asks, text = "";

export const dockVisible = () => !!input && getComputedStyle($("#dock")).display !== "none";
export const focusDock = () => input?.focus();
export function setDockInput(t) { input.value = input.value.trim() ? `${input.value.trim()} ${t}` : t; autosize(); paint(); input.focus(); }

function autosize() { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 120) + "px"; }
function paint() {
  const running = chat.busy(), waiting = Object.values(state.runs).some((r) => r.status === "waiting");
  send.classList.toggle("stop", running); send.disabled = !running && !input.value.trim();
  setIcon(send, running ? "stop" : "send", 18); send.setAttribute("aria-label", running ? "Stop" : "Send");
  dot.className = `dot ${waiting ? "warn" : running ? "ok pulse" : ""}`;
  dot.title = waiting ? "Waiting for your approval" : running ? "Working…" : "Idle";
}
function paintReply() {
  reply.replaceChildren(...(text ? [h("button", { class: "icon-btn sm dock-x", type: "button", "aria-label": "Dismiss", title: "Dismiss", onclick: () => { text = ""; paintReply(); }, html: icon("x", 14) }),
    h("div", { class: "md", html: md(text) })] : []));
  reply.classList.toggle("on", !!text);
  reply.scrollTop = reply.scrollHeight;
}
function ask(ev) {
  const decide = (ok) => api.approve(ev.id, ok).catch(() => {});
  asks.append(h("div", { class: `approval dock-ask ${ev.risk === "destructive" ? "destructive" : ""}`, "data-id": ev.id },
    h("div", { class: "a-top", html: `${icon("shield", 15)} ${ev.share ? "Share private data with a cloud model?" : ev.risk === "destructive" ? "Needs approval · irreversible" : "Needs approval"}` }),
    h("div", { class: "a-what" }, ev.describe || ev.title),
    ev.preview ? h("pre", { class: "diff" }, ev.preview.slice(0, 1200)) : null,
    codeProblem(ev) ? h("div", { class: "err" }, codeProblem(ev)) : null,
    h("div", { class: "a-btns" }, h("button", { class: "btn primary", type: "button", onclick: () => decide(true) }, ev.share ? "Allow once" : "Approve", h("kbd", {}, "⌘↵")),
      ev.share && h("button", { class: "btn", type: "button", onclick: () => api.approve(ev.id, true, "chat").catch(() => {}) }, "Allow for this chat"),
      ev.job && h("button", { class: "btn", type: "button", onclick: () => api.approve(ev.id, true, "job").catch(() => {}) }, "Always allow for this job"),
      h("button", { class: "btn", type: "button", onclick: () => decide(false) }, "Deny", h("kbd", {}, "Esc")))));
}
const clearAsk = (id) => (id ? asks.querySelector(`[data-id="${id}"]`)?.remove() : asks.replaceChildren());

function submit() {
  if (chat.busy()) return chat.stop();
  const t = input.value.trim(); if (!t) return;
  input.value = ""; autosize(); text = ""; paintReply(); paint(); chat.send(t);
}

export function initDock() {
  const dock = $("#dock");
  input = h("textarea", { rows: 1, placeholder: "Tell Diatonic what to do…", "aria-label": "Message the agent" });
  send = h("button", { class: "send-btn", type: "button", onclick: submit });
  dot = h("i", { class: "dot" }); reply = h("div", { class: "dock-reply" }); asks = h("div", { class: "dock-asks" });
  dock.replaceChildren(h("div", { class: "dock-in" }, asks, reply, h("div", { class: "dock-top" }, h("div", { class: "ctx-chip", hidden: true }), h("div", { class: "dock-mods" })),
    h("div", { class: "composer" }, dot, input, h("button", { class: "mic-btn", type: "button", "aria-label": "Voice input", onclick: () => $("#mic").click(), html: icon("mic", 18) }), send)));
  input.addEventListener("input", () => { autosize(); paint(); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); } });
  on("runs-changed", paint);
  on("view-event", ({ ev, v }) => {   // mirrors the conversation being viewed, whether or not its column is on screen
    if (ev.type === "token" || ev.type === "rewrite") { text = v.segText; paintReply(); }
    else if (ev.type === "tool_call" && ev.needs_approval) ask(ev);
    else if (ev.type === "tool_status" || ev.type === "tool_result") clearAsk(ev.id);
    else if (ev.type === "message" || ev.type === "done") { clearAsk(); if (v.segText.trim()) { text = v.segText; paintReply(); } }
    else if (ev.type === "error") { text = ev.message; paintReply(); }
  });
  paint();
}
