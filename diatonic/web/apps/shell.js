// Terminal: real shell sessions (your login shell on a pseudo-terminal) in tabs, plus the history of every command the
// agent ran. Sessions live in the server: close the panel or reload and they keep running, and reattach with their output.
import { icon } from "/js/icons.js";
import { emit } from "/js/store.js";

export const full = true;
export const flush = true;

const instances = new WeakMap(); // state-ok: keyed by this panel's ctx

export async function navigate(p, ctx) { return instances.get(ctx)?.navigate(p); }

export async function render(el, ctx) {
  let inst = instances.get(ctx);
  if (!inst) {
    inst = mount(el, ctx);
    instances.set(ctx, inst);
  }
  return inst.render(el, ctx);
}

function mount(initialEl, initialCtx) {
  const B = "/api/apps/shell";
  const T = new Map();          // session id -> {term, fit, host, es, title, exited}
  let active = null, tab = "term", ui = null;
  let autoId = null;            // the tab opened automatically on first open (dropped if a specific folder was asked for)
  const typed = new Set();      // tabs someone has typed into
  let ro = null;

function loadXterm() {
  if (window.Terminal && window.FitAddon) return Promise.resolve();
  const css = document.createElement("link"); css.rel = "stylesheet"; css.href = "/vendor/xterm/xterm.css"; document.head.append(css);
  const js = (src) => new Promise((ok, bad) => { const s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = () => bad(new Error(`Couldn't load ${src}`)); document.head.append(s); });
  return js("/vendor/xterm/xterm.js").then(() => js("/vendor/xterm/addon-fit.js"));
}

function theme() {
  const v = (n, d) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || d;
  const light = document.documentElement.dataset.theme === "light";
  return {
    background: light ? "#fbfbfc" : "#0b0c10", foreground: light ? "#1f2328" : "#d7dae0", cursor: v("--accent", "#7c9cff"), cursorAccent: light ? "#fff" : "#0b0c10",
    selectionBackground: light ? "#b9cdfb" : "#33415e",
    ...(light ? {} : { black: "#1d1f24", brightBlack: "#5c6370", blue: "#61afef", brightBlue: "#82c1ff", cyan: "#56b6c2", green: "#98c379", magenta: "#c678dd", red: "#e06c75", yellow: "#e5c07b", white: "#d7dae0" }),
  };
}

// keystrokes go out in order, batched while a request is in flight
function sender(id) {
  let buf = "", busy = false;
  const flush = async () => {
    if (busy || !buf) return;
    busy = true; const data = buf; buf = "";
    try { await fetch(`${B}/term/${id}/input`, { method: "POST", headers: { "Content-Type": "application/json", "X-Diatonic": "1" }, body: JSON.stringify({ data }) }); }
    finally { busy = false; flush(); }
  };
  return (d) => { buf += d; flush(); };
}

function attach(info) {
  const host = document.createElement("div"); host.className = "term-host";
  const term = new window.Terminal({ fontFamily: getComputedStyle(document.documentElement).getPropertyValue("--mono").trim() || "Menlo, monospace",
    fontSize: 13, lineHeight: 1.15, cursorBlink: true, scrollback: 10000, allowProposedApi: false, macOptionIsMeta: true, theme: theme() });
  const fit = new window.FitAddon.FitAddon(); term.loadAddon(fit);
  const t = { id: info.id, term, fit, host, title: `zsh ${T.size + 1}`, exited: info.exited != null };
  const send = sender(info.id);
  term.onData((d) => {
    if (t.exited) { closeSession(t.id); return; }
    typed.add(info.id);
    send(d);
  });
  term.onTitleChange((s) => { t.title = s.replace(/^.*?:\s*/, "").slice(0, 40) || t.title; paintTabs(); });
  term.attachCustomKeyEventHandler((e) => {
    if (e.type !== "keydown" || !e.metaKey) return true;
    if (e.key === "c" && term.hasSelection()) { navigator.clipboard.writeText(term.getSelection()); return false; }
    if (e.key === "k") { term.clear(); return false; }
    return true;
  });
  const es = new EventSource(`${B}/term/${info.id}/stream`);
  es.onmessage = (m) => {
    const d = JSON.parse(m.data);
    if (d.d) term.write(Uint8Array.from(atob(d.d), (c) => c.charCodeAt(0)));
    if (d.exit != null) { t.exited = true; es.close(); term.write(`\r\n\x1b[2m[process exited${d.exit ? ` with code ${d.exit}` : ""} · press any key to close]\x1b[0m\r\n`); paintTabs(); }
  };
  t.es = es;
  T.set(info.id, t);
  return t;
}

async function newSession(cwd) {
  const r = await fetch(`${B}/term`, { method: "POST", headers: { "Content-Type": "application/json", "X-Diatonic": "1" }, body: JSON.stringify({ cwd: cwd || "~", cols: 100, rows: 30 }) });
  const info = await r.json();
  if (info.error) throw new Error(info.error);
  active = attach(info).id; tab = "term";
  initialCtx.state.set("sessions", [...T.keys()]);
  show();
}

async function closeSession(id) {
  const t = T.get(id); if (!t) return;
  t.es?.close(); t.term.dispose(); T.delete(id);
  initialCtx.state.set("sessions", [...T.keys()]);
  fetch(`${B}/term/${id}`, { method: "DELETE", headers: { "X-Diatonic": "1" } });
  if (active === id) active = [...T.keys()].pop() || null;
  if (!T.size && ui?.el.isConnected) return newSession();
  show();
}

/* ------------------------------------------------------------ panel */
async function render(el, ctx) {
  const { h } = ctx;
  ctx.onClose(() => {
    ro?.disconnect();
    for (const t of T.values()) {
      t.es?.close();
      t.term.dispose();
    }
    T.clear();
  });
  ui = { el, ctx, tabs: h("div", { class: "term-tabs", role: "tablist" }), body: h("div", { class: "term-body" }) };
  el.replaceChildren(h("div", { class: "term-wrap" },
    h("div", { class: "term-bar" }, ui.tabs, h("div", { class: "grow" }),
      h("button", { class: "btn sm", title: "Send the selection (or the last lines) to the agent", onclick: askAgent }, h("span", { html: icon("chat", 14) }), " Ask the agent"),
      h("button", { class: `btn sm ${tab === "history" ? "on" : ""}`, onclick: () => { tab = tab === "history" ? "term" : "history"; show(); } }, "Agent history")),
    ui.body));
  try { await loadXterm(); } catch (e) { ui.body.replaceChildren(h("div", { class: "err", style: "margin:16px" }, e.message)); return; }
  const list = await ctx.api.get(`${B}/term`).catch(() => []);
  const mySessionIds = new Set(ctx.state.get("sessions", []));
  for (const s of list) if (mySessionIds.has(s.id) && !T.has(s.id)) attach(s);
  for (const id of [...T.keys()]) if (!list.some((s) => s.id === id)) { T.get(id).es?.close(); T.get(id).term.dispose(); T.delete(id); }   // server restarted
  if (!T.size) return newSession().then(() => { autoId = active; }).catch((e) => ui.body.replaceChildren(h("div", { class: "err", style: "margin:16px" }, e.message)));
  if (!T.has(active)) active = [...T.keys()].pop();
  show();
}

function paintTabs() {
  if (!ui) return;
  const { h } = ui.ctx;
  ui.tabs.replaceChildren(...[...T.values()].map((t) => h("div", { class: `term-tab ${t.id === active && tab === "term" ? "on" : ""} ${t.exited ? "dead" : ""}`, role: "tab" },
    h("button", { type: "button", class: "tt-name", onclick: () => { active = t.id; tab = "term"; show(); } }, t.title),
    h("button", { type: "button", class: "tt-x", "aria-label": "Close tab", title: "Close", onclick: () => closeSession(t.id), html: icon("x", 12) }))),
    h("button", { type: "button", class: "icon-btn sm", title: "New tab", "aria-label": "New tab", onclick: () => newSession(), html: icon("plus", 15) }));
}

function show() {
  if (!ui?.el.isConnected) {
    requestAnimationFrame(() => {
      if (ui?.el.isConnected) show();
    });
    return;
  }
  paintTabs();
  ui.el.querySelector(".term-bar .btn.on")?.classList.remove("on");
  if (tab === "history") { ui.el.querySelectorAll(".term-bar .btn")[1]?.classList.add("on"); return history(); }
  const t = T.get(active); if (!t) return;
  ui.body.replaceChildren(t.host);
  if (!t.term.element) t.term.open(t.host);
  ro?.disconnect();
  let timer;
  const refit = () => {
    try { t.fit.fit(); } catch { return; }
    clearTimeout(timer);
    timer = setTimeout(() => fetch(`${B}/term/${t.id}/resize`, { method: "POST", headers: { "Content-Type": "application/json", "X-Diatonic": "1" },
      body: JSON.stringify({ cols: t.term.cols, rows: t.term.rows }) }), 80);
  };
  ro = new ResizeObserver(refit); ro.observe(t.host);
  requestAnimationFrame(() => { refit(); if (document.activeElement && ui.el.contains(document.activeElement)) t.term.focus(); });
}

function askAgent() {
  const t = T.get(active); if (!t) return;
  let text = t.term.getSelection();
  if (!text) {   // no selection: the last screenful
    const b = t.term.buffer.active, lines = [];
    for (let i = Math.max(0, b.length - 60); i < b.length; i++) lines.push(b.getLine(i)?.translateToString(true) ?? "");
    text = lines.join("\n").replace(/\n{3,}/g, "\n\n").trim();
  }
  emit("prefill", `Look at this terminal output and help me with it:\n\`\`\`\n${text.slice(-6000)}\n\`\`\`\n`);
}

/** Every command the agent ran (or tried), newest first. */
async function history() {
  const { h, api } = ui.ctx;
  ui.body.replaceChildren(h("div", { class: "skeleton", style: "margin:16px" }));
  const rows = await api.get(`${B}/log?limit=150`).catch(() => []);
  const when = (t) => new Date(t * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  ui.body.replaceChildren(h("div", { class: "term-hist" },
    !rows.length && h("div", { class: "p-empty", style: "padding:24px" }, "The agent hasn't run any commands yet. Turn shell access on in Settings → Automation."),
    ...rows.map((r) => h("details", { class: `th-row ${r.decision === "refused" || (r.exit_code ?? 0) !== 0 ? "bad" : ""}` },
      h("summary", {}, h("code", {}, r.command), h("span", { class: `pill d-${r.decision.split(" ")[0]}` }, r.decision),
        r.exit_code != null && h("span", { class: "p-s" }, `exit ${r.exit_code}`), h("span", { class: "p-s" }, `${when(r.at)}${r.source === "scheduled" ? " · scheduled" : ""}`)),
      h("div", { class: "p-s" }, r.cwd || ""),
      h("pre", { class: "term-out" }, r.output || "(no output)"),
      r.decision !== "refused" && h("button", { class: "btn sm", onclick: () => { const t = T.get(active); if (t) { tab = "term"; show(); t.term.paste(r.command); } } }, "Put in terminal")))));
}

/** The agent (or a link) can open a terminal in a folder. */
async function navigate(p) {
  if (!(p.item || p.path)) return;
  const spare = autoId && T.has(autoId) && !typed.has(autoId) ? autoId : null;
  autoId = null;
  await newSession(p.item || p.path);
  if (spare) closeSession(spare);   // the empty home-folder tab made on first open isn't wanted
}

return { render, navigate };
}
