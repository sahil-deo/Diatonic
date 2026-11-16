// Settings → Layouts and Macros: the same managers the status bar menu uses, with a line of explanation each.
import { h } from "./dom.js";
import { presetGrid, layoutManager, macroManager } from "./layout-menu.js";

export function layoutsPane() {
  return [
    h("div", { class: "help", style: "margin-bottom:14px" }, "A layout is how your panels are arranged on screen. Pick a preset, or arrange panels yourself and save the result under a name to come back to it later."),
    h("div", { class: "p-section" }, "Presets"), presetGrid(),
    h("div", { class: "p-section", style: "margin-top:18px" }, "Saved layouts"), layoutManager(),
  ];
}

export function macrosPane() {
  return [
    h("div", { class: "help", style: "margin-bottom:14px" }, "A macro replays a list of steps, such as opening apps and arranging panels. Record one by doing the steps yourself, then run it from here, the command bar (/run) or the scheduler."),
    macroManager(),
  ];
}
