// The one panel every tool app uses (an app the agent built on Calendar, Mail, GitHub... tools). Actions that only read
// and need no input fill the list at the top; actions that need an item (an event, an email) become buttons on each row;
// the rest are forms. Everything runs through the server (/api/tapps/...), on the user's own accounts.
import { state } from "/js/store.js";
import { icon } from "/js/icons.js";

const enc = encodeURIComponent;
const nice = (s) => s.replace(/[_.-]+/g, " ").replace(/^\w/, (c) => c.toUpperCase());
const LABEL = (x) => x.title || x.summary || x.subject || x.name || x.ref || "";
let refreshList = null;

export async function render(el, ctx) {
  const { api, h } = ctx;
  const id = state.panelApp;
  const info = await api.get(`/api/tapps/${enc(id)}`);
  const acts = Object.entries(info.actions);
  const broken = acts.filter(([, a]) => a.error);
  const ok = acts.filter(([, a]) => !a.error);
  const lists = ok.filter(([, a]) => a.risk === "read" && !a.ref && !a.schema.required.length);
  const perItem = ok.filter(([, a]) => a.ref);
  const forms = ok.filter(([n, a]) => !a.ref && !lists.some(([m]) => m === n));
  const box = h("div", { class: "tapp-list" });
  // With a list to look at, forms (Schedule...) open from a button; without one they're the app, so they stay open.
  const formCards = forms.map(([n, a]) => {
    const card = formCard(ctx, id, n, a, {}, () => { refreshList?.(); if (lists.length) card.hidden = true; });
    card.hidden = lists.length > 0;
    const toggle = h("button", { class: "btn sm primary", onclick: () => { card.hidden = !card.hidden; if (!card.hidden) card.querySelector("input, textarea")?.focus(); } },
      h("span", { html: icon("plus", 13) }), ` ${a.title}`);
    return { card, toggle };
  });

  el.replaceChildren(h("div", { class: "xapp tapp" },
    info.description && h("p", { class: "p-s tapp-desc" }, info.description),
    !acts.length && h("div", { class: "p-empty" }, "No actions yet. Tell the agent what this app should do."),
    ...broken.map(([, a]) => h("div", { class: "err" }, a.error)),
    lists.length ? h("div", { class: "tapp-bar" }, ...formCards.map((f) => f.toggle)) : null,
    ...formCards.map((f) => f.card),
    lists.length ? h("section", { class: "xapp-card" },
      h("div", { class: "xapp-title" }, h("strong", {}, lists[0][1].title),
        h("button", { class: "icon-btn sm", title: "Refresh", "aria-label": "Refresh", onclick: () => refreshList?.(), html: icon("refresh", 13) })),
      box) : null));

  refreshList = lists.length ? () => listInto(ctx, id, lists[0][0], box, perItem) : null;
  refreshList?.();
}

async function listInto(ctx, id, action, box, perItem) {
  const { api, h } = ctx;
  if (!box.childElementCount) box.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  let r;
  try { r = (await api.post(`/api/tapps/${enc(id)}/call/${enc(action)}`, { args: {} })).result; }
  catch (e) { return box.replaceChildren(h("div", { class: "err" }, e.message)); }
  let rows = Array.isArray(r) ? r : Object.values(r || {}).find(Array.isArray);
  if (rows) {   // a repeating meeting shows once, at its next time
    const seen = new Set();
    rows = rows.filter((x) => !x.repeats || !seen.has(LABEL(x)) && seen.add(LABEL(x)));
  }
  if (!rows) return box.replaceChildren(h("div", { class: "p-empty" }, typeof r === "string" ? r : Object.values(r || {}).find((v) => typeof v === "string") || "Nothing here."));
  box.replaceChildren(...rows.map((x) => row(ctx, id, x, perItem)));
}

function row(ctx, id, x, perItem) {
  const { h } = ctx;
  const extra = h("div", { class: "tapp-extra" });
  const when = [x.start && fmt(x.start), x.end && fmt(x.end, x.start)].filter(Boolean).join(" – ");
  const guests = Array.isArray(x.guests) ? x.guests : Array.isArray(x.attendees) ? x.attendees : null;
  return h("div", { class: "tapp-row" },
    h("div", { class: "tapp-main" },
      h("div", { class: "tapp-t" }, LABEL(x)),
      h("div", { class: "p-s" }, [when, x.repeats && "repeats", x.where, x.from, x.state].filter(Boolean).join(" · ")),
      guests?.length ? h("div", { class: "p-s" }, `Guests: ${guests.join(", ")}`) : null),
    h("div", { class: "tapp-btns" },
      x.meet && h("a", { class: "btn sm primary", href: x.meet, target: "_blank", rel: "noopener" }, "Join"),
      x.url && !x.meet && h("a", { class: "btn sm", href: x.url, target: "_blank", rel: "noopener" }, "Open"),
      ...perItem.map(([n, a]) => h("button", { class: `btn sm ${a.risk === "destructive" ? "danger" : ""}`, onclick: () => itemAction(ctx, id, n, a, x, extra) }, a.title))),
    extra);
}

function itemAction(ctx, id, name, a, item, extra) {
  const rest = Object.keys(a.schema.properties).filter((p) => p !== a.ref);
  const args = { [a.ref]: item.ref };
  if (!rest.length || a.risk === "destructive" && !a.schema.required.some((p) => p !== a.ref)) {
    if (a.risk === "destructive" && !confirm(`${a.title}: “${LABEL(item)}”? This can't be undone.`)) return;
    return run(ctx, id, name, a, args, () => refreshList?.());
  }
  if (extra.firstChild?.dataset?.action === name) return extra.replaceChildren();   // the same button again closes it
  const prefill = { summary: item.title, start: item.start, end: item.end, location: item.where, attendees: item.guests?.join(", ") };
  extra.replaceChildren(formCard(ctx, id, name, a, { fixed: args, prefill, only: rest }, () => { extra.replaceChildren(); refreshList?.(); }));
}

function formCard(ctx, id, name, a, { fixed = {}, prefill = {}, only = null } = {}, after) {
  const { h } = ctx;
  const keys = (only || Object.keys(a.schema.properties));
  const fields = keys.map((k) => field(h, k, a.schema.properties[k], a.schema.required.includes(k), prefill[k]));
  const out = h("div", { class: "xapp-out" });
  const go = async () => {
    const args = { ...fixed };
    for (const f of fields) {
      const v = f.input.type === "checkbox" ? f.input.checked : f.input.value.trim();
      if (only && v === f.initial) continue;   // editing: send only what changed
      if (v !== "" && v !== false) args[f.key] = v;
    }
    if (only && Object.keys(args).length === Object.keys(fixed).length) return out.replaceChildren(h("div", { class: "p-s" }, "Nothing changed."));
    const need = a.schema.required.filter((k) => args[k] == null);
    if (need.length) return out.replaceChildren(h("div", { class: "err" }, `Fill in ${need.map(nice).join(", ")}.`));
    if (a.risk === "destructive" && !confirm(`${a.title}? This can't be undone.`)) return;
    out.replaceChildren(h("div", { class: "skeleton" }));
    const ok = await run(ctx, id, name, a, args, after, out);
    if (ok && !only) fields.forEach((f) => { if (f.input.type === "checkbox") return; f.input.value = ""; });
  };
  const card = h("section", { class: "xapp-card", "data-action": name },
    h("div", { class: "xapp-title" }, h("strong", {}, a.title)),
    h("div", { class: "xapp-form" }, ...fields.map((f) => f.el),
      h("button", { class: `btn sm ${a.risk === "destructive" ? "danger" : "primary"}`, onclick: go }, a.title)),
    out);
  card.addEventListener("keydown", (e) => e.key === "Enter" && e.target.tagName === "INPUT" && go());
  const st = fields.find((f) => f.key === "start")?.input, en = fields.find((f) => f.key === "end")?.input;
  if (st?.type === "datetime-local" && en) st.addEventListener("change", () => {   // keep the end after the start (30 min by default)
    if (!st.value || en.value && en.value > st.value) return;
    const d = new Date(st.value); d.setMinutes(d.getMinutes() + 30); en.value = localInput(d.toISOString());
  });
  return card;
}

function field(h, key, spec = {}, required, value) {
  let input;
  if (spec.type === "boolean") input = h("input", { type: "checkbox", checked: !!value });
  else if (spec.enum) input = h("select", {}, h("option", { value: "" }, "—"), ...spec.enum.map((o) => h("option", { value: o, selected: o === value }, nice(o))));
  else if (key === "description") input = h("textarea", { rows: 2, placeholder: spec.description || "" });
  else if (/^(start|end)$/.test(key)) { input = h("input", { type: "datetime-local", step: 300 }); value = localInput(value); }
  else input = h("input", { type: spec.type === "integer" ? "number" : "text", placeholder: hint(key, spec), autocomplete: "off" });
  if (value != null && input.tagName !== "SELECT" && input.type !== "checkbox") input.value = value;
  input.setAttribute("aria-label", nice(key));
  const label = { summary: "Title", attendees: "Guests", add_guests: "Add guests", remove_guests: "Remove guests" }[key] || nice(key);
  return { key, input, initial: input.type === "checkbox" ? input.checked : input.value.trim(), el: h("label", { class: `xapp-field ${spec.type === "boolean" ? "check" : ""} ${spec.type === "array" || key === "description" ? "wide" : ""}` }, h("span", {}, label, required ? h("b", {}, " *") : null), input) };
}

/** "2026-11-16T09:30:00+05:30" -> "2026-11-16T09:30" in this browser's time, what a datetime-local input takes. */
function localInput(s) {
  if (!s || s.length <= 10) return s ? `${s}T09:00` : s;
  const d = new Date(s);
  if (isNaN(d)) return "";
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function hint(key, spec) {
  if (spec.type === "array") return "comma-separated, e.g. a@x.com, b@y.com";
  return (spec.description || "").slice(0, 60);
}

async function run(ctx, id, name, a, args, after, out) {
  try {
    const r = await ctx.api.post(`/api/tapps/${enc(id)}/call/${enc(name)}`, { args, confirm: a.risk === "destructive" });
    ctx.toast(`${a.title}: done`);
    out?.replaceChildren(summary(ctx.h, r.result));
    after?.();
    return true;
  } catch (e) {
    (out || null)?.replaceChildren(ctx.h("div", { class: "err" }, e.message));
    if (!out) ctx.toast(e.message, "error");
    return false;
  }
}

function summary(h, r) {
  const x = r && typeof r === "object" ? Object.values(r).find((v) => v && typeof v === "object" && !Array.isArray(v)) : null;
  if (!x) return h("div", { class: "p-s" }, "Done.");
  return h("div", { class: "p-s" }, [LABEL(x), x.start && fmt(x.start), x.meet && "Meet link added"].filter(Boolean).join(" · "),
    x.meet ? h("span", {}, " · ", h("a", { href: x.meet, target: "_blank", rel: "noopener" }, "Join")) : null);
}

function fmt(s, sameDayAs) {
  const d = new Date(s);
  if (isNaN(d)) return s;
  if (s.length <= 10) return d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" });
  const t = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (sameDayAs && new Date(sameDayAs).toDateString() === d.toDateString()) return t;
  return `${d.toLocaleDateString([], { weekday: "short", month: "short", day: "numeric" })} ${t}`;
}
