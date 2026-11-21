import { render as md } from "/js/md.js";
import { emit } from "/js/store.js";

const BASE = "/api/apps/notes";
export const wide = true;

const instances = new WeakMap(); // state-ok: keyed by this panel's ctx
export const current = (ctx) => {
  const open = instances.get(ctx)?.getOpen?.();
  return open ? { kind: "note", ref: open, title: open } : null;
};
/** Called by the agent: open a note by title, or show a search. */
export async function navigate(p, ctx) {
  const nav = instances.get(ctx)?.nav;
  if (nav) return p.item ? nav.edit(p.item) : nav.list(p.query || "");
}

export async function render(el, ctx) {
  const { api, h, toast, reload } = ctx;
  let open = null;
  const nav = { list, edit };
  instances.set(ctx, { nav, getOpen: () => open });
  ctx.list({ items: () => [...el.querySelectorAll(".p-item")] });   // ↑/↓ and Enter in the note list
  await list("");

  async function list(q) {
    el.replaceChildren(); delete el.dataset.dirty; open = null; emit("screen");
    const d = await api.get(`${BASE}/notes${q ? `?q=${encodeURIComponent(q)}` : ""}`);
    const search = h("input", { type: "search", placeholder: "Search notes…", value: q, "aria-label": "Search notes" });
    let t; search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => list(search.value.trim()).then(() => el.querySelector("input[type=search]").focus()), 250); });
    el.append(h("div", { class: "quick-add" }, search, h("button", { class: "btn primary sm", onclick: () => edit(null) }, "New note")),
      h("div", { class: "p-s" }, `${d.notes.length} note${d.notes.length === 1 ? "" : "s"} in ${d.folder}`),
      ...d.notes.map((n) => h("button", { class: "p-item", onclick: () => edit(n.slug) },
        h("div", { class: "p-t" }, n.title), h("div", { class: "p-s clamp" }, n.preview || "(empty)"),
        h("div", { class: "p-s" }, (n.tags.length ? n.tags.map((x) => `#${x}`).join(" ") + " · " : "") + new Date(n.modified * 1000).toLocaleDateString([], { month: "short", day: "numeric" })))));
    if (!d.notes.length) el.append(h("div", { class: "p-empty" }, q ? "No notes match." : "No notes yet. Create one, or ask the agent to jot something down."));
  }

  async function edit(slug) {
    const n = slug ? await api.get(`${BASE}/notes/${encodeURIComponent(slug)}`) : { title: "", tags: [], body: "" };
    let cur = slug, preview = false, timer;
    open = n.title || null; emit("screen");
    const title = h("input", { type: "text", class: "note-title", placeholder: "Title", value: n.title });
    const tags = h("input", { type: "text", placeholder: "tags, comma separated", value: n.tags.join(", ") });
    const body = h("textarea", { class: "note-body", placeholder: "Write in markdown…", spellcheck: "true" }, n.body);
    const view = h("div", { class: "md note-view", hidden: true });
    const status = h("span", { class: "p-s" }, slug ? "Saved" : "");
    const save = async () => {
      clearTimeout(timer);
      if (!title.value.trim() && !body.value.trim()) return;
      const payload = { title: title.value.trim() || "Untitled", tags: tags.value.split(",").map((x) => x.trim()).filter(Boolean), body: body.value };
      try {
        const r = cur ? await api.put(`${BASE}/notes/${encodeURIComponent(cur)}`, payload) : await api.post(`${BASE}/notes`, payload);
        cur = r.slug; delete el.dataset.dirty; status.textContent = "Saved";
      } catch (e) { status.textContent = "Couldn't save: " + e.message; }
    };
    const dirty = () => { el.dataset.dirty = "1"; status.textContent = "Editing…"; clearTimeout(timer); timer = setTimeout(save, 1200); };
    [title, tags, body].forEach((i) => i.addEventListener("input", dirty));
    el.addEventListener("keydown", (e) => { if ((e.metaKey || e.ctrlKey) && e.key === "s") { e.preventDefault(); save(); } });
    const toggle = h("button", { class: "btn sm", onclick: () => {
      preview = !preview; view.hidden = !preview; body.hidden = preview; toggle.textContent = preview ? "Edit" : "Preview";
      if (preview) view.innerHTML = md(body.value);
    } }, "Preview");
    el.replaceChildren(
      h("div", { class: "note-bar" },
        h("button", { class: "btn sm", onclick: async () => { await save(); list(""); } }, "← Notes"), h("div", { class: "grow" }), status, toggle,
        cur && h("button", { class: "btn sm danger", onclick: async () => { if (confirm("Move this note to the trash folder?")) { await api.del(`${BASE}/notes/${encodeURIComponent(cur)}`); list(""); } } }, "Delete")),
      title, tags, body, view);
    (slug ? body : title).focus();
  }
}
