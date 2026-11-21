# How it works

Approvals, running several chats at once, the guardrails that make a small model usable, adding an app, and the code layout. Back to the [README](../README.md).

## Approvals

Approvals work in any chat, including background ones. Every tool declares its effects (see [platform.md](platform.md)): a tool that only reads runs immediately; one that writes, deletes, runs code or spends shows an approval card (**⌘↵** approve, **Esc** deny) with exactly what will happen: the diff for a file edit, the full message for an email, the code for a script.

Some writes run without a card because they are easy to undo and can't reach outside Diatonic: theme and CSS changes and top-bar buttons. They appear as tool cards in the chat, and interface changes offer an **Undo**. Each app's list in Settings → Apps shows which tools ask; you can mark more as *always allow* there.

With a cloud model, private apps get an extra card, "Share private data with a cloud model?" (**Allow once**, **Allow for this chat**, **Deny**). Pressing **Stop** while a card is waiting cancels it. Voice input never approves anything, so a mis-heard word can't merge a PR.

## Running several chats at once

Each conversation is its own background run, so you can start a task, hit **New chat** (⌘⇧O) and start another while the first is still working.

- The sidebar shows a spinner on chats that are working, an amber shield on ones waiting for your approval, and a blue dot on ones that finished while you were elsewhere. The status bar and the tab title summarize how many are active; click the status bar item to jump to one.
- Switching back to a running chat replays what it has done so far and carries on live.
- Spoken summaries and approval alerts from a background chat are prefixed with its title ("In *Calendar prep*: …").
- A chat runs one task at a time; **Stop** (the square button) cancels it and keeps what it produced.
- Runs are separate from the browser connection but live in the server process: restarting `diatonic` stops them.
- Ollama decides how many requests it serves at once. If background chats seem to queue behind each other, start Ollama with more parallelism, e.g. `OLLAMA_NUM_PARALLEL=2 ollama serve` (costs extra memory for each slot).

## Guardrails for small models

Small local models loop, narrate instead of acting, and repeat tool calls. The agent loop assumes that and contains it (`diatonic/agent/guards.py`, `agent/loop.py`):

- **Repetition is cut off mid-stream.** Repeated sentences and character-level loops are detected as tokens arrive; generation is aborted (so Ollama stops too), the runaway text is replaced, and the model is retried once with hotter sampling and a stronger repeat penalty. A second loop ends the run with a clean partial answer and a notice. History is de-duplicated too, so an old looped chat can't poison new turns.
- **Identical tool calls never run twice.** A repeated read returns the earlier result; a repeated write is refused (and never re-prompts for approval). Two duplicates, three failures or the tool budget force a final answer from a tool-less turn.
- **Hard budgets per run:** model calls (`max_steps`), tool executions (`max_tool_calls`), wall-clock (`run_timeout`), reply length (`max_tokens`), first-token and idle timeouts. A stuck model gives a message, never a hang.
- **"Let me check…" with no action** gets one nudge to actually call the tool; so does a made-up answer to a request that clearly needed an app. Narration before a tool call is dropped from the UI, the stored chat and the context, so the model can't imitate it.
- **Honest errors:** unknown tools, bad arguments and tool failures go back to the model as short messages; every notice is shown in the chat ("The model started repeating itself, so I cut it off and retried.").

### Speed on weak hardware

With a small local model most of the time goes into reading the prompt, so the harness keeps prompts small and stable:

- **Tool routing:** only the apps your message names (plus those already used in the chat, and the app on screen) offer tools, and an app can narrow its own tools per request. A request that names no app gets only its few best-matching tools, and plain chat gets none. Add `keywords` to a new app to take part.
- **Never over the context window:** before each model call the lowest-ranked tools are dropped until the prompt plus room for the reply fits the model's context. If the model calls a real tool that wasn't offered, it still runs.
- **Compact schemas and results:** self-explanatory parameter descriptions are dropped, tool results are compacted and clipped, history is capped by characters, and only the latest tool-using answer replays its tool trace.
- **Instructions are files:** the system prompt is the sections in `diatonic/prompts/`, and how to build an app is a skill the agent reads on demand (`docs/platform.md`, "Instructions are files").
- **KV-cache friendly:** the system prompt and tool list stay byte-identical between turns about the same app (the clock rides on the user message), so Ollama reuses its cache. Chat within one app is much faster than alternating apps.
- **Sampling tuned for Qwen3-instruct** (temperature 0.6, top-p 0.8, top-k 20, repeat penalty 1.05) instead of the near-greedy settings that make small models loop. Spoken summaries use a tiny capped call.

All limits are in Settings → Agent and `~/.diatonic/config.json`.

## Rule-based model routing

Instead of locking all queries to a single model, Diatonic can route each request to the best-suited model based on its intent (`diatonic/agent/router.py`). Configure this in **Settings → Models → Task-based Model Routing**.

The router evaluates requests across five standard tiers in priority order, plus any custom rules:

1. **Custom Rules**: user-defined regex rules (e.g. `\b(sql|postgres)\b` → specialized code model).
2. **App Creation (`builder`)**: requests to build, create, or modify apps and trackers (e.g. "create an app to track my habits").
3. **Deep Research & Code Analysis (`complex`)**: multi-file refactoring, deep architectural inquiries, complex diff analysis, or deep research tasks.
4. **UI Navigation (`ui`)**: lightweight layout management, theme adjustments, and window zooming (e.g. "zoom the calendar", "split mail", "toggle sidebar").
5. **Multi-App Workflows (`multi_app`)**: tasks requiring cross-app coordination and multiple tools (e.g. "check my calendar for conflicts and draft email responses").
6. **Trivial Tasks (`trivial`)**: greetings, short factual lookups, or simple single-app commands.
7. **Default**: falls back to the globally selected model.

When a message arrives, `agent/runs.py` classifies the task, resolves the model (local Ollama, cloud API, or Antigravity), tags the run metadata, and dispatches execution via `agent/loop.py`. You can test routing patterns in real time using the prompt tester in Settings → Models.

## Antigravity and MCP dual-mode permissions

When Antigravity (`agy`) is selected, Diatonic communicates with it over the Agent Client Protocol (ACP) via `agent/acp.py` and `agent/antigravity.py`. Each conversation runs in an isolated subprocess (`agy acp --sandbox`). Diatonic simultaneously runs a local Streamable HTTP MCP server (`diatonic/mcp.py`) exposing enabled apps as tools to the external agent with per-conversation bearer authentication.

In **Settings → Models → Antigravity Permissions**, you can choose how Antigravity handles permissions:

- **Strict**: Every file read/write, skill execution, and tool execution prompts an approval card in Diatonic.
- **Auto (Like Local Models)**: Safe file reads and writes inside shared folders, skill executions, and read-only tools run automatically without interrupting you with approval cards. Sensitive and destructive actions (sending emails, deleting files, shell commands) still produce approval cards.
- **Command Guardrails**: Regardless of permission mode, desktop launcher commands (`open -a`, `osascript`, `kill`, `ps`) and direct access to `~/.diatonic` are intercepted and blocked by strict guardrails.

## Adding an app

Create `diatonic/apps/<name>/__init__.py` exposing `create(ctx)`; it is discovered automatically.

```python
# diatonic/apps/bookmarks/__init__.py
from ..base import App, Tool, WRITE

class BookmarksApp(App):
    id, name, icon = "bookmarks", "Bookmarks", "box"   # icon: any key in web/js/icons.js
    description = "Save links for later."
    keywords = ["bookmark", "link", "save for later"]  # requests mentioning these get this app's tools
    private = True                                      # a cloud model must ask before using it
    settings = [{"key": "api_key", "label": "API key", "secret": True}]   # shown in Settings → Apps

    def tools(self):
        return [
            Tool("add_bookmark", "Save a link.",
                 {"properties": {"url": {"type": "string"}}, "required": ["url"]},
                 self.add_bookmark, WRITE, describe=lambda a: f"Save {a['url'][:60]}"),
        ]

    async def add_bookmark(self, args):
        ...                                              # raise ToolError("message") to report failures
        return {"saved": True}

def create(ctx):
    return BookmarksApp(ctx)
```

- Tools are exposed to the model as `<app>_<tool>` and validated against their JSON Schema before running.
- Add `routes()` for backend endpoints (mounted at `/api/apps/<id>/`) and set `panel = True` plus `web/apps/<id>.js` (exporting `render(el, ctx)`) for a side panel. Export `navigate(params, ctx)` too and the agent can open items in it; `current()` tells the agent what "this" refers to.
- Keep tool descriptions short and the tool count modest; a 4B model chooses better from fewer, sharper tools. An app with many tools can override `tools_for(text)` to offer only those a request needs. Building an app does not use it: that is a builder session with a fixed toolset (`apps/ui/builder.py`).
- For a web API you don't need code at all: ask the agent to build it (see [Making it yours](customizing.md#apps-on-web-apis-built-by-the-agent)).

## Layout

```
diatonic/
  server.py            Starlette app, SSE event stream, static UI
  mcp.py               Streamable HTTP MCP server exposing apps as tools to outside agents
  agent/               loop.py (tool routing, budget), guards.py, approvals.py, runs.py, session.py,
                       router.py (rule-based model routing), acp.py (Agent Client Protocol client),
                       antigravity.py (sandboxed runner for agy),
                       providers/ (ollama.py, openai.py for Gemini, Groq, and antigravity.py)
  models.py            saved models, switching, keys per provider host
  mods.py              interface changes the agent made, with undo history
  google/              multi-account OAuth, tokens, authenticated requests
  apps/                base.py, registry.py, ui/ (app.py show and layout, looks.py theme and styles, builder.py building apps), shell/ (Terminal + agent shell), github/, gcal/, gmail/, files/, …
  shell/               classify.py (read-only / approve / refuse), runner.py (agent commands), terminal.py (PTY sessions)
  voice/               stt.py (Whisper), tts.py (Piper), summarizer.py, models.py
  web/                 index.html, css/, js/ (ES modules), apps/ (panels)
tests/
```

Config lives in `~/.diatonic/config.json` (override the directory with `DIATONIC_HOME`); conversations and data-app items in `~/.diatonic/diatonic.db`; interface changes and app definitions in `~/.diatonic/ui/mods.json`. Keys and tokens are in the macOS Keychain.

## Development

```bash
uv pip install -p .venv -e ".[dev]"
.venv/bin/python -m pytest
```
