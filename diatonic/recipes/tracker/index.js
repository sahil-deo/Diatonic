export async function render(el, ctx) {
  const { ui, h } = ctx;
  const stats = h("div", { style: "display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px" });
  const view = ui.listApp({
    search: true, empty: "No topics yet. Add some below.",
    filters: [{ id: "all", label: "All", test: () => true }, { id: "open", label: "Open", test: (r) => !r.done }, { id: "done", label: "Done", test: (r) => r.done }],
    marks: { key: "done", label: "Mark topic", onChange: async (r, v) => { await ctx.store.update("topics", r.id, { done: v }); await load(); } },
    actions: [{ label: "Delete", kind: "danger", run: async (r) => { await ctx.store.delete("topics", r.id); await load(); return "Deleted"; } }],
    detail: (r) => ui.kv({ items: [{ label: "Topic", value: r.title }, { label: "Subject", value: r.subject || "None" }, { label: "Status", value: r.done ? "Done" : "Open" }] }),
  });
  async function load() {
    const rows = (await ctx.store.query("topics", { order: "id" })).map((r) => ({ ...r, badge: r.subject || undefined }));
    view.set(rows);
    const done = rows.filter((r) => r.done).length;
    stats.replaceChildren(ui.stat({ label: "Done", value: `${done} / ${rows.length}`, tone: rows.length && done === rows.length ? "ok" : undefined }),
      ui.stat({ label: "Left", value: rows.length - done }));
  }
  const add = ui.form({
    submit: "Add",
    fields: [{ name: "subject", label: "Subject", placeholder: "Optional" }, { name: "lines", label: "Topics", type: "textarea", placeholder: "One per line", help: "Paste as many as you like: they are added in one go." }],
    onSubmit: (v, form) => ctx.ui.run("Add topics", async () => {
      const titles = String(v.lines || "").split("\n").map((s) => s.trim()).filter(Boolean);
      if (!titles.length) throw new Error("Type at least one topic.");
      await ctx.store.addMany("topics", titles.map((title) => ({ title, subject: v.subject.trim(), done: false })));
      form.reset(); await load();
      return `Added ${titles.length}`;
    }),
  });
  el.append(stats, view, ui.card({ title: "Add topics", body: add }));
  await load();
}
