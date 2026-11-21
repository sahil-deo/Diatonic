// The panel menu: everything you can do to one panel, by mouse. Opened from the ⋯ button on a tab bar or by right-clicking a tab.
import { h } from "./dom.js";
import { icon } from "./icons.js";
import { state } from "./store.js";
import { openPopover, closePopover } from "./popover.js";
import { findTab } from "./layout.js";
import { getPins, addPin, removePin } from "./statusbar.js";
import * as ws from "./workspace.js";

const SPECIAL = { chat: ["Chat", "chat"], chats: ["Chats", "chat"], launcher: ["New tab", "plus"], tiles: ["Home", "layout"], components: ["Components", "layout"], home: ["Home", "layout"] };
const metaOf = (app) => {
  if (SPECIAL[app]) return SPECIAL[app];
  const a = state.apps.find((x) => x.id === app);
  return [a?.name || app, a?.icon || "box"];
};

export function openPanelMenu(anchor, tabId, stackId) {
  const found = tabId && findTab(ws.getTree(), tabId);
  if (!found) return;
  const { tab, stack } = found;
  const appObj = state.apps.find((x) => x.id === tab.app);
  const [name, ic] = metaOf(tab.app);
  const dirs = ws.neighbourDirections(tabId);
  const pinned = getPins().some((p) => p.appId === tab.app);
  const pinnable = !["launcher"].includes(tab.app);
  const zoomed = ws.isZoomed();
  const go = (fn) => () => { closePopover(); fn(); };

  const row = (label, rowIcon, fn, hint, off = false, danger = false) => h("button", { type: "button", class: `pm-row${danger ? " danger" : ""}`, disabled: off || undefined, onclick: go(fn) },
    h("span", { html: icon(rowIcon, 15) }), h("span", { class: "pm-name" }, label), hint && h("span", { class: "pm-key" }, hint));
  const arrows = (label, fn) => h("div", { class: "pm-arrows" }, h("span", { class: "pm-name" }, label),
    ["left", "up", "down", "right"].map((d) => h("button", { type: "button", class: `icon-btn sm pm-arrow pm-arrow-${d}`, disabled: !dirs.includes(d) || undefined,
      title: dirs.includes(d) ? `${label} ${d}` : `Nothing to the ${d}`, "aria-label": `${label} ${d}`, html: icon("chevron", 14), onclick: go(() => fn(tabId, d)) })));

  const appRows = [];
  if (tab.app === "shell") {
    const termBody = () => document.querySelector(`.workspace-panel-body[data-body-for="${tabId}"]`);
    appRows.push(
      row("Ask the agent", "chat", () => termBody()?.dispatchEvent(new CustomEvent("shell:ask-agent"))),
      row("Agent history", "clock", () => termBody()?.dispatchEvent(new CustomEvent("shell:toggle-history"))),
      h("div", { class: "pm-sep" })
    );
  }

  const pop = openPopover(anchor, h("div", { class: "lm pm panel-menu" },
    row("Reload", "refresh", () => ws.reloadTab(tabId, true)),
    h("div", { class: "pm-sep" }),
    ...appRows,
    row("Split to the right", "split-v", () => ws.splitPanel(tabId, "row", "launcher"), "⌘; w v"),
    row("Split below", "split-h", () => ws.splitPanel(tabId, "col", "launcher"), "⌘; w s"),
    h("div", { class: "pm-sep" }),
    row(zoomed ? "Show all panels" : "Zoom: fill the window", "maximize", () => ws.toggleZoom(tabId), "⌘; w z"),
    row("Duplicate", "plus", () => ws.duplicateTab(tabId), "⌘; w d"),
    arrows("Move", ws.moveTabToward),
    arrows("Swap with", ws.swapTabWith),
    h("div", { class: "pm-sep" }),
    pinnable && row(pinned ? "Unpin from status bar" : "Pin to status bar", "star", () => (pinned ? removePin(tab.app) : addPin({ appId: tab.app, label: name, icon: ic, zone: "center" }))),
    row("Open in its own window", "popout", () => { window.open(`/?app=${encodeURIComponent(tab.app)}`, "_blank", "width=850,height=650"); ws.closePanel(tabId); }),
    h("div", { class: "pm-sep" }),
    row("Close tab", "x", () => ws.closePanel(tabId)),
    row("Close other tabs", "x", () => ws.closeOtherTabs(tabId), null, stack.tabs.length < 2, true),
    appObj?.file_app ? h("div", { class: "pm-sep" }) : null,
    appObj?.file_app ? row("Delete custom app...", "trash", async () => {
      if (confirm(`Permanently delete custom app "${name}" and all its files?`)) {
        try {
          const { api } = await import("./api.js");
          const { refreshApps } = await import("./settings.js");
          await api.del(`/api/custom-apps/${tab.app}`);
          ws.closePanel(tabId);
          await refreshApps();
        } catch (err) {
          alert(`Failed to delete app: ${err.message}`);
        }
      }
    }, null, false, true) : null),
    { align: "right", below: true });
  pop?.place();
}
