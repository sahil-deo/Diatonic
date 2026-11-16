const BASE = "/api/apps/todos";
/** Called by the agent: show the open or the completed list. */
export async function navigate(p, ctx) { if (p.view) { ctx.state.set("showDone", p.view === "done"); await ctx.reload(); } }

export async function render(el, ctx) {
  const { api, h, reload } = ctx;
  const showDone = ctx.state.get("showDone", false);      // per panel, not per module: two To-dos panels can show different lists
  const d = await api.get(`${BASE}/panel${showDone ? "?done=1" : ""}`);
  const err = h("div", { class: "form-err" });

  const title = h("input", { type: "text", placeholder: "Add a to-do…", "aria-label": "To-do" });
  const due = h("input", { type: "text", placeholder: "due (optional)", "aria-label": "Due" });
  const add = async () => {
    if (!title.value.trim()) return;
    err.textContent = "";
    try { await api.post(`${BASE}/create`, { title: title.value.trim(), due: due.value.trim() }); reload(); }
    catch (e) { err.textContent = e.message; }
  };
  [title, due].forEach((i) => i.addEventListener("keydown", (e) => e.key === "Enter" && add()));

  const act = (action, id, extra = {}) => api.post(`${BASE}/action`, { action, id, ...extra }).then(reload);
  const row = (t) => {
    const label = h("span", { class: "todo-t" }, t.title);
    label.addEventListener("dblclick", () => {
      const inp = h("input", { type: "text", value: t.title, class: "inline" });
      label.replaceWith(inp); inp.focus(); inp.select();
      const done = (save) => save && inp.value.trim() && inp.value !== t.title ? act("update", t.id, { title: inp.value.trim() }) : reload();
      inp.addEventListener("keydown", (e) => { if (e.key === "Enter") done(true); if (e.key === "Escape") done(false); });
      inp.addEventListener("blur", () => done(true));
    });
    return h("div", { class: `todo ${t.status === "done" ? "done" : ""}` },
      h("input", { type: "checkbox", checked: t.status === "done", "aria-label": `Complete ${t.title}`, onchange: (e) => act(e.target.checked ? "complete" : "reopen", t.id) }),
      h("div", { class: "grow" }, label,
        (t.due || t.priority !== "none") && h("div", { class: "p-s" },
          t.priority !== "none" && h("span", { class: `prio ${t.priority}` }, t.priority + " "), t.due && h("span", { class: t.overdue ? "overdue" : "" }, (t.overdue ? "overdue · " : "") + t.due))),
      h("button", { class: "icon-btn sm del", title: "Delete", "aria-label": `Delete ${t.title}`, onclick: () => act("delete", t.id), html: "✕" }));
  };

  el.append(h("div", { class: "quick-add" }, title, due, h("button", { class: "btn primary sm", onclick: add }, "Add")), err,
    h("div", { class: "p-section" }, `${showDone ? "All" : "Open"} (${d.todos.length})`),
    ...(d.todos.length ? d.todos.map(row) : [h("div", { class: "p-empty" }, "Nothing to do. Nice.")]),
    h("label", { class: "chk", style: "margin-top:14px" }, h("input", { type: "checkbox", checked: showDone, onchange: (e) => { ctx.state.set("showDone", e.target.checked); reload(); } }), "Show completed"),
    h("div", { class: "p-s", style: "margin-top:10px" }, "Double-click a title to edit it. Drag files or emails here to create to-dos."));

  el.ondragover = (e) => {
    if (e.dataTransfer.types.includes("text/diatonic-file") || e.dataTransfer.types.includes("text/diatonic-email") || e.dataTransfer.types.includes("text/plain")) {
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      el.classList.add("drop-target");
    }
  };
  el.ondragleave = () => el.classList.remove("drop-target");
  el.ondrop = async (e) => {
    e.preventDefault();
    el.classList.remove("drop-target");
    let todoText = "";
    try {
      const fileRaw = e.dataTransfer.getData("text/diatonic-file");
      const emailRaw = e.dataTransfer.getData("text/diatonic-email");
      if (fileRaw) {
        const f = JSON.parse(fileRaw);
        todoText = `Review file: ${f.name}`;
      } else if (emailRaw) {
        const em = JSON.parse(emailRaw);
        todoText = `Follow up on email: ${em.subject}`;
      } else {
        todoText = e.dataTransfer.getData("text/plain");
      }
    } catch {
      todoText = e.dataTransfer.getData("text/plain");
    }
    if (todoText && todoText.trim()) {
      try {
        await api.post(`${B}/todos`, { title: todoText.trim() });
        reload();
      } catch (err) {
        toast(err.message, "error");
      }
    }
  };
  title.focus();
}
