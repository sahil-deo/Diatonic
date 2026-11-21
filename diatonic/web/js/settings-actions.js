// Settings → Actions: manage saved prompts and script buttons.
import { h, toast } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { field, text, select, toggle, RESERVED } from "./form.js";
import { eventToHotkey, prettyHotkey } from "./voice.js";
import { rerenderSettings } from "./settings.js";
import { invoke } from "./actions.js";

let editing = null;     // an action object being edited, or "new"
let wantEdit = null;    // set by the quick bar: open this id (or "new") when the tab renders
export const requestEdit = (id) => { wantEdit = id; };

const EXAMPLES = [
  { kind: "prompt", label: "Plan my day", name: "plan", icon: "🗓️", description: "Today's calendar, to-dos and reminders", bar: true, send: true,
    prompt: "It's {{day}} {{date}}. Look at my calendar, open to-dos and reminders for today and give me a short, ordered plan for the day." },
  { kind: "prompt", label: "Summarize", name: "summarize", icon: "✂️", description: "Summarize pasted text or the clipboard", bar: true, send: false,
    prompt: "Summarize this in 3 bullet points:\n\n{{input}}" },
  { kind: "prompt", label: "Standup", name: "standup", icon: "🧍", description: "Draft a standup from my GitHub activity", bar: false, send: true,
    prompt: "Check my open GitHub issues and pull requests and write a short standup: yesterday, today, blockers." },
  { kind: "script", label: "Disk space", name: "disk", icon: "💽", description: "df -h", bar: false, confirm: false, command: "df -h /", cwd: "", timeout: 15 },
  { kind: "script", label: "Git status", name: "gs", icon: "🌿", description: "git status in a folder you give", bar: false, confirm: false, command: 'cd "${1:-.}" && git status -sb', cwd: "~", timeout: 20 },
];

async function persist(list) {
  try { state.config.actions = await api.put("/api/actions", list); emit("config"); return true; }
  catch (e) { toast(e.message, "error"); return false; }
}

export function actionsPane() {
  if (wantEdit) {
    editing = wantEdit === "new" ? "new" : state.config.actions.find((a) => a.id === wantEdit) || null;
    wantEdit = null;
  }
  if (editing) return [editor(editing === "new" ? { kind: "prompt", bar: true } : editing)];
  const list = state.config.actions;
  const move = async (i, d) => { const l = [...list]; [l[i], l[i + d]] = [l[i + d], l[i]]; await persist(l); rerenderSettings(); };
  return [
    h("div", { class: "notice" }, "Saved prompts and script buttons appear above the message box and as ", h("code", {}, "/commands"),
      ". In prompts, ", h("code", {}, "{{input}}"), " is what you type after the command; ", h("code", {}, "{{date}} {{time}} {{day}} {{clipboard}}"), " also work. Scripts get your input as ", h("code", {}, "$1"), " and ", h("code", {}, "$INPUT"), "."),
    h("div", { class: "row", style: "margin-bottom:14px" },
      h("button", { class: "btn primary", onclick: () => { editing = "new"; rerenderSettings(); } }, "New action"),
      !list.length && h("button", { class: "btn", onclick: async () => { if (await persist(EXAMPLES)) rerenderSettings(); } }, "Add examples")),
    ...(list.length ? list.map((a, i) => h("div", { class: "act-row" },
      h("span", { class: "ic" }, a.icon || (a.kind === "script" ? "›_" : "✦")),
      h("div", { class: "grow" }, h("div", {}, a.label, " ", h("span", { class: `pill ${a.kind}` }, a.kind)),
        h("div", { class: "nm" }, [a.name && "/" + a.name, a.shortcut && prettyHotkey(a.shortcut), a.bar ? "on bar" : "hidden from bar"].filter(Boolean).join("  ·  "))),
      h("button", { class: "icon-btn sm", title: "Move up", disabled: i === 0, onclick: () => move(i, -1), html: "↑" }),
      h("button", { class: "icon-btn sm", title: "Move down", disabled: i === list.length - 1, onclick: () => move(i, 1), html: "↓" }),
      h("button", { class: "btn sm", onclick: () => { editing = a; rerenderSettings(); } }, "Edit")))
      : [h("div", { class: "p-empty" }, "No actions yet. Add one, or load a few examples to start from.")]),
  ];
}

function editor(a0) {
  const a = { icon: "", name: "", description: "", label: "", prompt: "", command: "", cwd: "", timeout: 0, send: false, new_chat: false, confirm: false, shortcut: "", ...a0 };
  const isNew = !a0.id;
  const body = h("div", { class: "act-form" });
  const draw = () => {
    const prompt = a.kind === "prompt";
    const hk = h("input", { type: "text", class: "hotkey", readonly: true, value: a.shortcut ? prettyHotkey(a.shortcut) : "None. Click, then press keys", });
    hk.addEventListener("focus", () => { hk.classList.add("capturing"); hk.value = "Press a combination…"; });
    hk.addEventListener("blur", () => { hk.classList.remove("capturing"); hk.value = a.shortcut ? prettyHotkey(a.shortcut) : "None. Click, then press keys"; });
    hk.addEventListener("keydown", (e) => {
      e.preventDefault(); e.stopPropagation();
      if (e.key === "Escape") return hk.blur();
      if (e.key === "Backspace" || e.key === "Delete") { a.shortcut = ""; return hk.blur(); }
      const k = eventToHotkey(e); if (!k) return;
      if (!/Control|Alt|Shift|Meta/.test(k)) return toast("Include a modifier (⌥, ⌃, ⇧ or ⌘).", "error");
      if (RESERVED[k] || k === state.config.voice.hotkey) return toast(`${prettyHotkey(k)} is already in use.`, "error");
      const clash = state.config.actions.find((x) => x.shortcut === k && x.id !== a.id);
      if (clash) return toast(`${prettyHotkey(k)} is used by “${clash.label}”.`, "error");
      a.shortcut = k; hk.blur();
    });
    const ta = (key, ph, mono, rows) => { const t = h("textarea", { placeholder: ph, class: mono ? "mono" : "", rows, oninput: (e) => { a[key] = e.target.value; } }, a[key]); return t; };
    body.replaceChildren(...[
      h("div", { class: "row", style: "margin-bottom:6px" }, h("button", { class: "btn sm", onclick: () => { editing = null; rerenderSettings(); } }, "← Back"), h("strong", {}, isNew ? "New action" : "Edit action")),
      field("Type", select(a.kind, [["prompt", "Saved prompt: fills (or sends) a message"], ["script", "Script: runs a shell command"]], (v) => { a.kind = v; draw(); })),
      h("div", { class: "row" },
        field("Label", text(a.label, (v) => (a.label = v), { placeholder: "Plan my day" })),
        field("Icon (emoji)", text(a.icon, (v) => (a.icon = v), { placeholder: "🗓️" })),
        field("/command", text(a.name, (v) => (a.name = v.replace(/^\//, "")), { placeholder: "plan" }))),
      field("Description (shown in the / menu)", text(a.description, (v) => (a.description = v), { placeholder: "Optional" })),
      prompt ? field("Prompt", ta("prompt", "What should I ask?  Use {{input}} for text typed after the command.", false, 6), "{{input}}, {{date}}, {{time}}, {{day}}, {{clipboard}}")
             : field("Command", ta("command", 'e.g.  du -sh "$1"   or   ~/bin/backup.sh', true, 5), "Runs with /bin/sh. Your input is available as $1 and $INPUT, never pasted into the command text, so it can't inject code."),
      !prompt && h("div", { class: "row" },
        field("Working folder", text(a.cwd, (v) => (a.cwd = v), { placeholder: "~ (home)" })),
        field("Timeout (s)", text(a.timeout || "", (v) => (a.timeout = v), { type: "number", min: 1, max: 900, placeholder: "default" }))),
      field("Keyboard shortcut", hk),
      toggle("Show on the quick-action bar", null, a.bar, (v) => (a.bar = v)),
      prompt ? toggle("Send immediately", "Off = put it in the message box so you can edit it first.", a.send, (v) => (a.send = v)) : null,
      prompt ? toggle("Start a new chat first", null, a.new_chat, (v) => (a.new_chat = v)) : null,
      toggle("Ask before running", prompt ? null : "Shows the command and waits for your OK.", a.confirm, (v) => (a.confirm = v)),
      !prompt && !state.config.scheduler.allow_scripts && h("div", { class: "notice warn" }, "Scripts are turned off. You can save this button, but it won't run until you enable “Allow scripts” in Settings → Automation."),
      h("div", { class: "row", style: "margin-top:8px" },
        h("button", { class: "btn primary", onclick: async () => {
          const item = { ...a, timeout: Number(a.timeout) || 0 };
          const list = isNew ? [...state.config.actions, item] : state.config.actions.map((x) => (x.id === a.id ? item : x));
          if (await persist(list)) { editing = null; toast("Saved"); rerenderSettings(); }
        } }, "Save"),
        !isNew && h("button", { class: "btn", onclick: async () => { const l = state.config.actions.find((x) => x.id === a.id); if (l && await persist([...state.config.actions, { ...l, id: "", label: l.label + " copy", name: "", shortcut: "" }])) { editing = null; rerenderSettings(); } } }, "Duplicate"),
        !isNew && h("button", { class: "btn danger", onclick: async () => { if (confirm(`Delete “${a.label}”?`) && await persist(state.config.actions.filter((x) => x.id !== a.id))) { editing = null; rerenderSettings(); } } }, "Delete"),
        !isNew && a.kind === "script" && h("button", { class: "btn", title: "Run the saved version", onclick: () => { document.querySelector("#settings").close(); invoke(a); } }, "Test run")),
    ].filter(Boolean));
  };
  draw();
  return body;
}
