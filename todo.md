# Diatonic TODO

The plan for making this harness good: **hackable** (the agent and the user can build real apps and reshape the UI) and **fast and reliable** on a small local model. Use the **Telemetry** panel to decide what is worth doing and to prove it worked; items say how to measure them.

Design rationale lives in two notes (read them before starting the matching phase):
- [`hackable-todo.md`](hackable-todo.md): why agent-built apps fail today (no feedback loop, undocumented SDK, code in a single tool argument) and the fixes.
- [`panel-ui-todo.md`](panel-ui-todo.md): the panel workspace design (layout tree, tabs, command registry, keymap, launcher, status bar pins, macros).

## Working assumption

**The 4B local model is the only model we can use.** Nothing here may depend on a bigger model writing the code. So the harness, not the model, has to carry the difficulty: small composable pieces, templates to copy, and fast test feedback, rather than asking a 4B to write raw DOM. This is why Phase 1 leans on helpers and templates, and why model escalation is optional (Phase 4).

## Order of work, and why

The two plans share a foundation, so neither goes first alone. A **command registry** and a **panel contract** are what the keymap, palette, status bar, macros, agent layout tools *and* agent-built panels all sit on. Build them once, document them, and both plans use them.

1. ~~**Phase 0: Foundations**~~ done 2026-11-15 (command registry, keymap, panel contract v2, keyboard navigation).
2. ~~**Phase 1: Make agent-built apps work**~~ done 2026-11-16 (test-run before approval, SDK and templates, panels reach tools and data, edit in place). Leftover feature work is listed under it.
3. ~~**Phase 2: Panel workspace core**~~ done 2026-11-16 (per-panel state, layout tree, multi-instance panels, mouse & keyboard workflow, linked panel context).
4. ~~**Phase 3: Workspace features**~~ done 2026-11-16 (launcher, status bar pins, command bar modes, macros, saved layouts, layout tools & undo, tiles panel, drag-reorder, scratchpad, panel detach).

Tracks that run alongside: **Speed and reliability** (below), **Security**, **Verification debt**.

## Baseline (measured 2026-11-15)

Apple M4, 16 GB, `qwen3:4b-instruct`, Ollama 100% on GPU.

- Prompt processing about **190-290 tokens/s**, generation about **36 tokens/s**. A cached identical prompt: 0.2 s instead of 7 s for 1.3k tokens.
- A typical lookup ("list my 3 most recent github repos") is **two model calls** of about 1.7k tokens each. Cold: 30 s. Warm cache: 5.4 s.
- Over a 5-run sample: reading prompts 47% of wall time, writing 26%, model loading 14%, tools 5%.

Targets: first message in a chat under 6 s, follow-ups under 3 s, plain lookups under 2 s.

---

## Phase 0: Foundations (done 2026-11-15)

- [x] **Command registry** (`js/commands.js`, built-ins in `js/commands-core.js`). Palette, keys, panels and scripts run commands by id; `ctx.command()` and `diatonic.command()` add more. The agent's `ui` tools are **not** rewired onto it yet (they still emit their own events): do that when the layout tree lands (Phase 2) so they don't change twice.
- [x] **Keymap + leader key** (`js/keys.js`, `js/keymap.js`, `js/keymap-defaults.js`, `keymap.json` in the Diatonic home via `/api/keymap`). Sequences, `when` conditions, which-key popup, hand-edits reload on window focus, Settings → Keyboard editor with conflict detection. See `docs/keyboard.md`.
- [x] **Better palette:** fuzzy search with match highlighting, recently-used ordering, key hints from the keymap.
- [x] **Panel contract v2** (single instance for now): `ctx.id/app/params/el/state/onResize/command/list`, cleaned up when the panel closes. Documented in `docs/panels.md` with the audit: 9 of the 12 built-in panels keep module-level state and must move it onto `ctx` before multi-instance panels (Phase 2).
- [x] **Keyboard navigation inside panels:** arrows, PgUp/PgDn, Home/End, Space scroll the side you last clicked; ↑/↓/Enter step through lists in mail, files and notes (`ctx.list`); Esc leaves the chat box; vim-style keys behind a setting.
- [x] **Build eval prompt set** (`evals/build_prompts.json`, 10 prompts with checks); the runner came with Phase 1.
- Follow-ups noticed while building: To-dos, GitHub and Scheduler panels don't offer `ctx.list` yet; focus is "chat or app panel" until Phase 2 replaces it with real panels; the `focus.*` commands are already named for that.

## Phase 1: Make agent-built apps work (done 2026-11-16)

*Why first: it needs no layout refactor, and API apps already prove the pattern (they work because every step is test-called).*

- [x] **Verify step for `add_panel` / `add_script`** (done 2026-11-15). New code is test-run in a sandboxed frame in the user's open window *before* the approval card (`js/verify.js`, `UiApp._verify`, `POST /api/mods/verify/{id}`): syntax errors, undefined names, errors in timer ticks and panels that show nothing go back to the model as a tool error, so the user is only shown code that runs. The frame has no network (CSP), no cookies or page access, and server calls just never answer, so only what happens before the first server call is checked. No window or no answer in 6 s falls back to the approval card. A node check lives in the page-side test, the flow in `tests/test_verify.py`.
- [x] **SDK reference + 3 templates** in the `add_panel` description (`apps/ui/sdk.py`, about 400 tokens): counter/timer, result-as-you-type, a list kept with `ctx.state`. Templates are syntax-checked by `tests/test_verify.py`. Still to do from this item: helpers (list, form, button) and updating `docs/customizing.md`.
- [x] **Build routing** (found by the first eval run: "make a stopwatch" was offered no build tools): `THINGS` nouns (calculator, converter, timer, stopwatch, clock, counter, game…) count as building, and weather/news/stocks/Open-Meteo requests get the API tools. `tests/test_build_routing.py` checks the eval prompts against the router with no model.
- [x] **Build eval runner** (`evals/run_build_eval.mjs`, prompts in `evals/build_prompts.json`): drives the real page against a scratch server and the real model, approves what it proposes, opens what was built and applies the checks. First run on `qwen3:4b-instruct` 2026-11-15: **5/10** (4 of the 5 failures were routing, now fixed; one check was too strict). After the routing fix and two checks corrected (one wanted a `<textarea>` where an `<input>` is fine, one only read visible text): **10/10 in one full run**. Pomodoro had failed its countdown check in the first run and passed in the next two, so expect some run-to-run noise. No tuning rounds were done; the prompts are close to the templates, so this is an optimistic number for unseen requests. Re-run after any change to the build tools: `node evals/run_build_eval.mjs --only id,id`.
- [x] **Panels get the harness** (done 2026-11-15): `ctx.tool("todos_list_todos", {})` runs any enabled app's **read-only** tool (`POST /api/panel/tools/{app}/{tool}`; changing tools, the terminal and the interface tools are refused, so a panel can't do what needs an approval card), `ctx.data("app-id")` reads and adds to the user's tracker apps. The test run knows the valid tool names and tracker ids, so a wrong name fails verification with the right ones listed, and a server call that is never awaited is flagged. The scheduler is not reachable from panels yet.
- [x] **Edit instead of regenerate** (done 2026-11-15): `ui_edit_panel(name, old, new)` replaces one piece of a panel or script, the new code is test-run, and the approval card shows a diff; `ui_add_panel` over an existing name also shows a diff instead of all the code; `ui_list_mods(name)` returns the code to copy from. Offered when a request names something already built.
- [x] **Approval card summary** (2026-11-16): `add_panel`, `add_script` and `edit_panel` take an optional one-sentence `summary` that leads the approval card, next to the code or diff. Optional on purpose: a required argument is one more thing a 4B can drop.
- [x] **Build-intent routing**: also "make a panel that lists my to-dos" no longer offers the to-do tools (they would just answer the question), and a request that names something already built (`change the timer`) is offered only the ways to change it (no "add a field" tools, which the 4B once used instead). `num_ctx` was not the limit in any run.
- [x] **Result shapes in the SDK** (2026-11-16): to-dos, notes and calendar events, and that a tool with nothing to list returns a text message, not a list. This fixed the to-do viewer (it now shows the to-dos).
- [x] **Forgiving `edit_panel`** (2026-11-16): `old` matches ignoring spacing, indentation and line breaks; a miss lists the closest lines; the panel is found by a loose name (`pomodoro` finds `timer`, or the only panel there is); `list_mods(name)` is just as loose. Reminders and jobs are readable from panels already (`scheduler_list_reminders`, `scheduler_list_jobs`).
- [x] **Build eval, final tally on `qwen3:4b-instruct`**: full run 11/13 (all builds, to-do viewer and top-bar button pass). The two edit prompts: `edit-counter` passes after the forgiving match; `edit-pomodoro` edits in place but the 4B adds a duration field instead of changing the countdown to 10:00, which is a model limit, not a harness gap. Noise between runs is real (pomodoro and the edits have flipped between runs).
- [ ] Known weakness: when the 4B is asked to *change a value* inside a panel it sometimes adds a feature around it instead. Ideas if it matters: a `summary`-first prompt ("say what you will change, then change it"), or an edit tool shaped as "set <thing> to <value>" for numbers and labels.
- Data apps (trackers) were removed in v3 Phase 0, 2026-11-21; their open items went with them.
- API apps and connected apps were retired in v3 Phase 5, 2026-11-21; an app on a web API is a file app on `ctx.fetch`, and their open items went with them.

## v3 (proposed 2026-11-21)

The plan for making the harness something the agent can build on (catalog, `ctx` v2, UI kit, instructions, builder session) is in [`docs/v3-plan.md`](docs/v3-plan.md). Phase 0 (banish trackers) code is done as of 2026-11-21, except the export of the real data, which the user runs (see the handoff notes in the plan). Phase 1 (the catalog) is done as of 2026-11-21; Phase 2 (`ctx` v2) and Phase 3 (UI kit and app shape) are done as of 2026-11-21; Phase 4 (instructions) is done as of 2026-11-21 apart from the items listed under it; Phase 5 (builder session) is done as of 2026-11-21 apart from the items listed under it.

- [x] **v3 Phase 0, code** (2026-11-21): `dataapps.py`, the tracker tools and routing, `/api/dapps`, the `data` mod kind, `_data.js`, `ctx.data`, the `app_items` methods and the tracker tests and docs are deleted. Suite: 561 passed.
- [x] **v3 Phase 1, the catalog** (2026-11-21): `Tool.effects` and `domain` with approval, the panel rule and the untrusted-content rule reading them; `diatonic/catalog.py`, `GET /api/catalog`, `POST /api/catalog/commands`, `ui_platform_search` and `ui_platform_describe`; `docs/platform.md`. Components, skills and `ctx` entries join in Phases 2 to 4.
- [x] **v3 Phase 2.3, `ctx.store`** (2026-11-21): `diatonic/appstore.py` (per-app key/value and tables in the main SQLite file, never-reused row ids, size limits), `POST /api/store/<app>/<op>`, `ctx.store` in `panel.js` and a stub in the `verify.js` sandbox, removed with the app, `ctx-api` catalog entries in `diatonic/ctxapi.py`, `tests/test_appstore.py`. 
- [x] **v3 Phase 2.4, `ctx.fetch`** (2026-11-21): `diatonic/appfetch.py`, `POST /api/fetch/<app>`, `tests/test_appfetch.py`. GET only, 2 MB, 15 s, text types only, no custom headers, private addresses refused on every redirect hop.
- [x] **v3 Phase 2, items 1, 2, 5, 6, 7, 8** (2026-11-21): `ctx.notify` (`POST /api/notify/<app>`, 20 a minute), `ctx.timer` (`diatonic/apptimers.py`, table `app_timers`, survives panel close and server restart), `ctx.tool` for changing tools (needs the declaration and an approval card per call, caller sent in `X-Diatonic-App`), `ctx.on` (`timer:done`, `todo:changed`, `<app>:changed`), manifest `permissions` (`diatonic/appperms.py`, one card for the whole list, re-asked when it grows, `js/perm-ask.js`), `FILE_SDK` deleted (the write-app tool points to `ui_platform_search`), `ctx-api` entries in `ctxapi.py`. Tests: `test_appperms.py`, `test_appfetch.py`, `test_appstore.py`.
  - Changed from the plan: the approval of permissions (domains included) is kept in the user's config (`apps.<id>.permissions_ok`), not the manifest, because the agent writes the manifest. Timers use their own table and tasks, not the Scheduler panel's rows (they would have shown up there). Read tools need no declaration; only tools that change things do. `mail:new` is not emitted (no mail polling exists).
  - Not done: a Settings view to see and revoke what an app was allowed; a panel that calls the approval route itself is not stopped (panel code shares the page with the shell).
- [x] **v3 Phase 3, UI kit and app shape** (2026-11-21): `web/js/ui/index.js` (15 components, `ui.listApp`, `ui.run`) declared once in `web/js/ui/components.json`; `ctx.ui` in `panel.js`; a name-only stub in the `verify.js` sandbox (a wrong component name fails the pre-approval run); `diatonic/uikit.py` turns the JSON into `component` catalog entries; the **Components** panel (`web/apps/components.js`, the gallery); `ui-*` CSS from the tokens only; three starter recipes in `diatonic/recipes/` (timer, tracker on `ctx.store`, search on `ctx.fetch`); `scripts/render_recipes.mjs`; tests `tests/js/ui-kit.test.mjs` and `tests/test_uikit.py`.
- [x] **v3 Phase 5, builder session** (2026-11-21): the first build or change request pins a chat to one app and a fixed toolset (`apps/ui/builder.py`), logged errors and pasted errors reach the next turn, API apps and connected apps are deleted (`scripts/export_api_apps.py` saves any that exist), `ui/app.py` is split, a `tools` recipe exists. Not done: the toolset is about 5,800 characters, not "a few hundred tokens"; `build_max_tokens` and `build_num_ctx` stay (a whole `index.js` is one argument); no real-model run, so scenario 2 (YouTube) and the 4B's use of the session are unchecked. Details in `docs/v3-plan.md`.
- [ ] **v3 Phase 0, real data:** run `scripts/export_trackers.py --home ~/.diatonic --delete` with the server stopped (not done: it touches the user's real data).

## Build reliability (2026-11-21)

Found by reading the build path, then measured with the build eval on `qwen3:4b-instruct` (scratch server, rewritten for file apps). Start of the day: **0/3** on pomodoro, counter and unit-converter. Last full run: **10/13**. Two of the three failures were an eval artifact and a product bug (below); fixes after that run (not re-run): the to-do viewer's on-screen-panel widening, the patch hint wording. **The eval was not re-run after those, so 13/13 is not claimed.**

- [x] **The sandbox check rejected everything** (found by running the eval): regexes in the template string of `js/verify.js` lost their backslashes, the frame script did not parse and every check ended as "never finished running". Fixed; `tests/js/verify-frame.test.mjs` now compiles the string the browser receives (the old test compiled the file text and could not see this).
- [x] **File apps are checked before the approval card**; a patch whose `old` is not in the file is an error with the closest lines; a patch lists numbers and words it took out that are still in the file.
- [x] **An unbuilt app is a failed run** (`unbuilt_failure` in `agent/loop.py`): a build request with no successful code write gets a plain failure message, not the model's "done". A run the user declined is left alone.
- [x] **Longer replies when a run can build:** `build_max_tokens` 4096 and `build_num_ctx` 16384; a cut-off reply gets one retry; tool arguments that are not valid JSON are reported, not turned into `{}`.
- [x] **Routing:** things that do something (timer, counter, converter) are built as file apps, record-keeping (tracker, log, habits) too since v3 Phase 0; after building or naming a file app only the file-app tools are offered; delete requests get `delete_file_app`; a build request is not widened to the panel that is open; other apps' tools are not run during a build.
- [x] **Deleting an app:** the tab closes, no empty folder is recreated (reads no longer create folders, writes to a missing app are refused), `config.json` entries (switch, rail, pins, home tiles) are removed, and empty leftover folders are pruned at start.
- [x] **An edited app loads its new code** when a tab is opened later (the module URL carries the manifest version).
- [x] **`tools.py` is not imported by the sandbox check, and its tools always need approval** and cannot be called from a panel, whatever risk they declare.
- [x] **Legacy panels retired** (2026-11-21): `add_panel`, `edit_panel`, the `panel` kind in `mods.json` (old entries are dropped on load), `runPanel` and `modApps` in the front end, and their tests (about 28). Agent-built panels are file apps only. Scripts, styles and buttons stay.
- [x] `apps/ui/app.py` split into `app.py`, `looks.py` and `builder.py` (v3 Phase 5, 2026-11-21).
- [ ] Still open: re-run the eval after the last two fixes and add edit/delete prompts; model-written tool arguments over 4096 tokens are untested.

## Phase 2: Panel workspace core (done 2026-11-16)

Design details in [`panel-ui-todo.md`](panel-ui-todo.md). Three steps, in this order; each ends with a working app, and the current look stays the default throughout.

Handing Phase 2 to another coding agent (Gemini via `agy`): `AGENTS.md` (rules, style, how to run), `docs/phase2-guide.md` (order, recipe, checks), `scripts/` (scratch server, module-state lint, mount-twice smoke test), and two panels already converted as examples.

### Step 1: per-panel state onto `ctx` (no visible change; the riskiest step, so first)

Two copies of one app can't coexist while a panel keeps its state in module variables. Audit (see `docs/panels.md`): gcal `S`, gmail `M`, files `st` and `host`, shell `T`/`active`/`tab`/`ui`/`autoId`/`typed`/`ro`, notes and presentations `nav`/`open`, todos `showDone`, telemetry `days`, generic data and tool panels `open`/`refreshList`; github and scheduler have none.

- [x] Examples done: `todos` and `telemetry` converted; `ctx.onClose` added; `current(ctx)` now receives the panel's own ctx.
- [x] Move each panel's state into `render` (or `ctx.state`), one panel at a time (done 2026-11-16: notes, presentations, _data, _tools, files, gmail, gcal). `node scripts/check_module_state.mjs` reports 0 lines of module-level state.
- [x] **Shell last:** its terminal sessions live on the server, so the module state is only the connection to them. Scoped per instance with `mount()` closure, tab list and resize observer, with teardown in `ctx.onClose` (done 2026-11-16).
- [x] A test or browser check that mounts the same panel twice and shows the two don't affect each other (verified with `check_module_state.mjs` and per-instance scoping).

### Step 2: layout tree, with chat as a panel

- [x] **Layout tree** (`split` and `stack` nodes, tab stack per leaf), serialised to JSON, stored with localStorage fallback. Tree layout is the default across the app (`layout.js`, `workspace.js`, tested by `tests/js/layout.test.mjs`, verified in headless Chrome 2026-11-16).
- [x] **Chat as a panel** (`apps/chat.js`): the first chat panel is the main chat; there is only one chat panel (2026-11-21: extra chat panels were removed; layouts keep the first chat and opening Chat again focuses it).
- [x] **A PDF went back to page 1 on any click** (2026-11-17): every layout render rebuilt the panel area and re-attached each panel's content, and re-attaching an `<iframe>` reloads it. Panel content now lives in its own layer and is only positioned over its stack's slot (`syncBodies`), so it is never moved. Checked with an iframe through switching, adding, closing, moving, swapping, zoom and a real click (same document each time), bodies stay on their slots through divider drags, window resizes, sidebar and theme changes, and eight drag-and-drops; the terminal still fits. Not preserved: applying a layout preset or undoing one, which opens fresh panels. Found on the way: `applyTree` dropped panels without removing their content (harmless before, ghost panels now), fixed. Not checked with a real PDF in Safari or Zen, only the same mechanism in Chrome.
- [x] **Home panel: nothing opened** (2026-11-17): `tiles.js` took `openPanel` from the panel context, which has no such member, so every tile's open button and item row threw `openPanel is not a function` (broken since the Home panel was built in Phase 3). It now imports the real one from `panel.js`. Checked with real clicks on all nine open buttons from a fresh page, panel-menu Reload and the tile refresh buttons. A test now checks that every panel only takes things from `ctx` that it provides.
- [x] **Last panel cut off at the right and bottom** (2026-11-17): split children were sized as fixed percentages of the whole, so with the 8px dividers of the new design the total overflowed by 8px per split and `overflow: hidden` clipped the last panel (borders and the bottom of tables). Children now share what the dividers leave (`flex: <share> 1 0px`); measured 8px overflow before and 0px after, and dragging a divider moves exactly as far as the mouse.
- [x] **Empty panels after a mouse move** (2026-11-17, found from a screen recording): a drag to a panel's edge splits off a copy and then closes the original, which renders the panel area twice while the panel is still loading. The first render's load callback then appended the panel into its own, already discarded container, taking it from the one on screen, so the moved panel stayed empty until the next click re-rendered. Reproduced in Chrome (a moved Telemetry panel had 0 characters) and fixed by checking the container is still on the page (`workspace.js`, the `mountTab(...).then` callback). Stress-tested with eight drags over all three drop paths. Along the way: layout changes now wait for the drag to end (`afterDrag`, and the tiles panel), blur was removed from the panel cards, rail, sidebar, status bar and composer, and tab-to-tab drops, which had never worked (an undefined `moveTab` inside `try/catch {}`), were fixed. My first explanation (a repaint problem after the drag in Safari and Firefox) was wrong: it was this race. The `afterDrag` delay and the blur removal are kept as cheap safety, not as the cause.
- [x] **A mouse path for every command** (2026-11-17): Layouts menu in the status bar (presets, save with a name box, switch, two-step delete, undo/redo/reopen), Settings → Layouts and Macros (record, run, delete), a visible Recording button, status bar buttons for Layouts, Scratchpad, command bar and Settings, a + to pin or unpin apps. `layout.save` and `macro.record.start` no longer use `prompt()`. Panel menu (⋯ on every tab bar, or right-click a tab): split, zoom, duplicate, move, swap, pin, reload, own window, close others; commands `panel.zoom`, `panel.duplicate`, `panel.move.*`, `panel.swap.*`, `tab.close_others`, `panel.menu` and keys `<leader> w z/d/m`. The terminal is transparent and re-themes with light/dark. Still without a button: moving keyboard focus between panels (the `focus.*` keys work), and `_api.js` asks for an API key with a browser prompt.
- [x] **Layout polish** (2026-11-17): page-level scrollbar from a 32px status bar in a 28px grid row; thin hover-only scrollbars; tab strip scrolls on its own with the split/detach buttons pinned; icon/label misalignment in chips, tabs and buttons; quick-add form rows (to-dos, reminders) of equal height; composer textarea no longer styled by the generic panel rules. Status bar pins (2026-11-17): icons sized to match labels (a global `svg.i` rule forced 18px), and the current panel's pin shows an accent icon and a dot instead of bold white text. Deleting a saved layout: type `delete layout` in the command bar, one row per layout, with a confirmation (`layout.delete` with no name opens that list). Known gap: the global 18px `svg.i` rule still overrides the size passed to `icon(name, n)` elsewhere (tabs, buttons).
- [x] **Multi-instance panels:** all panels run independently on instance closures with per-instance state storage.
- [x] **Rewire the agent's `ui` tools** (`open_app`, `open_item`, `set_layout`) onto commands (done 2026-11-16, first ticked early: until this date they still sent `action: open/layout` events and `main.js` called the workspace directly). Now they send `app.open` (with params), `panel.split.right` (into a launcher), `layout.preset.single` and `sidebar.set`; the old handler branches are gone. `tests/test_ui.py` asserts the commands.

### Step 3: mouse and keyboard workflow

- [x] **Resize, split, close** by mouse and by command; double-click a divider to equalize; commands `panel.split.right/down`, `panel.close` (2026-11-16).
- [x] **Directional focus and movement commands:** `focus.left/right/up/down`, `tab.next/prev`, key bindings in `keymap-defaults.js` (2026-11-16).
- [x] **Linked panels:** workspace screen context resolution across active and sibling panels with per-panel context chip support (`workspace.getScreenContext()`, `state.screenContext`, done 2026-11-16).

## Phase 3: Workspace features

- [x] **Launcher instead of the rail:** a new or split leaf shows search, recents and pinned apps (done 2026-11-16: apps/launcher.js, in-place tab replacement, split directly to launcher, search and keyboard navigation). The rail becomes optional, off by default.
- [x] **Status bar pins:** left/center/right zones; pinned commands or apps, panel-contributed items with badges (unread mail). Pin from the palette or right-click; modifier-click opens in a split. Keep today's health, voice and runs items (done 2026-11-16).
- [x] **Command bar modes:** plain text = fuzzy search with frecency over apps, panels, conversations, commands, automations, layouts; `/` = command grammar (`/open`, `/run`, `/split`, `/layout`); a second shortcut (e.g. ⌘J) opens straight into ask-the-agent. Enter replaces the focused chat, ⌘Enter opens a new chat in a split, ⌥Enter runs in the background and notifies. `/run` merges scheduler jobs, saved macros and shell scripts, with a small form for arguments (done 2026-11-16: palette modes, inline arg form, layout presets, CDP verified).
- [x] **Macros:** declarative JSON sequences of commands (optional wait/if), recordable, saved by the agent ("save this layout as Dev"), runnable from the scheduler. Script macros need an approval card (done 2026-11-16: diatonic/macros.py, macros.js runner & recorder, scheduler support, agent tools with untrusted guardrails, pytest and CDP verified).
- [x] **Saved layouts** ("Inbox zero", "Dev"): one concept called a layout, saved by name, switched from the palette, restored per window. Done 2026-11-16 as "named workspaces"; renamed 2026-11-17 so there is one word and one way to save: `diatonic/layouts.py` (`layouts.json`, reads an old `workspaces.json` until the next save), `/api/layouts`, commands `layout.save/switch/delete/apply`, agent tools `save_layout/switch_layout/list_layouts/delete_layout`, palette `/layout <name>` (presets and saved layouts). The macro-based "save layout as macro" is gone; macros are for command sequences. A macro saved earlier with `workspace.applyTree` still runs.
- [x] **Agent layout tools** over the command registry ("mail left, calendar top-right"), free and undoable. Eval: a handful of layout requests checked against the resulting tree (done 2026-11-16: layout_builder.py, ui_arrange_panels, ui_undo_layout, layout.undo & redo in commands-core and workspace.js, evals/tests in test_layout_eval.py, CDP verified).
- [x] Layout undo/redo and reopen closed panel (⌘⇧T), reusing the mods history (done 2026-11-16: closedTabsHistory in workspace.js, ⌘⇧T & <leader>wt keybindings, tab.reopen command, ModStore layout logging & revert, agent arrange_panels & revert integration, CDP verified).
- [x] **Home screen becomes a tiles panel** (replaces `set_home` and the tile layouts); drag-reorder comes from the layout tree (done 2026-11-16: apps/tiles.js, workspace.js tab drag-and-drop & edge drop zones, agent tiles resolution & arrangement, scratchpad overlay drawer, panel detach).
- [x] **Workspace interactions:** drag items between panels (file or email onto a chat or todo list), edge drop zones, scratchpad (quick chat or dropdown terminal), detach a panel to its own window, narrow-window collapse to tabs (done 2026-11-16: files.js & gmail.js draggable rows, todos.js & chat.js drop handling, scratchpad.js, panel.detach command, responsive media query, CDP verified).

---

## Track: Speed and reliability

- [ ] **Cache-friendly, append-only context.** Keep each chat's exact message list and only append; compact once, deliberately. Today the history is rebuilt per turn (`(Now: ...)` suffix, trimming, tool-trace replay) so Ollama re-reads it. *Measure:* Telemetry "Cache reuse" and prefill time for 2nd+ messages.
- [ ] **Skip the second model call for plain lookups.** Render the tool result in code (events, to-dos, repos, reminders, notes) and only call the model when the request needs reasoning. Needs a per-tool `render()` and a cheap "does this need the model?" rule. Also covers a code-parsed fast path for "show/open <app>" and daily commands ("remind me in 20 minutes...", "add a to-do...").
- [ ] **Tool-level routing.** Rank individual tools with a local scorer and offer the top 6-8 instead of per-app keywords. Fix keyword overlaps (Calendar lists "remind"). Optionally a tiny constrained classification call, only for ambiguous requests.
- [ ] **Routing eval harness.** About 40 realistic requests with expected tool and arguments, run against the real model, comparing routing and prompt changes (accuracy, steps, tokens, seconds). Run nightly with our own scheduler and show regressions in Telemetry. Every change in this file should be judged with it (the build eval from Phase 0 lives alongside).
- [ ] **Answer grounding check.** Verify in code that numbers, names and paths in the final answer appear in the tool results; otherwise render directly or regenerate once. Targets made-up "I've retrieved your repos..." and "File created: notes/todo.md" answers.
- [ ] **Files edit reliability on a 4B model.** A line-range edit once dropped a needed line. Prefer `replace_text` for single-line changes, re-read right before editing, re-parse Python/JSON after the change, or compute the line range in code from an "old text" the model supplies.
- [ ] **Constrained decoding for tool calls** (Ollama `format` with a JSON schema). Only if the counters below show malformed calls.
- [ ] **Reference resolution** in code ("close the second one", "snooze that") via numbered handles, and **rolling state** (a short structured "what we were just doing") instead of replaying old turns.
- [ ] Parallel tool execution for independent reads.
- [ ] Ollama tuning: keep the model loaded longer (14% of time was loading), pre-warm the prompt cache after idle, a `scripts/bench_ollama.py` comparing `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, `num_ctx` 4096 vs 8192, and MLX runtimes.
- [ ] Models: automatic fallback to the local model when a cloud API is down; verify Gemini and Groq with real keys (so far mocked).

### Telemetry

- [ ] Count malformed/invalid tool calls and tool-selection misses separately (feeds the constrained-decoding decision).
- [ ] Record approval wait time (lands in "Other") so human latency doesn't pollute model latency.
- [ ] Real token-level cache accounting instead of the speed-based estimate.
- [ ] Per-app and per-routing-strategy breakdowns, and before/after comparison when a setting or prompt changes; regression alerts through the notification inbox.
- [ ] Track the build eval pass rate over time.

## Track: Security

- [ ] **Taint tracking beyond mail.** Today only Gmail declares `untrusted_tools`. Add web and GitHub issue text, and extend the "refused in any run that has read untrusted content" rule to keymap, macro, layout and script edits (a poisoned email must not be able to rebind a key to a shell macro).
- [ ] **Mail sends:** warn on the approval card when a recipient or link came only from email content; refuse auto-approval of sends in any run that read mail.
- [ ] Per-account permissions: let one connected account be read-only for the agent.
- [ ] Shell: spawn PTYs without `fork` in a threaded process (Python warns about `forkpty`); per-folder allow rules ("always allow `npm test` in Diatonic").

## Track: Verification debt (built, never exercised for real)

- [ ] **Real Google account** end to end: consent flow, token refresh, restricted Gmail scopes in Testing mode, shared calendars, Meet conferenceData, HTML mail from real senders, large mailboxes. Everything so far ran against a local fake.
- [ ] Voice: push-to-talk in Zen/Firefox; Whisper `small.en` (about 500 MB) not downloaded; Piper spoken summaries and reminders in the browser.
- [ ] macOS notification banners, browser notifications, quiet hours.
- [ ] Background service: `diatonic install-service` / `service-status` / `uninstall-service` (plist generated and tested, never installed).
- [ ] Files in Zen: real shared folder, the `@` picker and chips, embedded PDF viewer (blank in the Chromium preview; Text tab is the fallback), restoring from History, Trash on a non-sandbox HOME.
- [ ] Quick-action bar drag-reorder and Import; notes folder pointed at an existing markdown folder.
- [ ] "Learned for next time" tool quirks firing on a real rejection (tests only so far).
- [ ] Navigation, event/mail/note opening and dock approvals checked in Zen (Chrome done).

## Backlog (low priority, app polish)

- Mail: draft threading, signatures, label management, snooze, unsubscribe helper, attachments on compose, offline cache and background sync, unread badge.
- Calendar: free/busy overlay across accounts, "find a time" across accounts, other time zones, incremental sync.
- Files: manual edit/save in the viewer, rename/move/new-folder, CSV as a table, git status badges, `.gitignore`-aware listing.

- [x] **Antigravity as the chat backend, and Diatonic as an MCP server for it** (2026-11-21). Done: `agent/acp.py` (ACP client), `agent/antigravity.py` (runs `agy acp --sandbox` per conversation, native MCP bridge in `session/new`, permission requests become approval cards, MCP token redacted from cards), `mcp.py` and the `/mcp` route (Streamable HTTP, bearer token per conversation, `server/discover`, calls go through `_execute`), thoughts streaming with collapsible UI, auto title generation, real prompt/completion token telemetry, conversation history rehydration across reboots, model picker populated from `agy models`, stance/mode selector (`default`, `auto`, `plan`, `accept-edits`), thinking level settings, and Settings → Apps toggles for MCP access. 19 tests in `tests/test_antigravity.py`.
- [x] **Rule-based model routing** (2026-11-21). Done: `agent/router.py` classifies requests across 5 default tiers (`ui`, `trivial`, `multi_app`, `builder`, `complex`) + user-defined custom regex rules; model resolution and route metadata in `agent/runs.py` and `agent/loop.py`; API endpoints `GET/PUT /api/models/routing` and `POST /api/models/routing/test` in `server.py`; configuration in `config.py`; UI management and live prompt tester in `web/js/settings-models.js`. 10 tests in `tests/test_router.py`.
- [x] **Antigravity Auto Permissions Mode & Desktop Guardrails** (2026-11-21). Done: configurable `antigravity.permissions` ("strict" vs "auto") in `config.py`, `agent/antigravity.py`, and `mcp.py`. Safe file ops, skills, and tools run without approval cards in auto mode like local models, while preserving approval cards on destructive actions (email send, file delete, shell commands). Guardrail interceptor blocks unauthorized desktop app launchers (`open -a`, `osascript`, `kill`, `ps`) and `~/.diatonic` tampering across all modes. Tested in `tests/test_antigravity.py` and `tests/test_mcp.py`.
- [x] **Non-destructive window zoom** (2026-11-21). Done: `ui_set_layout(mode="zoom" | "unzoom")` in `apps/ui/app.py` and `web/js/workspace.js`. Temporarily expands any active panel to full view while preserving the layout tree, restoring cleanly on unzoom or tab menu actions.

## Parked / decided against (for now)

- **Classify-then-hand-off multi-agent.** Each hop is another full model call, the classifier prompt evicts Ollama's prefix cache, and cross-app requests need the union of tools anyway. Revisit with a stronger planner; model escalation (Phase 4) captures most of the benefit.
- **Spoken approvals.** A misheard word shouldn't be able to authorise a change.
- **Free-floating windows** in the panel workspace (tiling and tabs first).
- **PWA/Tauri wrapper** (browser tab only for now, so ⌘W/⌘T/⌘N/⌘1-9 stay unavailable, hence the leader key).
