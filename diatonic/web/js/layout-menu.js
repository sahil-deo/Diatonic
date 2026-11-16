// The mouse way to everything the layout and macro commands do: presets, saved layouts, undo/redo, recording.
// Used by the status bar menu and by Settings, so both always show the same thing.
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { on } from "./store.js";
import { openPopover, closePopover } from "./popover.js";
import * as ws from "./workspace.js";
import { getMacros, loadMacros, saveMacros, runMacro, isRecording, startRecording, saveRecorded, stopRecording } from "./macros.js";

// [id for applyPreset, label, columns, rows, number of cells]
const PRESETS = [["single", "Single", 1, 1, 1], ["side-by-side", "Side by side", 2, 1, 2], ["stacked", "Stacked", 1, 2, 2], ["three-column", "Three columns", 3, 1, 3], ["grid", "Grid", 2, 2, 4]];

/** Re-runs `render` whenever `event` fires, until `el` leaves the page. */
function live(el, event, render) {
  const off = on(event, () => { if (!el.isConnected) return off(); render(); });
}

/** A button that asks "Delete?" in place before doing anything, so no dialog is needed. */
function confirmDelete(label, onConfirm) {
  const box = h("span", { class: "lm-del" });
  const idle = () => box.replaceChildren(h("button", { type: "button", class: "icon-btn sm", title: `Delete ${label}`, "aria-label": `Delete ${label}`, html: icon("trash", 14), onclick: (e) => { e.stopPropagation(); ask(); } }));
  const ask = () => box.replaceChildren(
    h("button", { type: "button", class: "btn sm danger", onclick: (e) => { e.stopPropagation(); onConfirm(); } }, "Delete"),
    h("button", { type: "button", class: "btn sm", onclick: (e) => { e.stopPropagation(); idle(); } }, "Keep"));
  idle();
  return box;
}

export function presetGrid() {
  return h("div", { class: "lm-presets" }, PRESETS.map(([id, label, cols, rows, n]) =>
    h("button", { type: "button", class: "lm-preset", title: `${label} layout`, onclick: () => ws.applyPreset(id) },
      h("span", { class: "lm-thumb", style: `grid-template-columns: repeat(${cols}, 1fr); grid-template-rows: repeat(${rows}, 1fr)` },
        Array.from({ length: n }, () => h("i", {}))),
      h("span", {}, label))));
}

/** Save the current layout under a name, and list, switch to and delete saved ones. */
export function layoutManager({ focusSave = false } = {}) {
  const root = h("div", { class: "lm-saved" });
  const render = () => {
    const name = h("input", { type: "text", class: "lm-name", placeholder: "Name this layout, e.g. Dev", "aria-label": "Layout name", maxlength: 60 });
    const save = async () => {
      const v = name.value.trim();
      if (!v) return name.focus();
      await ws.saveCurrentLayout(v);
    };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); save(); } });
    const active = ws.getActiveLayoutId();
    const list = ws.getLayouts();
    root.replaceChildren(
      h("div", { class: "lm-save" }, name, h("button", { type: "button", class: "btn primary sm", onclick: save }, "Save current")),
      list.length
        ? h("div", { class: "lm-list" }, list.map((l) => h("div", { class: `lm-row${l.id === active ? " on" : ""}`, role: "button", tabindex: "0", title: `Switch to ${l.name}`,
            onclick: () => ws.switchLayout(l.id), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); ws.switchLayout(l.id); } } },
            h("span", { class: "lm-row-name" }, l.name, l.id === active && h("em", {}, "current")),
            l.description && l.description !== "Saved layout" && h("span", { class: "lm-row-desc" }, l.description),
            confirmDelete(`layout ${l.name}`, () => ws.deleteLayout(l.id)))))
        : h("div", { class: "lm-empty" }, "No saved layouts yet. Arrange your panels, then name it above."));
    if (focusSave) { focusSave = false; setTimeout(() => name.focus(), 30); }
  };
  render();
  live(root, "layouts", render);
  live(root, "layout:switched", render);
  return root;
}

function historyButtons() {
  const b = (label, ic, fn) => h("button", { type: "button", class: "btn sm", onclick: fn }, h("span", { html: icon(ic, 14) }), label);
  return h("div", { class: "lm-foot" }, b("Undo", "refresh", () => ws.undoLayout()), b("Redo", "refresh", () => ws.redoLayout()), b("Reopen tab", "plus", () => ws.reopenClosedTab()));
}

/** The status bar menu. `anchor` is the button; `focusSave` puts the cursor in the name box. */
export function openLayoutMenu(anchor = document.getElementById("sb-layout"), { focusSave = false } = {}) {
  if (!anchor) return;
  const pop = openPopover(anchor, h("div", { class: "lm" },
    h("div", { class: "lm-title" }, "Layout"),
    presetGrid(),
    h("div", { class: "lm-title" }, "Saved layouts"),
    layoutManager({ focusSave }),
    historyButtons(),
    h("button", { type: "button", class: "lm-link", onclick: async () => { closePopover(); (await import("./settings.js")).openSettings("layouts"); } }, "More in Settings")),
    { align: "right" });
  pop?.place();
}

/** Record, run and delete macros. */
export function macroManager() {
  const root = h("div", { class: "lm-saved" });
  const render = () => {
    const recording = isRecording();
    const name = h("input", { type: "text", class: "lm-name", placeholder: "Name this macro, e.g. Morning setup", "aria-label": "Macro name", maxlength: 60 });
    const begin = () => { startRecording(name.value.trim() || "New macro"); render(); };
    name.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); begin(); } });
    const list = getMacros();
    root.replaceChildren(
      recording
        ? h("div", { class: "lm-save rec" }, h("span", { class: "rec-dot" }), h("span", { class: "grow" }, "Recording. Use the app as usual, then stop."),
            h("button", { type: "button", class: "btn primary sm", onclick: async () => { await saveRecorded(); render(); } }, "Stop and save"),
            h("button", { type: "button", class: "btn sm", onclick: () => { stopRecording(); render(); } }, "Discard"))
        : h("div", { class: "lm-save" }, name, h("button", { type: "button", class: "btn primary sm", onclick: begin }, "Start recording")),
      list.length
        ? h("div", { class: "lm-list" }, list.map((m) => h("div", { class: "lm-row", role: "button", tabindex: "0", title: `Run ${m.name}`,
            onclick: () => runMacro(m), onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); runMacro(m); } } },
            h("span", { class: "lm-row-name" }, m.name),
            h("span", { class: "lm-row-desc" }, `${m.steps?.length || 0} steps`),
            h("button", { type: "button", class: "btn sm", onclick: (e) => { e.stopPropagation(); runMacro(m); } }, "Run"),
            confirmDelete(`macro ${m.name}`, () => saveMacros(getMacros().filter((x) => x.id !== m.id))))))
        : h("div", { class: "lm-empty" }, "No macros yet. A macro replays the things you do, such as opening apps and arranging panels."));
  };
  render();
  loadMacros().then(render);
  live(root, "macros", render);
  live(root, "macro:recording", render);
  return root;
}
