# Diatonic

A local, modular agentic harness. One configurable agent, many **apps** (GitHub, Google Calendar, …), voice in and a short spoken summary out. Everything runs on your machine at **http://localhost:5005**.

- **Apps:** GitHub, Google Calendar, **Scheduler** (reminders and scheduled prompt/script jobs), **To-dos** and **Notes**, all controllable by the agent and by hand.
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

## Telemetry: see how fast and reliable it is

The **Telemetry** app (pulse icon in the left rail) shows, for the last 6 hours to 30 days: runs, median and 90th-percentile time, time to first output, generation speed, prompt size, **estimated cache reuse**, tool error rate, guard events, where the time goes (reading prompts, writing, tools, model loading), the last 40 runs as stacked bars, per-tool stats, and the slowest/recent/problem runs. Click a run for its timeline (each model call with prompt tokens, cache estimate and output tokens, each tool with its duration). Every reply also carries a faint line such as `5.4 s · 1.6k in · cache ~94% · 60 out`, and you can ask the agent "how fast has it been today?".

It costs essentially nothing: a run only appends a few small records in memory, token counts and durations come free from Ollama's final stream chunk (no extra model calls), and there is **one small database write per run**, measured at about 0.4 ms against runs that take seconds. A failure while recording can never affect a run. Rows are kept 30 days (max 5,000). Turn recording off, or stop storing the first 160 characters of each request, from the panel's footer; **Clear data** wipes it. The cache figure is an estimate (Ollama reports the full prompt size but only the time spent on the uncached part). Planned work that this data should drive is in [`todo.md`](todo.md).

## Memory across chats (small and bounded)

Two things keep the agent from repeating the same mistakes, and neither grows into a prompt burden:

1. **Forgiving tool arguments (fix at the source).** Most "lessons" are really harness bugs. A model asking for `limit=50` when a tool's maximum is 30 used to cost a failed call and a retry in *every* chat. Now harmless slips (a number over the maximum, `"5"` for 5, `"High"` for `high`, a comma-separated string for a list) are corrected silently, and the model is told when a value was clamped so it doesn't claim more results than it got. The GitHub limits also now match what GitHub allows (100).
2. **A tiny memory for what code can't fix** (Settings → Memory, `/remember …`, or "remember that …" in chat, which asks first):
   - **Facts** about you or your setup ("my default repo is sahil-deo/Mid-Map"), 200 characters each, 30 at most. Per request only facts for *everywhere* plus the apps that request involves are added to the prompt, capped at 8 facts / 600 characters (about 150 tokens). With no memory the prompt is byte-identical to before, so Ollama's cache still works.
   - **Learned tool quirks.** When a tool *rejects* an argument and the retry with a different number, switch or fixed choice *works*, the harness records that for that one tool and appends it to that tool's description only. Learned without a model call, only from numbers/booleans/choices (never free text from a web page or an issue), never from outages or auth errors, shown in the chat as "Learned for next time: …", de-duplicated, forgotten after 90 days unused.
   - You can see, edit, pin, disable or delete everything. Turn memory or auto-learning off in the same tab. Memory is only text in the prompt: it can't change approvals or any setting.

## Make it yours: quick actions, `/` commands and interface

**Saved prompts and script buttons** (Settings → Actions, or **+ Add** on the bar above the message box). Each one can be a chip on the bar, a `/command`, a keyboard shortcut, or all three.

- **Saved prompt:** text that is dropped into the message box (so you can tweak it) or sent straight away, optionally in a fresh chat. Placeholders: `{{input}}` (whatever you type after the command), `{{date}}`, `{{time}}`, `{{day}}`, `{{clipboard}}`. Example: `/summarize <paste text>` fills in `Summarize in 3 bullets: …`. A chip whose prompt needs `{{input}}` asks for it first.
- **Script button:** runs a shell command from a saved action and streams its output into the chat as a live card (with Stop, Copy and *Send output to the agent*). Your input arrives as `$1` and `$INPUT`; it is never pasted into the command text, so it can't inject code. Optional working folder, timeout and "ask before running". Scripts need **Settings → Automation → Allow scripts** (the same switch as scheduled script jobs; off by default). The server only ever runs a *saved* action by id, never a command sent by the browser, and the agent has no tool to create or run these.
- **`/` menu:** type `/` for your actions plus built-ins: `/new`, `/settings [tab]`, `/theme dark|light|system`, `/speak on|off`, `/panel <app>`, `/sidebar`, `/stop`, `/actions`, `/help`. Arrow keys to move, **Tab** to complete, **Enter** to run (`/say hello` passes "hello" as the argument), **Esc** to close.
- **On the bar:** drag chips to reorder, right-click to edit, press a chip's shortcut from anywhere. **Add examples** loads a few starters.

**Interface** (Settings → Interface), all applied live: theme, accent colour, density, text size, font, corner roundness, chat width, show/hide the sidebar (⌘B), quick-action bar, status bar, spoken-summary button and empty-chat suggestions, reorder or hide apps in the left rail, and a custom-CSS box for anything else (`--accent`, `--bg`, `--surface`, … are all variables). **Export** saves your appearance and actions to a JSON file; **Import** restores it (validated first, so a bad file changes nothing).

## Reminders, scheduled jobs, to-dos and notes

Say what you want in plain words ("remind me to call mom tomorrow at 9", "every weekday at 8 give me my to-do briefing", "add a to-do to send the invoice by Friday", "jot down that the wifi password is on the fridge") or use the panels in the left rail.

**Scheduler** (`clock` icon)
- **Reminders:** a notification at a time you give. Times are parsed by code, not by the model: `in 20 minutes`, `tomorrow 9am`, `friday 5pm`, `2026-11-20 15:00`, `every weekday at 9`, `every 30 minutes`, `monthly on the 1st at 8am` or a 5-field cron like `0 9 * * 1-5`.
- **Prompt jobs:** run an AI prompt on a schedule in their own chat (named `⏰ <job> · <time>`) and notify you with the result. They are **read-only**: the tools that change things aren't even offered. In the panel you can tick *Allow it to propose changes*; writes still need approval, and with nobody there they time out and are denied. Only the UI can grant this, never the agent.
- **Script jobs:** run a shell command on a schedule (timeout, killed as a whole process group, minimal environment, output captured, notification on failure). **Off by default**: turn them on in Settings → Automation. If the agent proposes one you get an approval card with the exact command.
- **Reliability:** everything is stored in SQLite. After downtime a missed one-off reminder fires once, marked as late; a missed recurring job fires once and then resumes on schedule; a job still running is skipped rather than doubled. Run history is kept per job.

**To-dos:** title, due date, priority, optional reminder at the due time (completing or deleting the to-do cancels its reminder).

**Notes:** plain markdown files in `~/Documents/Diatonic Notes` (change it in Settings → Apps → Notes, or point it at an existing folder). Searchable by the agent, editable in the wide Notes panel with autosave and preview. Deleting moves a note to `.trash/` inside that folder.

**Notifications** go to an inbox (bell in the top bar, with Snooze and Done) and, per Settings → Automation, to macOS Notification Center (works with the browser closed; banners appear under "Script Editor" because they are sent with AppleScript), browser notifications while the page is open in the background, and spoken aloud when the spoken-summary toggle is on. Quiet hours silence banners and speech, not the inbox.

### Keep it running in the background

Reminders and jobs only fire while the server runs. To run it at login and restart it if it crashes (macOS):

```bash
.venv/bin/diatonic install-service        # add --print to just see the launchd plist
.venv/bin/diatonic service-status
.venv/bin/diatonic uninstall-service
```

Logs go to `~/.diatonic/logs/`. If you run `.venv/bin/diatonic` by hand as well, stop it first, since both use port 5005.

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
| ⌘B | Show/hide the sidebar |
| ⌘⇧O | New chat |
| / | Focus the composer; typing it in the message box opens commands and saved prompts |
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
