// Settings → Models: the models you switch between from the status bar. Local (Ollama) or cloud (Gemini via
// Google AI Studio, Groq, any OpenAI-compatible API). Keys go to the keychain, one per API host, never back to the page.
import { h, toast, debounce } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { field } from "./form.js";
import { rerenderSettings } from "./settings.js";

let data = null, editing = null;
let testInput = "", testResult = null;
const tests = {};
const reload = async () => { data = await api.get("/api/models"); rerenderSettings(); emit("models-changed"); };
const host = (u) => { try { return new URL(u).hostname; } catch { return u; } };

export function modelsPane() {
  if (!data) { reload().catch((e) => toast(e.message, "error")); return [h("div", { class: "skeleton" })]; }
  return [
    h("div", { class: "help", style: "margin-bottom:12px" }, "Click the model name in the status bar to switch. The choice applies everywhere: chats, spoken summaries and scheduled jobs."),
    ...data.models.map(row),
    editing ? editor() : h("button", { class: "btn primary", onclick: () => { editing = { preset: "ollama", ...data.presets.ollama, model: "", label: "" }; rerenderSettings(); } }, "+ Add a model"),
    routingSection(),
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
    m.provider !== "antigravity" && h("button", { class: "btn sm", onclick: () => test(m.id) }, "Test"),
    h("button", { class: "btn sm", onclick: () => { editing = { ...m, preset: presetOf(m) }; rerenderSettings(); } }, "Edit"),
    !m.active && h("button", { class: "icon-btn sm", title: "Delete", "aria-label": "Delete", html: "×", onclick: () => del(m) }));
}

const presetOf = (m) => Object.entries(data.presets).find(([k, p]) => k !== "openai" && p.provider === m.provider && (m.provider === "ollama" || p.endpoint === m.endpoint))?.[0] || "openai";

function editor() {
  const e = editing, p = data.presets;
  const agy = e.preset === "antigravity";   // it signs in itself: no API key, and no list to load
  const cloud = e.preset !== "ollama" && !agy && !/localhost|127\.0\.0\.1/.test(e.endpoint || "");
  const keySet = data.models.some((m) => host(m.endpoint) === host(e.endpoint) && m.key_set);
  const list = h("datalist", { id: "model-names" });
  const name = h("input", { type: "text", value: e.model, list: "model-names", placeholder: agy ? "Leave empty for your account's default" : e.preset === "ollama" ? "qwen3:8b" : e.preset === "gemini" ? "gemini-…" : "model name", oninput: (ev) => (e.model = ev.target.value.trim()) });
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
      agy ? "Run `agy acp --login` in a terminal once to sign in. Click “Load list” to choose a model."
        : "Exactly as the provider names it. “Load list” asks the provider which models you can use."),
    agy && h("div", { class: "row" },
      field("Permissions", h("select", { onchange: async (ev) => { await api.put("/api/config", { antigravity: { permissions: ev.target.value } }); state.config = (await api.state()).config; } },
        h("option", { value: "strict", selected: (state.config?.antigravity?.permissions || "strict") === "strict" }, "Strict (ask before actions)"),
        h("option", { value: "auto", selected: state.config?.antigravity?.permissions === "auto" }, "Auto (like local models: safe actions run automatically)")), "Safe actions run without cards; destructive tools still ask."),
      field("Thinking effort", h("select", { onchange: async (ev) => { await api.put("/api/config", { antigravity: { thought_level: ev.target.value } }); state.config = (await api.state()).config; } },
        h("option", { value: "high", selected: (state.config?.antigravity?.thought_level || "high") === "high" }, "High"),
        h("option", { value: "medium", selected: state.config?.antigravity?.thought_level === "medium" }, "Medium"),
        h("option", { value: "low", selected: state.config?.antigravity?.thought_level === "low" }, "Low")), "Reasoning effort for thinking models.")),
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

async function saveRouting(routing) {
  try {
    data.routing = await api.put("/api/models/routing", routing);
    rerenderSettings();
    toast("Routing updated");
  } catch (e) {
    toast(e.message, "error");
  }
}

function routingSection() {
  const r = data.routing || { enabled: false, rules: [] };
  const rules = r.rules || [];
  const custom = r.custom_rules || [];

  return h("div", { class: "model-routing-pane", style: "margin-top:24px; padding-top:16px; border-top:1px solid var(--border);" },
    h("div", { class: "p-section", style: "margin-bottom:4px;" }, "Model Routing Rules"),
    h("div", { class: "help", style: "margin-bottom:12px;" },
      "Route different kinds of requests to specialized models instead of sending everything to a single model. "
      + "Keep UI navigation on fast local models while routing app creation and deep research to more potent models."
    ),
    h("div", { class: "row", style: "align-items:center; margin-bottom:12px;" },
      h("label", { style: "display:flex; align-items:center; gap:8px; cursor:pointer;" },
        h("input", {
          type: "checkbox",
          checked: Boolean(r.enabled),
          onchange: async (ev) => {
            await saveRouting({ ...r, enabled: ev.target.checked });
          }
        }),
        h("strong", {}, "Enable rule-based model routing")
      )
    ),
    r.enabled ? h("div", { class: "routing-rules-list" },
      ...rules.map((rule) =>
        h("div", { class: "model-route-row" },
          h("div", { class: "grow" },
            h("div", {}, h("strong", {}, rule.name)),
            h("div", { class: "p-s" }, rule.description)
          ),
          h("select", {
            style: "min-width:180px; padding:4px 8px;",
            onchange: async (ev) => {
              rule.model = ev.target.value;
              await saveRouting(r);
            }
          },
            h("option", { value: "", selected: !rule.model }, "(Default active model)"),
            ...data.models.map((m) =>
              h("option", { value: m.id, selected: rule.model === m.id }, `${m.label} (${m.model})`)
            )
          )
        )
      ),
      h("div", { style: "margin-top:14px;" },
        h("div", { class: "p-section", style: "font-size:12px; margin-bottom:6px;" }, "Custom Rules"),
        ...custom.map((cr, idx) =>
          h("div", { class: "model-route-row" },
            h("div", { class: "grow" },
              h("div", {}, h("strong", {}, cr.name || "Custom Rule")),
              h("div", { class: "p-s" }, `Matches pattern: "${cr.pattern}"`)
            ),
            h("select", {
              style: "min-width:180px; padding:4px 8px;",
              onchange: async (ev) => {
                cr.model = ev.target.value;
                await saveRouting(r);
              }
            },
              h("option", { value: "", selected: !cr.model }, "(Default active model)"),
              ...data.models.map((m) =>
                h("option", { value: m.id, selected: cr.model === m.id }, `${m.label} (${m.model})`)
              )
            ),
            h("button", {
              class: "icon-btn sm",
              title: "Delete rule",
              "aria-label": "Delete rule",
              html: "×",
              onclick: async () => {
                r.custom_rules.splice(idx, 1);
                await saveRouting(r);
              }
            })
          )
        ),
        h("button", {
          class: "btn sm",
          style: "margin-top:6px;",
          onclick: async () => {
            const pattern = prompt("Enter a keyword or regex pattern to match:");
            if (!pattern?.trim()) return;
            const name = prompt("Enter a label for this rule (e.g. 'GitHub requests'):") || pattern;
            r.custom_rules = r.custom_rules || [];
            r.custom_rules.push({ id: `custom-${Date.now()}`, name: name.trim(), pattern: pattern.trim(), model: "" });
            await saveRouting(r);
          }
        }, "+ Add custom rule")
      ),
      testBox()
    ) : null
  );
}

function testBox() {
  const resultDiv = h("div", { class: "p-s", style: "margin-top:6px; min-height:16px; color:var(--accent);" },
    testResult ? `⚡ Routes to: ${testResult.route_info?.model_label || testResult.resolved_model?.label} · Tier: ${testResult.rule_name}` : ""
  );
  const testInputEl = h("input", {
    type: "text",
    placeholder: "Type a prompt to test routing (e.g. 'open mail in split' or 'create a timer')...",
    value: testInput,
    style: "width:100%; margin-top:4px;",
    oninput: debounce(async (ev) => {
      testInput = ev.target.value.trim();
      if (!testInput) { testResult = null; resultDiv.textContent = ""; return; }
      try {
        testResult = await api.post("/api/models/routing/test", { text: testInput });
        resultDiv.textContent = `⚡ Routes to: ${testResult.route_info?.model_label || testResult.resolved_model?.label} · Tier: ${testResult.rule_name}`;
      } catch (e) {
        resultDiv.textContent = e.message;
      }
    }, 250)
  });

  return h("div", { style: "margin-top:16px; padding:12px; border:1px dashed var(--border); border-radius: calc(8px * var(--rs, 1)); background:var(--surface);" },
    h("div", { class: "p-section", style: "font-size:12px; margin-bottom:2px;" }, "Test Prompt Routing"),
    h("div", { class: "help", style: "margin-bottom:6px;" }, "Verify which model handles your query under the current rules:"),
    testInputEl,
    resultDiv
  );
}

/** For the status-bar menu. */
export const loadModels = async () => (data = await api.get("/api/models"));
export const resetModels = () => { data = null; };
