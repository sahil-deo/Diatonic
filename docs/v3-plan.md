# v3 plan: a harness the agent can build on

Status: all six phases (0 to 5) are built as of 2026-11-21; see the handoff notes at the end for what each left undone. Running apps in a sandbox with a broker is a planned v4 feature ([v4-plan.md](v4-plan.md)); no v3 feature depends on it. Tick items here and in `todo.md` only when they are done and checked.

## Why

The agent builds poor apps even with a strong cloud model (82 Gemini 3.5 Flash Lite runs in the telemetry). So the model is not the main problem. What those runs show:

- **Follow-ups got no tools.** "there is no pause restart options at all" got zero tool calls and an apology. "integrate that then" got "No available tool can modify the timer app interface directly." Tools are chosen per message by regexes, and a follow-up matches none.
- **The platform cannot do what is asked.** Timer notifications, a timer that runs with the panel closed, a YouTube search that needs the network ("Failed to fetch videos"), and adding 15 topics one call at a time (the tool budget ran out, the user typed "Continue" three times).
- **No shared UI.** Every app invents its own DOM and CSS ("this UI is shit", "should match the harness UI").
- **Thin instructions.** The build guidance is a few lines inside a tool description.
- **Huge prompts.** 15k to 35k tokens a turn, mostly tool definitions.

`docs/reference/compos/` shows a harness that avoids these. The ideas to take (not its stack; its edit provenance and revision history are left out on purpose, see "Out of scope"):

1. One searchable catalog of everything the system can do, with signatures, examples and effects. The agent looks things up (`apropos`) instead of being told in the prompt.
2. One action unit (a named command) with declared effects, and one policy that uses them.
3. A fixed app shape and a component catalog, so the agent fills in data and does not write DOM.
4. A prompt made of named, stable sections, plus skills loaded on demand, plus "for the agent" rules in the docs.
5. Primitives for background work, so timers, events and notifications exist.

## How to work (applies to every step)

From `AGENTS.md`, restated because they decide the order below:

- The app works after every step: the test suite is green. The browser check (panels open, no console errors on the scratch server) is done **once at the end of each phase**, not after every step. This is the user's decision for v3 and narrows the "load it in the scratch Chrome" line in `AGENTS.md` to that.
- Small steps, one at a time, in the order given. Stop and report after each phase.
- A behaviour change gets a test that fails on the old code.
- No new Python dependencies. No weakening of approvals, the untrusted-content rule, cloud privacy prompts, path checks or the terminal's command classes.
- Use only the scratch server (`scripts/dev_scratch.sh`). Never touch port 5005 or `~/.diatonic`. Every script or test that builds a context must set `DIATONIC_HOME` to a temp folder (a probe on 2026-11-21 wrote into the real home by forgetting this).
- Update the matching file in `docs/` and `todo.md` in the same change. Say exactly what was run and what it printed.
- **Delete as you go.** A step that replaces something removes it in the same change (the old code, its tests, its docs). There is no separate cleanup phase.
- **No model evals.** Not in any phase, not to "confirm" a change. Each phase is checked by tests, the end-of-phase browser check and the acceptance scenarios below. Running them against a real model is optional and the user's call.

## Keeping the token cost down

The user's rules for running v3. They exist because a long session re-sends its whole history on every turn, which is most of the cost.

1. **One fresh session per phase.** Start it by reading only this file (the phase being done, the handoff notes, "How to work") and `AGENTS.md`. Do not carry the previous session forward.
2. **Read files in parts.** Use `grep`, line ranges and `offset`/`limit`. Read a whole file only when the change really needs all of it. The big ones (`apps/ui/app.py`, `agent/loop.py`, `web/js/workspace.js`, `server.py`) are always read in parts.
3. **Browser check once per phase**, at the end (see above). Steps in between rely on tests.
4. **No evals** (see above).
5. **End every phase with a handoff note** (below), so the next session does not need this one's history.

### Handoff note

Added to the "Handoff notes" section at the end of this file when a phase ends, before the session closes. Keep it under about 25 lines. Say only what the next session cannot get from the code and the plan:

```
### Phase N: <name> (<date>)
Done: what is built, as a list of files and behaviours.
Changed from the plan: anything done differently, and why.
Not done / deferred: with the reason.
Checked: the exact commands run and what they printed (tests, browser check).
Decisions made: any open question that was settled, and the answer.
Watch out: traps found, files that are fragile, things the next phase will hit.
Next: the first step of the next phase, and any prerequisite.
```

If a step cannot be checked, say so here. Do not write "done" for anything that was not run.

## What already exists and is reused

| Need | Already there |
|---|---|
| Named UI actions, keys, palette | `js/commands.js`, `js/commands-core.js`, `js/keymap*.js` (Phase 0) |
| Panel context | `js/panel.js` (`ctx.state`, `ctx.list`, `ctx.tool` for read tools, `ctx.data`, `ctx.onClose`, `ctx.command`) |
| Server-side timers, reminders, jobs | the Scheduler app and `ctx.scheduler` |
| Notifications | `ctx.notifier` (native and in-app) |
| Agent app folders | `fileapps.py`, `/custom-apps/`, hot reload, `read_app_logs` |
| Tool risk and approvals | `Tool.risk`, `needs_approval`, approval cards |
| Sandbox test run | `web/js/verify.js` |

## Phase 0: banish trackers (first, so nothing carries them)

Trackers (the `create_app` / `add_list` family, "data apps") are a second way to build an app, with their own tools, routing, panel, routes and table. v3 replaces them with file apps on `ctx.store`, so they go first and are not migrated or kept working in the meantime.

1. **Export once, then delete the data.** Write the existing `study` tracker (5 subjects, 24 topics) and the 5 orphan rows left by removed apps to `~/.diatonic/exports/trackers.json`, then delete the app and every row. This is the only step that touches real data, so it runs on the scratch server first and the real run is done by the user, or with the user's say-so.
2. **Delete the code:** `diatonic/dataapps.py`; the data tools in `apps/ui/app.py` (`create_app`, `add_list`, `add_field`, `remove_field`, `remove_list`, `DATA_TOOLS`, `RECORDS`, `BUILDING`); the `/api/dapps` routes and `dapp_*` handlers in `server.py`; the `data` kind in `mods.py` and `registry.py`; `web/apps/_data.js`; `ctx.data` in `panel.js` and `verify.js`; the `app_items` table and its methods in `db.py` (the table is dropped by a migration after the export); `tests/test_dataapps.py` and the tracker tests elsewhere; the `habit-tracker` eval prompt; the tracker sections of `docs/`.
3. **Routing:** a request that wants records (track, log, habits) is built as a file app like any other. Until `ctx.store` exists (first item of Phase 2) such an app can only keep data in `ctx.state`, which lives in the browser. This gap is accepted and short.
4. Done when: `grep -ri "dapp\|dataapp\|create_app\|add_list"` finds nothing but this plan and `todo.md` history, the suite is green, the scratch server opens every panel with no console errors, and the agent no longer offers the tracker tools.

Size: a net deletion of roughly 1,000 lines. Risk: low (it removes code), apart from the one data step above.

## Phase 1: the catalog (the spine)

Goal: the agent can find out what exists, how to call it and what it costs, by asking.

1. **Effects on every tool.** Add `effects` (a set of `read`, `write`, `destroy`, `external`, `execute`, `spend`, `display`) and `domain` to `Tool`. Default them from `risk` so nothing breaks. Approval, the untrusted-content rule and the panel read-only rule all read `effects`, in one place (`agent/loop.py` and `server.py:panel_tool`). Done when: no code path decides approval from anything but `effects`; a test per rule.
2. **One catalog.** A server-side registry that holds, per entry: `kind` (tool, command, component, skill, ctx-api), `name`, doc, args or props with docs, one example, `effects`, `domain`, `app`. Tools come from the registry; commands are pushed once by the page at load (`POST /api/catalog/commands`); components and skills come from the files added in Phases 3 and 4. Done when: `GET /api/catalog?q=` returns ranked results and a test covers each kind.
3. **`platform_search(query, kind?)` and `platform_describe(name)`** as read tools of the Interface app. Results are short (name, one line, signature) and `describe` returns the full entry with the example. Private apps' entries are hidden from a cloud model unless the user has allowed that app (same rule as tools). Done when: searching "notify" or "timer" finds the right `ctx` API once Phase 2 exists, and the results fit in a few hundred tokens.
4. **Docs and tests.** `docs/platform.md` describes the catalog entry format. `tests/test_catalog.py`.

Risk: catalog drift from the code. Mitigation: entries are generated from the live registries, never written by hand, and a test fails if a tool has no doc or example.

## Phase 2: `ctx` v2 (the framework)

Goal: the platform can do what people ask for. Each item is a server route plus a `ctx` method plus a catalog entry plus a test, and each documents its approval rule.

1. **`ctx.notify(title, body)`** on `ctx.notifier`. No approval (it only notifies the user).
2. **`ctx.timer`**: `start(seconds, {label})`, `pause`, `resume`, `cancel`, `onDone(fn)`, `list()`. Runs on the server scheduler so it survives closing the panel and the browser; completion sends a notification and an event the panel can listen to. Done when: a timer started, panel closed, reopened shows the right remaining time (scratch server, scripted).
3. **`ctx.store` (do this first in Phase 2, it closes the gap from Phase 0)**: per-app key/value and small tables with `get`, `set`, `add`, `addMany`, `update`, `updateMany`, `delete`, `query`. Stored server-side per app id, removed with the app. Replaces the one-at-a-time data-app tools for new apps.
4. **`ctx.fetch(url, opts)`** through the server. The first request to a new domain shows an approval card ("App X wants to read youtube.com"); approved domains are kept per app in its manifest. Only `http(s)`, no private network addresses, size and time limits. Done when: a panel can search YouTube's public page or API through it, and a request to `localhost` or a private range is refused (tests).
5. **`ctx.tool` for write tools** through the normal approval card, labelled with the app that asked. Panels still cannot run `shell` or `ui` tools.
6. **`ctx.on(event, fn)`**: a small event stream (`timer:done`, `todo:changed`, `mail:new` where the app can emit it). Cleaned up with `ctx.onClose`.
7. **Manifest `permissions`**: an app declares what it uses (`notify`, `timer`, `fetch: [domains]`, `tools: [names]`). The first run shows the list once for approval. Undeclared use is refused with a message that says what to declare.
8. `docs/panels.md` documents every method. The write-app tool description stops listing `ctx` and points to `platform_search`; the `FILE_SDK` text in `apps/ui/sdk.py` is deleted.

Risk: `ctx.fetch` is an outbound path. It is guarded by domain approval, private-address blocking, limits, and the untrusted-content rule (a run that read mail cannot add a domain). A cloud-model run cannot approve a domain for itself.

## Phase 3: UI kit and app shape

Goal: apps look like the harness and the model fills in data, not DOM.

1. **Components** in `web/js/ui/` (no framework, `h()`): `card`, `section`, `row`, `list`, `table`, `tabs`, `form`, `field`, `button`, `kv`, `stat`, `badge`, `empty`, `modal`, `chart` (simple bar and line). Each is a pure function of props, declared in a `props` table with docs and one example that renders with no app state. Styled only from the `:root` tokens, so light and dark work.
2. **`ctx.ui.*`** exposes them. Catalog entries are generated from the declarations (Phase 1).
3. **Component gallery** panel listing every component with its example, for the user and for checking by eye.
4. **App shape**: `ctx.ui.listApp({ rows, columns, detail, actions, filters })` gives list, detail pane, marks, filter, keys (via `ctx.list`) and the same actions on row and detail. A starter recipe uses it.
5. **Rules**: no module-level state, clean up in `ctx.onClose`, report an action's start, result and failure (failure goes to the app's log). Enforced by `scripts/check_module_state.mjs` and the existing tests.

Done when: the timer, tracker and search recipes (Phase 4) render with these components on the scratch server, in light and dark, with no console errors.

## Phase 5: builder session

Goal: a conversation about one app keeps working on that app.

1. **Builder session.** The first build or "change this app" request pins the conversation to one app. For its length the agent gets a fixed small toolset: `platform_search`, `platform_describe`, `read_app_file`, `write_app_file`, `patch_app_file`, `list_app_files`, `read_app_logs`, `open_app`, `delete_app`. No regex routing. It ends when the user says so or asks for something unrelated. This replaces `tools_for` for building (and removes most of `BUILD`, `CUSTOM`, `EDIT_WORDS`).
2. **Errors flow back.** Client-side errors from the open app are attached to the next builder turn automatically (today they only reach `read_app_logs`). A pasted error from the user is treated the same way.
3. The existing "unbuilt app is a failed run" check stays as the last step.
4. **Move building out of `apps/ui/app.py`** into its own module while the routing is being replaced anyway. Navigation and layout, theming and building end up in separate files, so `app.py` shrinks without a separate refactor.
5. **Retire API apps and connected apps.** Checked 2026-11-21 against the real `~/.diatonic` (read only): there are none, and no file apps either (`mods.json` holds only the dead `study` tracker, which Phase 0 already ignores). So there is nothing to convert. Delete their separate tool families (`connect_api`, `add_action`, `set_fields`, `remove_action`, `test_app`, `create_connected_app`, `add_app_action` and the rest), `diatonic/apiapps.py` and `diatonic/toolapps.py`, and their routing words (`API_TOOLS`, `API_WORDS`, `API_TOPICS`). "Build an app on a web API" is the `search` recipe over `ctx.fetch`, and "an app that uses another app's tools" is a recipe over `ctx.tool`; add the second recipe. A mod of kind `api` or `tools` found in a user's `mods.json` is ignored on load, as `data` is now, and a one-off `scripts/export_api_apps.py` (with a test) writes it to `~/.diatonic/exports/` first, the way `export_trackers.py` does, so nothing is lost for someone who has them.
6. Delete the build-time overrides (`build_max_tokens`, `build_num_ctx`) if the pinned toolset makes them unnecessary.

Done when: the acceptance scenarios 1, 2 and 4 below pass on the scratch server with a scripted conversation.

## Acceptance scenarios

These come from the user's real conversations. Each is checked on the scratch server with a scripted browser, and may be tried by hand.

1. **Timer.** "Create a timer app with start, pause, resume, reset and Sec/Min/Hour options; notify when done." Done when: the buttons work, a started timer keeps going with the panel closed, a notification arrives when it ends, and "there is no pause option" or "integrate that" in the next message edits the same app.
2. **Search through the network.** "Search YouTube videos and show name, thumbnail and a button to open them." Done when: the first search asks to allow the domain, results show, and a failure shows a readable message, not "Load failed".
3. **Tracker with bulk data.** "Track my study: subjects with units and topics, mark done." Built as a file app on `ctx.store` (after Phase 2.3). Done when: adding 5 units to 5 subjects is one call, not 25.
4. **Change request.** "The UI is bad, match the harness, dark mode only." Done when: the app uses the kit and tokens, with no custom colours, and the change is a patch to the existing app.
5. **Delete.** "Remove the timer app." Done when: the folder, tab, rail slot, settings and `ctx.store` data are gone (already true for files and settings; `ctx.store` comes with Phase 2).

## Order and sizes

| Phase | Depends on | Size |
|---|---|---|
| 0 Banish trackers | none | small (net deletion) |
| 1 Catalog | none | medium |
| 2 `ctx` v2 | 1 (for entries) | large, item by item |
| 3 UI kit and shape | 1 | large |
| 5 Builder session (includes the move out of `ui/app.py` and retiring API and connected apps) | 1 to 4 | medium to large |

Phases 2 and 3 can run in either order after Phase 1. Stop and report after each phase.

## Open questions (need a decision before the phase starts)

1. **Sandbox and broker: planned for v4, not part of v3** ([v4-plan.md](v4-plan.md)). No v3 feature depends on it. v3 keeps approvals per write and makes `ctx.fetch` safe with domain approval, private-address blocking and limits.
2. **Fetch limits** (Phase 2.4). Default allowed methods, body size and time limit; whether `POST` is allowed at all in the first version.
3. **Built-ins in the catalog** (Phase 1.2). Whether the catalog lists only agent-visible tools and commands, or also every internal command.
4. **Cloud models.** Decided 2026-11-21: yes. Recipe and skill text may go to a cloud model by default (it contains no user data), while catalog entries of private apps stay hidden until the user allows that app.

## Out of scope

App history, undo and edit provenance (the existing `mods.json` undo for styles, scripts and buttons stays as it is), rewriting in another language, a new editor model (buffers), the compos event bus as a whole (only the small `ctx.on` stream), model evals, and changing the guardrails listed above.

## Risks

- **Scope.** Six phases (0 to 5). Mitigation: each phase ships alone and leaves the app working.
- **A bigger platform needs more review.** `ctx.fetch`, `ctx.tool` writes and the permissions manifest all widen what agent code can do. Each ships with its approval rule, its tests and a doc entry, and none ships without them.
- **Catalog quality.** If entries are vague the search is useless. A test fails when an entry has no doc or example.
- **The agent still guesses.** The platform removes the main reasons for it; it does not make the model reliable. The unbuilt check and the app log stay.

## Handoff notes

### Phase 0 (code done 2026-11-21, one step left for the user)

- Step 1: `scripts/export_trackers.py` is written and tested (`tests/test_export_trackers.py`) but **not run against `~/.diatonic`**. Stop the real server, then run `python scripts/export_trackers.py --home ~/.diatonic` to export only, or add `--delete` to export and then remove the tracker and every row. It refuses to overwrite an existing export.
- The `app_items` table is dropped on the next start, but only when it is empty or `~/.diatonic/exports/trackers.json` exists, so rows cannot be lost. Old `data` entries in `mods.json` are ignored on load.
- Steps 2 and 3 are done. The `unbuilt` guard now counts only `ui_write_app_file`, `ui_patch_app_file` and `ui_add_script` for every build request (`wants_code` is gone), so an API or connected app that is built only with `connect_api` or `create_connected_app` reports "I did not build that". That was already so for requests without record words; check it when Phase 3 revisits the guard.
- Step 4: the grep finds only this plan, `todo.md`, `hackable-todo.md`, `scripts/export_trackers.py`, its test and unrelated `create_app` (the server factory, `fileapps.create_app`). Suite 561 passed (`.venv/bin/python -m pytest -q`, includes the node tests). Scratch server: main page loads with no console errors; `node scripts/mount_twice.mjs` passes for 10 of 11 panels, and `shell` shows "rendered nothing" because xterm draws without text (checked by hand: it renders, no errors).
- Not checked: the real-model eval (needs Ollama; skipped as instructed), and `hackable-todo.md` still mentions trackers.

### Phase 1: the catalog (2026-11-21)
Done: `Tool.effects` and `Tool.domain` (`apps/base.py`; `risk` still works and sets the effects, and is the card label); `needs_approval`, the dup-call guard (`guards.remember(changes=)`), `panel_tool`, `tapp_call`, file-app tool trust, tool apps and API apps all read `effects`. The untrusted-content rule is now one check in `agent/loop.py:_execute` for tools with the `display` effect (the Interface app's `tools()` tags every tool whose precheck is `_blocked`); the per-tool `_guard_mod` calls stay as a second line. `web` search/read are `read+external`, the shell `run` is `execute`. `diatonic/catalog.py` (`Catalog`, `Entry`, `ctx.catalog`), `GET /api/catalog?q=&kind=&limit=` and `?name=`, `POST /api/catalog/commands` (the page sends its commands once at load, `main.js`), `ui_platform_search` and `ui_platform_describe` (offered with the build and file-app tool sets only). `docs/platform.md`, `tests/test_effects.py`, `tests/test_catalog.py`.
Changed from the plan: the tools are named `ui_platform_search` and `ui_platform_describe` (they belong to the Interface app). Commands are pushed once at load, so commands a panel registers later are not in the catalog yet. `display` means "changes the interface itself"; navigation tools do not have it.
Not done / deferred: `component`, `skill` and `ctx-api` entries (the hook is `catalog.add_source`, no source yet). The two lookup tools are not in the default tool set, only in build and file-app requests; Phase 5 pins them.
Checked: `.venv/bin/python -m pytest -q` printed 575 passed (includes the node tests). The new tests were not run against the old code with a stash (the tree has many uncommitted changes); they use `effects=` and `ctx.catalog`, which did not exist, so they cannot pass on it. Scratch server: page loads with no console errors, `/api/catalog?q=close&kind=command` returns the commands the page pushed. `node scripts/mount_twice.mjs` over every file in `web/apps`: 13 pass, 2 fail: `shell` (known, xterm draws without text) and `tiles` ("Refresh all" in copy A changed copy B). I did not touch `tiles.js` and did not investigate it.
Decisions made: open question 3 (built-ins in the catalog): every command the page registers at load is listed, not only agent-visible ones.
Watch out: the catalog is rebuilt on every search (tools of every enabled app); fine now, cache it if Phase 2 adds many entries. `ui/app.py` imports a function called `catalog` from `toolapps`; the new module is `diatonic/catalog.py`, so import it as `from ...catalog import ...`.
Next: Phase 2 item 3, `ctx.store` (closes the Phase 0 gap), then the rest of Phase 2 in order. Open question 1 (sandbox and broker) must be decided before item 4.

### Phase 2: `ctx` v2 (2026-11-21)
Done: `ctx.store` (`appstore.py`), `ctx.fetch` (`appfetch.py`), `ctx.notify`, `ctx.timer` (`apptimers.py`), `ctx.on`, `ctx.tool` for changing tools (card per call), manifest `permissions` (`appperms.py`), approval cards (`web/js/perm-ask.js`), `ctx-api` catalog entries (`ctxapi.py`), `FILE_SDK` deleted (`FILE_HINT` in `apps/ui/sdk.py` points to `ui_platform_search`), `docs/panels.md`. Routes: `/api/store|fetch|notify|timers/<app>`; changing tools go through `/api/panel/tools` with the caller in `X-Diatonic-App`.
Changed from the plan: the approved permissions (domains too) are in the user's config (`apps.<id>.permissions_ok`), not the manifest, because the agent writes the manifest; the manifest only declares. Timers have their own table and tasks, not Scheduler rows. Read tools need no declaration, only tools that change things. Open question 2 settled: `GET` only, 2 MB, 15 s, text types, no custom headers.
Not done / deferred: a Settings view to see and revoke what an app may use; `mail:new` (nothing polls mail); a panel could still call the approval route itself (panel code shares the page with the shell).
Checked: `.venv/bin/python -m pytest -q` printed 609 passed before the last doc edits (rerun below). Scratch server with a real file app: permission card, per-call tool card, store, notify, `todo:changed`, timer done and a timer that kept running with the panel closed all worked, console errors []. `node scripts/mount_twice.mjs`: 10 of 11 pass, `shell` fails as before (xterm). Mutation: with the tool permission check removed, `test_a_tool_that_changes_things_needs_a_declaration_and_a_card_every_time` fails.
Decisions made: see "Changed from the plan".
Watch out: `tests/appsupport.py` has the fixtures (`web`, `user`, `declare`, `web_pages`) for anything that tests apps. A file app's panel renders more than once on open (custom_app_changed reload); listeners are disposed correctly. Two existing tests were edited (`test_catalog.py`) because their made-up queries matched the new real entries.
Next: Phase 3 (UI kit and app shape) or Phase 4; both need only Phase 1. Component entries join the catalog through `catalog.add_source`.

### Phase 3: UI kit and app shape (2026-11-21)
Done: `web/js/ui/index.js` (button, badge, card, section, row, list, table, tabs, field, form, kv, stat, empty, modal, chart; `listApp(ctx, spec)`; `runner`), `web/js/ui/components.json` (the one declaration: docs, props, example), `ctx.ui` and `ctx.ui.run` in `panel.js`, `ui-*` CSS in `app.css` (tokens only, a test fails on a hex colour), `diatonic/uikit.py` (catalog `component` entries from the JSON), `ctxapi.py` entries for `ctx.ui.listApp` and `ctx.ui.run`, the Components panel (`apps/components.js`, in `panelApps()` and `panel-menu.js`), recipes `diatonic/recipes/{timer,tracker,search}` (manifest + index.js), `scripts/render_recipes.mjs`, docs in `docs/panels.md` ("The UI kit") and `docs/platform.md`.
Changed from the plan: `listApp` takes `marks: {key, label, onChange}` (a check box per row) and `filters: [{id, label, test}]`; "report an action's start, result and failure" is `ui.run` (disables the button, shows a returned string, shows a failure and writes it to the app's log). The recipes live in `diatonic/recipes/` now because the Phase 3 done-condition needs them; Phase 4 gives them their skill and index.
Not done / deferred: deleting the `sdk.py` templates and offering the recipes to the agent (Phase 4); `modal` and `tabs` are not used by any recipe.
Checked: `.venv/bin/python -m pytest -q` printed 616 passed (includes `tests/js/ui-kit.test.mjs`: every example renders against a fake DOM). On a fresh scratch server `node scripts/render_recipes.mjs <folder>` printed 19 ok lines and no failures: all three recipes render, a timer starts, pauses and resumes, the tracker adds three topics in one call, marks one (count 1 / 3) and opens the detail pane, the search returned 8 real results, the gallery shows all 16 cards with every example rendered, no console errors; screenshots in dark and light were looked at. `node scripts/mount_twice.mjs components` passed; over all panels 10 of 11 pass, `shell` fails as before (xterm). In the browser, `verify.js` accepted `ctx.ui.card` and refused `ctx.ui.cardd` with the list of real names.
Decisions made: none of the open questions needed settling.
Watch out: a second run of `render_recipes.mjs` on the same scratch server can hang on tabs left by the first run: stop and start the scratch server between runs. Permission cards appear a moment after the call, so scripts must wait for them (`allow()` in the script). Flex parents shrink blocks: kit blocks set `flex-shrink: 0`. Three existing tests were edited, all for the same reason: they used made-up catalog entries whose names or words now collide with real ones (`test_catalog.py` twice) or looked for text in `panel.js` that moved (`test_web_js.py`).
Next: Phase 4 (instructions): prompt sections as files, skills (`app-creator`, `recipes`), the recipes as the model's examples (replacing and deleting the `sdk.py` templates), "for the agent" rules in the docs. Open question 4 is settled: recipe and skill text may go to a cloud model by default (private apps' entries stay hidden until allowed).

### Phase 5: builder session (2026-11-21)
Done: `apps/ui/builder.py` (`BuilderMixin`: the file-app tools, the verify sandbox call, `platform_*` and `read_skill`, `builder_session()`, `BUILDER_TOOLS`), `apps/ui/looks.py` (`LooksMixin`: theme, home, styles, buttons, scripts, undo), `apps/ui/app.py` is now show and layout only (1,556 lines to 599). `run_agent` asks `ui.builder_session(...)` first; if it answers, the model gets `BUILDER_TOOLS` and a note naming the app and the errors it logged since the last turn, and no routing runs. Deleted: `apiapps.py`, `toolapps.py`, the `_api.js` and `_tools.js` panels, the `/api/xapps` and `/api/tapps` routes, `sync_api_apps`, `BUILD`, `API_*`, `EDIT_WORDS`, `API_TOPICS`, `CONNECTED*`, `LOCAL_ONLY`, `DELETE_TOOLS` and the old `tools_for` branches, and `tests/test_apiapps.py` and `tests/test_toolapps.py`. Mods of kind `app` or `tools` are ignored on load (`mods.KINDS`). `scripts/export_api_apps.py` with `tests/test_export_api_apps.py`. New recipe `diatonic/recipes/tools` (an app on another app's tools through `ctx.tool`), checked in `scripts/render_recipes.mjs`. New tests: `tests/test_build_routing.py` (rewritten for sessions) and `tests/test_builder_scenarios.py` (scenarios 1, 4 and 5 as one scripted conversation).
Changed from the plan: the session toolset also has `create_file_app` and `read_skill` (an app has to be created, and the Phase 4 flow starts with a skill), and the delete tool keeps its name `delete_file_app`; the tool names were not changed because the eval and tests use them. `export_file_app` and `import_file_app` still exist but are no longer offered to the agent (no session tool, no route uses them). The session is not stored: it is worked out each turn from the conversation (a builder tool was used before, and the request is not plainly about another app), and the app is the one open on screen, else the one the last builder tool touched. Item 6 was not done: `build_max_tokens` and `build_num_ctx` are still needed, because a whole `index.js` is one tool argument whatever the toolset is.
Not done / deferred: the toolset is about 5,800 characters (was 7,800), plus a 1,061 character prompt; `open_app` alone is 1,030 of it. That is still not "a few hundred tokens". Acceptance scenario 2 (YouTube through the network) was not run with a real model: its parts (`ctx.fetch`, the domain card, readable failures) are covered by Phase 2 and the `search` recipe, which returned 8 real results on the scratch server. Nothing was run against a real model (no evals, as the plan says), so whether the 4B uses the session well is unchecked. `hackable-todo.md` still describes API apps (a historical file).
Checked: `.venv/bin/python -m pytest -q` printed 619 passed (it includes the node tests). Mutation: making the session ignore earlier building turns fails `test_a_follow_up_with_no_building_words_stays_in_the_session_on_the_same_app`, `test_errors_the_open_app_logged_reach_the_next_turn_once` and `test_the_loop_gives_a_follow_up_the_builder_tools_and_the_app`. Scratch server: `node scripts/render_recipes.mjs` printed 24 ok lines and no failures (all four recipes, including the new one: it lists the to-dos app's rows and Done changes it after the card is allowed), main page console errors []; `node scripts/mount_twice.mjs` over every panel: 14 of 16 pass, `shell` (xterm) and `tiles` ("Refresh all" in copy A changed copy B) fail as before in Phase 1. The scratch server was stopped.
Tests edited: `tests/conftest.py` (`VERIFY_WAIT` moved to `apps.ui.builder`), `test_mods.py` (`hex_colour` moved to `looks`), `test_fileapps.py` (the delete test uses `builder_session`), `test_instructions.py` (the size test measures the builder toolset, limit 8,800 characters), `test_uikit.py` (four recipes). `test_build_routing.py` was rewritten because the regex routing it tested is gone. `evals/build_prompts.json`: the `api` and `tools` prompts are now `panel` prompts.
Decisions made: the session ends on a closing phrase or a request that names another of the user's apps without a change word; looks requests are not part of it.
Watch out: a session only starts from words (`MAKE_A`, `NEW_APP`, `CHANGE` plus an app's name, `DELETE_APP`, a pasted error) or from an earlier builder tool in the chat, so a first message that says "I want a thing that counts" starts nothing; that is the place to widen it. `UiApp` is three classes now (`LooksMixin`, `BuilderMixin`, `UiApp`) sharing `self`; `_guard_mod`, `_blocked` and `_done` live in `app.py`.
Next: v3 is done. The real-data export (`scripts/export_trackers.py`) and `scripts/export_api_apps.py` are the user's to run with the server stopped. Then the real-model eval (`node evals/run_build_eval.mjs`) is the first thing to look at, and `docs/v4-plan.md` (sandbox and broker) is next.
