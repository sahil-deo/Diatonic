// The command palette (⌘K / ⌘J): everything in the command registry plus apps, saved actions,
// conversations, automations, and layouts, with plain text frecency search, "/" command grammar,
// and dedicated Ask-the-Agent mode.
import { $, h, toast } from "./dom.js";
import { icon } from "./icons.js";
import { state } from "./store.js";
import { api } from "./api.js";
import * as chat from "./chat.js";
import { openPanel } from "./panel.js";
import { list, provided, labelOf } from "./commands.js";
import { hintFor } from "./keymap.js";
import { rankItems } from "./fuzzy.js";
import { invoke as invokeAction } from "./actions.js";
import { getMacros, runMacro } from "./macros.js";
import { getLayouts, switchLayout } from "./workspace.js";

const USE_KEY = "diatonic.palette.use";
let sel = 0, shown = [];
let schedulerJobs = [];
const dlg = () => $("#palette");

async function loadSchedulerJobs() {
  try {
    const res = await api.get("/api/apps/scheduler/panel").catch(() => null);
    if (res && Array.isArray(res.jobs)) schedulerJobs = res.jobs;
  } catch {}
}

async function runJob(j) {
  try {
    await api.post("/api/apps/scheduler/action", { id: j.id, action: "run" });
    toast("Started: " + (j.name || "Untitled job"));
  } catch (e) {
    toast(e.message, "error");
  }
}

const usage = () => { try { return JSON.parse(localStorage.getItem(USE_KEY)) || {}; } catch { return {}; } };
function remember(id) {
  const u = usage(), cur = u[id] || [0, 0];
  u[id] = [cur[0] + 1, Date.now()];
  const keep = Object.entries(u).sort((a, b) => b[1][1] - a[1][1]).slice(0, 200);
  try { localStorage.setItem(USE_KEY, JSON.stringify(Object.fromEntries(keep))); } catch {}
}

export function getAvailableApps() {
  const builtins = [{ id: "chat", name: "Chat", icon: "chat" }];
  const panelApps = [...(state.apps || []), ...(state.modApps || [])].filter((a) => a.enabled !== false && a.panel);
  const seen = new Set(["chat"]);
  const res = [...builtins];
  for (const a of panelApps) {
    if (!seen.has(a.id)) {
      seen.add(a.id);
      res.push({ id: a.id, name: a.name || a.id, icon: a.icon || "box" });
    }
  }
  return res;
}

/** Everything that can be picked in normal mode. */
export function paletteItems() {
  const cmds = list().map((c) => ({ id: c.id, group: c.group, label: labelOf(c), icon: c.icon, hint: hintFor(c.id), run: () => c.run({}) }));
  const jobs = schedulerJobs.map((j) => ({
    id: `sched:${j.id}`,
    group: "Automations",
    label: `Run job: ${j.name || j.payload?.prompt || j.payload?.command || "Untitled"}`,
    icon: "activity",
    hint: j.kind,
    run: () => runJob(j),
  }));
  return [...cmds, ...provided(), ...jobs];
}

const mark = (label, hits = []) => {
  if (!hits || !hits.length) return label;
  const on = new Set(hits);
  return [...label].map((ch, i) => (on.has(i) ? h("b", {}, ch) : ch));
};

export function openPalette(opts = {}) {
  loadSchedulerJobs();
  const d = dlg();
  let mode = opts.mode || (opts.initialQuery?.startsWith("?") ? "agent" : opts.initialQuery?.startsWith("/") ? "slash" : "normal");

  const modeBadge = h("span", {
    class: `pal-mode-badge ${mode}`,
    style: mode === "normal" ? "display:none;" : "",
    onclick: () => { setMode("normal"); input.value = ""; paint(); },
  }, h("span", { html: icon(mode === "agent" ? "chat" : "command", 13) }), mode === "agent" ? "Agent" : "/");

  const input = h("input", {
    placeholder: mode === "agent"
      ? "Ask the agent anything…"
      : mode === "slash"
      ? "Type a command (/open, /split, /layout, /run)…"
      : "Type a command, app or conversation…",
    "aria-label": "Command",
    value: opts.initialQuery || "",
  });

  const header = h("div", { class: "pal-header" }, modeBadge, input);
  const list_ = h("div", { class: "pal-list", role: "listbox" });
  const hintBar = h("div", {
    class: "pal-agent-hint",
    style: mode === "agent" ? "" : "display:none;",
  },
    h("span", {}, h("kbd", {}, "Enter"), " active chat"),
    h("span", {}, h("kbd", {}, "⌘Enter"), " split"),
    h("span", {}, h("kbd", {}, "⌥Enter"), " background")
  );

  function setMode(m) {
    mode = m;
    modeBadge.className = `pal-mode-badge ${m}`;
    modeBadge.replaceChildren(
      h("span", { html: icon(m === "agent" ? "chat" : "command", 13) }),
      m === "agent" ? "Agent" : "/"
    );
    modeBadge.style.display = m === "normal" ? "none" : "inline-flex";
    hintBar.style.display = m === "agent" ? "flex" : "none";
    input.placeholder = m === "agent"
      ? "Ask the agent anything…"
      : m === "slash"
      ? "Type a command (/open, /split, /layout, /run)…"
      : "Type a command, app or conversation…";
  }

  function showArgForm(item, onRun) {
    const argInput = h("input", { type: "text", placeholder: "Arguments (e.g. for {{input}} or $1)...", "aria-label": "Arguments" });
    const runBtn = h("button", { type: "button", class: "btn primary sm", onclick: () => { d.close(); onRun(argInput.value.trim()); } }, "Run");
    const cancelBtn = h("button", { type: "button", class: "btn sm", onclick: () => { paint(); input.focus(); } }, "Cancel");
    const form = h("div", { class: "pal-arg-form" },
      h("div", { class: "pal-arg-title" }, `Run: ${item.label}`),
      argInput,
      h("div", { class: "pal-arg-actions" }, cancelBtn, runBtn)
    );
    argInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); d.close(); onRun(argInput.value.trim()); }
      else if (e.key === "Escape") { e.preventDefault(); paint(); input.focus(); }
    });
    list_.replaceChildren(form);
    setTimeout(() => argInput.focus(), 20);
  }

  const run = (it) => {
    d.close();
    remember(it.id);
    try { Promise.resolve(it.run()).catch(() => {}); } catch {}
  };

  const paint = () => {
    const q = input.value;

    if (mode === "agent") {
      const starters = [
        { id: "starter:summary", group: "Suggestions", label: "Summarize unread emails", icon: "mail", run: () => { input.value = "Summarize my unread emails"; paint(); } },
        { id: "starter:cal", group: "Suggestions", label: "What is on my calendar today?", icon: "calendar", run: () => { input.value = "What is on my calendar today?"; paint(); } },
        { id: "starter:todos", group: "Suggestions", label: "Check my open to-dos", icon: "check-square", run: () => { input.value = "Check my open to-dos"; paint(); } },
        { id: "starter:prs", group: "Suggestions", label: "Review open pull requests", icon: "git-pull-request", run: () => { input.value = "Review open pull requests"; paint(); } },
      ];
      const recents = (state.conversations || []).slice(0, 8).map((c) => ({
        id: `conv:${c.id}`,
        group: "Recent conversations",
        label: c.title,
        icon: "chat",
        run: () => { d.close(); chat.openConversation(c.id); },
      }));
      const agentItems = [...starters, ...recents];
      shown = q.trim() ? rankItems(agentItems, q.trim(), usage()) : agentItems;
    } else if (mode === "slash") {
      const raw = q.startsWith("/") ? q.slice(1) : q;
      const parts = raw.trimStart().split(/\s+/);
      const verb = parts[0]?.toLowerCase() || "";
      const subQ = parts.slice(1).join(" ").trim();
      const hasSpace = /\s/.test(raw);

      if (!hasSpace) {
        const verbs = [
          { id: "verb:open", group: "Commands", label: "/open <app>", hint: "open app", icon: "box", run: () => { input.value = "/open "; input.focus(); paint(); } },
          { id: "verb:split", group: "Commands", label: "/split [right|down] <app>", hint: "split", icon: "split-v", run: () => { input.value = "/split "; input.focus(); paint(); } },
          { id: "verb:layout", group: "Commands", label: "/layout <name>", hint: "preset or saved layout", icon: "sliders", run: () => { input.value = "/layout "; input.focus(); paint(); } },
          { id: "verb:run", group: "Commands", label: "/run <job|macro|script>", hint: "run", icon: "command", run: () => { input.value = "/run "; input.focus(); paint(); } },
        ];
        shown = verb ? rankItems(verbs, verb, usage()) : verbs;
      } else if (verb === "open") {
        const apps = getAvailableApps();
        shown = rankItems(apps.map((a) => ({
          id: `slash:open:${a.id}`,
          group: "Open App",
          label: a.name,
          icon: a.icon || "box",
          appId: a.id,
          run: () => { d.close(); openPanel(a.id); },
        })), subQ, usage());
      } else if (verb === "split") {
        const restParts = parts.slice(1);
        let dir = "row";
        let appFilter = subQ;
        if (restParts[0] === "right" || restParts[0] === "v") {
          dir = "row";
          appFilter = restParts.slice(1).join(" ");
        } else if (restParts[0] === "down" || restParts[0] === "h") {
          dir = "col";
          appFilter = restParts.slice(1).join(" ");
        }
        const apps = getAvailableApps();
        shown = rankItems(apps.map((a) => ({
          id: `slash:split:${a.id}`,
          group: `Split ${dir === "row" ? "Side-by-side" : "Stacked"}`,
          label: `Split with ${a.name}`,
          icon: dir === "row" ? "split-v" : "split-h",
          appId: a.id,
          run: async () => {
            d.close();
            const ws = await import("./workspace.js");
            ws.splitActive(dir, a.id);
          },
        })), appFilter, usage());
      } else if (verb === "layout") {
        const presets = [
          { id: "single", label: "Single panel", icon: "box", run: async () => { d.close(); const ws = await import("./workspace.js"); ws.applyPreset("single"); } },
          { id: "side-by-side", label: "Side-by-side", icon: "split-v", run: async () => { d.close(); const ws = await import("./workspace.js"); ws.applyPreset("side-by-side"); } },
          { id: "stacked", label: "Stacked", icon: "split-h", run: async () => { d.close(); const ws = await import("./workspace.js"); ws.applyPreset("stacked"); } },
          { id: "three-column", label: "Three columns", icon: "chevron", run: async () => { d.close(); const ws = await import("./workspace.js"); ws.applyPreset("three-column"); } },
          { id: "grid", label: "2x2 Grid", icon: "chevron", run: async () => { d.close(); const ws = await import("./workspace.js"); ws.applyPreset("grid"); } },
        ].map((l) => ({ id: `slash:layout:${l.id}`, group: "Layout Presets", label: l.label, icon: l.icon, run: l.run }));
        const savedItems = (getLayouts() || []).map((w) => ({
          id: `slash:layout:saved:${w.id}`,
          group: "Saved Layouts",
          label: w.name,
          hint: w.description || "Layout",
          icon: "sliders",
          run: () => {
            d.close();
            switchLayout(w.id);
          },
        }));
        shown = rankItems([...presets, ...savedItems], subQ, usage());
      } else if (verb === "run") {
        const jobItems = schedulerJobs.map((j) => ({
          id: `slash:run:sched:${j.id}`,
          group: "Scheduler Jobs",
          label: j.name || (j.payload?.prompt || j.payload?.command || "Job").slice(0, 50),
          icon: "activity",
          hint: j.kind,
          run: () => { d.close(); runJob(j); },
        }));
        const actionItems = (state.config?.actions || []).map((a) => ({
          id: `slash:run:act:${a.id}`,
          group: "Saved Actions",
          label: a.label,
          icon: a.kind === "script" ? "command" : "chat",
          hint: a.kind,
          run: () => {
            const needsInput = a.input || a.prompt?.includes("{{input}}") || a.kind === "script";
            if (needsInput) {
              showArgForm(a, (args) => invokeAction(a, args));
            } else {
              d.close();
              invokeAction(a);
            }
          },
        }));
        const macroItems = (getMacros() || []).map((m) => ({
          id: `slash:run:macro:${m.id}`,
          group: "Saved Macros",
          label: m.name,
          icon: "command",
          hint: `${m.steps?.length || 0} steps`,
          run: () => {
            d.close();
            runMacro(m);
          },
        }));
        shown = rankItems([...jobItems, ...actionItems, ...macroItems], subQ, usage());
      } else {
        shown = [];
      }
    } else {
      // Normal Mode
      const items = paletteItems();
      shown = rankItems(items, q, usage());
    }

    sel = Math.min(sel, Math.max(0, shown.length - 1));
    let last = null;
    const rows = [];
    shown.forEach((it, idx) => {
      if (!q.trim() && it.group !== last) {
        rows.push(h("div", { class: "pal-group" }, it.group));
        last = it.group;
      }
      rows.push(h("div", {
        class: `pal-item ${idx === sel ? "sel" : ""}`,
        role: "option",
        "aria-selected": String(idx === sel),
        onclick: () => run(it),
        onmousemove: () => { if (sel !== idx) { sel = idx; paint(); } },
      },
        h("span", { html: icon(it.icon || "command", 16) }),
        h("span", { class: "pal-label" }, mark(it.label, it.hits)),
        q.trim() && it.group && h("span", { class: "pal-tag" }, it.group),
        it.hint && h("span", { class: /[⌘⇧⌥⌃↵⎋]/.test(it.hint) ? "k key" : "k" }, it.hint)
      ));
    });
    list_.replaceChildren(...(rows.length ? rows : [h("div", { class: "p-empty" }, "No matches")]));
    list_.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
  };

  input.addEventListener("input", () => {
    const v = input.value;
    if (mode === "normal") {
      if (v.startsWith("/")) {
        setMode("slash");
      } else if (v.startsWith("?")) {
        setMode("agent");
        input.value = v.slice(1).trimStart();
      }
    } else if (mode === "slash") {
      if (!v.startsWith("/")) {
        setMode("normal");
      }
    }
    sel = 0;
    paint();
  });

  input.addEventListener("keydown", async (e) => {
    if (e.key === "Backspace" && !input.value) {
      if (mode !== "normal") {
        e.preventDefault();
        setMode("normal");
        paint();
        return;
      }
    }

    if (mode === "agent") {
      if (e.key === "Enter") {
        e.preventDefault();
        const text = input.value.trim();
        if (!text) {
          if (shown[sel]) { run(shown[sel]); }
          return;
        }
        d.close();
        if (e.altKey) {
          // ⌥Enter: background run and notify
          try {
            await api.chat({ message: text });
            toast("Agent task started in background");
          } catch (err) {
            toast(err.message, "error");
          }
        } else if (e.metaKey || e.ctrlKey) {
          // ⌘Enter: new chat in a split
          const ws = await import("./workspace.js");
          await ws.splitActive("row", "chat");
          chat.newChat();
          await chat.send(text);
        } else {
          // Enter: active chat
          await openPanel("chat");
          await chat.send(text);
        }
        return;
      }
      if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) {
        e.preventDefault();
        sel = Math.min(shown.length - 1, sel + 1);
        paint();
        return;
      }
      if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) {
        e.preventDefault();
        sel = Math.max(0, sel - 1);
        paint();
        return;
      }
    }

    if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) {
      e.preventDefault();
      sel = Math.min(shown.length - 1, sel + 1);
      paint();
    } else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) {
      e.preventDefault();
      sel = Math.max(0, sel - 1);
      paint();
    } else if (e.key === "Enter" && shown[sel]) {
      e.preventDefault();
      const it = shown[sel];
      if ((e.metaKey || e.ctrlKey) && it.appId) {
        d.close();
        remember(it.id);
        import("./workspace.js").then((m) => m.splitActive("row", it.appId));
      } else if (e.shiftKey && it.appId) {
        d.close();
        remember(it.id);
        import("./workspace.js").then((m) => m.splitActive("col", it.appId));
      } else {
        run(it);
      }
    }
  });

  d.replaceChildren(header, list_, hintBar);
  sel = 0;
  paint();
  if (!d.open) d.showModal();
  input.focus();
}

/** Search and pick an installed or built-in app. Resolves to the app id, or null if cancelled. */
export function pickApp(placeholder = "Search apps to open…") {
  return new Promise((resolve) => {
    const items = getAvailableApps().map((a) => ({
      id: `app:${a.id}`,
      appId: a.id,
      group: "Apps",
      label: a.name,
      icon: a.icon || "box",
      hint: a.id,
    }));

    let d = $("#app-picker");
    if (!d) {
      d = h("dialog", { id: "app-picker", class: "modal palette" });
      document.body.append(d);
      d.addEventListener("click", (e) => { if (e.target === d) d.close(); });
    }
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      d.removeEventListener("close", onClose);
      if (d.open) d.close();
      resolve(result);
    };
    const onClose = () => finish(null);
    d.addEventListener("close", onClose, { once: true });

    const input = h("input", { placeholder, "aria-label": "Search apps" });
    const list_ = h("div", { class: "pal-list", role: "listbox" });
    let pickSel = 0;
    let pickShown = [];

    const choose = (it) => {
      remember(it.id);
      finish(it.appId);
    };

    const paint = () => {
      const q = input.value;
      pickShown = rankItems(items, q, usage());
      pickSel = Math.min(pickSel, Math.max(0, pickShown.length - 1));
      let last = null;
      const rows = [];
      pickShown.forEach((it, idx) => {
        if (!q.trim() && it.group !== last) {
          rows.push(h("div", { class: "pal-group" }, it.group));
          last = it.group;
        }
        rows.push(h("div", {
          class: `pal-item ${idx === pickSel ? "sel" : ""}`,
          role: "option",
          "aria-selected": String(idx === pickSel),
          onclick: () => choose(it),
          onmousemove: () => {
            if (pickSel !== idx) {
              pickSel = idx;
              paint();
            }
          },
        },
          h("span", { html: icon(it.icon || "box", 16) }),
          h("span", { class: "pal-label" }, mark(it.label, it.hits)),
          q.trim() && it.group && h("span", { class: "pal-tag" }, it.group),
          it.hint && h("span", { class: "k" }, it.hint)
        ));
      });
      list_.replaceChildren(...(rows.length ? rows : [h("div", { class: "p-empty" }, "No matching apps")]));
      list_.querySelector(".sel")?.scrollIntoView({ block: "nearest" });
    };

    input.addEventListener("input", () => { pickSel = 0; paint(); });
    input.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || (e.ctrlKey && e.key === "n")) {
        e.preventDefault();
        pickSel = Math.min(pickShown.length - 1, pickSel + 1);
        paint();
      } else if (e.key === "ArrowUp" || (e.ctrlKey && e.key === "p")) {
        e.preventDefault();
        pickSel = Math.max(0, pickSel - 1);
        paint();
      } else if (e.key === "Enter" && pickShown[pickSel]) {
        e.preventDefault();
        choose(pickShown[pickSel]);
      } else if (e.key === "Escape") {
        e.preventDefault();
        finish(null);
      }
    });

    d.replaceChildren(input, list_);
    pickSel = 0;
    paint();
    if (!d.open) d.showModal();
    input.focus();
  });
}

export function initPalette() {
  dlg().addEventListener("click", (e) => { if (e.target === dlg()) dlg().close(); });
}
