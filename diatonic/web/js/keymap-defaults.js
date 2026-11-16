// The built-in key bindings. The user's keymap.json is merged over these (see keys.js buildKeymap); "Mod" is ⌘ on a Mac.
// `when` names are set by keymap.js: input (typing in a text box), composer (the chat box), terminal, dialog, idle (not typing), plain (idle and
// not on a button or link), vim, panel, panel:<app>, app:<app>, focus:chat, focus:panel, list.
export const DEFAULT_LEADER = "Mod+;";

// Letters after "<leader> a" that open an app.
const APP_KEYS = { a: "chat", f: "files", m: "gmail", c: "gcal", n: "notes", t: "todos", g: "github", s: "shell", p: "presentations", j: "scheduler", y: "telemetry", h: "tiles" };

export const DEFAULTS = [
  { key: "Mod+k", command: "palette.open" },
  { key: "Mod+j", command: "chat.ask" },
  { key: "Mod+,", command: "settings.open" },
  { key: "Mod+b", command: "sidebar.toggle" },
  { key: "Mod+Shift+o", command: "chat.new" },
  { key: "Mod+Shift+t", command: "tab.reopen" },
  { key: "Mod+\\", command: "scratchpad.toggle" },
  { key: "/", command: "composer.focus", when: "idle" },
  { key: "Escape", command: "focus.release", when: "composer && !dialog" },

  // Leader: ⌘; then a key. The which-key popup lists what can follow.
  { key: "<leader> <leader>", command: "palette.open" },
  { key: "<leader> n", command: "chat.new" },
  { key: "<leader> p", command: "scratchpad.toggle" },
  { key: "<leader> s", command: "settings.open" },
  { key: "<leader> k", command: "settings.open", args: { tab: "keyboard" } },
  { key: "<leader> b", command: "sidebar.toggle" },
  { key: "<leader> t", command: "theme.toggle" },
  { key: "<leader> m", command: "model.menu" },
  { key: "<leader> x", command: "panel.close", when: "panel" },
  { key: "<leader> z", command: "panel.toggleFull", when: "panel" },
  { key: "<leader> r", command: "panel.refresh", when: "panel" },
  { key: "<leader> u", command: "layout.undo" },
  { key: "<leader> U", command: "layout.redo" },
  { key: "<leader> h", command: "focus.left" },
  { key: "<leader> l", command: "focus.right" },
  { key: "<leader> w s", command: "panel.split.down" },
  { key: "<leader> w v", command: "panel.split.right" },
  { key: "<leader> w t", command: "tab.reopen" },
  { key: "<leader> [", command: "tab.prev" },
  { key: "<leader> ]", command: "tab.next" },
  ...Object.entries(APP_KEYS).map(([k, id]) => ({ key: `<leader> a ${k}`, command: "app.open", args: { id }, when: `app:${id}` })),

  // Moving around without a mouse, when you are not typing.
  { key: "ArrowDown", command: "nav.down", when: "idle" },
  { key: "ArrowUp", command: "nav.up", when: "idle" },
  { key: "PageDown", command: "nav.pageDown", when: "idle" },
  { key: "PageUp", command: "nav.pageUp", when: "idle" },
  { key: "Home", command: "nav.top", when: "idle" },
  { key: "End", command: "nav.bottom", when: "idle" },
  { key: "Space", command: "nav.pageDown", when: "plain" },
  { key: "Shift+Space", command: "nav.pageUp", when: "plain" },
  { key: "Enter", command: "nav.open", when: "plain && list" },

  // Vim-style keys: Settings > Keyboard turns them on.
  { key: "j", command: "nav.down", when: "idle && vim" },
  { key: "k", command: "nav.up", when: "idle && vim" },
  { key: "Ctrl+d", command: "nav.halfDown", when: "idle && vim" },
  { key: "Ctrl+u", command: "nav.halfUp", when: "idle && vim" },
  { key: "g g", command: "nav.top", when: "idle && vim" },
  { key: "G", command: "nav.bottom", when: "idle && vim" },
  { key: "i", command: "composer.focus", when: "idle && vim" },
];
