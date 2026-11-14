// Mic button, push-to-talk hotkey, spoken-summary toggle.
import { $, toast } from "./dom.js";
import { setIcon } from "./icons.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { Recorder, speaker, matchesHotkey, parseHotkey, prettyHotkey } from "./voice.js";

let rec = null, mode = "idle"; // idle | recording | transcribing
let hooks = {};
const mic = () => $("#mic");

function setMode(m) {
  mode = m;
  const b = mic(), c = $("#composer");
  b.classList.toggle("recording", m === "recording");
  b.classList.toggle("busy", m === "transcribing");
  c.classList.toggle("recording", m === "recording");
  setIcon($("#mic-icon"), "mic", 18);
  if (m !== "recording") $("#mic-level").style.transform = "scale(0)";
  b.setAttribute("aria-label", m === "recording" ? "Stop recording" : "Voice input");
  updateHint();
}

export function updateHint(extra) {
  const hk = prettyHotkey(state.config?.voice.hotkey || "");
  const el = $("#hint");
  if (mode === "recording") el.innerHTML = `<span class="live">● Listening…</span><span>release ${hk} or click the mic to finish</span>`;
  else if (mode === "transcribing") el.innerHTML = "<span>Transcribing…</span>";
  else el.innerHTML = `<span>Enter to send · Shift+Enter for a new line · / for commands</span><span>Hold ${hk} to talk</span>${extra ? `<span>${extra}</span>` : ""}`;
}

function voiceReady() {
  const s = state.health?.voice?.stt;
  if (!s?.available) { toast("Voice input needs the voice extra: pip install 'diatonic[voice]'", "error"); hooks.openSettings("voice"); return false; }
  if (!s.ready) { toast("Download the speech models first (Settings → Voice).", "error"); hooks.openSettings("voice"); return false; }
  return true;
}

async function begin(how) {
  if (mode !== "idle" || !voiceReady()) return;
  speaker.stop(); // never listen to ourselves
  rec = new Recorder({
    vad: how === "toggle",
    onSilence: () => finish(),
    onLevel: (l) => { $("#mic-level").style.transform = `scale(${0.3 + l * 1.4})`; },
  });
  try { await rec.start(); }
  catch (e) {
    rec = null;
    toast(e.name === "NotAllowedError" ? "Microphone blocked. Click the mic icon in the address bar to allow it for localhost:5005." : `Can't use the microphone: ${e.message}`, "error");
    return;
  }
  setMode("recording");
}

async function finish() {
  if (mode !== "recording" || !rec) return;
  const r = rec; rec = null;
  setMode("transcribing");
  try {
    const { pcm, heard, seconds } = await r.stop();
    if (!heard || seconds < 0.4) { toast("Didn't catch anything."); return; }
    const text = (await api.transcribe(pcm)).trim();
    if (!text) { toast("Didn't catch that. Try again?"); return; }
    if (state.config.voice.auto_send) hooks.send(text);
    else hooks.setInput(text);
  } catch (e) { toast(e.message, "error"); }
  finally { setMode("idle"); }
}

export function initVoiceUI(h) {
  hooks = h;
  setMode("idle");
  mic().addEventListener("click", () => (mode === "idle" ? begin("toggle") : mode === "recording" ? finish() : null));

  addEventListener("keydown", (e) => {
    if (e.repeat || !state.config) return;
    if (matchesHotkey(e, state.config.voice.hotkey)) { e.preventDefault(); begin("ptt"); }
  });
  addEventListener("keyup", (e) => {
    if (mode !== "recording" || !state.config) return;
    const { key, mods } = parseHotkey(state.config.voice.hotkey);
    const k = (e.code || "").replace(/^Key|^Digit/, "");
    const modReleased = ["Control", "Alt", "Shift", "Meta"].some((m) => mods.has(m) && !e.getModifierState(m));
    if (k === key || modReleased) finish();
  });
  addEventListener("blur", () => { if (mode === "recording" && !rec?.vad) finish(); });
}

/* ---- spoken summary toggle ---- */
export function paintSpeakToggle() {
  const on = !!state.config?.voice.speak_summary;
  const b = $("#speak-toggle");
  b.setAttribute("aria-pressed", String(on));
  setIcon($("#speak-icon"), on ? "volume" : "mute", 16);
}
export async function setSpeak(on) {
  if (on) speaker.unlock();
  else speaker.stop();
  state.config = await api.putConfig({ voice: { speak_summary: on } });
  paintSpeakToggle(); emit("config");
}
