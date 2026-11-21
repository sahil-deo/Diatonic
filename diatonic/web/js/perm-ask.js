// The questions an app must ask the user, answered here and never by the app itself: the list of things it wants to use (manifest
// permissions), and each change a panel asks the agent's tools to make. Cards stack in the corner and clear when answered anywhere.
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { api } from "./api.js";
import { on } from "./store.js";

export function initPermAsk() {
  const box = h("div", { class: "app-asks", role: "region", "aria-label": "App requests" });
  document.body.append(box);
  const find = (id) => box.querySelector(`[data-id="${id}"]`);
  const card = (ev, top, what, detail, yes) => {
    if (find(ev.id)) return;
    const decide = (ok) => api.approve(ev.id, ok).catch(() => {});
    box.append(h("div", { class: `approval ${ev.risk === "destructive" ? "destructive" : ""}`.trim(), "data-id": ev.id },
      h("div", { class: "a-top", html: `${icon("shield", 15)} ${top}` }), h("div", { class: "a-what" }, what), detail,
      h("div", { class: "a-btns" }, h("button", { class: "btn primary", type: "button", onclick: () => decide(true) }, yes),
        h("button", { class: "btn", type: "button", onclick: () => decide(false) }, "Deny"))));
  };
  on("perm_ask", (ev) => card(ev, "Needs approval", `${ev.name} wants to:`,
    h("ul", { class: "perm-list" }, ev.items.map((t) => h("li", {}, t, ev.new.includes(t) && ev.new.length < ev.items.length ? h("span", { class: "perm-new" }, " new") : null))), "Allow"));
  on("tool_ask", (ev) => card(ev, ev.risk === "destructive" ? "Needs approval · irreversible" : "Needs approval", `${ev.name} wants to: ${ev.describe}`, null, "Approve"));
  on("perm_ask_done", (ev) => find(ev.id)?.remove());
}
