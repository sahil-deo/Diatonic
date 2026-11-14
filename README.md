# Diatonic

A local, modular agentic harness. One configurable agent, many **apps** (GitHub, Google Calendar, …), voice in and a short spoken summary out. Everything runs on your machine at **http://localhost:5005**.

- **Agent:** Ollama with `qwen3:4b-instruct` by default; provider, model and prompts are configurable in Settings.
- **Apps:** each app is a self-contained folder that contributes tools to the agent and an optional side panel to the UI. Adding one never touches core code.
- **Parallel chats:** every chat runs as a background task on the server. Start one, switch to another, even close the tab; runs keep going and you're alerted when one finishes or needs approval.
- **Safe by default:** anything that changes data asks for your approval first (per-tool "always allow" is available).
- **Voice:** push-to-talk dictation with local Whisper (`small.en`); an optional spoken *summary* (not the whole reply) with local Piper.
- **Lightweight:** Python backend on Starlette; vanilla HTML/CSS/JS frontend (no React, no build step, no frontend dependencies).

## Quick start

Requirements: Python 3.11+ (3.12 recommended for voice), [uv](https://docs.astral.sh/uv/), and [Ollama](https://ollama.com).

```bash
ollama pull qwen3:4b-instruct

uv venv --python 3.12 .venv
uv pip install -p .venv -e ".[voice]"      # drop [voice] if you don't want voice
.venv/bin/diatonic --open                  # http://localhost:5005
```

The server binds to `127.0.0.1` only, and rejects requests with a foreign `Host` or `Origin` header (DNS-rebinding and cross-site protection).

### Voice setup

```bash
.venv/bin/diatonic setup-voice             # or Settings → Voice → Download voice models
```

Downloads Whisper `small.en` (about 500 MB) and the Piper voice `en_US-lessac-medium` (about 60 MB) into `~/.diatonic/models/`. Piper's code is GPL-licensed; that doesn't matter for personal use, but check it if you redistribute.

- **Talk:** hold **⌥⇧Space** (rebindable in Settings → Voice), or click the mic and just speak. The transcript lands in the composer for review; turn on *Send voice input automatically* to skip that.
- **Spoken summary:** toggle it in the top bar. When a run finishes, the agent says one or two short sentences, only if something is worth saying: a run that used apps, needed approval, failed or was slow. Trivial chat stays silent, and the model can answer `NONE` to stay quiet. You can tune this under *When to speak*.
- If Piper isn't installed the app falls back to your system voice.
- Dictation always runs server-side Whisper, so it works the same in Firefox/Zen, Chrome and Safari. Browsers only ask for mic permission once for `localhost:5005`.

## Connecting apps

### GitHub
Settings → Apps → GitHub. Paste a personal access token with the `repo` scope, or leave it empty to reuse your `gh auth login` session. Optionally set a default owner so "my-repo" resolves to "owner/my-repo".

### Google Calendar
1. In [Google Cloud Console](https://console.cloud.google.com/) create a project and enable the **Google Calendar API**.
2. *APIs & Services → OAuth consent screen*: configure it (External is fine; add yourself as a test user).
3. *Credentials → Create credentials → OAuth client ID → Desktop app*.
   (If you pick *Web application* instead, add `http://localhost:5005/api/apps/gcal/auth/callback` as an authorized redirect URI.)
4. Paste the client ID and secret into Settings → Apps → Google Calendar, save, then click **Connect Google**.

Credentials and tokens are stored in the macOS Keychain (fallback: `~/.diatonic/secrets.json`, mode 0600).

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

On CPU the cost is mostly prompt processing, so the harness keeps prompts small and stable:

- **Tool routing:** only the apps your message names (plus those already used in the chat) expose tools: about 750 to 1,000 tokens instead of 2,000+. Add `keywords` to a new app to take part.
- **Compact schemas and results:** self-explanatory parameter descriptions are dropped, tool results are compacted and clipped, history is capped by characters, and only the latest tool-using answer replays its tool trace.
- **KV-cache friendly:** the system prompt and tool list stay byte-identical between turns (the clock rides on the user message), so Ollama reuses its cache. Chat within one app is much faster than alternating apps.
- **Sampling tuned for Qwen3-instruct** (temperature 0.6, top-p 0.8, top-k 20, repeat penalty 1.05) instead of the near-greedy settings that make small models loop. Spoken summaries use a tiny capped call.

All limits are in Settings → Agent and `~/.diatonic/config.json`.

## Approvals

Approvals work in any chat, including background ones. Every tool declares a risk level: `read` runs immediately; `write` and `destructive` show an approval card (**⌘↵** approve, **Esc** deny) with exactly what will happen. Voice input never approves anything, so a mis-heard word can't merge a PR. Mark specific tools *always allow* under Settings → Apps.

## Keyboard

| Key | Action |
|---|---|
| ⌘K | Command palette |
| ⌘, | Settings |
| ⌘⇧O | New chat |
| / | Focus the composer |
| Hold ⌥⇧Space | Push-to-talk |
| Enter / Shift+Enter | Send / new line |

## Adding an app

Create `diatonic/apps/<name>/__init__.py` exposing `create(ctx)`; it is discovered automatically.

```python
# diatonic/apps/notes/__init__.py
from ..base import App, Tool, WRITE

class NotesApp(App):
    id, name, icon = "notes", "Notes", "box"       # icon: any key in web/js/icons.js
    description = "Jot things down."
    settings = [{"key": "api_key", "label": "API key", "secret": True}]   # shown in Settings

    def tools(self):
        return [
            Tool("add_note", "Save a note.",
                 {"properties": {"text": {"type": "string"}}, "required": ["text"]},
                 self.add_note, WRITE, describe=lambda a: f"Save note “{a['text'][:60]}”"),
        ]

    async def add_note(self, args):
        ...                                          # raise ToolError("message") to report failures
        return {"saved": True}

def create(ctx):
    return NotesApp(ctx)
```

- Tools are exposed to the model as `<app>_<tool>` and validated against their JSON Schema before running.
- Add `routes()` for backend endpoints (mounted at `/api/apps/<id>/`) and set `panel = True` plus `web/apps/<id>.js` (exporting `render(el, ctx)`) for a side panel.
- Keep tool descriptions short and the tool count modest; a 4B model chooses better from fewer, sharper tools.

## Layout

```
diatonic/
  server.py            Starlette app, SSE chat stream, static UI
  agent/               loop.py, approvals.py, providers/ (ollama.py)
  apps/                base.py, registry.py, github/, gcal/
  voice/               stt.py (Whisper), tts.py (Piper), summarizer.py, models.py
  web/                 index.html, css/, js/ (ES modules), apps/ (panels)
tests/
```

Config lives in `~/.diatonic/config.json` (override the directory with `DIATONIC_HOME`); conversations in `~/.diatonic/diatonic.db`.

## Development

```bash
uv pip install -p .venv -e ".[dev]"
.venv/bin/python -m pytest
```

## Troubleshooting

- **"Can't reach Ollama":** start it with `ollama serve` or open the Ollama app; check the endpoint in Settings → Agent.
- **First reply is slow:** the model is loading into memory; it stays warm for 30 minutes afterwards.
- **Mic blocked:** click the permissions icon in the address bar and allow the microphone for `localhost:5005`.
- **Push-to-talk shortcut does nothing:** your browser may reserve the combination; click the field in Settings → Voice and press another.
- **`⌘K` opens the browser search bar:** the page overrides it, but if your browser doesn't allow that, use the command button in the top bar.
