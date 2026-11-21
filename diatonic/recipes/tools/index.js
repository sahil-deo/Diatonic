export async function render(el, ctx) {
  const { ui } = ctx;
  const view = ui.listApp({
    search: true, empty: "Nothing left to do.",
    actions: [{ label: "Done", run: async (r) => { await ctx.tool("todos_complete_todo", { id: r.id }); await load(); return "Marked done"; } }],
    detail: (r) => ui.kv({ items: [{ label: "To-do", value: r.title }, { label: "Due", value: r.due || "No date" }, { label: "Priority", value: r.priority }] }),
  });
  async function load() {
    const todos = await ctx.tool("todos_list_todos", { status: "open", limit: 50 });
    view.set(Array.isArray(todos) ? todos.map((t) => ({ id: t.id, title: t.title, subtitle: t.due || "", due: t.due, priority: t.priority, badge: t.overdue ? "overdue" : undefined })) : []);
  }
  el.append(ui.card({ title: "Open to-dos", body: view }));
  await load();
}
