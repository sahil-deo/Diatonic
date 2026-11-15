// Applies appearance settings: accent, density, fonts, corners, width, visibility, rail order, custom CSS.
import { state } from "./store.js";

export const DEFAULT_APPEARANCE = {
  theme: "system", accent: "", density: "comfortable", font: "system", font_size: 14, radius: "default", width: "default",
  sidebar: "show", sidebar_width: "default", show_status_bar: true, show_quick_actions: true, show_suggestions: true, show_speak_button: true,
  rail_order: [], rail_hidden: [], custom_css: "",
};
export const ACCENTS = ["#7c9cff", "#a78bfa", "#f472b6", "#fb923c", "#34d399", "#22d3ee", "#facc15", "#ef4444"];
export const FONTS = {
  system: '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Inter", system-ui, sans-serif',
  sans: '"Helvetica Neue", Arial, system-ui, sans-serif',
  serif: 'Charter, "Iowan Old Style", Georgia, serif',
  mono: 'ui-monospace, "SF Mono", Menlo, monospace',
  rounded: 'ui-rounded, "SF Pro Rounded", system-ui, sans-serif',
};
const RADIUS = { sharp: 0.35, default: 1, round: 1.5 };
const WIDTH = { narrow: "660px", default: "780px", wide: "980px", full: "100%" };
const SIDEBAR = { narrow: "210px", default: null, wide: "340px" };

const luminance = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};

export function applyAppearance(a = state.config?.appearance) {
  if (!a) return;
  const root = document.documentElement, set = (k, v) => (v == null ? root.style.removeProperty(k) : root.style.setProperty(k, v));
  const ok = /^#[0-9a-f]{6}$/i.test(a.accent || "");
  set("--accent", ok ? a.accent : null);
  set("--accent-soft", ok ? `color-mix(in srgb, ${a.accent} 16%, transparent)` : null);
  set("--accent-ink", ok ? (luminance(a.accent) > 0.4 ? "#0b1020" : "#ffffff") : null);
  root.dataset.density = a.density;
  set("--font", FONTS[a.font] || FONTS.system);
  set("--fs", `${a.font_size}px`);
  set("--rs", String(RADIUS[a.radius] ?? 1));
  set("--content-w", WIDTH[a.width] || WIDTH.default);
  set("--sb-w", SIDEBAR[a.sidebar_width] ?? null);
  const shell = document.getElementById("shell");
  shell.classList.toggle("hide-status", !a.show_status_bar);
  shell.classList.toggle("hide-qa", !a.show_quick_actions);
  shell.classList.toggle("hide-suggest", !a.show_suggestions);
  shell.classList.toggle("hide-speak", !a.show_speak_button);
  shell.classList.toggle("sb-hidden", a.sidebar === "hide");
  let css = document.getElementById("user-css");
  if (!css) { css = document.createElement("style"); css.id = "user-css"; document.head.append(css); }
  css.textContent = a.custom_css || "";
}

/** Apps in the order and visibility the user chose for the left rail. */
export function railApps(apps, a = state.config.appearance) {
  const hidden = new Set(a.rail_hidden), idx = (id) => { const i = a.rail_order.indexOf(id); return i < 0 ? 999 : i; };
  return [...apps, ...(state.modApps || [])].filter((x) => x.enabled && x.panel && !hidden.has(x.id)).sort((x, y) => idx(x.id) - idx(y.id));
}
