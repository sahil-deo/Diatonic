export const state = {
  config: null, apps: [], health: null,
  convId: null, conversations: [], panelApp: null,
  runs: {},            // conversation id -> {status: "running" | "waiting", title}
  inbox: [], unreadCount: 0,   // notification inbox
  openFile: null,              // path the Files panel should show next
  unread: new Set(),   // conversations that finished while you were elsewhere
};
const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
/** Subscribe; returns a function that unsubscribes. */
export const on = (name, fn) => { const l = (e) => fn(e.detail); bus.addEventListener(name, l); return () => bus.removeEventListener(name, l); };

/** Identifies this browser tab to the server so spoken output is played by exactly one tab. */
export const CLIENT_ID = (globalThis.crypto?.randomUUID?.() || Math.random().toString(36).slice(2) + Date.now().toString(36));
