# Diatonic TODO

The plan for making this harness good: **hackable** (the agent and the user can build real apps and reshape the UI) and **fast and reliable** on a small local model. Use the **Telemetry** panel to decide what is worth doing and to prove it worked; items say how to measure them.

Design rationale lives in two notes:
- [`hackable-todo.md`](hackable-todo.md): why agent-built apps fail today (no feedback loop, undocumented SDK, code in a single tool argument) and the fixes.
- [`panel-ui-todo.md`](panel-ui-todo.md): the panel workspace design (layout tree, tabs, command registry, keymap, launcher, status bar pins, macros).

## Working assumption

**The 4B local model is the only model we can use.** Nothing here may depend on a bigger model writing the code. So the harness, not the model, has to carry the difficulty: small composable pieces, templates to copy, and fast test feedback, rather than asking a 4B to write raw DOM.

## Baseline (measured 2026-11-15)

Apple M4, 16 GB, `qwen3:4b-instruct`, Ollama 100% on GPU.

- Prompt processing about **190-290 tokens/s**, generation about **36 tokens/s**. A cached identical prompt: 0.2 s instead of 7 s for 1.3k tokens.
- A typical lookup ("list my 3 most recent github repos") is **two model calls** of about 1.7k tokens each. Cold: 30 s. Warm cache: 5.4 s.
- Over a 5-run sample: reading prompts 47% of wall time, writing 26%, model loading 14%, tools 5%.

Targets: first message in a chat under 6 s, follow-ups under 3 s, plain lookups under 2 s.

---

## Active & Open Items

### Real Data Migration
- [ ] **v3 Phase 0, real data:** run `scripts/export_trackers.py --home ~/.diatonic --delete` with the server stopped (not done: touches the user's real data).

### Build Reliability & Eval Follow-ups
- [ ] Re-run build eval after recent fixes and add edit/delete prompts; test model-written tool arguments over 4096 tokens.
- [ ] Small-model value editing: when the 4B is asked to *change a value* inside a panel it sometimes adds a feature around it instead. Ideas if it matters: a `summary`-first prompt ("say what you will change, then change it"), or an edit tool shaped as "set <thing> to <value>" for numbers and labels.

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
- **Free-floating windows** in the panel workspace (tiling and tabs first).
- **PWA/Tauri wrapper** (browser tab only for now, so ⌘W/⌘T/⌘N/⌘1-9 stay unavailable, hence the leader key).
