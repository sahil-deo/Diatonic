# Working on Diatonic

Diatonic is a local agent harness: a small on-device model (Ollama, `qwen3:4b-instruct`) controls the user's mail, calendar, files, notes, to-dos and terminal through apps and tools, with approvals and guardrails. Python (Starlette) server, plain JavaScript front end. Read `docs/how-it-works.md` once; the rest of this file is the rules.

## Run and check (do this after every change)

```bash
.venv/bin/python -m pytest -q                    # whole suite, about 30 s; it also runs the node tests below
node --test tests/js/*.test.mjs                  # just the browser-side logic (keys, search, sandbox page)
scripts/dev_scratch.sh start                     # throwaway server on :5071 + headless Chrome (DevTools :9333)
scripts/dev_scratch.sh restart                   # after changing Python. JS and CSS are served fresh on every load, no restart
scripts/dev_scratch.sh stop                      # always stop it when you are done
node scripts/check_module_state.mjs [file]       # module-level state in panels (see docs/phase2-guide.md)
node scripts/mount_twice.mjs <app-id>...         # mounts a panel twice, checks the copies are independent
node evals/run_build_eval.mjs --only id,id       # real-model eval of agent-built panels (slow, needs Ollama; skip unless asked)
```

To look at the UI, drive the scratch Chrome with `scripts/cdp.mjs` (a 34-line DevTools helper: `open(url, port)`, `ev(js)`, `key(...)`, `errors`). Examples of its use are in `evals/run_build_eval.mjs` and `scripts/mount_twice.mjs`. A UI change is not done until you have loaded it in the scratch Chrome and the console shows no errors.

## Hard rules

- **Never touch the user's real setup.** Port 5005 is their running server and `~/.diatonic` is their real data. Use only the scratch server (own port, own data folder). Never run `./diatonic.sh` or `diatonic run` without `--port` and `DIATONIC_HOME`.
- **Do not commit, push, merge or rebase unless asked.** Work on the branch you were given. Never `git reset --hard`, `git clean` or force-push.
- **No new dependencies.** The front end has no framework and no build step; third-party code lives in `diatonic/web/vendor/`. Python dependencies are in `pyproject.toml`; do not add any.
- **The app must work after every step.** Tests green, panels open, no console errors. Make the change in small steps and check each one, instead of one big rewrite.
- **Do not weaken a guardrail to make something work.** Approvals, the untrusted-content rule (a run that read mail cannot change things unapproved), the cloud-model privacy prompts, the `X-Diatonic` header and Host/Origin checks, path checks in Files and the terminal's command classes exist on purpose. If a task seems to need loosening one, stop and say so.
- **Agent-written code never runs outside the sandbox before the user approves it.** The only place new panel or script code runs unapproved is the sandboxed frame in `web/js/verify.js`.
- **Tests with every behaviour change.** A new test must fail on the old code (check this: stash the fix, run it). Do not edit or delete a test to make it pass unless the test is wrong, and then say why.
- **Report faithfully.** Say exactly which commands you ran and what they printed. If something failed, was skipped or you could not check it, say so. Never write "all tests pass" without a run that shows it.

## Code style

Match the file you are editing. The patterns below are what the codebase already does.

**Python** (3.11+, 4 spaces, double quotes, lines up to about 160 characters)
- Start modules with `from __future__ import annotations` and a docstring saying what the module is for.
- Modern hints (`str | None`, `list[dict]`). Small functions; a docstring when the *why* is not obvious.
- User-facing failures in tools are `raise ToolError("what is wrong and what to do")`. HTTP handlers return `JSONResponse({"error": "..."}, status)`. Do not let a bug surface as a 500 with a traceback.
- HTTP routes are defined inside `create_app` in `server.py` as nested functions and listed in `routes`; an app's own routes live in its `routes()`.
- Naming: `snake_case`; tool names are `verb_noun` (`list_todos`); the full name the model sees is `<app>_<tool>`.

**JavaScript** (ES modules, 2 spaces, double quotes, semicolons)
- A `//` comment at the top of each file says what it is for. One-line `/** */` comments on functions that are not obvious.
- Build DOM with `h(tag, attrs, ...children)` from `js/dom.js` (`class`, `html`, `onclick`...), query with `$`. Never concatenate untrusted text into `html:`; use `esc()` or text children.
- Server calls go through `api.get/post/put/patch/del` in `js/api.js` (it sends the `X-Diatonic` header). Events between modules: `emit(name, detail)` / `on(name, fn)` from `js/store.js`.
- Everything the UI can do is a command (`js/commands.js`, built-ins in `js/commands-core.js`). Command ids are `group.verb` (`panel.close`, `chat.new`). New key bindings go in `js/keymap-defaults.js`; every default must be valid (a test checks this).
- Names: `camelCase` functions, `SCREAMING_CASE` for constant tables, kebab-case CSS classes with a short prefix per feature (`kb-`, `wk-`, `pres-`).
- Long lines are common here (up to about 200); do not reformat code you are not changing.

**Panels** (`diatonic/web/apps/<id>.js`): follow `docs/panels.md`. The one rule that matters most: **no module-level state**. Keep state in `render`'s closure or in `ctx.state`; start nothing at module level; clean up with `ctx.onClose`.

**CSS**: variables on `:root` (`--bg --surface --text --accent --radius ...`), scale radii with `calc(Npx * var(--rs, 1))`, work in light and dark themes. Append to `css/app.css` under a comment header for the feature.

**Comments**: say why, not what. Plain English, short, no author tags, no commented-out code, no TODOs without a `todo.md` entry.

**User-visible text** (buttons, errors, docs): plain and short, no jargon, no exclamation marks, no emoji. An error says what happened and what to do next.

**Tests**: `tests/test_<area>.py` (pytest, fixtures in `tests/conftest.py`, notably `env`) and `tests/js/*.test.mjs` for logic that has no DOM. Test names are sentences (`test_a_panel_cannot_run_tools_that_change_things`). Tests must not depend on the date or time zone (there was a bug where the week flipped at the wrong hour), the network, or the user's folders.

**Docs**: when behaviour changes, update the matching file in `docs/` and tick or edit the item in `todo.md` with the date and an honest note. Keep `todo.md` true; do not tick something that is only partly done.

## Where things are

- `diatonic/server.py` routes and wiring · `diatonic/agent/` the loop, providers, approvals, guards, runs · `diatonic/apps/<id>/app.py` one folder per app (tools, settings, routes) · `diatonic/apps/ui/` the agent's interface and app-building tools
- `diatonic/web/index.html`, `js/` shell modules, `apps/` one panel per app, `css/app.css`
- `diatonic/mods.py` interface changes the agent made · `diatonic/keymap.py` the keymap file · `diatonic/dataapps.py`, `apiapps.py`, `toolapps.py` apps the agent builds
- `docs/` user and developer docs · `todo.md` the plan, in phases · `evals/` the build eval

## Things that have already caused trouble

- Python changes need a server restart; JS and CSS do not. `sed -i` differs on macOS (needs an extension); prefer your editor tool.
- `node --check` does not look inside strings. The sandbox page in `web/js/verify.js` is a template string: no backticks inside it; `tests/js/verify-frame.test.mjs` compiles it.
- The chat list has `scroll-behavior: smooth`, so `scrollTop` right after a key press can still read 0. Wait, or set `style.scrollBehavior = "auto"` in a check.
- Esc leaves the chat box only (a `when: "composer"` binding). Other text boxes keep their own Esc; the to-do inline editor saves on blur.
- The terminal panel gets every key except the leader. Do not add global key handlers that would break typing in it.
- A panel that adds a listener to `document`/`window`, a timer or an observer must remove it in `ctx.onClose`, or it leaks and fires after the panel is gone.
- `ctx.state` is stored per app today (localStorage `panelstate:<app id>`), so two panels of one app would share it until Phase 2 step 2 keys it per instance. Do not rely on sharing.
- A panel sits inside `.panel-body`, whose generic `input`/`textarea` rules (border, padding, `input + input { margin-top }`) leak into anything in it, the main chat composer included. Prefix your rule with `.panel-body` or raise its specificity, and measure the control in the browser. An icon next to a label goes in a span with only the icon; the app styles it so it is centred.
- Extra chat panels are self-contained and do not stream; only the first one is the main chat (see `docs/panels.md`). Do not use `chat.send` from a panel other than the main chat: it writes into the main chat's message list.
- Tools are called by a 4B model: descriptions and argument names are part of the product. Do not rename a tool or argument casually; the eval and `tests/test_build_routing.py` depend on them.

## Working on Phase 2

Read `docs/phase2-guide.md` first. It has the order, the per-panel recipe, how to check each step, and what "done" means. One task per branch or commit, in the order given; stop and report after each step.
