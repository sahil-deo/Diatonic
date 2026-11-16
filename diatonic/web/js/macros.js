// Macros: declarative JSON sequences of commands, runner, recorder, and storage sync.
import { api } from "./api.js";
import { on, emit } from "./store.js";
import { toast } from "./dom.js";
import * as commands from "./commands.js";
import { compileWhen } from "./keys.js";
import { flags } from "./keymap.js";

let macrosList = [];
let recording = false;
let recordingName = "";
let recordedSteps = [];
let recorderHooked = false;

function showToast(msg, kind) {
  if (typeof document !== "undefined") {
    try { toast(msg, kind); } catch {}
  }
}

export function getMacros() {
  return macrosList;
}

export function isRecording() {
  return recording;
}

export async function loadMacros() {
  try {
    const res = await api.get("/api/macros");
    macrosList = res.macros || [];
    emit("macros", macrosList);
    return macrosList;
  } catch (e) {
    console.warn("Failed to load macros:", e);
    return [];
  }
}

export async function saveMacros(list) {
  try {
    const res = await api.put("/api/macros", list);
    macrosList = res.macros || [];
    emit("macros", macrosList);
    return macrosList;
  } catch (e) {
    showToast(e.message, "error");
    throw e;
  }
}

export function testCondition(cond, flagsProvider = flags) {
  if (!cond) return true;
  try {
    const fn = compileWhen(cond);
    const fl = typeof flagsProvider === "function" ? flagsProvider() : flagsProvider;
    return fn(fl || new Set());
  } catch (e) {
    console.warn("Macro condition evaluation failed:", e);
    return false;
  }
}

export async function runMacro(macro, { flagsProvider = flags } = {}) {
  if (!macro || !Array.isArray(macro.steps)) {
    showToast("Invalid macro", "error");
    return false;
  }
  emit("macro:start", { macro });
  try {
    for (const step of macro.steps) {
      if (step.if && !testCondition(step.if, flagsProvider)) {
        continue;
      }
      if (step.command) {
        await commands.run(step.command, step.args || {});
      }
      if (typeof step.wait === "number" && step.wait > 0) {
        await new Promise((r) => setTimeout(r, step.wait));
      }
    }
    emit("macro:done", { macro });
    showToast(`Macro "${macro.name}" completed`);
    return true;
  } catch (e) {
    showToast(`Macro "${macro.name}" failed: ${e.message}`, "error");
    emit("macro:error", { macro, error: e });
    return false;
  }
}

export async function runById(macroId) {
  const target = String(macroId || "").toLowerCase();
  const m = macrosList.find((x) => x.id.toLowerCase() === target || x.name.toLowerCase() === target);
  if (!m) {
    showToast(`No macro "${macroId}" found`, "error");
    return false;
  }
  return runMacro(m);
}

export function startRecording(name = "New Macro") {
  recording = true;
  recordingName = name.trim() || "New Macro";
  recordedSteps = [];
  emit("macro:recording", { recording: true, name: recordingName });
  showToast(`Recording macro: ${recordingName}`);
}

export function stopRecording() {
  if (!recording) return null;
  recording = false;
  const result = { name: recordingName, steps: [...recordedSteps] };
  emit("macro:recording", { recording: false });
  showToast(`Recorded ${result.steps.length} steps`);
  return result;
}

export async function saveRecorded(name, description = "") {
  const rec = stopRecording();
  if (!rec || !rec.steps.length) {
    showToast("No steps were recorded", "error");
    return null;
  }
  const mid = "macro_" + (name || rec.name).toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const newMacro = {
    id: mid || `macro_${Date.now()}`,
    name: name || rec.name,
    description: description || `Recorded ${new Date().toLocaleTimeString()}`,
    steps: rec.steps,
  };
  const nextList = [...macrosList.filter((m) => m.id !== newMacro.id), newMacro];
  await saveMacros(nextList);
  showToast(`Saved macro "${newMacro.name}"`);
  return newMacro;
}

export async function saveCurrentLayoutAsMacro(name, description = "") {
  const ws = await import("./workspace.js");
  const tree = ws.getTree();
  if (!tree) {
    showToast("No layout to save", "error");
    return null;
  }
  const macroName = name.trim() || "Saved Layout";
  const mid = "layout_" + macroName.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
  const newMacro = {
    id: mid || `layout_${Date.now()}`,
    name: macroName,
    description: description || "Restores workspace layout",
    steps: [
      { command: "workspace.applyTree", args: { tree } }
    ]
  };
  const nextList = [...macrosList.filter((m) => m.id !== newMacro.id), newMacro];
  await saveMacros(nextList);
  showToast(`Saved layout macro "${newMacro.name}"`);
  return newMacro;
}

export function hookRecorder() {
  if (recorderHooked) return;
  recorderHooked = true;
  commands.onRun((id, args) => {
    if (!recording) return;
    if (id.startsWith("macro.") || id.startsWith("palette.")) return;
    recordedSteps.push({
      command: id,
      ...(args && Object.keys(args).length ? { args } : {})
    });
  });
}

export function initMacros() {
  hookRecorder();
  loadMacros();
  on("macros_changed", loadMacros);
  on("run_macro", (ev) => {
    if (ev?.macro_id) {
      runById(ev.macro_id);
    }
  });
}
