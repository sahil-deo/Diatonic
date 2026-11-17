// Terminal: real shell session (your login shell on a pseudo-terminal), plus the history of every command the
// agent ran. Sessions live in the server: close the panel or reload and it keeps running, and reattaches with output.
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
  let session = null;
  let tab = "term", ui = null;
  let typed = false;
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
    const accent = v("--accent", "#7c9cff");
    return {
      background: "rgba(0,0,0,0)", foreground: light ? "#1b2233" : "#dfe3ee", cursor: accent, cursorAccent: light ? "#ffffff" : "#0b0c10",
      selectionBackground: light ? "rgba(69,96,230,.28)" : "rgba(124,156,255,.30)",
      ...(light
        ? { black: "#2a3144", brightBlack: "#6b7388", blue: "#3b5bdb", brightBlue: "#4c6ef5", cyan: "#0c8599", green: "#2b8a3e", magenta: "#9c36b5", red: "#c92a2a", yellow: "#b5700a", white: "#5b6275", brightWhite: "#1b2233" }
        : { black: "#1d1f24", brightBlack: "#6a7287", blue: "#74a0ff", brightBlue: "#9bbcff", cyan: "#5fd0d6", green: "#8fd6a1", magenta: "#c79bff", red: "#ff8a95", yellow: "#f0cf8a", white: "#dfe3ee", brightWhite: "#ffffff" }),
    };
  }

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
      fontSize: 13, lineHeight: 1.15, cursorBlink: true, allowTransparency: true, scrollback: 10000, allowProposedApi: false, macOptionIsMeta: true, theme: theme() });
    const fit = new window.FitAddon.FitAddon(); term.loadAddon(fit);
    const s = { id: info.id, term, fit, host, title: "zsh", exited: info.exited != null };
    const send = sender(info.id);
    term.onData((d) => {
      if (s.exited) { newSession(); return; }
      typed = true;
      send(d);
    });
    term.onTitleChange((t) => { s.title = t.replace(/^.*?:\s*/, "").slice(0, 40) || s.title; updateTitle(); });
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
      if (d.exit != null) {
        s.exited = true;
        es.close();
        term.write(`\r\n\x1b[2m[process exited${d.exit ? ` with code ${d.exit}` : ""} · press any key to restart]\x1b[0m\r\n`);
        updateTitle();
      }
    };
    s.es = es;
    session = s;
    return s;
  }

  async function newSession(cwd) {
    if (session) {
      session.es?.close();
      session.term.dispose();
      fetch(`${B}/term/${session.id}`, { method: "DELETE", headers: { "X-Diatonic": "1" } }).catch(() => {});
      session = null;
    }
    const r = await fetch(`${B}/term`, { method: "POST", headers: { "Content-Type": "application/json", "X-Diatonic": "1" }, body: JSON.stringify({ cwd: cwd || "~", cols: 100, rows: 30 }) });
    const info = await r.json();
    if (info.error) throw new Error(info.error);
    attach(info);
    tab = "term";
    initialCtx.state.set("sessionId", info.id);
    show();
  }

  function updateTitle() {}

  /* ------------------------------------------------------------ panel */
  async function render(el, ctx) {
    const { h } = ctx;
    const retheme = new MutationObserver(() => { if (session) session.term.options.theme = theme(); });
    retheme.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "style"] });
    const onAsk = () => askAgent();
    const onToggleHist = () => { tab = tab === "history" ? "term" : "history"; show(); };
    el.addEventListener("shell:ask-agent", onAsk);
    el.addEventListener("shell:toggle-history", onToggleHist);
    ctx.onClose(() => {
      retheme.disconnect();
      ro?.disconnect();
      el.removeEventListener("shell:ask-agent", onAsk);
      el.removeEventListener("shell:toggle-history", onToggleHist);
      if (session) {
        session.es?.close();
        session.term.dispose();
        session = null;
      }
    });
    ui = {
      el, ctx,
      body: h("div", { class: "term-body" }),
    };
    el.replaceChildren(h("div", { class: "term-wrap" }, ui.body));
    try { await loadXterm(); } catch (e) { ui.body.replaceChildren(h("div", { class: "err", style: "margin:16px" }, e.message)); return; }
    const list = await ctx.api.get(`${B}/term`).catch(() => []);
    const savedId = ctx.state.get("sessionId");
    const existing = list.find((s) => s.id === savedId);
    if (existing && !session) attach(existing);
    if (!session) return newSession().catch((e) => ui.body.replaceChildren(h("div", { class: "err", style: "margin:16px" }, e.message)));
    show();
  }

  function show() {
    if (!ui?.el.isConnected) {
      requestAnimationFrame(() => {
        if (ui?.el.isConnected) show();
      });
      return;
    }
    if (tab === "history") return history();
    if (!session) return;
    ui.body.replaceChildren(session.host);
    if (!session.term.element) session.term.open(session.host);
    ro?.disconnect();
    let timer;
    const refit = () => {
      try { session.fit.fit(); } catch { return; }
      clearTimeout(timer);
      timer = setTimeout(() => fetch(`${B}/term/${session.id}/resize`, {
        method: "POST", headers: { "Content-Type": "application/json", "X-Diatonic": "1" },
        body: JSON.stringify({ cols: session.term.cols, rows: session.term.rows })
      }), 80);
    };
    ro = new ResizeObserver(refit);
    ro.observe(session.host);
    requestAnimationFrame(() => {
      refit();
      if (document.activeElement && ui.el.contains(document.activeElement)) session.term.focus();
    });
  }

  function askAgent() {
    if (!session) return;
    let text = session.term.getSelection();
    if (!text) {
      const b = session.term.buffer.active, lines = [];
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
      h("div", { class: "th-head", style: "display:flex;align-items:center;justify-content:space-between;padding:4px 0 8px;border-bottom:1px solid var(--border);" },
        h("strong", {}, "Agent command history"),
        h("button", { type: "button", class: "btn sm", onclick: () => { tab = "term"; show(); } }, "← Back to terminal")
      ),
      !rows.length && h("div", { class: "p-empty", style: "padding:24px" }, "The agent hasn't run any commands yet. Turn shell access on in Settings → Automation."),
      ...rows.map((r) => h("details", { class: `th-row ${r.decision === "refused" || (r.exit_code ?? 0) !== 0 ? "bad" : ""}` },
        h("summary", {}, h("code", {}, r.command), h("span", { class: `pill d-${r.decision.split(" ")[0]}` }, r.decision),
          r.exit_code != null && h("span", { class: "p-s" }, `exit ${r.exit_code}`), h("span", { class: "p-s" }, `${when(r.at)}${r.source === "scheduled" ? " · scheduled" : ""}`)),
        h("div", { class: "p-s" }, r.cwd || ""),
        h("pre", { class: "term-out" }, r.output || "(no output)"),
        r.decision !== "refused" && h("button", { class: "btn sm", onclick: () => { if (session) { tab = "term"; show(); session.term.paste(r.command); } } }, "Put in terminal")))));
  }

  /** The agent (or a link) can open a terminal in a folder. */
  async function navigate(p) {
    if (!(p.item || p.path)) return;
    const folder = p.item || p.path;
    if (!typed && session) {
      session.term.paste(`cd ${folder.includes(" ") ? `"${folder}"` : folder}\n`);
      typed = true;
      tab = "term";
      show();
    } else {
      await newSession(folder);
    }
  }

  return { render, navigate };
}
