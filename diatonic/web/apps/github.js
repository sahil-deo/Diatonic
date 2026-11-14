export async function render(el, { api, h, prompt }) {
  const d = await api.get("/api/apps/github/panel");
  if (d.error) return el.append(h("div", { class: "p-empty" }, d.error));
  const item = (i, verb) => h("button", { class: "p-item", onclick: () => prompt(`${verb} ${i.repo}#${i.number}`) },
    h("div", { class: "p-t" }, i.title),
    h("div", { class: "p-s" }, `${i.repo}#${i.number}`, i.labels.length ? ` · ${i.labels.slice(0, 3).join(", ")}` : "", ` · ${i.comments} comments`));
  el.append(
    h("div", { class: "p-section" }, `Assigned to you (${d.issues.length})`),
    ...(d.issues.length ? d.issues.map((i) => item(i, "Summarize issue")) : [h("div", { class: "p-empty" }, "No open issues assigned to you.")]),
    h("div", { class: "p-section" }, `Pull requests involving you (${d.prs.length})`),
    ...(d.prs.length ? d.prs.map((i) => item(i, "Review pull request")) : [h("div", { class: "p-empty" }, "No open pull requests.")]),
    h("div", { class: "p-s", style: "margin-top:14px" }, "Click an item to ask the agent about it."));
}
