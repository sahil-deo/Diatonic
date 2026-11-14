import { $, h, toast, debounce } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { speaker, eventToHotkey, prettyHotkey } from "./voice.js";
import { paintSpeakToggle, setSpeak, updateHint } from "./voiceui.js";

const dlg = () => $("#settings");
let tab = "agent";
export const RESERVED = { "Meta+K": "command palette", "Meta+,": "settings", "Meta+Shift+O": "new chat" };

async function save(patch) {
  try { state.config = await api.putConfig(patch); emit("config"); }
  catch (e) { toast(e.message, "error"); }
}
const saveSoon = debounce(save, 500);

/* ---- field helpers ---- */
const field = (label, input, help) => h("div", { class: "field" }, h("label", {}, label), input, help && h("div", { class: "help" }, help));
const text = (v, onChange, o = {}) => h("input", { type: o.type || "text", value: v ?? "", placeholder: o.placeholder, list: o.list, step: o.step, min: o.min, max: o.max,
  oninput: (e) => onChange(o.type === "number" ? Number(e.target.value) : e.target.value) });
const select = (v, opts, onChange) => h("select", { onchange: (e) => onChange(e.target.value) },
  opts.map(([val, lbl]) => h("option", { value: val, selected: val === v }, lbl)));
const toggle = (label, desc, checked, onChange) => h("div", { class: "toggle-row" },
  h("div", {}, h("div", {}, label), desc && h("div", { class: "d" }, desc)),
  h("span", { class: "switch" }, h("input", { type: "checkbox", checked, "aria-label": label, onchange: (e) => onChange(e.target.checked) })));

/* ---- panes ---- */
function agentPane() {
  const a = state.config.agent;
  const models = state.health?.agent?.models || [];
  const result = h("div", { class: "notice", style: "display:none" });
  return [
    h("datalist", { id: "models" }, models.map((m) => h("option", { value: m }))),
    field("Provider", select(a.provider, [["ollama", "Ollama (local)"]], (v) => save({ agent: { provider: v } })), "More providers can be added without touching the core."),
    field("Endpoint", text(a.endpoint, (v) => saveSoon({ agent: { endpoint: v } }), { placeholder: "http://localhost:11434" })),
    field("Model", text(a.model, (v) => saveSoon({ agent: { model: v } }), { list: "models" }), "Default: qwen3:4b-instruct. Run `ollama pull <model>` for others."),
    h("div", { class: "row" },
      field("Temperature", text(a.temperature, (v) => saveSoon({ agent: { temperature: v } }), { type: "number", step: "0.1", min: 0, max: 2 })),
      field("Context (tokens)", text(a.num_ctx, (v) => saveSoon({ agent: { num_ctx: v } }), { type: "number", step: "1024", min: 2048 })),
      field("Max steps", text(a.max_steps, (v) => saveSoon({ agent: { max_steps: v } }), { type: "number", min: 1, max: 20 }))),
    field("Custom instructions", h("textarea", { oninput: (e) => saveSoon({ agent: { system_prompt: e.target.value } }), placeholder: "e.g. I work in IST. Default GitHub repo is my-org/api. Keep answers short." }, a.system_prompt),
      "Added to the system prompt on every request."),
    h("div", { class: "row" },
      h("button", { class: "btn", onclick: async () => {
        result.style.display = ""; result.textContent = "Testing…";
        state.health = await api.health(); const g = state.health.agent;
        result.textContent = g.ok && g.model_present ? `Connected. ${g.model} is ready.` : g.error; emit("health");
      } }, "Test connection")),
    result,
  ];
}

function hotkeyField() {
  const v = state.config.voice.hotkey;
  const inp = h("input", { type: "text", class: "hotkey", readonly: true, value: prettyHotkey(v) });
  inp.addEventListener("focus", () => { inp.classList.add("capturing"); inp.value = "Press a combination…"; });
  inp.addEventListener("blur", () => { inp.classList.remove("capturing"); inp.value = prettyHotkey(state.config.voice.hotkey); });
  inp.addEventListener("keydown", (e) => {
    e.preventDefault(); e.stopPropagation();
    if (e.key === "Escape") return inp.blur();
    const hk = eventToHotkey(e); if (!hk) return;
    if (!/Control|Alt|Shift|Meta/.test(hk)) return toast("Include a modifier (⌥, ⌃, ⇧ or ⌘) so it doesn't fire while typing.", "error");
    if (RESERVED[hk]) return toast(`${prettyHotkey(hk)} is already used for ${RESERVED[hk]}.`, "error");
    save({ voice: { hotkey: hk } }).then(() => { updateHint(); inp.blur(); });
  });
  return field("Push-to-talk shortcut", inp, "Hold to record, release to finish. Zen/Firefox keep some shortcuts for themselves, so pick one that works and click to change it.");
}

function voicePane() {
  const v = state.config.voice, hv = state.health?.voice;
  const setup = hv?.setup || {};
  const ready = hv?.stt.ready && hv?.tts.ready;
  const status = h("div", { class: `notice ${ready ? "" : "warn"}` });
  const paint = () => {
    const s = state.health?.voice?.setup || {};
    status.replaceChildren(...[
      h("div", {}, `Speech-to-text (Whisper ${v.stt_model}): ${hv?.stt.available ? (hv.stt.ready ? "ready" : "not downloaded") : "not installed"}`),
      h("div", {}, `Text-to-speech (Piper ${v.tts_voice}): ${hv?.tts.available ? (hv.tts.ready ? "ready" : "not downloaded") : "not installed"}`),
      !hv?.stt.available && h("div", { style: "margin-top:6px" }, "Install with: pip install 'diatonic[voice]'"),
      s.message && h("div", { style: "margin-top:6px" }, s.message)].filter(Boolean));
  };
  paint();
  let poll;
  const download = h("button", { class: "btn primary", onclick: async () => {
    download.disabled = true;
    await api.setupVoice();
    poll = setInterval(async () => {
      state.health = await api.health();
      const s = state.health.voice.setup; paint();
      if (s.state !== "running") { clearInterval(poll); download.disabled = false; emit("health"); if (s.state === "done") renderPane(); }
    }, 1500);
  } }, "Download voice models");
  if (setup.state === "running") download.disabled = true;

  return [
    status,
    h("div", { class: "row", style: "margin-bottom:16px" }, download,
      h("button", { class: "btn", onclick: () => { speaker.unlock(); speaker.speak("Hi, this is how I'll sound when I summarize a task."); } }, "Test voice")),
    toggle("Spoken summaries", "After a task finishes, say a short summary. Never the full reply.", v.speak_summary, (on) => setSpeak(on)),
    toggle("Send voice input automatically", "Off = review and edit the transcript before sending.", v.auto_send, (on) => save({ voice: { auto_send: on } })),
    h("div", { style: "height:16px" }),
    h("div", { class: "p-section" }, "When to speak"),
    toggle("After runs that used apps", null, v.speak_when.tools, (x) => save({ voice: { speak_when: { tools: x } } })),
    toggle("When I need to approve something", null, v.speak_when.approvals, (x) => save({ voice: { speak_when: { approvals: x } } })),
    toggle("When something fails", null, v.speak_when.errors, (x) => save({ voice: { speak_when: { errors: x } } })),
    toggle("Only when this tab isn't focused", "Stay quiet while you're looking at it.", v.speak_when.only_unfocused, (x) => save({ voice: { speak_when: { only_unfocused: x } } })),
    h("div", { style: "height:16px" }),
    field("Also speak after slow replies (seconds)", text(v.speak_when.min_seconds, (x) => saveSoon({ voice: { speak_when: { min_seconds: x } } }), { type: "number", min: 0 })),
    hotkeyField(),
    h("div", { class: "row" },
      field("Speech model", select(v.stt_model, ["tiny.en", "base.en", "small.en", "medium.en"].map((m) => [m, m]), (x) => save({ voice: { stt_model: x } }))),
      field("Voice speed", text(v.tts_speed, (x) => saveSoon({ voice: { tts_speed: x } }), { type: "number", step: "0.1", min: 0.5, max: 2 }))),
    field("Piper voice", text(v.tts_voice, (x) => saveSoon({ voice: { tts_voice: x } }), { placeholder: "en_US-lessac-medium" }), "Any voice from the Piper catalog, e.g. en_US-amy-medium. Press “Download voice models” after changing."),
    field("Summary model (optional)", text(v.summary_model, (x) => saveSoon({ voice: { summary_model: x } }), { placeholder: "Same as the agent" })),
  ];
}

function appsPane() {
  return state.apps.map((a) => {
    const inputs = {};
    const fields = a.fields.map((f) => {
      const inp = f.secret
        ? h("input", { type: "password", placeholder: f.set ? "•••••••• saved. Type to replace" : f.placeholder, autocomplete: "off" })
        : h("input", { type: "text", value: f.value, placeholder: f.placeholder });
      inputs[f.key] = [f, inp];
      return field(f.label, inp, f.help);
    });
    const saveApp = async () => {
      const secrets = {}, settings = {};
      for (const [k, [f, inp]] of Object.entries(inputs)) (f.secret ? (inp.value && (secrets[k] = inp.value)) : (settings[k] = inp.value));
      try { await api.putApp(a.id, { secrets, settings }); await refreshApps(); toast(`${a.name} saved`); } catch (e) { toast(e.message, "error"); }
    };
    const st = a.status;
    return h("div", { class: "app-card" },
      h("header", {}, h("h3", {}, a.name), h("div", { class: "grow" }),
        h("span", { class: "switch" }, h("input", { type: "checkbox", checked: a.enabled, "aria-label": `Enable ${a.name}`,
          onchange: async (e) => { await api.putApp(a.id, { enabled: e.target.checked }); await refreshApps(); } }))),
      h("div", { class: "muted", style: "margin-bottom:10px" }, a.description),
      a.enabled && st && h("div", { class: `notice ${st.connected ? "" : "warn"}` },
        h("span", { class: `dot ${st.connected ? "ok" : "warn"}`, style: "margin-right:8px" }), st.detail,
        st.action && h("a", { class: "btn sm", style: "margin-left:10px;text-decoration:none", href: st.action.href }, st.action.label)),
      ...fields,
      fields.length ? h("button", { class: "btn", onclick: saveApp }, "Save") : null,
      a.enabled && h("div", { class: "p-section" }, "Tools"),
      ...(a.enabled ? a.tools.map((t) => h("div", { class: "tool-row", title: t.description },
        h("span", { class: "n" }, t.title), h("span", { class: `pill ${t.risk}` }, t.risk),
        t.risk === "read" ? h("label", {}, "auto") : h("label", {}, h("input", { type: "checkbox", checked: t.always_allow,
          onchange: async (e) => {
            const cur = a.tools.filter((x) => x.always_allow).map((x) => x.name).filter((n) => n !== t.name);
            if (e.target.checked) cur.push(t.name);
            await api.putApp(a.id, { always_allow: cur }); await refreshApps();
          } }), "always allow"))) : []));
  });
}

function appearancePane() {
  return [field("Theme", select(state.config.appearance.theme, [["system", "Match system"], ["dark", "Dark"], ["light", "Light"]], (v) => save({ appearance: { theme: v } })))];
}

const TABS = [["agent", "Agent", agentPane], ["voice", "Voice", voicePane], ["apps", "Apps", appsPane], ["appearance", "Appearance", appearancePane]];

export async function refreshApps() {
  state.apps = await api.get("/api/apps"); emit("apps"); if (dlg().open) renderPane();
}

function renderPane() {
  const pane = $(".set-pane", dlg()); if (!pane) return;
  const top = pane.scrollTop;
  pane.replaceChildren(...TABS.find((t) => t[0] === tab)[2]());
  pane.scrollTop = top;
  $$nav().forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
}
const $$nav = () => [...dlg().querySelectorAll(".set-nav button")];

export async function openSettings(which) {
  if (which) tab = which;
  state.health = await api.health().catch(() => state.health);
  const d = dlg();
  d.replaceChildren(
    h("div", { class: "set-head" }, "Settings", h("div", { class: "grow" }),
      h("button", { class: "icon-btn", "aria-label": "Close", onclick: () => d.close(), html: "✕" })),
    h("div", { class: "set-body" },
      h("nav", { class: "set-nav" }, TABS.map(([id, label]) => h("button", { "data-tab": id, onclick: () => { tab = id; renderPane(); } }, label))),
      h("div", { class: "set-pane" })));
  if (!d.open) d.showModal();
  renderPane();
}

export function initSettings() {
  dlg().addEventListener("click", (e) => { if (e.target === dlg()) dlg().close(); });
  dlg().addEventListener("close", () => { paintSpeakToggle(); emit("health"); });
}
