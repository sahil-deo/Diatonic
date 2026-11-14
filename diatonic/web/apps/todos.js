const BASE = "/api/apps/todos";
let showDone = false;

export async function render(el, { api, h, reload }) {
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
    h("label", { class: "chk", style: "margin-top:14px" }, h("input", { type: "checkbox", checked: showDone, onchange: (e) => { showDone = e.target.checked; reload(); } }), "Show completed"),
    h("div", { class: "p-s", style: "margin-top:10px" }, "Double-click a title to edit it."));
  title.focus();
}
