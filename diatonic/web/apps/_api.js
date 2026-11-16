// The one panel every API app uses: set the key once, then each action is a small form with a Run button.
// Calls go through the server (/api/xapps/...), so the key never reaches this page.
import { state } from "/js/store.js";

const enc = encodeURIComponent;
const remember = (k, v) => { try { v === undefined ? (v = JSON.parse(localStorage.getItem(k) || "null")) : localStorage.setItem(k, JSON.stringify(v)); } catch {} return v; };

export async function render(el, ctx) {
  const { api, h, toast } = ctx;
  const id = ctx.app || state.panelApp || ctx.id;
  const info = await api.get(`/api/xapps/${enc(id)}`);
  const m = info.manifest, host = new URL(m.base_url).hostname;

  if (info.needs_key && !info.key_set) {
    const key = h("input", { type: "password", placeholder: "Paste your API key", autocomplete: "off", "aria-label": "API key" });
    const save = async () => {
      if (!key.value.trim()) return;
      try { await api.put(`/api/apps/${enc(id)}/config`, { secrets: { api_key: key.value.trim() } }); toast("Key saved"); ctx.reload(); }
      catch (e) { toast(e.message, "error"); }
    };
    key.addEventListener("keydown", (e) => e.key === "Enter" && save());
    return el.replaceChildren(h("div", { class: "xapp" },
      h("h3", {}, m.title), h("p", { class: "muted" }, `This app needs an API key for ${host}. It is kept in your keychain and only ever sent to ${host}, from this computer.`),
      h("div", { class: "quick-add" }, key, h("button", { class: "btn primary sm", onclick: save }, "Save key"))));
  }

  const actions = Object.entries(m.actions);
  el.replaceChildren(h("div", { class: "xapp" },
    h("div", { class: "xapp-head" }, h("h3", {}, m.title), h("span", { class: "p-s" }, host),
      info.needs_key && h("button", { class: "btn sm", style: "margin-left:auto", onclick: () => changeKey(ctx, id) }, "Change key")),
    !actions.length && h("div", { class: "p-empty" }, "No actions yet. Ask the agent to add one, e.g. “add an action to the weather app for the current weather”."),
    ...actions.map(([name, a]) => actionCard(ctx, id, name, a))));
}

function actionCard(ctx, id, name, a) {
  const { api, h } = ctx;
  const saved = remember(`xapp:${id}:${name}`) || {};
  const nice = (s) => s.replace(/[_.-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  const inputs = a.params.map((p) => h("input", { type: "text", placeholder: p.default || (p.required ? "required" : "optional"), value: saved[p.name] ?? p.default ?? "", "aria-label": nice(p.label), "data-p": p.name }));
  const labelled = inputs.map((i, n) => h("label", { class: "xapp-field" }, h("span", {}, nice(a.params[n].label), a.params[n].required ? h("b", {}, " *") : null), i));
  const out = h("div", { class: "xapp-out" });
  const run = async () => {
    const args = Object.fromEntries(inputs.map((i) => [i.dataset.p, i.value.trim()]).filter(([, v]) => v));
    remember(`xapp:${id}:${name}`, args);
    out.replaceChildren(h("div", { class: "skeleton" }));
    try { show(h, out, await api.post(`/api/xapps/${enc(id)}/call/${enc(name)}`, { args })); }
    catch (e) { out.replaceChildren(h("div", { class: "err" }, e.message)); }
  };
  inputs.forEach((i) => i.addEventListener("keydown", (e) => e.key === "Enter" && run()));
  const card = h("section", { class: "xapp-card" },
    h("div", { class: "xapp-title" }, h("strong", {}, a.title), h("span", { class: "p-s" }, `${a.method} ${a.path}`)),
    h("div", { class: "xapp-form" }, ...labelled, h("button", { class: `btn sm ${a.method === "GET" ? "primary" : "danger"}`, onclick: run }, a.method === "GET" ? "Run" : `Run (${a.method})`)),
    out);
  if (a.method === "GET" && a.params.every((p) => !p.required || p.default || saved[p.name])) queueMicrotask(run);   // show something straight away
  return card;
}

function show(h, out, r) {
  if (r.fields) {
    return out.replaceChildren(h("dl", { class: "xapp-fields" }, ...Object.entries(r.fields).flatMap(([k, v]) => [
      h("dt", {}, k.split(".").slice(-2).join(" ").replace(/_/g, " ")), h("dd", {}, v == null ? "—" : typeof v === "object" ? JSON.stringify(v) : String(v))])));
  }
  out.replaceChildren(h("pre", { class: "xapp-json" }, typeof r.data === "string" ? r.data : JSON.stringify(r.data, null, 2)));
}

async function changeKey(ctx, id) {
  const v = prompt("New API key (stored in your keychain):");
  if (!v?.trim()) return;
  try { await ctx.api.put(`/api/apps/${enc(id)}/config`, { secrets: { api_key: v.trim() } }); ctx.toast("Key saved"); ctx.reload(); }
  catch (e) { ctx.toast(e.message, "error"); }
}
