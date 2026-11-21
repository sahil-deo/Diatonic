// A read-only list of the system prompt sections with their sizes, opened by the "Show the system prompt" command.
import { h } from "./dom.js";
import { api } from "./api.js";

let open = null;

/** Show the sections of the system prompt; a second call closes the list. */
export async function showPrompt() {
  if (open) { open(); return; }
  const data = await api.get("/api/prompt");
  const close = () => { document.removeEventListener("keydown", onKey, true); box.remove(); open = null; };
  const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  const box = h("div", { class: "pv-wrap", onclick: (e) => { if (e.target === box) close(); } },
    h("div", { class: "pv-card", role: "dialog", "aria-label": "System prompt" },
      h("div", { class: "pv-head" }, h("strong", {}, "System prompt"), h("span", { class: "pv-size" }, `${data.chars} characters`),
        h("button", { class: "btn sm", onclick: close }, "Close")),
      ...data.sections.map((s) => h("section", { class: "pv-sec" },
        h("div", { class: "pv-name" }, s.name, h("span", { class: "pv-size" }, `${s.chars} characters, ${s.source}`)),
        h("pre", { class: "pv-text" }, s.text || "(switched off)")))));
  document.addEventListener("keydown", onKey, true);
  open = close;
  document.body.append(box);
}
