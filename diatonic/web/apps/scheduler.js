const BASE = "/api/apps/scheduler";

export async function render(el, { api, h, toast, reload }) {
  const d = await api.get(`${BASE}/panel`);
  const err = h("div", { class: "form-err" });
  const submit = async (body, clear) => {
    err.textContent = "";
    try { await api.post(`${BASE}/create`, body); clear?.(); reload(); }
    catch (e) { err.textContent = e.message; }
  };

  /* quick reminder */
  const text = h("input", { type: "text", placeholder: "Remind me to…", "aria-label": "Reminder text" });
  const when = h("input", { type: "text", placeholder: "when: in 20 min, tomorrow 9am…", "aria-label": "When" });
  const add = () => text.value.trim() && when.value.trim() && submit({ kind: "notify", text: text.value.trim(), when: when.value.trim() });
  [text, when].forEach((i) => i.addEventListener("keydown", (e) => e.key === "Enter" && add()));

  el.append(
    h("div", { class: "p-section" }, "New reminder"),
    h("div", { class: "quick-add" }, text, when, h("button", { class: "btn primary sm", onclick: add }, "Add")),
    err,
    h("div", { class: "p-section" }, `Reminders (${d.reminders.length})`),
    ...(d.reminders.length ? d.reminders.map((r) => h("div", { class: `p-row ${r.state === "paused" ? "dim" : ""}` },
      h("div", { class: "grow" }, h("div", { class: "p-t" }, r.name), h("div", { class: "p-s" }, `${r.state === "paused" ? "paused · " : ""}${r.next} · ${r.when}`)),
      h("button", { class: "icon-btn sm", title: "Cancel reminder", "aria-label": "Cancel reminder", onclick: async () => { await api.post(`${BASE}/action`, { id: r.id, action: "delete" }); reload(); }, html: "✕" })))
      : [h("div", { class: "p-empty" }, "No reminders. Add one above or ask the agent.")]));

  /* jobs */
  el.append(h("div", { class: "p-section" }, `Jobs (${d.jobs.length})`));
  for (const j of d.jobs) el.append(jobCard(j));
  if (!d.jobs.length) el.append(h("div", { class: "p-empty" }, "No jobs. A job runs a prompt or script on a schedule."));

  function jobCard(j) {
    const hist = h("div", { class: "hist", hidden: true });
    const act = (action, extra = {}) => async () => { await api.post(`${BASE}/action`, { id: j.id, action, ...extra }); if (action === "run") toast("Started"); setTimeout(reload, action === "run" ? 800 : 0); };
    return h("div", { class: `jobcard ${j.state === "paused" ? "dim" : ""}` },
      h("div", { class: "job-head" }, h("span", { class: `pill ${j.kind}` }, j.kind), h("strong", { class: "grow" }, j.name),
        j.last && h("span", { class: `pill st-${j.last}` }, j.last)),
      h("div", { class: "p-s" }, `${j.state === "paused" ? "paused · " : ""}${j.when} · next ${j.next}`),
      j.kind === "script" ? h("pre", { class: "cmd" }, j.payload.command) : h("div", { class: "p-s clamp" }, j.payload.prompt),
      j.kind === "prompt" && h("label", { class: "chk", title: "Writes still need your approval; with nobody there they time out and are denied" },
        h("input", { type: "checkbox", checked: j.allow_writes, onchange: (e) => act("allow_writes", { value: e.target.checked })() }), "Allow it to propose changes"),
      h("div", { class: "job-acts" },
        h("button", { class: "btn sm", onclick: act("run") }, "Run now"),
        h("button", { class: "btn sm", onclick: act(j.state === "paused" ? "resume" : "pause") }, j.state === "paused" ? "Resume" : "Pause"),
        h("button", { class: "btn sm", onclick: async () => {
          hist.hidden = !hist.hidden; if (hist.hidden) return;
          const runs = await api.get(`${BASE}/history?id=${j.id}`);
          hist.replaceChildren(...(runs.length ? runs.map((r) => h("div", { class: "run" }, h("span", { class: `pill st-${r.status}` }, r.status),
            h("span", { class: "p-s" }, new Date(r.started * 1000).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) + (r.late ? " · late" : "")),
            r.output && h("pre", { class: "cmd" }, r.output.slice(0, 600)))) : [h("div", { class: "p-s" }, "No runs yet.")]));
        } }, "History"),
        h("button", { class: "btn sm danger", onclick: async () => { if (confirm(`Delete “${j.name}”?`)) { await api.post(`${BASE}/action`, { id: j.id, action: "delete" }); reload(); } } }, "Delete")),
      hist);
  }

  /* new job form */
  const kind = h("select", {}, h("option", { value: "prompt" }, "Run an AI prompt"), h("option", { value: "script", disabled: !d.scripts_enabled }, d.scripts_enabled ? "Run a script" : "Run a script (enable in Settings → Automation)"));
  const name = h("input", { type: "text", placeholder: "Name, e.g. Morning briefing" });
  const jw = h("input", { type: "text", placeholder: "When: every weekday at 8am, 0 9 * * 1-5, in 2 hours…" });
  const body = h("textarea", { placeholder: "Prompt: what should the assistant do each time?" });
  kind.addEventListener("change", () => { body.placeholder = kind.value === "script" ? "Shell command, e.g. ~/bin/backup.sh" : "Prompt: what should the assistant do each time?"; });
  el.append(h("details", { class: "newjob" }, h("summary", {}, "New job"),
    h("div", { class: "stack" }, kind, name, jw, body,
      h("button", { class: "btn primary", onclick: () => submit({ kind: kind.value, name: name.value.trim(), when: jw.value.trim(),
        [kind.value === "script" ? "command" : "prompt"]: body.value.trim() }, () => { name.value = jw.value = body.value = ""; }) }, "Create job"))));
}
