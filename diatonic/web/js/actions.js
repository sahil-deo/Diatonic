// Saved prompts and script buttons: the quick-action bar, "/" commands, shortcuts and script output cards.
import { $, h, toast } from "./dom.js";
import { icon } from "./icons.js";
import { api } from "./api.js";
import { state, emit, on } from "./store.js";
import * as chat from "./chat.js";
import { matchesHotkey, prettyHotkey } from "./voice.js";

let hooks = {};
const input = () => $("#input");
const acts = () => state.config?.actions || [];

/* ---------- templates ---------- */
const pad = (n) => String(n).padStart(2, "0");
async function expand(tpl, inputText) {
  const d = new Date();
  const vars = { input: inputText, date: `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`, time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
    day: d.toLocaleDateString([], { weekday: "long" }), datetime: d.toLocaleString() };
  if (/\{\{\s*clipboard\s*\}\}/.test(tpl)) { try { vars.clipboard = await navigator.clipboard.readText(); } catch { vars.clipboard = ""; toast("Couldn't read the clipboard (allow it in the browser prompt)."); } }
  return tpl.replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) => (k in vars ? vars[k] : m));
}
const needsInput = (a) => a.kind === "prompt" ? /\{\{\s*input\s*\}\}/.test(a.prompt) : /\$\{?(1|INPUT)\b/.test(a.command);

function setComposer(text, { send = false } = {}) {
  const el = input();
  if (send && !chat.busy()) { chat.send(text); text = ""; }   // busy chats keep the text so nothing is lost
  else if (send) toast("This chat is still working. The prompt is in the box; send it when it's done.");
  el.value = text; el.dispatchEvent(new Event("input", { bubbles: true })); el.focus();
  el.setSelectionRange(text.length, text.length);
}

function askInput(title, placeholder = "") {
  return new Promise((resolve) => {
    const field = h("input", { type: "text", placeholder, "aria-label": title });
    const d = h("dialog", { class: "modal dialog-small" }, h("div", { class: "body" }, h("strong", {}, title), h("div", { style: "height:10px" }), field,
      h("div", { class: "btns" }, h("button", { class: "btn", onclick: () => d.close("") }, "Cancel"), h("button", { class: "btn primary", onclick: () => d.close(field.value) }, "Run"))));
    d.addEventListener("close", () => { const v = d.returnValue; d.remove(); resolve(v ? v : null); });
    field.addEventListener("keydown", (e) => { if (e.key === "Enter") d.close(field.value || " "); });
    document.body.append(d); d.showModal(); field.focus();
  });
}

/* ---------- running ---------- */
export async function invoke(a, args = "") {
  args = args.trim();
  if (a.kind === "prompt") return runPrompt(a, args);
  return runScript(a, args);
}

async function runPrompt(a, args) {
  if (needsInput(a) && !args) args = (await askInput(a.label, "What should I use for {{input}}?"))?.trim() ?? null;
  if (args === null) return;
  const text = await expand(a.prompt, args);
  if (a.new_chat) chat.newChat();
  setComposer(text, { send: a.send });
}

async function runScript(a, args) {
  if (needsInput(a) && !args) { args = (await askInput(a.label, "Input for the script ($1 / $INPUT)"))?.trim() ?? null; if (args === null) return; }
  if (a.confirm && !confirm(`Run “${a.label}”?\n\n$ ${a.command}`)) return;
  let r;
  try { r = await api.post(`/api/actions/${a.id}/run`, { input: args }); }
  catch (e) {
    if (e.data?.code === "scripts_disabled") toast("Scripts are turned off.", "error", { label: "Turn on…", run: () => hooks.openSettings("automation") });
    else toast(e.message, "error");
    return;
  }
  scriptCard(a, r.run_id);
}

const cards = new Map(), buffered = new Map();
function scriptCard(a, rid) {
  const msgs = $("#messages");
  if (!msgs.querySelector(".msg")) msgs.replaceChildren();
  const out = h("pre", { class: "sc-out" }), status = h("span", { class: "pill" }, "running…");
  const stop = h("button", { class: "btn sm", onclick: () => api.post(`/api/actions/runs/${rid}/cancel`) }, "Stop");
  const send = h("button", { class: "btn sm", disabled: true, onclick: () => emit("prefill", `Output of “${a.label}”:\n\`\`\`\n${out.textContent.slice(-3000).trim()}\n\`\`\`\n`) }, "Send output to the agent");
  const copy = h("button", { class: "btn sm", onclick: () => navigator.clipboard.writeText(out.textContent).then(() => toast("Copied")) }, "Copy");
  const el = h("div", { class: "msg script-card" }, h("div", { class: "sc" },
    h("div", { class: "sc-head" }, h("span", { html: icon("chevron", 14) }), h("strong", {}, a.label), h("span", { class: "muted" }, a.command.split("\n")[0].slice(0, 60)), h("div", { class: "grow" }), status, stop),
    out, h("div", { class: "sc-acts" }, send, copy, h("div", { class: "grow" }), h("button", { class: "btn sm", onclick: () => el.remove() }, "Dismiss"))));
  msgs.append(el); msgs.scrollTo({ top: msgs.scrollHeight });
  const run = { out, status, stop, send, el };
  cards.set(rid, run);
  (buffered.get(rid) || []).forEach((ev) => onScriptEvent(ev)); buffered.delete(rid);
}

function onScriptEvent(ev) {
  const run = cards.get(ev.run_id);
  if (!run) { if (!buffered.has(ev.run_id)) buffered.set(ev.run_id, []); buffered.get(ev.run_id).push(ev); return; }
  if (ev.type === "script_output") {
    const m = $("#messages"), stick = m.scrollHeight - m.scrollTop - m.clientHeight < 160;
    run.out.textContent += ev.text; run.out.scrollTop = run.out.scrollHeight; if (stick) m.scrollTo({ top: m.scrollHeight });
  } else {
    const ok = ev.status === "ok";
    run.status.textContent = `${ev.status}${ev.code != null && !ok ? ` · exit ${ev.code}` : ""} · ${(ev.ms / 1000).toFixed(1)}s`;
    run.status.style.color = ok ? "var(--ok)" : ev.status === "cancelled" ? "var(--muted)" : "var(--danger)";
    run.stop.remove(); run.send.disabled = false;
    if (!run.out.textContent.trim()) run.out.textContent = "(no output)";
    cards.delete(ev.run_id);
  }
}

/* ---------- built-in slash commands ---------- */
const THEMES = ["dark", "light", "system"];
const builtins = () => [
  { name: "new", desc: "Start a new chat", run: () => hooks.newChat() },
  { name: "settings", desc: "Open settings: /settings voice", run: (a) => hooks.openSettings(a.trim() || undefined) },
  { name: "theme", desc: "dark, light or system", run: (a) => THEMES.includes(a.trim()) ? hooks.setTheme(a.trim()) : toast("Use: /theme dark | light | system") },
  { name: "speak", desc: "Spoken summary: on or off", run: (a) => hooks.setSpeak(a.trim() ? a.trim() === "on" : !state.config.voice.speak_summary) },
  { name: "panel", desc: "Open an app: /panel github (or close)", run: (a) => {
    const q = a.trim().toLowerCase();
    if (!q || q === "close") return hooks.closePanel();
    const app = state.apps.find((x) => x.panel && x.enabled && (x.id === q || x.name.toLowerCase().startsWith(q)));
    app ? hooks.openPanel(app.id) : toast(`No app panel called “${q}”.`);
  } },
  { name: "sidebar", desc: "Show or hide the sidebar", run: () => hooks.toggleSidebar() },
  { name: "remember", desc: "Remember a fact: /remember my default repo is …", run: (a) => !a.trim() ? toast("Usage: /remember <something short to remember>")
    : api.post("/api/memories", { text: a.trim(), scope: "global" }).then((r) => toast(r.status === "duplicate" ? "Already remembered" : "Remembered")).catch((e) => toast(e.message, "error")) },
  { name: "forget", desc: "Review and delete what's remembered", run: () => hooks.openSettings("memory") },
  { name: "stop", desc: "Stop the current chat's run", run: () => chat.stop() },
  { name: "actions", desc: "Edit your saved prompts and buttons", run: () => hooks.openSettings("actions") },
  { name: "help", desc: "Show all commands", run: () => setComposer("/") },
].map((c) => ({ ...c, kind: "command" }));

function items() {
  const mine = acts().filter((a) => a.name).map((a) => ({ name: a.name, kind: a.kind, desc: a.description || (a.kind === "prompt" ? a.prompt.slice(0, 70) : "$ " + a.command.slice(0, 70)), icon: a.icon, a }));
  return [...mine, ...builtins()];
}

/* ---------- slash menu ---------- */
let shown = [], sel = 0;
const menu = () => $("#slash");
const parse = () => { const m = input().value.match(/^\/([\w-]*)(?:[ \t]+([\s\S]*))?$/); return m ? { q: m[1].toLowerCase(), args: m[2], typing: m[2] === undefined } : null; };

function updateSlash() {
  const p = parse(), el = menu();
  if (!p || input().value.includes("\n")) return close();
  const all = items();
  const rank = (i) => (i.kind === "command" ? 2 : 0) + (i.name.startsWith(p.q) ? 0 : 1);   // your actions first, then prefix matches
  shown = (p.typing ? all.filter((i) => i.name.includes(p.q) || i.desc.toLowerCase().includes(p.q)) : all.filter((i) => i.name === p.q))
    .map((i, n) => [i, n]).sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map((x) => x[0]);
  if (!shown.length) return close();
  sel = Math.min(sel, shown.length - 1);
  let lastKind = null; const rows = [];
  shown.forEach((it, i) => {
    const grp = it.kind === "command" ? "Commands" : "Your actions";
    if (grp !== lastKind) { rows.push(h("div", { class: "sl-head" }, grp)); lastKind = grp; }
    rows.push(h("div", { class: `sl-item ${i === sel ? "sel" : ""}`, role: "option", onmousedown: (e) => { e.preventDefault(); choose(it); }, onmousemove: () => { if (sel !== i) { sel = i; updateSlash(); } } },
      it.icon && h("span", {}, it.icon), h("span", { class: "sl-name" }, "/" + it.name), h("span", { class: "sl-desc" }, it.desc), h("span", { class: "sl-kind" }, it.kind === "command" ? "" : it.kind)));
  });
  el.replaceChildren(...rows); el.classList.remove("hidden");
  el.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
}
const close = () => { menu().classList.add("hidden"); shown = []; };

function choose(it) {
  const p = parse();
  const args = p && !p.typing ? p.args : "";
  setComposer(""); close();
  if (it.kind === "command") it.run(args); else invoke(it.a, args);
}

function onKey(e) {
  if (menu().classList.contains("hidden") || e.isComposing) return;
  const stop = () => { e.preventDefault(); e.stopImmediatePropagation(); };
  if (e.key === "ArrowDown") { stop(); sel = (sel + 1) % shown.length; updateSlash(); }
  else if (e.key === "ArrowUp") { stop(); sel = (sel - 1 + shown.length) % shown.length; updateSlash(); }
  else if (e.key === "Tab") { stop(); const it = shown[sel]; setComposer(`/${it.name} `); }
  else if (e.key === "Escape") { stop(); close(); }
  else if (e.key === "Enter" && !e.shiftKey) { stop(); choose(shown[sel]); }
}

/* ---------- quick-action bar ---------- */
let dragId = null;
export function renderBar() {
  const bar = $("#qa-bar");
  const chips = acts().filter((a) => a.bar).map((a) => {
    const c = h("button", { class: `qa-chip ${a.kind}`, type: "button", draggable: "true", title: [a.description, a.shortcut && prettyHotkey(a.shortcut), "Right-click to edit"].filter(Boolean).join(" · "),
      onclick: () => invoke(a), oncontextmenu: (e) => { e.preventDefault(); hooks.editAction(a.id); },
      ondragstart: (e) => { dragId = a.id; c.classList.add("dragging"); e.dataTransfer.effectAllowed = "move"; },
      ondragend: () => { c.classList.remove("dragging"); bar.querySelectorAll(".over").forEach((x) => x.classList.remove("over")); },
      ondragover: (e) => { e.preventDefault(); c.classList.add("over"); }, ondragleave: () => c.classList.remove("over"),
      ondrop: async (e) => { e.preventDefault(); if (dragId && dragId !== a.id) await reorder(dragId, a.id); dragId = null; } },
      a.icon && h("span", {}, a.icon), a.label, a.shortcut && h("span", { class: "qa-key" }, prettyHotkey(a.shortcut)));
    return c;
  });
  chips.push(h("button", { class: "qa-chip qa-add", type: "button", title: "Add a saved prompt or script button", onclick: () => hooks.editAction("new") }, "+ Add"));
  bar.replaceChildren(...chips);
}

async function reorder(fromId, toId) {
  const list = [...acts()], from = list.findIndex((a) => a.id === fromId), [item] = list.splice(from, 1);
  list.splice(list.findIndex((a) => a.id === toId), 0, item);
  try { state.config.actions = await api.put("/api/actions", list); } catch (e) { toast(e.message, "error"); }
  renderBar();
}

/* ---------- init ---------- */
export function initActions(h_) {
  hooks = h_;
  input().addEventListener("keydown", onKey, true);
  input().addEventListener("input", updateSlash);
  input().addEventListener("blur", () => setTimeout(close, 120));
  on("script_output", onScriptEvent); on("script_done", onScriptEvent);
  on("config", renderBar);
  addEventListener("keydown", (e) => {
    if (e.repeat || !state.config || document.querySelector("dialog[open]")) return;
    const a = acts().find((x) => x.shortcut && matchesHotkey(e, x.shortcut));
    if (a) { e.preventDefault(); invoke(a); }
  });
  renderBar();
}
