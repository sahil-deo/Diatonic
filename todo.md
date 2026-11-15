# Diatonic TODO

The plan for making this harness good: **hackable** (the agent and the user can build real apps and reshape the UI) and **fast and reliable** on a small local model. Use the **Telemetry** panel to decide what is worth doing and to prove it worked; items say how to measure them.

Design rationale lives in two notes (read them before starting the matching phase):
- [`hackable-todo.md`](hackable-todo.md): why agent-built apps fail today (no feedback loop, undocumented SDK, code in a single tool argument) and the fixes.
- [`panel-ui-todo.md`](panel-ui-todo.md): the panel workspace design (layout tree, tabs, command registry, keymap, launcher, status bar pins, macros).

## Working assumption

**The 4B local model is the only model we can use.** Nothing here may depend on a bigger model writing the code. So the harness, not the model, has to carry the difficulty: small composable pieces, templates to copy, and fast test feedback, rather than asking a 4B to write raw DOM. This is why Phase 1 leans on helpers and templates, and why model escalation is optional (Phase 4).

## Order of work, and why

The two plans share a foundation, so neither goes first alone. A **command registry** and a **panel contract** are what the keymap, palette, status bar, macros, agent layout tools *and* agent-built panels all sit on. Build them once, document them, and both plans use them.

1. **Phase 0: Foundations** (small, unblocks everything)
2. **Phase 1: Make agent-built apps work** (biggest user-visible win per line of code; no layout refactor needed)
3. **Phase 2: Panel workspace core** (the big refactor)
4. **Phase 3: Workspace features** (launcher, status bar, command bar modes, macros)
5. **Phase 4: Apps as files** (model escalation only if ever possible)

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

## Phase 1: Make agent-built apps work

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
- Phase 1 is done. What's left under it is feature work, not needed for agent-built panels: **data apps** (sorting/filter views, due-date reminders, export) and **API apps** (pagination, POST bodies, OpenAPI import, an eval over about 8 public APIs), listed below.
- [ ] Data apps: sorting/filter views, due-date reminders through the scheduler, export as Markdown/CSV.
- [ ] API apps: pagination, POST bodies beyond flat fields, import from an OpenAPI file, eval over about 8 public APIs.

## Phase 2: Panel workspace core

Design details in [`panel-ui-todo.md`](panel-ui-todo.md). Each step ships on its own.

- [ ] **Layout tree** (`split` and `stack` nodes, tab stack per leaf), serialised to JSON, stored in server config with a versioned schema (localStorage fallback). The current UI becomes the default preset. Own the tree and renderer (about 500 lines, CSS grid + pointer events) because the agent must manipulate it; `dockview-core` is the fallback if drag-and-drop becomes a time sink.
- [ ] **Chat as a panel** (param `conversationId`); remove the special cases (dock, `screenContext`, `set_layout` modes). Conversation list, notifications, settings and the terminal become panels.
- [ ] **Multi-instance panels:** move all per-panel state into the instance; two chats and two Files panels can coexist. Hidden tabs keep state but stop working; throttle resize observers; container queries so panels work at any width.
- [ ] **Resize, split, close** by mouse and by command; double-click a divider to equalize; header menu (split, move, duplicate, pin).
- [ ] **Directional focus and movement commands:** `focus.left/right/up/down`, `panel.move.*`, `panel.swap`, `panel.resize.*`, `panel.zoom`, `tab.next/prev`, jump-to-panel hint mode.
- [ ] **Linked panels:** a chat panel can follow another panel ("sees that mail") with a visible chip, replacing the single `screenContext`.

## Phase 3: Workspace features

- [ ] **Launcher instead of the rail:** a new or split leaf shows search, recents and pinned apps. The rail becomes optional, off by default.
- [ ] **Status bar pins:** left/center/right zones; pinned commands or apps, panel-contributed items with badges (unread mail). Pin from the palette or right-click; modifier-click opens in a split. Keep today's health, voice and runs items.
- [ ] **Command bar modes:** plain text = fuzzy search with frecency over apps, panels, conversations, commands, automations, layouts; `/` = command grammar (`/open`, `/run`, `/split`, `/layout`); a second shortcut (e.g. ⌘J) opens straight into ask-the-agent. Enter replaces the focused chat, ⌘Enter opens a new chat in a split, ⌥Enter runs in the background and notifies. `/run` merges scheduler jobs, saved macros and shell scripts, with a small form for arguments.
- [ ] **Macros:** declarative JSON sequences of commands (optional wait/if), recordable, saved by the agent ("save this layout as Dev"), runnable from the scheduler. Script macros need an approval card.
- [ ] **Named workspaces** ("Inbox zero", "Dev"): switch from the palette, per-window restore.
- [ ] **Agent layout tools** over the command registry ("mail left, calendar top-right"), free and undoable. Eval: a handful of layout requests checked against the resulting tree.
- [ ] Layout undo/redo and reopen closed panel (⌘⇧T), reusing the mods history.
- [ ] Home screen becomes a tiles panel (replaces `set_home` and the tile layouts); drag-reorder comes from the layout tree.
- [ ] Later: drag items between panels (file or email onto a chat or todo list), edge drop zones, scratchpad (quick chat or dropdown terminal), detach a panel to its own window, narrow-window collapse to tabs.

## Phase 4: Apps as files (model escalation optional)

- [ ] **Apps as folders:** manifest, JS, CSS, optional Python tools; edit/patch operations, hot reload, logs the agent can read. Panels expose their own agent tools. Export/import of mods and apps.
- [ ] **Optional, only if a bigger model ever becomes available:** build on a stronger model, run on the small one. Route build intents (and guard-triggered failures, and scheduled prompt jobs) to an 8B local or cloud model while the 4B handles everyday requests. Needs an explicit exception to the taint and private-app rules (a cloud builder must not see mail). Not planned while the 4B is the only option.

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

## Parked / decided against (for now)

- **Classify-then-hand-off multi-agent.** Each hop is another full model call, the classifier prompt evicts Ollama's prefix cache, and cross-app requests need the union of tools anyway. Revisit with a stronger planner; model escalation (Phase 4) captures most of the benefit.
- **Spoken approvals.** A misheard word shouldn't be able to authorise a change.
- **Diatonic as an MCP server for outside agents** (`agy`, Claude Code…), and `agy` as the model backend. Decided against 2026-11-16: the point is that local models do the work, and those agents send your data to cloud models. Plan if it ever comes back: a `/mcp` endpoint over the existing tool schemas, bearer token, per-app toggles off by default, writes through approval cards, private apps treated like a cloud model, no `shell_*` or `ui_*`.
- **Free-floating windows** in the panel workspace (tiling and tabs first).
- **PWA/Tauri wrapper** (browser tab only for now, so ⌘W/⌘T/⌘N/⌘1-9 stay unavailable, hence the leader key).
