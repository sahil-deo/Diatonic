# Panels: the contract

An app panel is a JS module in `diatonic/web/apps/<id>.js` (or a panel the agent builds). The page loads it and calls:

```js
export async function render(el, ctx) { … }        // required: fill `el`
export async function navigate(params, ctx) { … }  // optional: move within the open panel (the agent uses it)
export const current = () => ({kind, ref, title})  // optional: what "this" means for "summarize this"
export const wide = true; export const width = 560; export const full = false; export const flush = false;
```

## `ctx`

| | |
|---|---|
| `api`, `h`, `esc`, `toast`, `reload()`, `prompt(text)` | as before: HTTP helper, element builder, escaping, toasts, re-render, fill the chat box |
| `id`, `app`, `params`, `el` | this panel instance, the app, what it was opened with, its container |
| `state.get(key, default)`, `state.set(key, value)` | data that survives reloads, kept per panel |
| `onResize((w, h) => …)` | called when the panel's size changes; returns an unsubscribe |
| `command({id, title, run, when?})` | add a command (palette, keys); removed when the panel closes |
| `list({items, open?})` | arrow keys, Home/End and Enter step through the panel's rows |
| `tool("gcal_list_events", args)` | run any app's read-only tool; resolves to its result (tools that change things are refused: only the agent does those, with approval) |
| `data("app-id")` | the user's own tracker apps: `.get()`, `.add(list, {field: value})`, `.update(id, {field: value})` |

Everything registered through `ctx` is undone when the panel is replaced or closed.

## The rule that matters: no module-level state

Today only one panel is open at a time, so a module can keep its state in `let` variables. The workspace described in `todo.md` (Phase 2) opens several panels at once, two of the same app included, and that only works if each panel keeps its state on `ctx` (or inside `render`), not in the module. New panels should follow this now.

Audit of the built-in panels (2026-11-15): these keep module-level state and need moving before multi-instance panels.

| Panel | Module-level state |
|---|---|
| gcal | `S` (view, cursor, events, accounts, element, token) |
| gmail | `M` (account, label, threads, selection, open thread, element) |
| files | `st` (path, file, filters, scroll), `host` |
| shell | `T` (terminal sessions), `active`, `tab`, `ui`, `autoId`, `typed`, `ro` |
| notes | `nav`, `open` |
| presentations | `nav`, `open` |
| todos | `showDone` |
| telemetry | `days` |
| generic data and tool panels (`_data`, `_tools`) | `open` (set of expanded items), `refreshList` |
| github, scheduler | none found |

The terminal is the hard one: its sessions live on the server, so the module state is only the connection to them.
