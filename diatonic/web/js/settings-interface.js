// Settings → Interface: theme, accent, density, type, corners, layout, rail, custom CSS, import/export.
import { h, toast } from "./dom.js";
import { api } from "./api.js";
import { state, emit } from "./store.js";
import { field, text, toggle, segmented, save, saveSoon } from "./form.js";
import { ACCENTS, DEFAULT_APPEARANCE, FONTS, applyAppearance } from "./appearance.js";
import { rerenderSettings } from "./settings.js";

const set = (patch, soon = false) => {  // apply instantly, persist (debounced for sliders)
  Object.assign(state.config.appearance, patch);
  applyAppearance(); (soon ? saveSoon : save)({ appearance: patch });
};

export function interfacePane() {
  const a = state.config.appearance;
  const accentInput = h("input", { type: "color", value: a.accent || "#7c9cff", "aria-label": "Custom accent", style: "width:34px;height:26px;padding:0;border:0;background:none", oninput: (e) => set({ accent: e.target.value }, true) });
  const cssBox = h("textarea", { class: "css-edit", spellcheck: "false", placeholder: "/* e.g. .msg.user .bubble { background: #1d2a44; } */" }, a.custom_css);
  const railApps = state.apps;
  const order = [...railApps].sort((x, y) => { const i = (id) => { const k = a.rail_order.indexOf(id); return k < 0 ? 999 : k; }; return i(x.id) - i(y.id); });
  const moveRail = (i, d) => { const ids = order.map((x) => x.id); [ids[i], ids[i + d]] = [ids[i + d], ids[i]]; set({ rail_order: ids }); emit("rail-changed"); rerenderSettings(); };
  const hide = (id, off) => { const s = new Set(a.rail_hidden); off ? s.add(id) : s.delete(id); set({ rail_hidden: [...s] }); emit("rail-changed"); rerenderSettings(); };

  return [
    h("div", { class: "p-section" }, "Look"),
    field("Theme", segmented(a.theme, [["system", "System"], ["light", "Light"], ["dark", "Dark"]], (v) => set({ theme: v }))),
    field("Accent colour", h("div", { class: "swatches" },
      ...ACCENTS.map((c) => h("button", { type: "button", class: `swatch ${a.accent === c ? "on" : ""}`, style: `background:${c}`, "aria-label": c, onclick: () => { set({ accent: c }); rerenderSettings(); } })),
      accentInput, h("button", { class: "btn sm", onclick: () => { set({ accent: "" }); rerenderSettings(); } }, "Default"))),
    field("Density", segmented(a.density, [["comfortable", "Comfortable"], ["compact", "Compact"]], (v) => set({ density: v }))),
    field(`Text size: ${a.font_size}px`, h("input", { type: "range", min: 11, max: 22, value: a.font_size, "aria-label": "Text size",
      oninput: (e) => { e.target.closest(".field").querySelector("label").textContent = `Text size: ${e.target.value}px`; set({ font_size: Number(e.target.value) }, true); } })),
    field("Font", segmented(a.font, Object.keys(FONTS).map((k) => [k, k[0].toUpperCase() + k.slice(1)]), (v) => set({ font: v }))),
    field("Corners", segmented(a.radius, [["sharp", "Sharp"], ["default", "Default"], ["round", "Round"]], (v) => set({ radius: v }))),
    field("Chat list width", segmented(a.sidebar_width || "default", [["narrow", "Narrow"], ["default", "Default"], ["wide", "Wide"]], (v) => set({ sidebar_width: v }))),
    field("Chat width", segmented(a.width, [["narrow", "Narrow"], ["default", "Default"], ["wide", "Wide"], ["full", "Full"]], (v) => set({ width: v }))),

    h("div", { style: "height:12px" }), h("div", { class: "p-section" }, "Layout"),
    toggle("Tree layout workspace (preview)", "Multi-panel workspace with tab stacks, split panes, and layout tree.", localStorage.getItem("diatonic:layout") === "tree", (v) => {
      if (v) {
        localStorage.setItem("diatonic:layout", "tree");
      } else {
        localStorage.removeItem("diatonic:layout");
      }
      toast(v ? "Tree layout enabled. Reloading..." : "Classic layout enabled. Reloading...");
      setTimeout(() => location.reload(), 600);
    }),
    toggle("Sidebar", "Conversation list. Toggle anytime with ⌘B.", a.sidebar === "show", (v) => set({ sidebar: v ? "show" : "hide" })),
    toggle("Left rail", "Apps and navigation bar on the left edge.", a.show_rail, (v) => { set({ show_rail: v }); emit("rail-changed"); }),
    toggle("Quick-action bar", "Your saved prompts and script buttons above the message box.", a.show_quick_actions, (v) => set({ show_quick_actions: v })),
    toggle("Status bar", null, a.show_status_bar, (v) => set({ show_status_bar: v })),
    toggle("Spoken-summary button", "In the top bar. Also in Settings → Voice.", a.show_speak_button, (v) => set({ show_speak_button: v })),
    toggle("Suggestions on an empty chat", null, a.show_suggestions, (v) => set({ show_suggestions: v })),

    h("div", { style: "height:12px" }), h("div", { class: "p-section" }, "Apps in the left rail"),
    ...order.filter((x) => x.enabled).map((x, i, arr) => h("div", { class: "rail-edit" },
      h("span", { class: "grow" }, x.name),
      h("button", { class: "icon-btn sm", disabled: i === 0, "aria-label": "Move up", onclick: () => moveRail(i, -1), html: "↑" }),
      h("button", { class: "icon-btn sm", disabled: i === arr.length - 1, "aria-label": "Move down", onclick: () => moveRail(i, 1), html: "↓" }),
      h("label", { class: "chk", style: "margin:0" }, h("input", { type: "checkbox", checked: !a.rail_hidden.includes(x.id), onchange: (e) => hide(x.id, !e.target.checked) }), "show"))),
    h("div", { class: "help", style: "margin-bottom:10px" }, "Hidden apps stay available from the command palette (⌘K) and /panel."),

    h("div", { style: "height:12px" }), h("div", { class: "p-section" }, "Custom CSS"),
    cssBox,
    h("div", { class: "row", style: "margin-top:8px" },
      h("button", { class: "btn", onclick: async () => { if (await save({ appearance: { custom_css: cssBox.value } })) { applyAppearance(); toast("Applied"); } } }, "Apply CSS"),
      h("button", { class: "btn", onclick: async () => { cssBox.value = ""; if (await save({ appearance: { custom_css: "" } })) applyAppearance(); } }, "Clear")),
    h("div", { class: "help" }, "Runs only on this page. Variables you can override: --accent, --bg, --surface, --border, --text, --radius."),

    h("div", { style: "height:22px" }), h("div", { class: "p-section" }, "Changes the agent made"),
    ...((state.mods || []).length ? state.mods.map((m) => h("div", { class: "rail-edit" },
      h("span", { class: "grow", title: m.kind === "style" ? "CSS" : "Runs in this page" }, `${m.title} · ${m.kind}`),
      h("label", { class: "chk", style: "margin:0" }, h("input", { type: "checkbox", checked: m.enabled, onchange: async (e) => { await api.put(`/api/mods/${encodeURIComponent(m.name)}`, { enabled: e.target.checked }).catch((x) => toast(x.message, "error")); setTimeout(rerenderSettings, 300); } }), "on"),
      h("button", { class: "icon-btn sm", "aria-label": "Remove", title: "Remove", onclick: async () => { await api.del(`/api/mods/${encodeURIComponent(m.name)}`).catch((x) => toast(x.message, "error")); setTimeout(rerenderSettings, 300); }, html: "×" })))
      : [h("div", { class: "help" }, "Nothing yet. Ask the agent, e.g. “make the sidebar narrower” or “add a panel with a clock”.")]),
    h("div", { class: "row", style: "margin-top:8px" },
      h("button", { class: "btn", onclick: async () => { try { toast((await api.post("/api/mods/revert")).what); } catch (x) { toast(x.message, "error"); } setTimeout(rerenderSettings, 300); } }, "Undo last change")),
    h("div", { class: "help" }, "Styles and scripts the agent adds load after the built-in interface. Add ?safe=1 to the address to start with all of them off."),

    h("div", { style: "height:22px" }), h("div", { class: "p-section" }, "Backup and reset"),
    h("div", { class: "row" },
      h("a", { class: "btn", style: "text-decoration:none", href: "/api/customization/export", download: "diatonic-customization.json" }, "Export customizations"),
      h("button", { class: "btn", onclick: () => pickFile() }, "Import…"),
      h("button", { class: "btn danger", onclick: async () => {
        if (!confirm("Reset the interface to defaults? Your actions are kept.")) return;
        if (await save({ appearance: { ...DEFAULT_APPEARANCE, theme: state.config.appearance.theme } })) { applyAppearance(); emit("rail-changed"); rerenderSettings(); }
      } }, "Reset interface")),
  ];
}

function pickFile() {
  const f = h("input", { type: "file", accept: "application/json,.json", style: "display:none" });
  f.addEventListener("change", async () => {
    try {
      const r = await api.post("/api/customization/import", JSON.parse(await f.files[0].text()));
      state.config.appearance = r.appearance; state.config.actions = r.actions;
      applyAppearance(); emit("config"); emit("rail-changed"); rerenderSettings(); toast("Imported");
    } catch (e) { toast(e.message || "Couldn't import that file", "error"); }
  });
  document.body.append(f); f.click(); setTimeout(() => f.remove(), 60000);
}
