// The built-in commands. Each is something the interface already did (open settings, toggle the sidebar, close the
// panel…); registering them gives every one an id, a place in the command palette and a key in keymap.json.
import { state } from "./store.js";
import { icon } from "./icons.js";
import * as chat from "./chat.js";
import { register, addProvider, labelOf, get } from "./commands.js";
import { openPanel, closePanel, refreshPanel, toggleFull } from "./panel.js";
import { openSettings } from "./settings.js";
import { openPalette } from "./palette.js";
import { invoke as invokeAction } from "./actions.js";
import { hintFor, keymap, userKeymap, saveKeymap } from "./keymap.js";
import { registerNav, setRegion, markKeyboard } from "./nav.js";

const panelApps = () => [...state.apps, ...(state.modApps || [])].filter((a) => a.enabled && a.panel);
const SETTINGS = [["voice", "Voice settings", "mic"], ["memory", "Memory: what the agent remembers", "brain"], ["interface", "Customize the interface", "sliders"],
  ["actions", "Edit saved prompts and buttons", "command"], ["keyboard", "Keyboard shortcuts", "command"]];

/** h: functions that live in main.js ({toggleSidebar, setTheme, setSpeak, openModelMenu, focusComposer}). */
export function registerCore(h) {
  registerNav();
  const panelOpen = () => !!state.panelApp;
  const light = () => document.documentElement.dataset.theme === "light";
  [
    { id: "palette.open", title: "Command palette", hidden: true, run: () => openPalette() },
    { id: "chat.new", title: "New chat", group: "Actions", icon: "plus", run: () => chat.newChat() },
    { id: "chat.stop", title: "Stop the current chat", group: "Actions", icon: "stop", when: () => chat.busy(), run: () => chat.stop() },
    { id: "speak.toggle", group: "Actions", icon: "volume", title: () => (state.config.voice.speak_summary ? "Turn off spoken summaries" : "Turn on spoken summaries"),
      run: () => h.setSpeak(!state.config.voice.speak_summary) },
    { id: "settings.open", title: "Settings", group: "Actions", icon: "sliders", run: (a) => openSettings(a.tab || undefined),
      titleFor: (a) => (a.tab ? `Settings: ${a.tab}` : "Settings") },
    { id: "sidebar.toggle", title: "Toggle sidebar", group: "Actions", icon: "menu", run: () => h.toggleSidebar() },
    { id: "theme.dark", title: "Theme: dark", group: "Actions", icon: "chevron", run: () => h.setTheme("dark") },
    { id: "theme.light", title: "Theme: light", group: "Actions", icon: "chevron", run: () => h.setTheme("light") },
    { id: "theme.toggle", title: "Theme: switch light / dark", group: "Actions", icon: "chevron", hidden: true, run: () => h.setTheme(light() ? "dark" : "light") },
    { id: "model.menu", title: "Switch model", group: "Actions", icon: "brain", run: () => h.openModelMenu() },
    { id: "composer.focus", title: "Write a message", group: "Navigate", hidden: true, run: () => { setRegion("chat"); h.focusComposer(); } },
    { id: "app.open", title: "Open an app", group: "Apps", hidden: true, run: (a) => {
      const app = panelApps().find((x) => x.id === a.id);
      if (!app) return false;
      if (state.panelApp === app.id) { setRegion("panel"); markKeyboard(); return; }
      return openPanel(app.id).then(() => setRegion("panel"));
    }, titleFor: (a) => `Open ${panelApps().find((x) => x.id === a.id)?.name || a.id}` },
    { id: "panel.close", title: "Close app panel", group: "Apps", icon: "x", when: panelOpen, run: () => closePanel() },
    { id: "panel.toggleFull", title: "Expand or shrink the app panel", group: "Apps", icon: "maximize", when: panelOpen, run: () => toggleFull() },
    { id: "panel.refresh", title: "Refresh the app panel", group: "Apps", icon: "refresh", when: panelOpen, run: () => refreshPanel() },
    { id: "keys.vim.toggle", group: "Navigate", icon: "command", title: () => (keymap().vim ? "Turn off vim-style keys (j k g G)" : "Turn on vim-style keys (j k g G)"),
      run: () => saveKeymap({ ...userKeymap(), vim: !keymap().vim }) },
  ].forEach(register);

  for (const [tab, title, ic] of SETTINGS) register({ id: `settings.${tab}`, title, group: "Actions", icon: ic, run: () => openSettings(tab) });

  addProvider(() => panelApps().map((a) => ({ id: `app.open:${a.id}`, group: "Apps", label: `Open ${a.name}`, icon: a.icon, hint: hintFor("app.open", { id: a.id }), run: () => get("app.open").run({ id: a.id }) })));
  addProvider(() => (state.config.actions || []).map((a) => ({ id: `action:${a.id}`, group: "Your actions", label: a.label, icon: a.kind === "script" ? "command" : "chat",
    hint: a.name ? "/" + a.name : "", run: () => invokeAction(a) })));
  addProvider(() => state.conversations.slice(0, 15).map((c) => ({ id: `conv:${c.id}`, group: "Conversations", label: c.title, icon: "chat", run: () => chat.openConversation(c.id) })));
}
