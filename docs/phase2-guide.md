# Phase 2 guide: the panel workspace

For whoever builds Phase 2 (read `AGENTS.md` first: the rules, how to run things, the style). The design and the reasoning are in `panel-ui-todo.md`; the checklist is in `todo.md`. This file is the order of work, a recipe for each step, and how to tell a step is done.

**Goal.** Today the screen is fixed: chat on the left, one app panel on the right. Phase 2 makes the screen a tree of panels that can be split, stacked in tabs and resized, with chat as one more panel and several panels of the same app allowed at once.

**Ground rules**
- The app works and the tests pass after every commit. The current look stays the default until the very end of step 2.
- One task at a time, in the order below. After each task: run the checks, fix, then stop and report (what you changed, the exact commands you ran and what they printed, anything you could not check).
- If something here is wrong or the code disagrees with it, say so and stop; do not guess.

## Step 1: per-panel state (no visible change)

Two copies of one app cannot coexist while a panel keeps state in module variables, because both copies would share them. Find them with:

```bash
node scripts/check_module_state.mjs             # lists every top-level `let`, `new Map/Set`, and one-letter state object in the panels
```

Two panels are already converted and are the pattern to copy: `apps/todos.js` (a view setting) and `apps/telemetry.js` (a range setting). Open them first.

### The recipe, for one panel

1. Read the whole file. List every module-level variable and what it is for.
2. Decide where each belongs:

| It is… | Put it… |
|---|---|
| a setting that should survive a reload and be remembered (view, filter, range) | in `ctx.state`: `ctx.state.get("key", default)` / `ctx.state.set("key", value)` (todos, telemetry) |
| data for the current render (a loaded list, the selected item) | a local variable inside `render`, or in an object created inside `render` |
| a reference to this panel's element or context (`S.el`, `S.ctx`, `M.el`) | a variable created inside `render`; functions that use it move inside `render` too, or take it as a parameter |
| a listener on `document`/`window`, a timer, an observer | created inside `render`, removed with `ctx.onClose(() => ...)` |
| something `navigate(p, ctx)` or `current(ctx)` must reach | `const instances = new WeakMap(); // state-ok: keyed by this panel's ctx`, then `instances.set(ctx, {...})` in `render` and `instances.get(ctx)` in `navigate`/`current` (the same `ctx` object is passed to all three) |
| a constant look-up table that never changes | leave it at module level |

3. If a file has many functions that read one big state object (`gcal.js` `S`, `gmail.js` `M`), the smallest safe change is to wrap everything below the imports in a function `function mount(el, ctx) { const S = {...}; ...the functions...; }` and have `render` call it. The code inside stays the same; only the first lines change. Do not reindent the whole file (it makes the diff unreadable); say in your report that you did not.
4. Check it:

```bash
node scripts/check_module_state.mjs diatonic/web/apps/<file>.js    # must print "no module-level state found"
scripts/dev_scratch.sh start                                        # (once)
node scripts/mount_twice.mjs <app-id>                               # must PASS
.venv/bin/python -m pytest -q                                       # must be green
```

   Then open the panel in the scratch Chrome (see `AGENTS.md`), do its two or three main actions (e.g. add a to-do, switch the view, open an item) and confirm nothing in the console. `mount_twice.mjs` is a smoke test: it proves the two copies render and one click in A does not change B. It cannot prove everything, which is why you also read the diff for anything still shared.
5. Commit message: `Panel <id>: keep state per panel`. One panel per commit.

### Order (easy first)

| Panel | Module-level state to move | Notes |
|---|---|---|
| todos, telemetry | done | the examples |
| github, scheduler | none found | only run `mount_twice.mjs`; add nothing |
| notes, presentations | `nav`, `open` | `navigate` and `current` use them: use the `WeakMap` row above |
| `_data.js`, `_tools.js` (the generic panels for agent-built apps) | `open` (a Set of expanded items), `refreshList` | the Set can live inside `render`; `refreshList` is a callback set in `render` and called later |
| files | `st` (path, file, filters, scroll), `host` | `st` becomes per panel; `host` is an element |
| gmail | `M` (everything) | it adds a `keydown` listener to `document` and removes it lazily when `M.el` is gone. With two panels the shared function name would remove the other's listener: create the handler inside `mount` and remove it in `ctx.onClose` |
| gcal | `S` (everything) | same listener pattern; `S.token` guards against stale async renders and must be per panel |
| shell (the terminal) | `T`, `active`, `tab`, `ui`, `autoId`, `typed`, `ro` | last. Its terminal sessions live on the server, so the module state is only this panel's connection to them. Each panel needs its own tab list, active session and resize observer, and must close its event streams in `ctx.onClose`. Be careful with `T`: a second panel must not see, close or type into the first panel's terminals |

**What the checks can and cannot see (measured on the current code).** `mount_twice.mjs` over every panel: all pass except `shell`, which fails because clicking a tab in one copy changes the other (the shared `T`/`active` state: this is the real thing to fix). `gmail` and `gcal` pass only trivially: the scratch server has no Google account, so they just show the "Connect" screen and there is nothing to click. For those two, rely on `check_module_state.mjs`, careful reading of the diff, and `tests/test_google.py` (which uses a fake Google, `tests/fake_google.py`). Say in your report that they were not exercised in the browser.

Rules of thumb: do not change what the panel looks like or does; do not "improve" the code while you are in it; if a panel has no test, you do not need to write one for the whole panel, but add the `mount_twice` check to your report.

### Finishing step 1

- `node scripts/check_module_state.mjs` prints "no module-level state found" for every panel (a line that is shared on purpose ends with `// state-ok: why`).
- `node scripts/mount_twice.mjs` (no arguments, all panels) passes.
- Update the audit table in `docs/panels.md` ("all clean") and tick the Step 1 items in `todo.md` with the date and what the checks printed.

## Step 2: the layout tree, with chat as a panel

This is the risky step. Work in the sub-steps below; each one ends with a working app and its own commit. Anything that changes how the whole screen is built should be tried behind a switch (`?layout=tree` in the URL) until 2e.

**2a. The data model, as pure functions with tests (no UI).** `diatonic/web/js/layout.js`, tested by `tests/js/layout.test.mjs` the way `keys.js` is. The tree is plain JSON:

```json
{ "v": 1, "root": { "type": "split", "dir": "row", "sizes": [0.6, 0.4], "children": [
    { "type": "stack", "active": 0, "tabs": [ { "id": "p1", "app": "chat", "params": { "conversationId": null } } ] },
    { "type": "stack", "active": 0, "tabs": [ { "id": "p2", "app": "files", "params": {} } ] } ] } }
```

Operations (all return a new tree, none touch the page): `openPanel`, `closePanel`, `split(panelId, dir, newPanel)`, `moveTab`, `swapPanels`, `resize(splitPath, sizes)`, `focusNeighbor(panelId, direction)`, `validate`, `fromPreset("current")` (chat left, one app panel right). Rules to test: closing the last tab of a stack removes the stack; a split with one child collapses into its child; sizes always sum to 1 and never go below a minimum; ids are unique; `validate` repairs or rejects a tree from a newer version. Also a migration hook for `v`.

**2b. The renderer, behind `?layout=tree`.** `js/workspace.js` builds the DOM from the tree (CSS grid or flex, dividers with pointer events, a tab bar per stack), mounts each panel with `panelContext`, and keeps hidden tabs alive but idle. The key change to `panel.js`: a panel instance is `{id, app, el, ctx}` and `ctx.reload()` re-renders *that instance*, not "the panel on screen". Key `ctx.state` by instance id (migrate the old per-app value for the first instance). Without the flag, nothing changes.

**2c. Chat as a panel.** *(Partly done: the first chat panel is the main chat; extra chats are simple and do not stream. See `docs/panels.md`, "Chat panels".)* `js/chat.js` is tied to global elements (`#messages`, `#input`, the composer, the dock). Extract a chat panel module whose `render(el, ctx)` builds its own message list and composer and takes `conversationId` from `ctx.params`. Do it in two moves: first make the existing chat code work against elements it is given instead of `#messages`/`#input` (still one chat on screen), then let `render` create them. The streaming event handling (`onEvent`, `applyEvent`) is per conversation already; keep one shared event stream and route events to the chat panel showing that conversation. The dock and `screenContext` go away only after chat is a panel (2d).

**2d. Make the tree the default.** Preset layout reproduces today's screen. Conversation list, notifications, settings and the terminal become panels you can open (settings can stay a dialog for now if it is a lot of work: say so). Remove the dock, `set_layout` modes and the single-panel code in `panel.js`. Persist the tree: server config with a version, `localStorage` as a fallback.

**2e. Move the agent's `ui` tools onto commands.** *(Done 2026-11-17: they send `app.open`, `panel.split.right`, `layout.preset.single` and `sidebar.set`.)* `open_app`, `open_item`, `set_layout` in `apps/ui/app.py` currently emit their own events. Make them call the command registry (`panel.open`, `panel.split`...) so a command, a key and the agent all do the same thing. Update the tests in `tests/test_ui.py`.

Done when: the old screen is reproduced by the preset, two chats can sit side by side, two Files panels can show different folders, a reload restores the layout, the full test suite is green, and the keyboard commands (`focus.left/right`, `panel.close`) still work.

## Step 3: mouse and keyboard workflow

Resize and split by mouse and by command; close; double-click a divider to equalise; a header menu (split, move, duplicate, pin). Directional focus and movement commands (`focus.left/right/up/down`, `panel.move.*`, `panel.swap`, `panel.resize.*`, `panel.zoom`, `tab.next/prev`) added to `commands-core.js`, bound in `keymap-defaults.js` under the leader, listed in `docs/keyboard.md`. A chat panel can follow another panel ("sees that mail") with a visible chip, replacing the single `screenContext`. Each command gets a test in `tests/js/layout.test.mjs` (the tree operation) and is checked once in the browser.

## Reporting a finished task

Say: what you changed (files), the exact commands you ran and what each printed, what you looked at in the browser, anything you did not check, and anything in this guide that turned out wrong. Do not commit unless you were told to.
