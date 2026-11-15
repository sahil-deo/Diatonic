// Voice: microphone capture (16 kHz PCM for local Whisper) and spoken-summary playback (Piper).
import { api } from "./api.js";

/* ---------- hotkeys ---------- */
const MODS = ["Control", "Alt", "Shift", "Meta"];
const SYMBOL = { Control: "⌃", Alt: "⌥", Shift: "⇧", Meta: "⌘" };
const keyName = (e) => (e.code || "").replace(/^Key|^Digit/, "");

export function parseHotkey(s) {
  const parts = (s || "").split("+");
  return { key: parts.pop(), mods: new Set(parts) };
}
export function eventToHotkey(e) {
  const key = keyName(e);
  if (!key || /^(Control|Alt|Shift|Meta)(Left|Right)$/.test(key)) return null;
  return [...MODS.filter((m) => e.getModifierState(m)), key].join("+");
}
export function matchesHotkey(e, hk) {
  const { key, mods } = parseHotkey(hk);
  return keyName(e) === key && MODS.every((m) => e.getModifierState(m) === mods.has(m));
}
export function prettyHotkey(hk) {
  const { key, mods } = parseHotkey(hk);
  return MODS.filter((m) => mods.has(m)).map((m) => SYMBOL[m]).join("") + (key === "Space" ? "Space" : key);
}

/* ---------- recorder ---------- */
function downsample(chunks, inRate, outRate = 16000) {
  let n = 0; for (const c of chunks) n += c.length;
  const flat = new Float32Array(n); let o = 0;
  for (const c of chunks) { flat.set(c, o); o += c.length; }
  const ratio = inRate / outRate, len = Math.floor(n / ratio), out = new Int16Array(len);
  for (let i = 0; i < len; i++) {
    const a = Math.floor(i * ratio), b = Math.min(n, Math.floor((i + 1) * ratio));
    let sum = 0; for (let k = a; k < b; k++) sum += flat[k];
    const v = Math.max(-1, Math.min(1, sum / Math.max(1, b - a)));
    out[i] = v < 0 ? v * 0x8000 : v * 0x7fff;
  }
  return out;
}

export class Recorder {
  constructor({ onLevel, onSilence, vad = false, maxSeconds = 90 } = {}) {
    Object.assign(this, { onLevel, onSilence, vad, maxSeconds });
    this.chunks = []; this.active = false;
  }
  async start() {
    this.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    this.ctx = new AudioContext();
    await this.ctx.audioWorklet.addModule("/js/pcm-worklet.js");
    this.src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "pcm-capture");
    this.src.connect(this.node);
    this.t0 = performance.now(); this.heard = false; this.quietSince = null; this.active = true;
    this.node.port.onmessage = (e) => {
      if (!this.active) return;
      const d = e.data; this.chunks.push(d);
      let s = 0; for (let i = 0; i < d.length; i++) s += d[i] * d[i];
      const rms = Math.sqrt(s / d.length);
      this.onLevel?.(Math.min(1, rms * 8));
      const now = performance.now();
      if (rms > 0.02) { this.heard = true; this.quietSince = null; }
      else if (this.heard && this.quietSince == null) this.quietSince = now;
      if (this.vad && this.heard && this.quietSince && now - this.quietSince > 1400) { this.onSilence?.(); this.vad = false; }
      if ((now - this.t0) / 1000 > this.maxSeconds) { this.onSilence?.(); }
    };
  }
  async stop() {
    this.active = false;
    const rate = this.ctx.sampleRate;
    this.stream.getTracks().forEach((t) => t.stop());
    this.node.disconnect(); this.src.disconnect();
    await this.ctx.close();
    return { pcm: downsample(this.chunks, rate), heard: this.heard, seconds: (performance.now() - this.t0) / 1000 };
  }
  async cancel() { if (this.active) await this.stop(); }
}

/* ---------- spoken output ---------- */
// 0.1 s of silence; playing it inside a click unlocks audio under autoplay policies.
const SILENCE = "data:audio/wav;base64,UklGRiQAAABXQVZFZm10IBAAAAABAAEAIlYAAESsAAACABAAZGF0YQAAAAA=";

export const speaker = {
  audio: null, url: null, token: 0,
  unlock() {
    try { const a = new Audio(SILENCE); a.volume = 0; a.play().catch(() => {}); } catch {}
  },
  stop() {
    this.token++;
    if (this.audio) { this.audio.pause(); this.audio = null; }
    if (this.url) { URL.revokeObjectURL(this.url); this.url = null; }
    speechSynthesis?.cancel();
  },
  async speak(text) {
    const now = Date.now();
    if (text === this.lastText && now - this.lastAt < 4000) return;   // never say the same thing twice in a row
    this.lastText = text; this.lastAt = now;
    this.stop();
    const my = this.token;
    let blob = null;
    try { blob = await api.tts(text); } catch {}
    if (my !== this.token) return;
    if (blob) {
      this.url = URL.createObjectURL(blob);
      this.audio = new Audio(this.url);
      this.audio.onended = () => this.stop();
      try { await this.audio.play(); return; } catch { /* fall through to system voice */ }
    }
    if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(text));
  },
};
