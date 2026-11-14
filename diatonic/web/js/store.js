export const state = {
  config: null, apps: [], health: null,
  convId: null, conversations: [], running: false, panelApp: null,
};
const bus = new EventTarget();
export const emit = (name, detail) => bus.dispatchEvent(new CustomEvent(name, { detail }));
export const on = (name, fn) => bus.addEventListener(name, (e) => fn(e.detail));
