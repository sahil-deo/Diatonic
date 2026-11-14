// Shared form helpers and config saving for the settings panes.
import { h, toast, debounce } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";

/** Keys the app already uses, so a custom shortcut can't shadow them. */
export const RESERVED = { "Meta+K": "command palette", "Meta+,": "settings", "Meta+Shift+O": "new chat", "Meta+B": "the sidebar toggle" };

export async function save(patch) {
  try { state.config = await api.putConfig(patch); emit("config"); return true; }
  catch (e) { toast(e.message, "error"); return false; }
}
export const saveSoon = debounce(save, 500);

export const field = (label, input, help) => h("div", { class: "field" }, h("label", {}, label), input, help && h("div", { class: "help" }, help));
export const text = (v, onChange, o = {}) => h("input", { type: o.type || "text", value: v ?? "", placeholder: o.placeholder, list: o.list, step: o.step, min: o.min, max: o.max,
  oninput: (e) => onChange(o.type === "number" ? Number(e.target.value) : e.target.value) });
export const select = (v, opts, onChange) => h("select", { onchange: (e) => onChange(e.target.value) },
  opts.map(([val, lbl]) => h("option", { value: val, selected: val === v }, lbl)));
export const toggle = (label, desc, checked, onChange) => h("div", { class: "toggle-row" },
  h("div", {}, h("div", {}, label), desc && h("div", { class: "d" }, desc)),
  h("span", { class: "switch" }, h("input", { type: "checkbox", checked, "aria-label": label, onchange: (e) => onChange(e.target.checked) })));
/** A row of mutually exclusive buttons. */
export const segmented = (value, opts, onChange) => {
  const box = h("div", { class: "seg", role: "group" });
  opts.forEach(([v, label]) => box.append(h("button", { type: "button", class: v === value ? "on" : "", onclick: () => {
    box.querySelectorAll("button").forEach((b) => b.classList.remove("on")); event.currentTarget.classList.add("on"); onChange(v); } }, label)));
  return box;
};
