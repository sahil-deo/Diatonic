// The built-in commands. Each is something the interface already did (open settings, toggle the sidebar, close the
// panel…); registering them gives every one an id, a place in the command palette and a key in keymap.json.
import { state } from "./store.js";
import { icon } from "./icons.js";
import { toast } from "./dom.js";
import * as chat from "./chat.js";
import { register, addProvider, labelOf, get } from "./commands.js";
import { openPanel, closePanel, refreshPanel, toggleFull } from "./panel.js";
import { openSettings } from "./settings.js";
import { openPalette } from "./palette.js";
import { openLayoutMenu } from "./layout-menu.js";
import { invoke as invokeAction } from "./actions.js";
import { getMacros, runMacro, startRecording, isRecording } from "./macros.js";
import { applyTree, toggleZoom, unzoom, duplicateTab, moveTabToward, swapTabWith, closeOtherTabs, getActiveTabId, getLayouts, switchLayout, saveCurrentLayout, deleteLayout, undoLayout, redoLayout } from "./workspace.js";
import { hintFor, keymap, userKeymap, saveKeymap } from "./keymap.js";
import { registerNav, setRegion, markKeyboard } from "./nav.js";
import { getOpenTabs, focusTab, applyPreset } from "./workspace.js";

const panelApps = () => [
  { id: "tiles", name: "Home", icon: "layout", enabled: true, panel: true },
  { id: "chats", name: "Chats", icon: "chat", enabled: true, panel: true },
  { id: "components", name: "Components", icon: "layout", enabled: true, panel: true },
  ...state.apps.filter((a) => a.enabled && a.panel)
];
const SETTINGS = [["voice", "Voice settings", "mic"], ["memory", "Memory: what the agent remembers", "brain"], ["interface", "Customize the interface", "sliders"],
  ["actions", "Edit saved prompts and buttons", "command"], ["keyboard", "Keyboard shortcuts", "command"],
  ["layouts", "Layouts: presets and saved layouts", "layout"], ["macros", "Macros: record and run", "activity"]];

/** h: functions that live in main.js ({toggleSidebar, setSidebar, setTheme, setSpeak, openModelMenu, focusComposer}). */
export function registerCore(h) {
  registerNav();
  const panelOpen = () => !!state.panelApp;
  const light = () => document.documentElement.dataset.theme === "light";
  [
    { id: "palette.open", title: "Command palette", hidden: true, run: () => openPalette() },
    { id: "chat.ask", title: "Ask the agent", group: "Actions", icon: "chat", run: () => openPalette({ mode: "agent" }) },
    { id: "chat.new", title: "New chat", group: "Actions", icon: "plus", run: () => chat.newChat() },
    { id: "chat.stop", title: "Stop the current chat", group: "Actions", icon: "stop", when: () => chat.busy(), run: () => chat.stop() },
    { id: "scratchpad.toggle", title: "Toggle Scratchpad drawer", group: "Actions", icon: "edit", run: () => { import("./scratchpad.js").then((m) => m.toggleScratchpad()); } },
    { id: "speak.toggle", group: "Actions", icon: "volume", title: () => (state.config.voice.speak_summary ? "Turn off spoken summaries" : "Turn on spoken summaries"),
      run: () => h.setSpeak(!state.config.voice.speak_summary) },
    { id: "settings.open", title: "Settings", group: "Actions", icon: "sliders", run: (a) => openSettings(a.tab || undefined),
      titleFor: (a) => (a.tab ? `Settings: ${a.tab}` : "Settings") },
    { id: "prompt.show", title: "Show the system prompt", group: "Settings", icon: "sliders", run: () => import("./prompt-view.js").then((m) => m.showPrompt()) },
    { id: "customization.export", title: "Export customizations", group: "Settings", icon: "download", run: () => { window.location.href = "/api/customization/export"; } },
    { id: "sidebar.set", title: "Show or hide the sidebar", group: "Actions", hidden: true, run: (a) => h.setSidebar(a.mode) },
    { id: "sidebar.toggle", title: "Toggle sidebar", group: "Actions", icon: "menu", run: () => h.toggleSidebar() },
    { id: "rail.set", title: "Show or hide the left rail", group: "Actions", hidden: true, run: (a) => h.setRail?.(a.mode) },
    { id: "rail.toggle", title: "Toggle left rail", group: "Actions", icon: "menu", run: () => h.toggleRail?.() },
    { id: "theme.dark", title: "Theme: dark", group: "Actions", icon: "chevron", run: () => h.setTheme("dark") },
    { id: "theme.light", title: "Theme: light", group: "Actions", icon: "chevron", run: () => h.setTheme("light") },
    { id: "theme.toggle", title: "Theme: switch light / dark", group: "Actions", icon: "chevron", hidden: true, run: () => h.setTheme(light() ? "dark" : "light") },
    { id: "model.menu", title: "Switch model", group: "Actions", icon: "brain", run: () => h.openModelMenu() },
    { id: "composer.focus", title: "Write a message", group: "Navigate", hidden: true, run: () => { setRegion("chat"); h.focusComposer(); } },
    { id: "app.open", title: "Open an app", group: "Apps", hidden: true, run: (a) => {
      const all = [{ id: "chat", name: "Chat", icon: "chat", enabled: true, panel: true }, ...panelApps()];
      const app = all.find((x) => x.id === a.id);
      if (!app) return false;
      return openPanel(app.id, a.params || {}).then(() => {
        if (a.focus === false) return;   // the agent opened it: leave the keyboard where it is
        setRegion("panel");
        markKeyboard();
      });
    }, titleFor: (a) => `Open ${panelApps().find((x) => x.id === a.id)?.name || a.id}` },
    { id: "panel.close", title: "Close app panel", group: "Apps", icon: "x", when: panelOpen, run: () => closePanel() },
    { id: "panel.detach", title: "Detach panel to own window", group: "Apps", icon: "popout", when: panelOpen, run: async () => {
      const ws = await import("./workspace.js");
      const currentTab = ws.getActiveTabId?.();
      if (currentTab) {
        const tabs = ws.getOpenTabs?.() || [];
        const found = tabs.find((t) => t.tabId === currentTab);
        if (found) {
          window.open(`/?app=${encodeURIComponent(found.appId)}`, "_blank", "width=850,height=650");
          ws.closePanel(currentTab);
        }
      }
    } },
    { id: "panel.toggleFull", title: "Expand or shrink the app panel", group: "Apps", icon: "maximize", when: panelOpen, run: () => toggleFull() },
    { id: "panel.refresh", title: "Refresh the app panel", group: "Apps", icon: "refresh", when: panelOpen, run: () => refreshPanel() },
    { id: "terminal.ask", title: "Terminal: Ask the agent about output", group: "Actions", icon: "chat", run: () => {
      const activeId = getActiveTabId();
      const panelEl = activeId ? document.querySelector(`.workspace-panel-body[data-body-for="${activeId}"]`) : document.querySelector(".workspace-panel-body[data-body-for]");
      panelEl?.dispatchEvent(new CustomEvent("shell:ask-agent"));
    } },
    { id: "terminal.history", title: "Terminal: Toggle agent command history", group: "Actions", icon: "clock", run: () => {
      const activeId = getActiveTabId();
      const panelEl = activeId ? document.querySelector(`.workspace-panel-body[data-body-for="${activeId}"]`) : document.querySelector(".workspace-panel-body[data-body-for]");
      panelEl?.dispatchEvent(new CustomEvent("shell:toggle-history"));
    } },
    { id: "focus.left", title: "Focus left panel", group: "Navigate", icon: "chevleft", run: () => { import("./workspace.js").then((m) => m.focusNeighbor("left")); setRegion("chat"); } },
    { id: "focus.right", title: "Focus right panel", group: "Navigate", icon: "chevright", run: () => { import("./workspace.js").then((m) => m.focusNeighbor("right")); setRegion("panel"); } },
    { id: "focus.up", title: "Focus upper panel", group: "Navigate", icon: "chevron", run: () => { import("./workspace.js").then((m) => m.focusNeighbor("up")); setRegion("panel"); } },
    { id: "focus.down", title: "Focus lower panel", group: "Navigate", icon: "chevron", run: () => { import("./workspace.js").then((m) => m.focusNeighbor("down")); setRegion("panel"); } },
    { id: "panel.split.right", title: "Split panel side-by-side", group: "Layout", icon: "chevron", run: (a) => { import("./workspace.js").then((m) => m.splitActive("row", a?.app)); } },
    { id: "panel.split.down", title: "Split panel stacked", group: "Layout", icon: "chevron", run: (a) => { import("./workspace.js").then((m) => m.splitActive("col", a?.app)); } },
    { id: "layout.preset.single", title: "Layout: Single panel", group: "Layout", icon: "box", run: () => applyPreset("single") },
    { id: "layout.preset.side_by_side", title: "Layout: Side-by-side", group: "Layout", icon: "split-v", run: () => applyPreset("side-by-side") },
    { id: "layout.preset.stacked", title: "Layout: Stacked", group: "Layout", icon: "split-h", run: () => applyPreset("stacked") },
    { id: "layout.preset.three_column", title: "Layout: Three columns", group: "Layout", icon: "chevron", run: () => applyPreset("three-column") },
    { id: "layout.preset.grid", title: "Layout: 2x2 Grid", group: "Layout", icon: "chevron", run: () => applyPreset("grid") },
    { id: "tab.next", title: "Next panel tab", group: "Layout", icon: "chevron", run: () => { import("./workspace.js").then((m) => m.focusNeighbor("right")); } },
    { id: "tab.prev", title: "Previous panel tab", group: "Layout", icon: "chevron", run: () => { import("./workspace.js").then((m) => m.focusNeighbor("left")); } },
    { id: "tab.reopen", title: "Reopen closed panel tab", group: "Layout", icon: "plus", run: () => { import("./workspace.js").then((m) => m.reopenClosedTab()); } },
    { id: "statusbar.pin", title: "Pin app to status bar", group: "Layout", icon: "command", run: async () => {
      const { pickApp } = await import("./palette.js");
      const { addPin } = await import("./statusbar.js");
      const appId = await pickApp("Pin app to status bar…");
      if (appId) {
        const app = panelApps().find((a) => a.id === appId) || { id: appId, name: appId, icon: "box" };
        addPin({ appId: app.id, label: app.name, icon: app.icon });
      }
    } },
    { id: "keys.vim.toggle", group: "Navigate", icon: "command", title: () => (keymap().vim ? "Turn off vim-style keys (j k g G)" : "Turn on vim-style keys (j k g G)"),
      run: () => saveKeymap({ ...userKeymap(), vim: !keymap().vim }) },
    { id: "layout.apply", title: "Apply a layout tree", group: "Layout", hidden: true,
      run: (a) => { if (a?.tree) applyTree(a.tree); } },
    { id: "workspace.applyTree", title: "Apply a layout tree", group: "Layout", hidden: true,   // old name, kept so macros saved earlier still run
      run: (a) => { if (a?.tree) applyTree(a.tree); } },
    { id: "macro.record.start", title: "Start recording macro", group: "Macros", icon: "activity",
      when: () => !isRecording(),
      run: (a) => {
        if (a?.name) return startRecording(a.name);
        openSettings("macros");   // a name box and a Start button, no browser prompt
      } },
    { id: "macro.record.stop", title: "Stop recording macro", group: "Macros", icon: "stop",
      when: () => isRecording(),
      run: async () => {
        const { saveRecorded } = await import("./macros.js");
        saveRecorded();
      } },
    { id: "layout.save", title: "Save current layout", group: "Layout", icon: "sliders",
      run: (a) => {
        if (a?.name) return saveCurrentLayout(a.name);
        openLayoutMenu(undefined, { focusSave: true });   // the menu has a name box: no browser prompt
      } },
    { id: "layout.switch", title: "Switch layout", group: "Layout", icon: "sliders",
      run: (a) => {
        if (!a?.id && !a?.name) return false;
        return switchLayout(a.id || a.name);
      } },
    { id: "layout.delete", title: "Delete layout", group: "Layout", icon: "x",
      run: (a) => {
        if (a?.id || a?.name) return deleteLayout(a.id || a.name);
        if (!getLayouts().length) { toast("No saved layouts to delete", "error"); return false; }
        openPalette({ initialQuery: "Delete layout" });   // a list of your layouts, one per row
      } },
    { id: "panel.menu", title: "Panel menu", group: "Layout", icon: "more", run: async () => { const el = document.querySelector(`[data-tab-id="${getActiveTabId()}"]`); if (el) (await import("./panel-menu.js")).openPanelMenu(el, getActiveTabId()); } },
    { id: "panel.zoom", title: "Zoom the panel to fill the window (again to undo)", group: "Layout", icon: "maximize", run: (a) => {
      let tid = a?.tabId;
      if (!tid && a?.app) {
        const tabs = typeof getOpenTabs === "function" ? getOpenTabs() : [];
        const found = tabs.find((x) => x.id === a.app || x.app === a.app);
        if (found) tid = found.tabId;
      }
      toggleZoom(tid || undefined);
    } },
    { id: "panel.unzoom", title: "Restore all panels (exit zoom)", group: "Layout", icon: "minimize", run: () => unzoom() },
    { id: "panel.duplicate", title: "Duplicate panel", group: "Layout", icon: "plus", run: () => duplicateTab() },
    { id: "tab.close_others", title: "Close other tabs in this panel", group: "Layout", icon: "x", run: () => closeOtherTabs() },
    { id: "panel.move.left", title: "Move panel left", group: "Layout", icon: "chevron", run: () => moveTabToward(undefined, "left") },
    { id: "panel.swap.left", title: "Swap with the panel to the left", group: "Layout", icon: "chevron", run: () => swapTabWith(undefined, "left") },
    { id: "panel.move.right", title: "Move panel right", group: "Layout", icon: "chevron", run: () => moveTabToward(undefined, "right") },
    { id: "panel.swap.right", title: "Swap with the panel to the right", group: "Layout", icon: "chevron", run: () => swapTabWith(undefined, "right") },
    { id: "panel.move.up", title: "Move panel up", group: "Layout", icon: "chevron", run: () => moveTabToward(undefined, "up") },
    { id: "panel.swap.up", title: "Swap with the panel to the up", group: "Layout", icon: "chevron", run: () => swapTabWith(undefined, "up") },
    { id: "panel.move.down", title: "Move panel down", group: "Layout", icon: "chevron", run: () => moveTabToward(undefined, "down") },
    { id: "panel.swap.down", title: "Swap with the panel to the down", group: "Layout", icon: "chevron", run: () => swapTabWith(undefined, "down") },
    { id: "layout.menu", title: "Layouts menu", group: "Layout", icon: "layout", run: () => openLayoutMenu() },
    { id: "layout.undo", title: "Undo layout change", group: "Layouts", icon: "refresh", run: () => undoLayout() },
    { id: "layout.redo", title: "Redo layout change", group: "Layouts", icon: "refresh", run: () => redoLayout() },
  ].forEach(register);

  for (const [tab, title, ic] of SETTINGS) register({ id: `settings.${tab}`, title, group: "Actions", icon: ic, run: () => openSettings(tab) });

  addProvider(() => panelApps().map((a) => ({ id: `app.open:${a.id}`, appId: a.id, group: "Apps", label: `Open ${a.name}`, icon: a.icon, hint: hintFor("app.open", { id: a.id }), run: () => get("app.open").run({ id: a.id }) })));
  addProvider(() => panelApps().map((a) => ({ id: `app.split.row:${a.id}`, appId: a.id, group: "Layout", label: `Split side-by-side with ${a.name}`, icon: "chevright", run: () => { import("./workspace.js").then((m) => m.splitActive("row", a.id)); } })));
  addProvider(() => panelApps().map((a) => ({ id: `app.split.col:${a.id}`, appId: a.id, group: "Layout", label: `Split stacked with ${a.name}`, icon: "chevron", run: () => { import("./workspace.js").then((m) => m.splitActive("col", a.id)); } })));
  addProvider(() => panelApps().map((a) => ({ id: `statusbar.pin:${a.id}`, group: "Status Bar", label: `Pin ${a.name} to status bar`, icon: a.icon, run: () => { import("./statusbar.js").then((m) => m.addPin({ appId: a.id, label: a.name, icon: a.icon })); } })));
  addProvider(() => (state.config.actions || []).map((a) => ({ id: `action:${a.id}`, group: "Your actions", label: a.label, icon: a.kind === "script" ? "command" : "chat",
    hint: a.name ? "/" + a.name : "", run: () => invokeAction(a) })));
  addProvider(() => state.conversations.slice(0, 15).map((c) => ({ id: `conv:${c.id}`, group: "Conversations", label: c.title, icon: "chat", run: () => chat.openConversation(c.id) })));

  const LAYOUT_PRESETS = [
    ["single", "Single panel"],
    ["side-by-side", "Side-by-side"],
    ["stacked", "Stacked"],
    ["three-column", "Three columns"],
    ["grid", "2x2 Grid"],
  ];
  addProvider(() => LAYOUT_PRESETS.map(([id, label]) => ({
    id: `layout.preset:${id}`,
    group: "Layouts",
    label: `Layout: ${label}`,
    icon: id === "stacked" ? "split-h" : id === "side-by-side" ? "split-v" : "box",
    run: () => applyPreset(id),
  })));

  addProvider(() => (typeof getOpenTabs === "function" ? getOpenTabs() : []).map((t) => ({
    id: `tab.focus:${t.tabId}`,
    group: "Open panels",
    label: `Switch to ${t.title} tab`,
    icon: t.icon || "box",
    run: () => focusTab(t.tabId),
  })));

  addProvider(() => getMacros().map((m) => ({
    id: `macro.run:${m.id}`,
    group: "Macros",
    label: `Run macro: ${m.name}`,
    icon: "command",
    hint: `${m.steps?.length || 0} steps`,
    run: () => runMacro(m),
  })));

  addProvider(() => getLayouts().map((w) => ({
    id: `layout.switch:${w.id}`,
    group: "Layouts",
    label: `Switch to layout: ${w.name}`,
    icon: "sliders",
    hint: w.description || "Layout",
    run: () => switchLayout(w.id),
  })));

  addProvider(() => getLayouts().map((w) => ({
    id: `layout.delete:${w.id}`,
    group: "Layouts",
    label: `Delete layout: ${w.name}`,
    icon: "x",
    hint: "Saved layout",
    run: () => { if (confirm(`Delete the layout "${w.name}"? Your open panels stay as they are.`)) return deleteLayout(w.id); },
  })));
}
