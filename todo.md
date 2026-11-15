# Diatonic TODO

Ideas and follow-ups, in rough priority order. Use the **Telemetry** panel (left rail) to decide what is worth doing and to prove it worked: every item below says how to measure it.

## Baseline (measured 2026-11-15)

Apple M4, 16 GB, `qwen3:4b-instruct`, Ollama 100% on GPU.

- Prompt processing about **190–290 tokens/s**, generation about **36 tokens/s** (microbenchmark). An identical prompt served from Ollama's prefix cache: 0.2 s instead of 7 s for 1.3k tokens. (An earlier note here assumed 50 tokens/s CPU; that was wrong.)
- A typical lookup ("list my 3 most recent github repos") is **two model calls** of about 1.7k tokens each. Cold or cache-missed: 30 s. Repeated with a warm cache: 5.4 s (cache about 94%).
- "hello, what is 2+2?" offered every tool: **4.8k-token prompt, 19.9 s, cache 0%**. This is the "no keyword matched, offer everything" fallback.
- Over a 5-run sample: reading prompts 47% of wall time, writing 26%, model loading 14%, tools 5%.

Targets: first-message-in-chat under 6 s, follow-ups under 3 s, plain lookups under 2 s.

---

## P1: latency (biggest wins first)

- [ ] **Cache-friendly, append-only context.** Each turn we rebuild the history from the database, so it differs slightly from what was sent last time (the `(Now: …)` suffix, trimming, tool-trace replay) and Ollama re-reads the whole thing. Keep each chat's exact message list and only append to it; compact once, deliberately, when it outgrows the budget. *Expected:* follow-up messages drop from about 5–15 s of prompt reading to roughly zero. *Measure:* Telemetry "Cache reuse" and per-run prefill time for 2nd+ messages in a chat.
- [ ] **Skip the second model call for plain lookups.** For list/show/what's-on requests, render the tool result with code (calendar events, to-dos, repos, reminders, notes) and show it immediately; only call the model when the request needs reasoning ("which is most urgent?", "summarize"). *Expected:* about 3 s saved per lookup, and no chance of the model misreporting the data. Needs a per-tool `render()` and a cheap "does this need the model?" rule.
- [ ] **Tool-level routing.** Replace per-app keyword routing with a local scorer (no model) that ranks individual tools and offers the top 6–8. Today a reminder request still pulls in 18 tools. *Measure:* "Tools offered" and prompt size in run detail.
  - [ ] Fix keyword overlaps (the calendar app lists "remind", which drags it into every reminder request).
  - [x] **Replaced the "offer every tool" fallback**: requests that name no app get their best-matching tools by word overlap (none for chit-chat), unoffered real tools still run if the model calls them, apps can narrow their own tools per request (`tools_for`), and a budget check trims tools so a prompt never exceeds `num_ctx` (the 9.3k-token overflow).
  - [ ] Optional: a tiny constrained classification call (enum output, about 8 tokens) for ambiguous requests only. Never on every request.
- [ ] **Code-parsed fast paths for daily commands.** "Remind me in 20 minutes to …", "add a to-do …", "what's on my calendar today" can be recognised and executed in about 0.1 s with the existing time parser, falling back to the model for fuzzy phrasing. Show what was understood and keep approvals for anything that changes data.
- [ ] **Eval harness.** About 40 realistic requests with the expected tool and arguments, run against the real model, comparing routing strategies and prompt changes (accuracy, steps, tokens, seconds). Store results; run nightly with our own scheduler; show regressions in the Telemetry panel. Every change in this file should be judged with it.

## P2: speed and capability

- [ ] **Stop reloading the model.** Model loading was 14% of sampled time. `keep_alive` is 30 minutes; with 16 GB consider keeping it loaded much longer (or forever).
- [ ] **Pre-warm the prompt cache.** After idle or restart, send a 1-token request with the most likely prefix (the last-used tool set) so the first real message starts warm. A gamble that depends on predicting the tool set, so do it after the routing work.
- [ ] **Benchmark Ollama settings.** Add `scripts/bench_ollama.py` (the 1.3k-token benchmark used above) and compare `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`, and `num_ctx` (8192 vs 4096). Also try MLX-based runtimes. Unmeasured; may be a free 10–30% on prompt processing.
- [ ] **Model escalation instead of multi-agent.** Keep the 4B for everything; when guards fire (loop, repeated failures, step limit) or a request needs planning, retry that one request on an 8B model (about 5 GB; fits alongside the 4B in 16 GB, roughly 2× slower, much better at multi-step work). Run scheduled prompt jobs on the 8B since nobody waits for them. Downloading the model needs a decision first.
- [ ] **Answer grounding check.** Verify in code that the numbers and names in the final answer appear in the tool results; if not, fall back to the direct rendering above or regenerate once. Targets the made-up "I've retrieved your repos…" answers seen earlier.
- [ ] **Constrained decoding for tool calls** (Ollama `format` with a JSON schema: tool name enum plus arguments). Only worth it if Telemetry shows malformed or invalid calls, so first add a counter for them.
- [ ] **Parallel tool execution** when the model emits several independent read calls in one step.
- [ ] **Reference resolution.** "Close the second one", "snooze that" resolved against the last results in code (numbered handles), not by the model.
- [ ] **Rolling state instead of history replay.** A short structured "what we were just doing" (last entities and results) in place of replaying old turns; cheaper and more cache-stable than summarising with the model.

## Interface control follow-ups

- [x] Agent opens apps, items and layouts (`ui` tools, `navigate()` hooks in panels).
- [x] **Always-visible bottom dock** (text, mic, latest reply, approvals) whenever a panel covers the chat. Not in the dock yet: `/` commands, `@` file mentions, quick-action buttons, and a narrow-screen version (under 820px the chat is shown instead).
- [x] **Agent-made interface changes** (`set_theme`, `add_style`, `add_script`, `add_panel`, undo) with approval cards showing the code, refused in any run that has read mail. Taint is per app tool (`untrusted_tools`); only Gmail declares any so far, so web or GitHub issue text isn't covered yet.
- [x] Declarative top-bar buttons (`ui_add_button`), rail icon show/hide and chat-list width in `set_theme`, scripts cleaned up and re-run on change (no reload), syntax warning on code approval cards, existing mod names shown to the model so edits replace instead of duplicating.
- [x] Screen context ("summarize this email") and a looser retry for over-specified mail searches.
- [x] Interface tools only report what they actually did (ignored options are named so the model can't claim them); Files takes folder paths and forgives near misses for reading (never for changes, never around the block list); a follow-up that names no app gets the on-screen app's tools.
- [x] **API apps** (`apiapps.py`): web-API apps as data, built step by step by the agent with immediate test calls; generic panel; server-side calls with host/SSRF/size limits; key in keychain. Verified end to end with Open-Meteo on the real model.
- [x] **Data apps** (`dataapps.py`): personal apps with no API or code (lists, typed fields, nesting), generic panel, generated tools found by name, agent deletes always ask. Verified end to end (exam tracker) on the real model.
- [ ] Data apps: sorting/filter views, due-date reminders through the scheduler, export as Markdown/CSV, reordering by drag.
- [ ] API apps: OAuth, pagination, import from an OpenAPI file (would make building almost fully code-driven), POST bodies beyond flat fields, eval over ~8 public APIs.
- [x] **Saved models + cloud providers**: status-bar switcher, Settings → Models (Ollama, Gemini via AI Studio, Groq, any OpenAI-compatible), keys in keychain, private apps behind approval for cloud models. Cloud verified only with mocked APIs so far: needs a real Gemini and Groq key test.
- [ ] Models: per-model sampling settings, token/cost totals per cloud model in Telemetry, automatic fallback to the local model when a cloud API is down.
- [x] **Home screen tiles**: layouts (4 small, 2 stacked, 2 side by side, 1 big, suggestions), a snapshot per app (to-dos, calendar, mail, scheduler, GitHub, telemetry, notes, data and API apps), Customize, and `ui_set_home` for the agent.
- [ ] Home: drag tiles to reorder, per-tile options (which mail account, which repo), more than 4 tiles.
- [x] **Shell**: Terminal panel (PTY sessions over SSE + POST, xterm.js), agent `shell_run` with parsed read-only/approve/refuse classes, audit log, scheduled-job pre-approved commands. Verified with the real model (du, git status, versions, injection file).
- [ ] Shell: spawn PTYs without fork in a threaded process (Python warns about `forkpty`), clickable links and search in the terminal, per-folder allow rules ("always allow `npm test` in Diatonic").
- [ ] **Answer grounding check** is still the general fix for confident claims: compare what the final answer says happened with the tool results.
- [ ] Mods: syntax-check scripts server-side (a broken script is only noticed after it runs), a diff when a mod is updated, and export/import of mods.
- [x] Navigation, event/mail/note opening, dock approvals and layout modes verified in Chrome over the DevTools protocol (real model, real accounts). Still to check in Zen.
- [ ] Code-parsed fast path for plain "show/open <app>" (no model call at all).

## Google (Calendar and Mail) follow-ups

- [ ] **Verify against real Google.** Everything was built and tested against a local fake of the OAuth, Calendar and Gmail APIs (plus the real model); connect a real account and check the consent flow, token refresh, restricted Gmail scopes in Testing mode, shared calendars, conferenceData (Meet), HTML mail from real senders, and large mailboxes.
- [ ] **Taint tracking for mail.** The model reads untrusted email; sending already always asks. Next step: warn on the approval card when a recipient or link came only from email content (not from the user's message or earlier mail with that person), and refuse auto-approval of sends in any run that read mail.
- [ ] Mail: threading for drafts (edit/send a saved draft), signatures, labels management (create/rename/apply), snooze, mute, "unsubscribe" helper, attachments on compose, rich-text compose, offline cache and background sync, unread badge on the rail icon.
- [ ] Calendar: working-hours and "free/busy" overlay across accounts in the grid, drag-copy, event colours, multiple reminders, availability suggestions in the create dialog ("find a time" using every account), time-zone display for other zones, printing, and push/incremental sync instead of refetching each view.
- [ ] Per-account permissions in the agent: let one account be read-only for the agent even if connected with more access.

## Files app follow-ups

- [ ] **Edit reliability on a 4B model.** On the real model a line-range edit dropped a needed line (the approval diff caught it, which is what the preview is for). Ideas: prefer `replace_text` for single-line changes, re-read the file right before editing, apply and re-verify (re-parse Python/JSON after the change), or let the harness compute the line range from an "old text" the model supplies.
- [ ] **Answer grounding for file claims.** The model once reported "File created: notes/todo.md" after calling the wrong tool; check that paths and counts claimed in the answer appear in the tool results (see the grounding item above).
- [ ] Verify the embedded PDF viewer in Zen/Firefox (it rendered blank in the Chromium preview pane here; the Text tab is the fallback).
- [ ] Optional: manual edit/save in the viewer, rename/move, new-folder, drag-and-drop a file onto the chat to reference it, CSV as a table, git status badges, `.gitignore`-aware listing and search.
- [ ] Per-file token estimate in the `@` menu, and a setting for how much of a referenced file is inlined.

## Telemetry follow-ups

- [ ] Count malformed/invalid tool calls and tool-selection misses separately (feeds the constrained-decoding decision).
- [ ] Record approval wait time (currently lands in "Other") so human latency doesn't pollute model latency.
- [ ] Real token-level cache accounting (Ollama tokenize) instead of the speed-based estimate; calibrate `prefill_baseline` automatically.
- [ ] Per-app and per-routing-strategy breakdowns, and before/after comparison when a setting or prompt changes.
- [ ] Regression alerts (median time up, cache down, error rate up) through the notification inbox.
- [ ] CSV/JSON export.

## Verification debt (built, but never exercised for real)

- [ ] Push-to-talk and microphone capture in Zen/Firefox; Whisper `small.en` (about 500 MB) is not downloaded yet, and the Piper voice only in a throwaway test home.
- [ ] Spoken summaries and reminders through Piper in the browser.
- [ ] Google Calendar OAuth end to end with a real account (the app has only been exercised "not connected").
- [ ] macOS Notification Center banners (tests deliberately never fire real ones), browser notifications, quiet hours.
- [ ] Background service: `diatonic install-service` / `service-status` / `uninstall-service` (the launchd plist is generated and tested, never installed).
- [ ] Quick-action bar drag-to-reorder, per-action keyboard shortcuts, and Import in Settings → Interface.
- [ ] Notes folder pointed at an existing folder of markdown files.
- [ ] Files: sharing a real folder, the `@` picker and chips in Zen, PDF viewing in Zen, restoring from History, Trash on a non-sandbox HOME.
- [ ] Learned tool quirks ("Learned for next time") firing on a real rejection; so far covered only by tests.

## Parked / decided against (for now)

- **Classify-then-hand-off multi-agent.** On this hardware each hop is another full model call, the classifier prompt evicts Ollama's prefix cache, and cross-app requests need the union of tools anyway. Revisit with a stronger planner model; escalation (above) captures most of the benefit.
- **Spoken approvals.** A misheard word shouldn't be able to authorise a change.
- **Agent-created or agent-run script buttons.** Scripts only run from saved actions you create in the UI.
