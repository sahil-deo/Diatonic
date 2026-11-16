// The command registry: everything the interface can do has an id. The command palette, the keymap, saved macros and
// panels all run commands by id, so a new way to trigger one (a key, a menu, the agent) needs no new code in the feature.
//
//   register({ id: "panel.close", title: "Close app panel", group: "Apps", icon: "x", run: (args) => …, when: () => bool })
//
// `when` (optional) says whether the command makes sense right now; unavailable commands stay out of the palette, and
// so do ones marked `hidden` (keys and panels can still run them). `titleFor(args)` names a binding that carries args.
// `register` returns a function that removes the command again, which is how a panel's own commands go away with it.

const cmds = new Map();
const providers = new Set();
const runListeners = new Set();

export function onRun(fn) {
  runListeners.add(fn);
  return () => runListeners.delete(fn);
}

export function register(def) {
  if (!def?.id || typeof def.run !== "function") throw new Error("A command needs an id and a run function.");
  const entry = { group: "Commands", ...def, title: def.title || def.id };
  cmds.set(def.id, entry);
  return () => { if (cmds.get(def.id) === entry) cmds.delete(def.id); };
}
export const registerAll = (defs) => defs.map(register);

/** A command's name for menus; `title` may be a function when it changes ("Turn on / off spoken summaries"). */
export const labelOf = (c) => (typeof c.title === "function" ? c.title() : c.title);

export const get = (id) => cmds.get(id) || null;
export const has = (id) => cmds.has(id);
export const available = (c) => { try { return !c.when || !!c.when(); } catch { return false; } };

/** Every command that can run now, in registration order. */
export const list = ({ all = false } = {}) => [...cmds.values()].filter((c) => all || (available(c) && !c.hidden));

/** Run a command by id. Resolves to what the command returned; an unknown or unavailable id is an Error the caller can show. */
export async function run(id, args) {
  const c = cmds.get(id);
  if (!c) throw new Error(`No command "${id}".`);
  if (!available(c)) throw new Error(`"${c.title}" isn't available right now.`);
  const res = await c.run(args || {});
  for (const fn of runListeners) {
    try { fn(id, args, res); } catch {}
  }
  return res;
}

/** Extra palette rows that aren't fixed commands (one per app, per conversation…): a function returning [{id, group, label, icon, hint, run}]. */
export function addProvider(fn) { providers.add(fn); return () => providers.delete(fn); }
export const provided = () => [...providers].flatMap((fn) => { try { return fn() || []; } catch { return []; } });
