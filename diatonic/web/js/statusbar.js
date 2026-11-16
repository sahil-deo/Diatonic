// Status bar pins, zones (left, center, right), and panel-contributed badges.
import { $, h } from "./dom.js";
import { icon } from "./icons.js";
import { state, on } from "./store.js";
import { api } from "./api.js";

const STORAGE_KEY = "diatonic.statusbar.pins";

const DEFAULT_PINS = [
  { id: "pin:gmail", label: "Mail", icon: "mail", appId: "gmail", zone: "center" },
  { id: "pin:shell", label: "Terminal", icon: "command", appId: "shell", zone: "center" },
  { id: "pin:files", label: "Files", icon: "folder", appId: "files", zone: "center" },
];

let contributedItems = new Map();
let unreadMailCount = 0;
let activeShellCount = 0;

export function getPins() {
  const cfgPins = state.config?.appearance?.statusbar_pins;
  if (Array.isArray(cfgPins) && cfgPins.length > 0) return cfgPins;
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return JSON.parse(raw);
  } catch {}
  return [...DEFAULT_PINS];
}

export function savePins(pins) {
  if (state.config?.appearance) {
    state.config.appearance.statusbar_pins = pins;
  }
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(pins));
  } catch {}
  api.putConfig({ appearance: { statusbar_pins: pins } }).catch(() => {});
  renderStatusBar();
}

export function addPin(spec) {
  const pins = getPins();
  const id = spec.id || `pin:${spec.appId || Date.now()}`;
  const existing = pins.findIndex((p) => p.id === id);
  const pin = {
    id,
    label: spec.label || spec.appId,
    icon: spec.icon || "box",
    appId: spec.appId,
    command: spec.command,
    args: spec.args,
    zone: spec.zone || "center",
  };
  if (existing >= 0) pins[existing] = pin;
  else pins.push(pin);
  savePins(pins);
}

export function removePin(id) {
  const pins = getPins().filter((p) => p.id !== id && p.appId !== id);
  savePins(pins);
}

/** Allows open panels to contribute live indicators (e.g. unread counts, running tasks). */
export function contributeStatus(spec) {
  if (!spec || !spec.id) return () => {};
  contributedItems.set(spec.id, spec);
  renderStatusBar();
  return () => {
    contributedItems.delete(spec.id);
    renderStatusBar();
  };
}

async function checkBadges() {
  // Query unread mail count
  try {
    const st = await api.get("/api/apps/gmail/state").catch(() => null);
    if (st && Array.isArray(st.threads)) {
      unreadMailCount = st.threads.filter((t) => t.unread).length;
    }
  } catch {}

  // Query active terminal count
  try {
    const sh = await api.get("/api/apps/shell/state").catch(() => null);
    if (sh && Array.isArray(sh.sessions)) {
      activeShellCount = sh.sessions.filter((s) => !s.exited).length;
    }
  } catch {}

  renderStatusBar();
}

function showContextMenu(e, pin) {
  e.preventDefault();
  const existing = $("#sb-context-menu");
  if (existing) existing.remove();

  const menu = h("div", {
    id: "sb-context-menu",
    class: "sb-menu",
    style: `position: fixed; left: ${e.clientX}px; bottom: 32px; z-index: 1000;`,
  },
    h("button", {
      type: "button",
      class: "sb-menu-item",
      onclick: async () => {
        menu.remove();
        const ws = await import("./workspace.js");
        ws.splitActive("row", pin.appId);
      },
    }, h("span", { html: icon("split-v", 14) }), "Open in side-by-side split"),
    h("button", {
      type: "button",
      class: "sb-menu-item",
      onclick: async () => {
        menu.remove();
        const ws = await import("./workspace.js");
        ws.splitActive("col", pin.appId);
      },
    }, h("span", { html: icon("split-h", 14) }), "Open in stacked split"),
    h("div", { class: "sb-menu-sep" }),
    h("button", {
      type: "button",
      class: "sb-menu-item danger",
      onclick: () => {
        menu.remove();
        removePin(pin.id);
      },
    }, h("span", { html: icon("trash", 14) }), `Unpin ${pin.label}`)
  );

  const closeMenu = (ev) => {
    if (!menu.contains(ev.target)) {
      menu.remove();
      document.removeEventListener("pointerdown", closeMenu);
    }
  };

  document.body.append(menu);
  setTimeout(() => document.addEventListener("pointerdown", closeMenu), 10);
}

function renderPin(pin) {
  let badgeCount = 0;
  if (pin.appId === "gmail" && unreadMailCount > 0) badgeCount = unreadMailCount;
  else if (pin.appId === "shell" && activeShellCount > 0) badgeCount = activeShellCount;

  const btn = h("button", {
    type: "button",
    class: `sb-pin ${state.panelApp === pin.appId ? "active" : ""}`,
    title: `${pin.label} (Click to open, ⌘Click to split side-by-side, Shift-Click to split stacked)`,
    onclick: async (ev) => {
      ev.preventDefault();
      const ws = await import("./workspace.js");
      if (ev.metaKey || ev.ctrlKey) {
        await ws.splitActive("row", pin.appId);
      } else if (ev.shiftKey) {
        await ws.splitActive("col", pin.appId);
      } else {
        await ws.openPanel(pin.appId);
      }
    },
    oncontextmenu: (ev) => showContextMenu(ev, pin),
  },
    h("span", { class: "sb-pin-icon", html: icon(pin.icon || "box", 14) }),
    h("span", { class: "sb-pin-label" }, pin.label),
    badgeCount > 0 && h("span", { class: "pill st-badge" }, String(badgeCount))
  );

  return btn;
}

export function renderStatusBar() {
  const leftZone = $("#sb-left");
  const centerZone = $("#sb-center");
  const rightZone = $("#sb-right");
  if (!leftZone || !centerZone || !rightZone) return;

  const pins = getPins();

  // Pins by zone
  const leftPins = pins.filter((p) => p.zone === "left");
  const centerPins = pins.filter((p) => !p.zone || p.zone === "center");
  const rightPins = pins.filter((p) => p.zone === "right");

  // Mount into center zone
  const centerElements = [
    ...centerPins.map(renderPin),
    ...Array.from(contributedItems.values()).map((it) => {
      return h("button", {
        type: "button",
        class: "sb-pin contributed",
        onclick: it.onClick || (() => {}),
        title: it.title || it.label,
      },
        it.icon && h("span", { class: "sb-pin-icon", html: icon(it.icon, 14) }),
        h("span", { class: "sb-pin-label" }, it.label),
        it.badge && h("span", { class: "pill st-badge" }, String(it.badge))
      );
    }),
  ];
  centerZone.replaceChildren(...centerElements);

  // Mount into left zone (pins container)
  let leftPinsEl = leftZone.querySelector(".sb-pins-left");
  if (!leftPinsEl) {
    leftPinsEl = h("div", { class: "sb-pins sb-pins-left" });
    leftZone.append(leftPinsEl);
  }
  leftPinsEl.replaceChildren(...leftPins.map(renderPin));

  // Mount into right zone (pins container before hint)
  let rightPinsEl = rightZone.querySelector(".sb-pins-right");
  if (!rightPinsEl) {
    rightPinsEl = h("div", { class: "sb-pins sb-pins-right" });
    rightZone.prepend(rightPinsEl);
  }
  rightPinsEl.replaceChildren(...rightPins.map(renderPin));
}

export function initStatusBar() {
  renderStatusBar();
  checkBadges();

  on("config", renderStatusBar);
  on("panel", renderStatusBar);
  on("apps", renderStatusBar);
  on("notification", checkBadges);
  setInterval(checkBadges, 30000);
}
