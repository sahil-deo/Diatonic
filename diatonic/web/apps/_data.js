// The one panel every data app uses. Top-level lists are sections of cards; lists inside them are checklists with
// progress. Everything is editable in place: tick, rename, details, notes, add, delete. The agent edits the same data.
import { icon } from "/js/icons.js";
import { state } from "/js/store.js";

export const wide = true;
const instances = new WeakMap(); // state-ok: keyed by this panel's ctx
const enc = encodeURIComponent;
const nice = (s) => s.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());

export async function render(el, ctx) {
  const id = ctx.app || state.panelApp || ctx.id;
  const d = await ctx.api.get(`/api/dapps/${enc(id)}`);
  const m = d.manifest, lists = m.lists;
  let open = instances.get(ctx);
  if (!open) { open = new Set(); instances.set(ctx, open); }
  const P = { el, ctx, id, m, items: d.items, open };
  const top = Object.keys(lists).filter((n) => !lists[n].parent);
  const scroll = el.scrollTop;
  el.replaceChildren(ctx.h("div", { class: "dapp" },
    ctx.h("h3", { class: "dapp-title" }, m.title),
    !top.length && ctx.h("div", { class: "p-empty" }, "This app has no lists yet. Ask the agent, e.g. “add a list of subjects”."),
    ...top.map((n) => section(P, n, null, true))));
  el.scrollTop = scroll;
}

const kidsOf = (P, list) => Object.keys(P.m.lists).filter((n) => P.m.lists[n].parent === list);
const checkField = (l) => l.fields.find((f) => f.type === "check");
const rowsOf = (P, list, parent) => P.items.filter((r) => r.list === list && (r.parent ?? null) === parent);

async function act(P, fn) {
  try { await fn(); } catch (e) { P.ctx.toast(e.message, "error"); }
  render(P.el, P.ctx);
}

function section(P, list, parent, top) {
  const { h } = P.ctx, l = P.m.lists[list], rows = rowsOf(P, list, parent);
  const add = h("input", { type: "text", placeholder: `Add ${l.item.replace(/_/g, " ")}…`, "aria-label": `Add ${l.item}` });
  const submit = () => add.value.trim() && act(P, () => P.ctx.api.post(`/api/dapps/${enc(P.id)}/${enc(list)}`, { data: { [l.fields[0].name]: add.value.trim() }, parent }));
  add.addEventListener("keydown", (e) => e.key === "Enter" && submit());
  const ck = checkField(l), done = ck ? rows.filter((r) => r.data[ck.name]).length : 0;
  return h("section", { class: top ? "dsec" : "dsub" },
    h("div", { class: "dsec-head" }, h("span", {}, nice(list)), ck && rows.length ? progress(h, done, rows.length) : h("span", { class: "p-s" }, rows.length ? String(rows.length) : "")),
    ...rows.map((r) => (top && kidsOf(P, list).length ? card(P, r) : row(P, r))),
    !rows.length && top && h("div", { class: "p-s dempty" }, `No ${list.replace(/_/g, " ")} yet.`),
    h("div", { class: "dadd" }, add, h("button", { class: "btn sm", type: "button", onclick: submit }, "Add")));
}

function progress(h, done, total) {
  return h("span", { class: "dprog", title: `${done} of ${total} done` }, h("i", {}, h("b", { style: `width:${Math.round((done / total) * 100)}%` })), `${done}/${total}`);
}

function card(P, r) {
  const { h } = P.ctx, l = P.m.lists[r.list];
  const kids = kidsOf(P, r.list);
  const prog = kids.map((k) => { const ck = checkField(P.m.lists[k]), rs = rowsOf(P, k, r.id); return ck && rs.length ? progress(h, rs.filter((x) => x.data[ck.name]).length, rs.length) : null; }).find(Boolean);
  return h("article", { class: "dcard" },
    head(P, r, l, prog),
    P.open.has(r.id) && details(P, r, l),
    ...kids.map((k) => section(P, k, r.id, false)));
}

function row(P, r) {
  const l = P.m.lists[r.list], kids = kidsOf(P, r.list);
  return P.ctx.h("div", { class: `drow ${P.open.has(r.id) ? "open" : ""}` }, head(P, r, l, null), P.open.has(r.id) && details(P, r, l),
    ...kids.map((k) => section(P, k, r.id, false)));
}

function head(P, r, l, prog) {
  const { h, api } = P.ctx, ck = checkField(l), name = l.fields[0].name;
  const patch = (data) => act(P, () => api.patch(`/api/dapps/${enc(P.id)}/items/${r.id}`, { data }));
  const chips = l.fields.slice(1).filter((f) => !["check", "notes"].includes(f.type) && r.data[f.name] != null && r.data[f.name] !== "")
    .map((f) => h("span", { class: `dchip ${f.type}` }, f.type === "date" ? fmtDate(r.data[f.name]) : String(r.data[f.name])));
  const hasNotes = l.fields.some((f) => f.type === "notes" && r.data[f.name]);
  return h("div", { class: "dhead" },
    ck && h("input", { type: "checkbox", checked: !!r.data[ck.name], "aria-label": `Mark ${r.data[name]} done`, onchange: (e) => patch({ [ck.name]: e.target.checked }) }),
    h("button", { type: "button", class: `dname ${ck && r.data[ck.name] ? "done" : ""}`, title: "Details", onclick: () => { P.open.has(r.id) ? P.open.delete(r.id) : P.open.add(r.id); render(P.el, P.ctx); } },
      r.data[name] || "(untitled)", hasNotes ? h("span", { class: "dnote", html: icon("file-text", 12), title: "Has notes" }) : null),
    ...chips, prog, h("div", { class: "grow" }),
    h("button", { type: "button", class: "icon-btn sm ddel", "aria-label": "Delete", title: "Delete", html: icon("trash", 14), onclick: () => {
      if (confirm(`Delete “${r.data[name]}”${kidsOf(P, r.list).length ? " and everything in it" : ""}?`)) act(P, () => api.del(`/api/dapps/${enc(P.id)}/items/${r.id}`));
    } }));
}

function details(P, r, l) {
  const { h, api } = P.ctx;
  let t;
  const save = (f, v) => { clearTimeout(t); t = setTimeout(async () => {
    try { await api.patch(`/api/dapps/${enc(P.id)}/items/${r.id}`, { data: { [f]: v } }); r.data[f] = v; } catch (e) { P.ctx.toast(e.message, "error"); }
    delete P.el.dataset.dirty;
  }, 600); };
  const input = (f) => {
    const v = r.data[f.name] ?? "";
    const on = (x) => { P.el.dataset.dirty = "1"; save(f.name, x); };   // don't let an agent refresh wipe what's being typed
    if (f.type === "notes") { const ta = h("textarea", { rows: 3, placeholder: "Notes…" }, v); ta.addEventListener("input", () => on(ta.value)); return ta; }
    if (f.type === "check") return h("input", { type: "checkbox", checked: !!v, onchange: (e) => on(e.target.checked) });
    if (f.type === "choice") { const s = h("select", {}, h("option", { value: "" }, "—"), ...f.options.map((o) => h("option", { value: o, selected: o === v }, o))); s.addEventListener("change", () => on(s.value)); return s; }
    const i = h("input", { type: f.type === "date" ? "date" : f.type === "number" ? "number" : "text", value: v });
    i.addEventListener(f.type === "text" ? "input" : "change", () => on(i.value)); return i;
  };
  const ck = checkField(l);   // already a checkbox on the row
  return h("div", { class: "ddetails" }, ...l.fields.filter((f) => f !== ck).map((f) => h("label", { class: `dfield ${f.type}` }, h("span", {}, nice(f.name)), input(f))));
}

function fmtDate(iso) {
  const d = new Date(iso + "T00:00:00"), days = Math.round((d - new Date(new Date().toDateString())) / 864e5);
  const s = d.toLocaleDateString([], { month: "short", day: "numeric" });
  return days === 0 ? "today" : days === 1 ? "tomorrow" : days > 1 && days < 60 ? `${s} · ${days}d` : s;
}
