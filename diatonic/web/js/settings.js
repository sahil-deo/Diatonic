import { $, h, toast, debounce } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { speaker, eventToHotkey, prettyHotkey } from "./voice.js";
import { paintSpeakToggle, setSpeak, updateHint } from "./voiceui.js";
import { askBrowserPermission } from "./inbox.js";
import { RESERVED, save, saveSoon, field, text, select, toggle } from "./form.js";
import { actionsPane } from "./settings-actions.js";
import { interfacePane } from "./settings-interface.js";
import { modelsPane } from "./settings-models.js";
import { memoryPane } from "./settings-memory.js";
import { accountsPane } from "./settings-accounts.js";

const dlg = () => $("#settings");
let tab = "agent";
export { RESERVED };

/* ---- panes ---- */
function agentPane() {
  const a = state.config.agent;
  const models = state.health?.agent?.models || [];
  const result = h("div", { class: "notice", style: "display:none" });
  return [
    field("Model", h("div", { class: "row", style: "align-items:center" }, h("strong", {}, state.health?.agent?.label || a.model),
      h("button", { class: "btn sm", onclick: () => openSettings("models") }, "Models…")), "Switch from the status bar, or add local and cloud models in Models."),
    h("div", { class: "row" },
      field("Temperature", text(a.temperature, (v) => saveSoon({ agent: { temperature: v } }), { type: "number", step: "0.1", min: 0, max: 2 })),
      field("Max steps", text(a.max_steps, (v) => saveSoon({ agent: { max_steps: v } }), { type: "number", min: 1, max: 20 }))),
    h("div", { class: "row" },
      field("Max reply tokens", text(a.max_tokens, (v) => saveSoon({ agent: { max_tokens: v } }), { type: "number", step: "128", min: 128 }), "Hard cap per model reply."),
      field("Repeat penalty", text(a.repeat_penalty, (v) => saveSoon({ agent: { repeat_penalty: v } }), { type: "number", step: "0.05", min: 1, max: 1.5 })),
      field("Run timeout (s)", text(a.run_timeout, (v) => saveSoon({ agent: { run_timeout: v } }), { type: "number", min: 30 }))),
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
      a.enabled && toggle("Private", "A cloud model must ask before it reads or changes anything here.", a.private,
        async (v) => { await api.putApp(a.id, { private: v }); await refreshApps(); }),
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

function automationPane() {
  const n = state.config.notifications, sc = state.config.scheduler;
  const quiet = (key) => h("input", { type: "time", value: n[key], onchange: (e) => save({ notifications: { [key]: e.target.value } }) });
  return [
    h("div", { class: "p-section" }, "Notifications"),
    toggle("macOS notifications", "Banners in Notification Center, even when the browser is closed. They appear under “Script Editor” because they're sent with AppleScript.", n.native, (x) => save({ notifications: { native: x } })),
    toggle("Browser notifications", "Shown while this page is open in a background tab.", n.browser, async (x) => {
      if (x && (await askBrowserPermission()) !== "granted") { toast("Allow notifications for localhost:5005 in your browser first.", "error"); return renderPane(); }
      save({ notifications: { browser: x } });
    }),
    toggle("Speak reminders", "Reads reminders and finished jobs aloud. Needs the Spoken summary toggle on.", n.speak, (x) => save({ notifications: { speak: x } })),
    toggle("Quiet hours", "No banners or speech in this window; everything still lands in the inbox.", n.quiet_enabled, (x) => save({ notifications: { quiet_enabled: x } })),
    h("div", { class: "row", style: "margin:12px 0 4px" }, field("From", quiet("quiet_start")), field("Until", quiet("quiet_end"))),
    h("div", { class: "row" }, h("button", { class: "btn", onclick: () => api.post("/api/notifications/test").catch((e) => toast(e.message, "error")) }, "Send a test notification")),
    h("div", { style: "height:22px" }),
    h("div", { class: "p-section" }, "Scheduled jobs"),
    toggle("Allow scripts", "Lets scheduled jobs and your script buttons run shell commands with your user's permissions. Only you can turn this on. If the agent proposes a script job you'll see the exact command to approve.", sc.allow_scripts, (x) => {
      if (x && !confirm("Script jobs run shell commands as you, unattended. Only enable this if you want that. Continue?")) return renderPane();
      save({ scheduler: { allow_scripts: x } });
    }),
    h("div", { style: "height:12px" }),
    h("div", { class: "row" },
      field("Script timeout (s)", text(sc.script_timeout, (v) => saveSoon({ scheduler: { script_timeout: v } }), { type: "number", min: 5, max: 900 })),
      field("Chats kept per prompt job", text(sc.keep_prompt_chats, (v) => saveSoon({ scheduler: { keep_prompt_chats: v } }), { type: "number", min: 1, max: 50 })),
      field("Unattended approval wait (s)", text(sc.approval_timeout, (v) => saveSoon({ scheduler: { approval_timeout: v } }), { type: "number", min: 30 }))),
    h("div", { class: "notice" }, "Reminders and jobs only fire while Diatonic is running. To keep it running in the background and start at login, run ", h("code", {}, "diatonic install-service"), " in a terminal (see the README). Anything due while it was off fires once, late, when it starts again."),
  ];
}

const TABS = [["agent", "Agent", agentPane], ["models", "Models", modelsPane], ["voice", "Voice", voicePane], ["accounts", "Accounts", accountsPane], ["apps", "Apps", appsPane], ["automation", "Automation", automationPane], ["actions", "Actions", actionsPane], ["memory", "Memory", memoryPane], ["interface", "Interface", interfacePane]];

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
  if (which && TABS.some((t) => t[0] === which)) tab = which;
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

export { renderPane as rerenderSettings };
export function initSettings() {
  dlg().addEventListener("click", (e) => { if (e.target === dlg()) dlg().close(); });
  dlg().addEventListener("close", () => { paintSpeakToggle(); emit("health"); });
}
