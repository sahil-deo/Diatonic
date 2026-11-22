# Diatonic: presentation guide

A study guide for presenting Diatonic: what it is, what it is built from, how a request travels through it, and the questions you are likely to get. Every claim here was checked against the code; file paths are given so you can open the source live. Back to the [README](../README.md).

---

## 1. The one-minute pitch

> Diatonic is a **local agent harness**. A small on-device model (a 4-billion-parameter Qwen3 running in Ollama) controls my mail, calendar, files, notes, to-dos, GitHub and terminal. The model is interchangeable; **the harness is the product.** A 4B model on its own loops, narrates instead of acting and picks the wrong tools. The harness gives it only the tools it needs, checks and repairs its calls, cuts off loops, enforces budgets, asks me before anything that matters, and keeps private data away from cloud models. On top of that, the agent can build new apps for me, and I read and approve that code before it ever runs.

Three words to anchor on: **local**, **contained**, **extensible**.

**Harness vs model.** The model only produces text and tool-call requests. Everything else is the harness: the tools, the loop, the approvals, the guardrails, the memory, the UI. A harness is to a model what an operating system is to a CPU.

---

## 2. Tech stack

| Layer | What | Where |
|---|---|---|
| Language (server) | Python 3.11+ | `diatonic/` |
| Web framework | **Starlette** (ASGI) served by **uvicorn** | `server.py` |
| HTTP client | httpx (async) | providers, Google, GitHub, web |
| Validation | **jsonschema**: every tool call is validated against the tool's JSON Schema | `agent/loop.py` |
| Storage | **SQLite** (conversations, messages, scheduler, app data); JSON config at `~/.diatonic/config.json` | `db.py`, `config.py` |
| Secrets | macOS **Keychain** via `keyring`; never in config, never sent to the page or a prompt | `secrets.py` |
| Scheduling | `cronsim` for cron expressions; own time parser | `scheduler/` |
| PDFs | pypdf (text extraction) | Files app |
| Local model | **Ollama**, default `qwen3:4b-instruct` | `agent/providers/ollama.py` |
| Cloud models | Any OpenAI-compatible API (Gemini, Groq, ...) | `agent/providers/openai.py` |
| External agent | Google **Antigravity** (`agy`) over the Agent Client Protocol (ACP) | `agent/acp.py`, `agent/antigravity.py` |
| Outside tool access | An **MCP** server (Streamable HTTP) exposing apps as tools | `mcp.py` |
| Voice (optional) | faster-whisper (speech to text), Piper (text to speech), all local | `voice/` |
| Front end | **Plain JavaScript ES modules, no framework, no build step**; xterm.js is the only vendored library | `web/` |
| Streaming | **Server-Sent Events** (one global event stream) | `server.py`, `agent/runs.py` |
| Tests | pytest (about 30 s) plus Node's built-in test runner for browser logic | `tests/` |

Only seven Python dependencies. The rule in `AGENTS.md` is "no new dependencies", and the front end has no build step, so changing JS or CSS needs no restart.

---

## 3. Architecture at a glance

```
 Browser (plain JS, no build)                       Outside agent (Antigravity / any MCP client)
 ┌───────────────────────────────┐                          │ bearer token
 │ panels: chat, mail, files...  │                          ▼
 │ command palette, keymap       │                 ┌──────────────┐
 └──────────┬────────────────────┘                 │  mcp.py      │
   REST + SSE│ (X-Diatonic header)                 └──────┬───────┘
            ▼                                              │ same checks
 ┌──────────────────────────────────────────────────────────────────────────┐
 │ server.py (Starlette): Host/Origin check, routes, SSE, static files      │
 │                                                                          │
 │  RunManager (agent/runs.py)   one background task per conversation       │
 │      │  router.py picks the model for this request                       │
 │      ▼                                                                   │
 │  run_agent (agent/loop.py)  ◄── guards.py (loops, budgets, duplicates)   │
 │      │   select tools → build prompt → stream model → run tools          │
 │      │                                                                   │
 │      ├─► providers/  ollama | openai-compatible | antigravity            │
 │      ├─► approvals.py  (future the UI resolves)                          │
 │      └─► registry.py ─► apps/<id>/app.py  (tools + routes + settings)    │
 │                          gmail gcal files notes todos github shell web   │
 │                          scheduler memory telemetry presentations ui     │
 │                          + file apps the agent built (fileapps.py)       │
 │                                                                          │
 │  db.py (SQLite)   config.py   secrets.py (Keychain)   memory.py          │
 └──────────────────────────────────────────────────────────────────────────┘
```

**Everything runs on `127.0.0.1:5005`.** There is no cloud service of ours.

---

## 4. The core idea: the engine

The engine is `diatonic/agent/loop.py` (`run_agent`). It is an async generator that yields events (`tool_call`, `tool_result`, `message`, `guard`, `done` ...) which the UI renders live.

### 4.1 The loop

```
user message
   │
   ├─ pick model (router.py)
   ├─ pick tools for THIS request (select_tools)
   ├─ build prompt: stable system prompt + facts + history + user turn (+ clock, + on-screen item)
   ├─ fit the prompt to the context window (fit_budget)
   ▼
 for step in range(max_steps):            # max_steps = 8 (14 when building an app)
     stream the model
     ├─ repetition detected?  → abort, retry once hotter; second time end cleanly
     ├─ no tool call
     │     ├─ "Let me check…" with no action → nudge once
     │     ├─ request clearly needed a tool, none called → nudge once
     │     └─ otherwise → this is the answer, stop
     └─ tool calls (max 4 per step)
           for each: _execute()
               duplicate? → return earlier result, don't run
               validate args (jsonschema), coerce types
               precheck / untrusted-content rule
               needs approval? → card, wait for the user
               run the handler with a timeout
               result clipped, remembered, shown
     too many dups/errors/calls → "forced" final turn with no tools
   ▼
 store the assistant message, optionally speak a short summary
```

### 4.2 Why a small model needs a harness: the guardrails

All in `agent/guards.py` and `agent/loop.py`. This is the heart of the talk.

| Problem with 4B models | What the harness does |
|---|---|
| **Repeats itself endlessly** | Detects repeated sentences and character loops *while streaming*, aborts generation (so Ollama stops too), retries once with temperature 0.9 and repeat penalty 1.2; a second loop ends the run with a clean partial answer. History is de-duplicated so an old looped chat can't poison new turns. |
| **Calls the same tool again** | Identical (name + args) calls are never re-run: a repeat read returns the earlier result, a repeat write is refused and never re-prompts for approval. |
| **Never stops** | Hard budgets per run: model calls (8), tool executions (10), wall-clock (300 s), reply length (1024 tokens), first-token and idle timeouts. After 2 duplicates, 3 errors or the tool budget, the harness forces a short answer from a turn with no tools. |
| **Says "Let me check…" and does nothing** | `looks_unfinished()` catches it and nudges once to call the tool. Narration before a tool call is deleted from the UI, the stored chat and the context so the model can't imitate it. |
| **Makes up an answer instead of calling a tool** | If the request named an app and no tool was called, one nudge. |
| **Says "I built your app" when it didn't** | `unbuilt_failure()` replaces the final text with what actually happened. |
| **Bad arguments** | JSON Schema validation, then `coerce.py` repairs types; errors go back to the model as short messages. |
| **Too many tools confuse it** | Tool routing (4.3). |
| **Prompt too slow to read on weak hardware** | Small stable prompts and KV-cache reuse (4.4). |

Defaults live in `config.py` (`DEFAULTS["agent"]`); all are editable in Settings.

### 4.3 Tool routing: the key trick

A 4B model chooses far better from 5 tools than from 80. `select_tools()` in `loop.py` does no model call, only rules:

1. **Navigation requests** ("show my calendar", "hide the sidebar") get only three interface tools (`open_app`, `open_item`, `set_layout`).
2. Otherwise, apps whose **keywords** appear in the message are offered (words inside file paths are ignored; apps can also have regex patterns), plus apps already used in this chat, plus the app on screen if the user said "this" or "it".
3. If no app is named: if all tools together are small enough (`FALLBACK_ALL_CHARS`, about 2.5k tokens) offer all; otherwise `rank_tools()` offers the 8 whose names and descriptions overlap the request. Chit-chat gets none.
4. An app with many tools can narrow itself with `tools_for(text)`.
5. `fit_budget()` then drops the lowest-ranked tools until the prompt plus the reply fits the context window (`num_ctx`, default 8192). The estimate is 3.2 characters per token, deliberately pessimistic.

Even if the model calls a tool that was not offered, it still runs if it exists; offering is a hint, not the gate.

### 4.4 Speed on weak hardware

- **KV-cache friendly prompt.** The system prompt and tool list are byte-identical between turns about the same app; the clock and on-screen item ride on the *user* message. Ollama reuses its cache, so chatting within one app is much faster than alternating.
- System prompt = five small files in `diatonic/prompts/` (`identity`, `scope`, `tools`, `discovery`, `building`). A file of the same name in `~/.diatonic/prompts/` overrides one.
- Compact tool schemas, results clipped to 3500 characters, history capped (12 messages / 6000 characters), only the latest tool-using answer replays its tool trace.
- Sampling tuned for Qwen3-instruct (temp 0.6, top-p 0.8, top-k 20, repeat penalty 1.05); near-greedy settings make small models loop.
- **Skills instead of prompt bloat.** Instructions for rarer jobs (building an app) are markdown skills in `diatonic/skills/` that the agent reads *on demand*, not carried in every prompt.

### 4.5 Background runs

`agent/runs.py`: every conversation is its own `asyncio` task, independent of the browser. Events get a sequence number, are buffered and fanned out to all connected UIs over one SSE stream. Switching chats or closing the tab never interrupts a run; the UI re-attaches by **replaying the buffer**. One run per chat at a time (`RunBusy`); Stop cancels the task and keeps the partial output. Runs live in the server process, so restarting the server stops them.

---

## 5. The app system ("extensive application support")

### 5.1 The contract

An app is a folder `diatonic/apps/<id>/` with `create(ctx)` returning an `App` subclass (`apps/base.py`). `Registry.discover()` (`apps/registry.py`) imports every sub-package automatically; **a broken app is logged and skipped, it never takes the harness down.**

An `App` declares:

| Piece | Purpose |
|---|---|
| `id`, `name`, `icon`, `description` | identity |
| `tools()` | list of `Tool`: name, description, JSON Schema `parameters`, async `handler`, `effects`, `describe` (text for the approval card), `preview` (diff), `precheck`, `ask`, `timeout` |
| `keywords`, `keyword_patterns` | routing: which requests are about this app |
| `routes()` | the app's own HTTP endpoints, mounted at `/api/apps/<id>/` |
| `settings` | fields shown in Settings → Apps (can be secret) |
| `private` | holds private data; a cloud model must ask before using it |
| `untrusted_tools` | tools whose results are written by others (email) |
| `default_allow` | tools that run without a card until the user changes it |
| `snapshot(n)` | a live tile for the home screen |
| `panel = True` + `web/apps/<id>.js` | a UI panel exporting `render(el, ctx)` |

The model sees tools as `<app>_<tool>` (for example `notes_create_note`). Names and argument names are part of the product: the 4B model reads them. The eval and `tests/test_build_routing.py` depend on them.

### 5.2 Effects: one model for all safety rules

Every tool declares **effects** instead of ad-hoc flags:

| Effect | Meaning | Asks first? |
|---|---|---|
| `read` | looks at data | no |
| `write` | changes data | yes |
| `destroy` | deletes data | yes (marked destructive) |
| `execute` | runs commands or code | yes |
| `spend` | costs money or quota | yes |
| `external` | talks to the network | no on its own |
| `display` | changes the interface (theme, panels) | no, but refused in a run that read untrusted content |

Three rules read effects and nothing else:
1. A card is shown when the tool has `write`, `destroy`, `execute` or `spend` (unless the user chose "always allow", or the tool decides itself with `ask`).
2. A panel may only call tools with none of those.
3. A run that read untrusted content cannot use a `display` tool.

This is why safety stays consistent as apps are added: a new app only declares what its tools can do.

### 5.3 Built-in apps

Mail and Calendar (Google, multi-account, via OAuth in `google/`), Files, Notes, To-dos, GitHub, Web (search and read as text), Terminal and shell, Scheduler (reminders, prompt jobs, script jobs), Memory, Telemetry, Presentations, and **Interface** (`apps/ui/`: theme, layout, mods, and the app builder).

### 5.4 Apps the agent builds itself (file apps)

The most distinctive feature. `fileapps.py` + `apps/ui/builder.py`:

- A file app is a folder in `~/.diatonic/apps/<id>/` with `manifest.json`, `index.js`, `style.css` and optional Python tools. `Registry.sync_file_apps()` turns each into a live `FileApp`.
- **Builder session.** The first build request starts a session: the agent gets a small fixed toolset (look up the platform catalog, read a skill or recipe, list/read/write/patch the app's files, read its log, open, delete) and stays on that one app for the conversation. "Add a reset button" later edits the same app.
- **Start from recipes**, not from scratch: `diatonic/recipes/` (`timer`, `tracker`, `search`, `tools`) are served through the same catalog.
- **Verify before asking.** New code is first run in a **sandboxed frame** inside the open browser window (`web/js/verify.js`: no network, no access to the page). Syntax errors, undefined names or an empty render go back to the agent to fix. The user is only asked to approve code that at least runs, and the approval card shows the code or diff. The sandbox is a convenience; **the card is the security check.**
- **Permissions.** An app declares what it needs in its manifest (`notify`, `timer`, `fetch` domains, specific tools). `appperms.py` asks the user once; the approved copy is stored in the *user's* settings, not the app's files, because the agent can write the manifest. Asking for more later re-prompts.
- Errors the app logs come back to the agent automatically on its next turn.
- Agent-written code never runs outside the sandbox before approval (a hard project rule).

### 5.5 The platform catalog: look things up, don't memorise

The agent does not carry a list of everything in its prompt. `ui_platform_search` and `ui_platform_describe` query a **catalog built from live code** each time (`catalog.py`): tools, UI commands, the `ctx` API, UI components and skills. It cannot go out of date, and a test fails if a built-in tool has no description.

### 5.6 Adding an app by hand (the demo-able version)

Create `apps/bookmarks/__init__.py` with a `BookmarksApp(App)`, give it a `Tool("add_bookmark", ..., WRITE)` and a `create(ctx)`. Restart: it appears in Settings, in routing (via `keywords`) and with approval cards, with no other wiring. Full example in [how-it-works.md](how-it-works.md#adding-an-app).

---

## 6. Safety model (expect questions here)

| Threat | Defence | Where |
|---|---|---|
| Agent does something destructive | Approval card with the exact diff / full email / command; ⌘↵ approve, Esc deny | `agent/approvals.py`, `loop._execute` |
| **Prompt injection through email** | Email is "untrusted". A run that read mail cannot change the interface or build apps. HTML mail renders in a script-free sandbox, remote images blocked. Tested with a hostile email. | `session.py`, `apps/gmail/` |
| Private data to a cloud model | Private apps (mail, files, notes) need an extra card: allow once / for this chat / deny. Facts and `@file` contents stay local. | `loop._execute` (`share`) |
| Other websites attacking localhost | Server binds `127.0.0.1`; **Host header check** (DNS rebinding) and **Origin check** on writes; the page sends an `X-Diatonic` header proving the request came from it (the terminal requires it) | `server.py` (`LocalOnly`), `web/js/api.js` |
| Path traversal in Files | Paths resolved through symlinks, must stay inside a shared folder; secrets (`.env`, `*.pem`, `.ssh/`...) blocked; only PDFs and images served inline | `apps/files/paths.py` |
| Dangerous shell commands | **Parsed, not pattern-matched**: `read` (runs), `run` (needs a card), `refuse` (never: `sudo`, keychain, Diatonic's own data). Chains, redirects and substitutions are never read-only. | `shell/classify.py` |
| Agent-written code | Sandboxed test, then a card showing the code; panels can only call read-only tools | `web/js/verify.js`, `appperms.py` |
| Unattended jobs | Scheduled prompt jobs are read-only by default; unattended writes need a UI-granted permission and time out to "denied" | `scheduler/`, `loop._execute` |
| Secrets leaking | Keychain only; never in config, the page or a prompt | `secrets.py` |
| Mis-heard voice command | Voice never approves anything | UI |

Project rule worth quoting: *do not weaken a guardrail to make something work.* If a task seems to need it, stop and say so.

---

## 7. Models and routing

- **Providers** (`agent/providers/`): all implement `stream()` and yield the same chunk shape, so the loop doesn't care which is active. `make_provider()` picks by config.
  - `ollama.py`: local.
  - `openai.py`: any OpenAI-compatible API (Gemini, Groq, ...). It converts message shapes, handles Gemini's thought signatures, retries rate limits and hides `<think>` output.
  - `antigravity.py`: see below.
- **Rule-based router** (`agent/router.py`, off by default): classifies each request into a tier and picks a model. Order: custom regex rules → `builder` (make or edit an app) → `complex` (deep research / code analysis) → `ui` (navigation) → `multi_app` → `trivial` → default. Idea: cheap local model for "zoom the calendar", a stronger one for building apps. `RunManager.start()` calls it and tags the run.
- **Antigravity (`agy`)** is different: it runs its *own* agent. Diatonic talks to it over ACP (a subprocess per conversation, `--sandbox`) and **exposes its apps back through its own MCP server** with a per-conversation bearer token. Every MCP call goes through the same `_execute`, so approvals, cloud-privacy prompts and the untrusted-content rule apply identically. Permission modes: Strict, or Auto (safe reads and workspace edits auto-run; sending mail, deletes, shell still ask). Launcher commands (`open -a`, `osascript`, `kill`, `ps`) and `~/.diatonic` access are always blocked.

---

## 8. The front end

- **No framework, no build.** `web/index.html`, `web/js/*.js` (about 7k lines of shell modules), `web/apps/<id>.js` (one panel per app), `web/css/app.css`. Served with `Cache-Control: no-cache`, so JS and CSS changes show on reload.
- Helpers: `h(tag, attrs, ...children)` builds DOM; `api.get/post/...` adds the `X-Diatonic` header; `emit/on` is a tiny event bus (`store.js`).
- **Everything is a command** (`commands.js`, `commands-core.js`): ids like `panel.close`, `chat.new`. The ⌘K palette, keymap (`keymap-defaults.js`), macros and the agent's UI tools all use the same commands.
- **Workspace.** The screen is a tree of panels: split, stacked tabs, resize, zoom/unzoom without losing the layout, saved layouts ("Dev", "Study"). Panels must keep **no module-level state** (state lives in `render`'s closure or `ctx.state`) so two copies of one app can coexist; `scripts/check_module_state.mjs` and `scripts/mount_twice.mjs` enforce it.
- Live **home tiles** come from each app's `snapshot(n)`.
- Panels get `ctx`: `ctx.state`, `ctx.tool()` (read-only tools only), `ctx.fetch` (allowed domains only), `ctx.notify`, `ctx.timer`, `ctx.store`.

---

## 9. Supporting systems

- **Memory** (`memory.py`): two kinds of short, capped, decaying entries. *Facts* ("my default repo is ...") go into the system prompt within a strict budget; *lessons* (a tool quirk the harness observed, such as a repeated bad-argument pattern) are appended to that **one tool's** description, costing nothing unless the tool is offered. Auto-learning is on by default; lessons unused for 90 days are forgotten. Facts stay local when a cloud model is active.
- **Scheduler** (`scheduler/`): reminders, scheduled **prompt jobs** (an AI prompt in its own chat, read-only by default) and **script jobs** (shell on a schedule, off by default). SQLite-backed; times parsed by *code*, not the model. Missed one-off reminders fire once, marked late; missed recurring jobs fire once then resume; a running job is skipped, not doubled.
- **Telemetry** (`telemetry.py`): each run is traced (model time, time to first token, tool timings, guard events), so you can show where time goes.
- **MCP server** (`mcp.py`): described in section 7.

---

## 10. Testing and engineering discipline

- `pytest` (about 30 s) also runs the Node tests (`tests/js/*.test.mjs`) for browser logic. About 45 Python test files cover guards, effects, router, files, shell, scheduler, MCP, builder routing, and more.
- Fakes for outside services: `tests/fake_google.py` (a local fake Google with seeded accounts, also a demo mode) and `tests/fake_acp_server.py`.
- A real-model **build eval** (`evals/run_build_eval.mjs`, needs Ollama) measures how well the agent builds panels.
- Project rules in `AGENTS.md`: a new test must fail on the old code; never touch the real `~/.diatonic` or port 5005 during development (a scratch server on :5071 with its own data folder is used); no commits unless asked; report faithfully.

---

## 11. Following one request end to end

Example: *"check my calendar for conflicts and draft email responses to Alex"*

1. Browser POSTs the message; `RunManager.start()` creates a `Run` and tags the request. The router (if enabled) classifies it as `multi_app` and picks the model.
2. `run_agent` builds the prompt. `select_tools` matches the keywords "calendar" and "email" and offers only Calendar and Mail tools; `fit_budget` trims if needed.
3. The model streams. It calls `gcal_list_events`. `_execute` validates the arguments, finds it is `read`, so no card, and runs it. The result is clipped and returned to the model.
4. Mail results are marked **untrusted** for the rest of the run.
5. The model calls `gmail_create_draft` (a `write`). `_execute` builds a preview, emits a `tool_call` with `needs_approval`; the run status becomes "waiting" (an amber shield in the sidebar). If a cloud model were active, the extra private-data card would appear first.
6. The user presses ⌘↵; the future in `approvals.py` resolves and the tool runs.
7. The model writes the final answer; the harness stores it, emits `message` and `done`, and optionally speaks a one-line summary.
8. At every step the guards watched for loops, duplicates and budgets.

---

## 12. Suggested demo (5 to 7 minutes)

Use the scratch server, not your real setup: `scripts/dev_scratch.sh start` (port 5071, own data folder). Stop it when done.

1. **Multi-panel workspace.** Show Files, Terminal, Chat, Notes side by side; ⌘K palette; zoom and unzoom.
2. **A simple tool call.** "Add a to-do to send the invoice by Friday." Show the tool card.
3. **An approval.** "Create a note called demo" or a file edit; show the diff card, press ⌘↵.
4. **The guardrails.** Open Settings → Agent to show the budgets; mention the loop cutoff. If you want it live, open the run's trace in Telemetry.
5. **Build an app.** "Create an app to track my exam studies." Show the sandbox test, then the card with the code, then the new panel appearing in the launcher.
6. **Safety story.** Explain (or demo with the fake Google) the hostile-email case: a run that read mail cannot change the interface.
7. **Model switching.** Click the model name in the status bar and switch local to cloud; show the private-data prompt.

Have a fallback: if Ollama is slow, say that on weak hardware the cost is prompt reading, which is why section 4.4 exists.

---

## 13. Likely questions, with answers

**Why a small local model instead of a big cloud one?** Privacy (mail and files stay on the machine), cost, offline use, and it is a harder engineering problem: if the harness makes a 4B model reliable, it makes a bigger one more reliable too. Cloud models are supported, with explicit privacy prompts.

**Why not LangChain or an agent framework?** The loop is about 700 lines, and the behaviour that matters (loop cutting, duplicate handling, budgets, approvals, untrusted-content rules) is specific and needs to be exact. Fewer dependencies is also a stated rule.

**How do you stop prompt injection?** I don't rely on the model to resist it. Structural rules do it: untrusted content marks the run, and a marked run can't use `display` tools or build apps. Anything that changes data still needs a human approval card showing exactly what will happen. Private data needs consent before a cloud model sees it.

**Can the agent run arbitrary code?** Only after I read it. Agent-written code runs first in a no-network sandbox frame for testing, then an approval card shows the code. Shell commands are parsed: read-only ones run, everything else needs a card, and some (`sudo`, the keychain) are refused outright.

**What happens if the model loops or gets stuck?** Detection mid-stream, one hotter retry, then a clean partial answer with a notice. Budgets for steps, tools and time guarantee it always ends.

**How does it choose tools?** Rules, not a model: keywords and patterns per app, apps already used in the chat, the app on screen, and a word-overlap ranking as a fallback. Then a context-budget fit. This keeps the prompt small and costs no extra model call.

**How do I add an app?** One folder, one class, one `create(ctx)`; tools declare effects, and approvals, routing, settings and the catalog follow automatically.

**What if two apps both match?** Both are offered; the model chooses. A builder session is the exception: it has a fixed toolset for the whole conversation.

**Is the data safe on disk?** Conversations and app data are in `~/.diatonic/diatonic.db` (SQLite); keys and tokens in the macOS Keychain. Files are changed atomically, a copy of the previous version is saved first, and deletes go to a Trash.

**What are the limits?** Honest ones: a 4B model still makes mistakes (the harness contains them, it does not eliminate them); runs live in the server process, so a restart stops them; web reading has no browser, so JavaScript-only pages come back empty; it is macOS-focused (Keychain, AppleScript notifications); Ollama parallelism is needed for true concurrent background chats.

**How do you know it works?** Test suite, a real-model build eval, and per-run telemetry traces.

**Is it multi-user?** No. It is a single-user, local tool by design; the server rejects other Hosts and Origins unless you explicitly allow one (for example a Tailscale proxy).

---

## 14. File map for quick lookup

| Question | Open |
|---|---|
| How does a run work? | `diatonic/agent/loop.py` (`run_agent`, `_execute`, `select_tools`) |
| Loops and budgets? | `diatonic/agent/guards.py` |
| Background runs and SSE? | `diatonic/agent/runs.py` |
| Approvals? | `diatonic/agent/approvals.py` |
| Which model? | `diatonic/agent/router.py`, `diatonic/models.py` |
| Providers? | `diatonic/agent/providers/` |
| The app contract and effects? | `diatonic/apps/base.py`, `diatonic/apps/registry.py` |
| An example app? | `diatonic/apps/todos/app.py` (small), `diatonic/apps/files/` (guarded) |
| Agent-built apps? | `diatonic/fileapps.py`, `diatonic/apps/ui/builder.py`, `diatonic/appperms.py`, `diatonic/web/js/verify.js` |
| Platform catalog and skills? | `diatonic/catalog.py`, `diatonic/instructions.py`, `diatonic/skills/`, `diatonic/recipes/` |
| Shell safety? | `diatonic/shell/classify.py` |
| Server, routes, Host/Origin check? | `diatonic/server.py` |
| MCP and Antigravity? | `diatonic/mcp.py`, `diatonic/agent/acp.py`, `diatonic/agent/antigravity.py` |
| Memory, scheduler, telemetry? | `diatonic/memory.py`, `diatonic/scheduler/`, `diatonic/telemetry.py` |
| Settings and defaults? | `diatonic/config.py` |
| Front-end shell and panels? | `diatonic/web/js/`, `diatonic/web/apps/` |
| More reading | [how-it-works.md](how-it-works.md), [platform.md](platform.md), [apps.md](apps.md), [customizing.md](customizing.md), [panels.md](panels.md) |
