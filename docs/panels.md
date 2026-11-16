# Panels: the contract

An app panel is a JS module in `diatonic/web/apps/<id>.js` (or a panel the agent builds). The page loads it and calls:

```js
export async function render(el, ctx) { … }        // required: fill `el`
export async function navigate(params, ctx) { … }  // optional: move within the open panel (the agent uses it)
export const current = (ctx) => ({kind, ref, title})  // optional: what "this" means for "summarize this" (ctx is this panel's)
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
| `onClose(fn)` | run `fn` when the panel is replaced or closed: remove document-level listeners, clear timers |
| `tool("gcal_list_events", args)` | run any app's read-only tool; resolves to its result (tools that change things are refused: only the agent does those, with approval) |
| `data("app-id")` | the user's own tracker apps: `.get()`, `.add(list, {field: value})`, `.update(id, {field: value})` |

Everything registered through `ctx` is undone when the panel is replaced or closed.

## The rule that matters: no module-level state

Today only one panel is open at a time, so a module can keep its state in `let` variables. The workspace described in `todo.md` (Phase 2) opens several panels at once, two of the same app included, and that only works if each panel keeps its state on `ctx` (or inside `render`), not in the module. New panels should follow this now.

Audit: all clean (2026-11-16). Every built-in panel keeps its state per instance (on `ctx`, inside `render`, or in a WeakMap keyed by `ctx`). Checked by `node scripts/check_module_state.mjs` (0 lines found).

## Chat panels

The first chat panel adopts the main chat (`#main`: streaming, approval cards, tool output, the home tiles). Any further chat panel is self-contained: it keeps its own conversation id in `ctx.state` (`cid`), sends with its own id, shows "Working…" while its run is active and loads the finished reply. It does not stream, show approval cards or tool output; use the main chat for those. It never touches `state.convId`, which belongs to the main chat. Full streaming in every chat needs `js/chat.js` split away from its single message list (still open, see `todo.md`).

## Styling a panel

A panel body is wrapped in `.panel-body`, whose generic form rules (border, background, padding on every `input`/`textarea`, and `input + input { margin-top: 8px }`) reach everything inside it, including the main chat's composer. When a control inside a panel must look different, give your rule a `.panel-body` prefix or a higher specificity, then check the control's size in the browser. An icon next to a label goes in a span that holds only the icon: the app styles that span so the icon is centred on the text.

## Layouts

A **layout** is how the panels are arranged: the tree of splits and tabs and the app in each. There is one word and one way to keep one:

- **By mouse:** the layout button at the bottom right of the window opens the Layouts menu: presets as thumbnails, a name box to save the current layout, your saved layouts (click to switch, trash icon to delete, asked in place), undo, redo and reopen a closed tab. Settings → Layouts shows the same. Everything below also has a command.
- Arrange it with presets (`layout.preset.*`), splits, drag and drop, or the agent (`ui_arrange_panels`); `layout.undo` and `layout.redo` step through changes.
- **Save** the current arrangement under a name with `layout.save` (or ask the agent: "save this layout as Dev"). **Switch** with `layout.switch` or `/layout <name>` in the command bar, which lists the presets and your saved layouts together. To delete one, open the command bar and type `delete layout`: each saved layout has its own row, and you are asked to confirm. (`layout.delete` with no name opens that list.)
- Saved layouts live in `layouts.json` in the Diatonic home folder (`GET`/`PUT /api/layouts`). A `workspaces.json` from an earlier version is read until the next save.
- A **macro** is a different thing: a list of commands to run. Do not save a layout as a macro.

In the code, "workspace" only names the area of the window that shows the layout (`js/workspace.js`, `.workspace-*` classes).

## Mouse and keyboard: both, always

Every command should have a place to click, so nothing depends on knowing a key. The places are the status bar (Layouts, Scratchpad, command bar and Settings buttons on the right; a **+** after the pinned apps to pin or unpin any app), Settings (Layouts and Macros pages next to Keyboard), and the tab bar (split, add tab, and the **⋯ panel menu**: split, zoom, duplicate, move and swap towards a neighbour, pin, reload, open in its own window, close tab or close the others; right-click a tab for the same menu). Zoom only changes what is shown, never the saved layout. A command that needs a name, such as `layout.save` or `macro.record.start`, opens the page that has a name box and does not use a browser `prompt()`; a test checks the core commands for that. When you add a command, add its button or menu in the same change.

