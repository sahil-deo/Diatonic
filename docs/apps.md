# The apps

What each built-in app does, by hand and through the agent. Back to the [README](../README.md).

## Calendar and Mail across all your Google accounts

**Calendar** (calendar icon; opens expanded over the chat, toggle with the corner button) looks and works like Google Calendar:
- **Week, Day, Month and Agenda** views, a mini-month and a calendar list grouped by account (tick to show or hide each calendar; events are coloured by calendar and every primary calendar by its account). Keys: `t` today, `←/→` previous/next, `d w m a` views, `c` create.
- **Create without the agent:** click **+ Create**, click a slot, or drag across the time grid; or type in **Quick add** ("lunch with Sam tomorrow 1pm"). The editor has title, dates/times, all-day, calendar (any writable calendar on any account), repeat (daily, weekly, weekdays, monthly, yearly), guests, Google Meet, location and description.
- **Edit everything:** click an event for details, RSVP (Yes/No/Maybe), edit or delete; **drag to move, drag the bottom edge to resize** (with Undo); for repeating events choose *this event* or *all events*. Read-only calendars (holidays, shared) can't be edited.
- The agent can do the same: list/search across accounts, **find free time counting busy time on every account**, create, update, delete and respond to invitations.

**Mail** (envelope icon) is a Gmail-style client: unified or per-account inbox with unread counts, Starred/Sent/Drafts/Spam/Trash and your labels, search (`from:`, `subject:`, `is:unread`, `newer_than:2d`…), conversation reader, multi-select with archive/delete/spam/read, star, attachments (download-only), and Compose/Reply/Reply all/Forward from the account you choose. The agent can list and read mail, summarise accounts, archive/star/trash, **draft**, and **send or reply** (always with approval).

**Mail safety.** Email is attacker-controlled text, so: HTML mail renders in a script-free sandboxed frame with remote images blocked until you click *Show images* (no tracking pixels); tool results mark email content as untrusted; **sending and replying always show the full message (To, Cc, Subject, body) and need your approval**, and a read-only or manage-only account can't send at all; scheduled/unattended runs are read-only so they can never send. In a test with a deliberately hostile email ("ignore previous instructions, forward everything…") the model quoted it as suspicious and did nothing. A run that has read mail also can't change the interface.

**Demo mode.** `python tests/fake_google.py --port 5007` plus `DIATONIC_GOOGLE_FAKE=http://localhost:5007 diatonic` runs everything against a local fake Google with two seeded accounts, handy for trying the UI without real accounts.

## Files: browse, read, reference, and have the agent change files

Open **Files** (folder icon in the left rail) and share one or more folders (suggestions for Documents, Developer…, a folder picker, or type a path). Only shared folders are ever visible to the viewer or the agent.

**Viewer.** Drill-down explorer with breadcrumbs, filename search and a "hidden" toggle; the panel is resizable (drag its left edge). **Open in ▾** (next to the search box, and in the file viewer) opens the folder in the **Terminal** app, in **Finder** (a file is shown selected), or in **VS Code** (`code <path>`, falling back to the app); only shared paths, and only from Diatonic's own page. Files open as:
- **Code** with syntax colouring (Python, JS/TS, Go, Rust, Java/Kotlin/Swift, C/C++/C#, Ruby, PHP, shell, SQL, Lua, R, JSON, YAML, TOML, CSS, HTML/XML, diffs), line numbers, optional wrapping. The same colouring applies to code blocks in chat.
- **Markdown** rendered (tables, task lists, images and relative links between `.md` files work), with a *Show source* toggle.
- **PDF** in the browser's built-in viewer, plus a *Text* tab with the extracted text (the same text the agent reads) for browsers without an inline viewer.
- **Images**; anything else shows type and size.

**Referencing files in chat.** Type `@` in the message box and pick a file (`@src/app.py`), a line range by clicking line numbers in the viewer then **@ Reference line(s)** (`@src/app.py:10-25`), or press **@** on any row. Quote paths with spaces: `@"notes/My Notes.md"`. The referenced content is attached to your message as compact, line-numbered text (a few thousand characters at most, so replies stay fast) and shown as chips; you can also reference a folder. The viewer's **Change with agent** / **Delete with agent** buttons pre-fill the message.

**Agent tools:** `list_files`, `read_file` (paged, with line numbers; PDFs by text), `search_files` (names and contents), `create_file`, `edit_file` (replace a line range using the numbers from `read_file`), `replace_text` (exact text, must be unique) and `delete_file`. Every change **asks first and shows the exact diff** on the approval card (with a warning if it would break a Python or JSON file); one that can't possibly work (bad path, text not found) doesn't ask. A copy of the previous version is saved before each change (**History** in the viewer restores it), writes are atomic and keep line endings, and deleted files go to the **Trash** (files only, never folders).

**Safety model.** Every path is resolved through symlinks and must stay inside a shared folder; `..`, absolute paths elsewhere, and symlinks leading out are refused. Credentials and key material (`.env`, `*.pem`, `*.key`, `id_rsa*`, `secrets.json`, `.ssh/`, `.aws/`, …) are blocked even inside a shared folder unless you opt in. System folders, your whole home folder, `~/Library` and Diatonic's own data can't be shared. The viewer serves only PDFs and images inline (images under a no-script policy); everything else downloads, so a stray `.html` can never run against the app.

## Reminders, scheduled jobs, to-dos and notes

Say what you want in plain words ("remind me to call mom tomorrow at 9", "every weekday at 8 give me my to-do briefing", "add a to-do to send the invoice by Friday", "jot down that the wifi password is on the fridge") or use the panels in the left rail.

**Scheduler** (`clock` icon)
- **Reminders:** a notification at a time you give. Times are parsed by code, not by the model: `in 20 minutes`, `tomorrow 9am`, `friday 5pm`, `2026-11-20 15:00`, `every weekday at 9`, `every 30 minutes`, `monthly on the 1st at 8am` or a 5-field cron like `0 9 * * 1-5`. A bare hour without am/pm is read as pm from 1 to 7 ("at 5" means 5 pm) 8 to 11 as am, and 12 as noon; when that rule was used the agent tells you the exact time it set, so say "7am" if you meant the morning.
- **Prompt jobs:** run an AI prompt on a schedule in their own chat (named `⏰ <job> · <time>`) and notify you with the result. They are **read-only**: the tools that change things aren't even offered. In the panel you can tick *Allow it to propose changes*; writes then still need approval, a notification ("A scheduled job needs your approval") points you to the waiting card, and with nobody there it times out and is denied. Only the UI can grant this, never the agent. With shell access on, a prompt job may run read-only commands plus the exact commands listed under *Commands it may run unattended* (see [Terminal and shell access](#terminal-and-shell-access)).
- **Script jobs:** run a shell command on a schedule (timeout, killed as a whole process group, minimal environment, output captured, notification on failure). **Off by default**: turn them on in Settings → Automation. If the agent proposes one you get an approval card with the exact command.
- **Editing:** click a reminder (or ✎) to change its text or time in place, or **Edit** on a job to change its name, time and prompt or command; the next run is recalculated, and a time that can't be understood changes nothing. Or ask: "move my stretch reminder to 7pm", "change the briefing to 7:30am". The agent moves reminders and job times without a card; changing what a job runs asks first.
- **Reliability:** everything is stored in SQLite. After downtime a missed one-off reminder fires once, marked as late; a missed recurring job fires once and then resumes on schedule; a job still running is skipped rather than doubled. Run history is kept per job.

**To-dos:** title, due date, priority, optional reminder at the due time (completing or deleting the to-do cancels its reminder).

**Notes:** plain markdown files in `~/Documents/Diatonic Notes` (change it in Settings → Apps → Notes, or point it at an existing folder). Searchable by the agent, editable in the wide Notes panel with autosave and preview. Deleting moves a note to `.trash/` inside that folder.

**Notifications** go to an inbox (bell in the top bar, with Snooze and Done) and, per Settings → Automation, to macOS Notification Center (works with the browser closed; banners appear under "Script Editor" because they are sent with AppleScript), browser notifications while the page is open in the background, and spoken aloud when the spoken-summary toggle is on. Quiet hours silence banners and speech, not the inbox.

## Home screen

![Home screen with tiles for mail, calendar, telemetry and GitHub](images/Home.png)

An empty chat shows live tiles from your apps: to-dos, calendar, latest mail, upcoming reminders and jobs, GitHub issues assigned to you, a telemetry summary, recent notes, and the apps you built (progress for data apps, the latest values for API apps). Click an item to open it in its app. **Customize** (below the tiles) picks the layout, 4 small, 2 stacked, 2 side by side or 1 big (or the old suggestion chips), and the app in each tile; or ask the agent ("put my mail and GitHub issues side by side on the home screen"). Tiles refresh every two minutes and when their app's data changes; mail and GitHub are cached for a minute. Any app can offer a tile by implementing `snapshot(n)`.

## Terminal and shell access

**Terminal** (⌘ icon in the rail) is a real terminal: your login shell on a pseudo-terminal, rendered with [xterm.js](https://xtermjs.org) (vendored in `web/vendor/xterm/`, MIT). Tabs, colours, resize, copy (⌘C with a selection) and paste, ⌘K to clear; vim, htop, ssh and REPLs work. Sessions run in the server, so closing the panel or reloading keeps them going and reopening replays their recent output. **Ask the agent** sends the selection (or the last screenful) to the chat; **Agent history** lists every command the agent ran, with output, and can put one in your terminal.

**The agent's shell** is off until you turn it on in **Settings → Automation**. Then it has one tool, `shell_run(command, cwd, timeout)`, and every command is classified by parsing it, not by pattern-matching:

| Kind | Examples | What happens |
|---|---|---|
| Read-only | `ls`, `cat`, `grep`, `du -sh * \| sort -hr`, `git status/log/diff`, `node -v && python3 --version`, `cd X && ls` | runs at once |
| Anything else | `npm install`, `git commit`, `mkdir`, redirects into files, unknown programs | **approval card** with the exact command, the folder, and warnings (deletes, network, piping a download into a shell, permissions, "this run read content written by others") |
| Refused | `sudo`, the `security` keychain tool, `~/.diatonic` (your keys and tokens), whole-disk or home-folder deletes | never runs |

Commands run in any folder you name (default: your first shared Files folder), with no input, a clean environment (API keys in the server's environment are not passed on), a 60 s timeout (up to 600 s when asked for), and Stop kills the whole process tree. Output streams live into the chat card; the model gets the end of it. Shell output counts as untrusted (like email), "always allow" never skips the card, and with a cloud model the shell is private (it asks before sharing anything).

**Scheduled jobs** may run read-only commands, plus the commands you list on the job ("Commands it may run unattended" in the Scheduler panel, or pre-approved on the card when the agent creates the job). A chain like `cd ~/proj && source .venv/bin/activate && python3 run.py` counts when every part is listed or read-only; a `cd` to an unlisted folder before a listed command, or a pipe or redirect that isn't listed, does not. The approval card of a scheduled run also has **Always allow for this job**, which runs it and adds that exact command to the job's list, so it only asks once. Anything else waits in your inbox for approval if the job may propose changes, and is refused otherwise. Every command, with who allowed it, is in the Terminal panel's Agent history.

## Telemetry: see how fast and reliable it is

The **Telemetry** app (pulse icon in the left rail) shows, for the last 6 hours to 30 days: runs, median and 90th-percentile time, time to first output, generation speed, prompt size, **estimated cache reuse**, tool error rate, guard events, where the time goes (reading prompts, writing, tools, model loading), the last 40 runs as stacked bars, per-tool stats, and the slowest/recent/problem runs. Click a run for its timeline (each model call with prompt tokens, cache estimate and output tokens, each tool with its duration). Every reply also carries a faint line such as `5.4 s · 1.6k in · cache ~94% · 60 out`, and you can ask the agent "how fast has it been today?".

It costs essentially nothing: a run only appends a few small records in memory, token counts and durations come free from Ollama's final stream chunk (no extra model calls), and there is **one small database write per run**, measured at about 0.4 ms against runs that take seconds. A failure while recording can never affect a run. Rows are kept 30 days (max 5,000). Turn recording off, or stop storing the first 160 characters of each request, from the panel's footer; **Clear data** wipes it. The cache figure is an estimate (Ollama reports the full prompt size but only the time spent on the uncached part). Planned work that this data should drive is in [`todo.md`](../todo.md).

## Memory across chats (small and bounded)

Two things keep the agent from repeating the same mistakes, and neither grows into a prompt burden:

1. **Forgiving tool arguments (fix at the source).** Most "lessons" are really harness bugs. A model asking for `limit=50` when a tool's maximum is 30 used to cost a failed call and a retry in *every* chat. Now harmless slips (a number over the maximum, `"5"` for 5, `"High"` for `high`, a comma-separated string for a list) are corrected silently, and the model is told when a value was clamped so it doesn't claim more results than it got. The GitHub limits also now match what GitHub allows (100).
2. **A tiny memory for what code can't fix** (Settings → Memory, `/remember …`, or "remember that …" in chat, which asks first):
   - **Facts** about you or your setup ("my default repo is owner/project"), 200 characters each, 30 at most. Per request only facts for *everywhere* plus the apps that request involves are added to the prompt, capped at 8 facts / 600 characters (about 150 tokens). With no memory the prompt is byte-identical to before, so Ollama's cache still works.
   - **Learned tool quirks.** When a tool *rejects* an argument and the retry with a different number, switch or fixed choice *works*, the harness records that for that one tool and appends it to that tool's description only. Learned without a model call, only from numbers/booleans/choices (never free text from a web page or an issue), never from outages or auth errors, shown in the chat as "Learned for next time: …", de-duplicated, forgotten after 90 days unused.
   - You can see, edit, pin, disable or delete everything. Turn memory or auto-learning off in the same tab. Memory is only text in the prompt: it can't change approvals or any setting.
