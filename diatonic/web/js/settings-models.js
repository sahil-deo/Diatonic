// Settings → Models: the models you switch between from the status bar. Local (Ollama) or cloud (Gemini via
// Google AI Studio, Groq, any OpenAI-compatible API). Keys go to the keychain, one per API host, never back to the page.
import { h, toast } from "./dom.js";
import { api } from "./api.js";
import { emit } from "./store.js";
import { field } from "./form.js";
import { rerenderSettings } from "./settings.js";

let data = null, editing = null;
const tests = {};
const reload = async () => { data = await api.get("/api/models"); rerenderSettings(); emit("models-changed"); };
const host = (u) => { try { return new URL(u).hostname; } catch { return u; } };

export function modelsPane() {
  if (!data) { reload().catch((e) => toast(e.message, "error")); return [h("div", { class: "skeleton" })]; }
  return [
    h("div", { class: "help", style: "margin-bottom:12px" }, "Click the model name in the status bar to switch. The choice applies everywhere: chats, spoken summaries and scheduled jobs."),
    ...data.models.map(row),
    editing ? editor() : h("button", { class: "btn primary", onclick: () => { editing = { preset: "ollama", ...data.presets.ollama, model: "", label: "" }; rerenderSettings(); } }, "+ Add a model"),
    h("div", { style: "height:18px" }), h("div", { class: "p-section" }, "Cloud models and your data"),
    h("div", { class: "help" }, "With a cloud model, your messages go to that provider. Private apps (Mail, Calendar, Files, Notes, Memory, GitHub and apps you built) "
      + "only share data after you approve it, once or for the chat. Remembered facts and @file contents stay local. Change which apps count as private in Settings → Apps."),
  ];
}

function row(m) {
  const t = tests[m.id];
  return h("div", { class: `model-row ${m.active ? "on" : ""}` },
    h("div", { class: "grow" },
      h("div", {}, h("strong", {}, m.label), " ", h("span", { class: `pill ${m.cloud ? "cloud" : "local"}` }, m.cloud ? "cloud" : "local"),
        m.cloud && !m.key_set ? h("span", { class: "pill warn" }, "no key") : null),
      h("div", { class: "p-s" }, `${m.model} · ${host(m.endpoint)} · ${Math.round(m.context / 1024)}k context`),
      t && h("div", { class: `p-s ${t.ok && t.tools ? "ok-t" : "err-t"}` }, t.busy ? "Testing…" : !t.ok ? t.error
        : `Answered in ${t.seconds}s · ${t.tools ? "can call tools" : "did NOT call the tool: it may not work well as the agent"}`)),
    m.active ? h("span", { class: "p-s" }, "In use") : h("button", { class: "btn sm primary", onclick: () => use(m.id) }, "Use"),
    h("button", { class: "btn sm", onclick: () => test(m.id) }, "Test"),
    h("button", { class: "btn sm", onclick: () => { editing = { ...m, preset: presetOf(m) }; rerenderSettings(); } }, "Edit"),
    !m.active && h("button", { class: "icon-btn sm", title: "Delete", "aria-label": "Delete", html: "×", onclick: () => del(m) }));
}

const presetOf = (m) => Object.entries(data.presets).find(([k, p]) => k !== "openai" && p.provider === m.provider && (m.provider === "ollama" || p.endpoint === m.endpoint))?.[0] || "openai";

function editor() {
  const e = editing, p = data.presets;
  const cloud = e.preset !== "ollama" && !/localhost|127\.0\.0\.1/.test(e.endpoint || "");
  const keySet = data.models.some((m) => host(m.endpoint) === host(e.endpoint) && m.key_set);
  const list = h("datalist", { id: "model-names" });
  const name = h("input", { type: "text", value: e.model, list: "model-names", placeholder: e.preset === "ollama" ? "qwen3:8b" : e.preset === "gemini" ? "gemini-…" : "model name", oninput: (ev) => (e.model = ev.target.value.trim()) });
  const key = h("input", { type: "password", autocomplete: "off", placeholder: keySet ? "Saved. Leave empty to keep it" : "Paste the API key", oninput: (ev) => (e.key = ev.target.value) });
  const loadNames = async () => {
    const q = new URLSearchParams({ provider: p[e.preset]?.provider || "openai", endpoint: e.endpoint || "" });
    if (e.key) q.set("key", e.key);
    const r = await api.get(`/api/models/available?${q}`);
    if (r.error) return toast(r.error, "error");
    list.replaceChildren(...r.names.map((n) => h("option", { value: n }))); toast(`${r.names.length} models found. Start typing to pick one.`); name.focus();
  };
  const save = async () => {
    try { data = await api.post("/api/models", { ...e, provider: p[e.preset]?.provider }); editing = null; rerenderSettings(); emit("models-changed"); toast("Model saved"); }
    catch (x) { toast(x.message, "error"); }
  };
  return h("div", { class: "model-edit" },
    h("div", { class: "p-section" }, e.id ? `Edit ${e.label}` : "Add a model"),
    field("Provider", h("select", { onchange: (ev) => { const k = ev.target.value; Object.assign(e, { preset: k, endpoint: p[k].endpoint, context: p[k].context }); rerenderSettings(); } },
      ...Object.entries(p).map(([k, v]) => h("option", { value: k, selected: k === e.preset }, v.label)))),
    field("Model name", h("div", { class: "quick-add" }, name, h("button", { class: "btn sm", type: "button", onclick: loadNames }, "Load list")), list,
      "Exactly as the provider names it. “Load list” asks the provider which models you can use."),
    field("Show as", h("input", { type: "text", value: e.label, placeholder: "e.g. Gemini Flash", oninput: (ev) => (e.label = ev.target.value) })),
    (e.preset === "openai" || e.preset === "ollama") && field("Endpoint", h("input", { type: "text", value: e.endpoint, placeholder: "https://…/v1", oninput: (ev) => (e.endpoint = ev.target.value.trim()) })),
    cloud && field("API key", key, `Kept in your keychain and sent only to ${host(e.endpoint) || "the provider"}. One key per provider.`),
    h("div", { class: "row" },
      field("Context (tokens)", h("input", { type: "number", min: 2048, step: 1024, value: e.context, oninput: (ev) => (e.context = Number(ev.target.value)) })),
      field("Longest reply (tokens)", h("input", { type: "number", min: 256, step: 256, value: e.max_tokens || 1024, oninput: (ev) => (e.max_tokens = Number(ev.target.value)) }))),
    h("div", { class: "row" }, h("button", { class: "btn primary", onclick: save }, "Save"), h("button", { class: "btn", onclick: () => { editing = null; rerenderSettings(); } }, "Cancel")));
}

async function use(id) {
  try { data = await api.put("/api/models/active", { id }); rerenderSettings(); emit("models-changed"); } catch (e) { toast(e.message, "error"); }
}
async function test(id) {
  tests[id] = { busy: true }; rerenderSettings();
  try { tests[id] = await api.post("/api/models/test", { id }); } catch (e) { tests[id] = { ok: false, error: e.message }; }
  rerenderSettings();
}
async function del(m) {
  if (!confirm(`Remove ${m.label} from your models? (Its API key stays saved for other models on the same provider.)`)) return;
  try { data = await api.del(`/api/models/${encodeURIComponent(m.id)}`); rerenderSettings(); } catch (e) { toast(e.message, "error"); }
}

/** For the status-bar menu. */
export const loadModels = async () => (data = await api.get("/api/models"));
export const resetModels = () => { data = null; };
