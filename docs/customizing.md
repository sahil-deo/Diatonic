# Making it yours

The agent driving the interface, interface changes the agent makes, apps it builds, models, and your own buttons and commands. Back to the [README](../README.md).

## The agent can drive the screen

Ask it to "show my calendar", "show the month view", "open my latest email", "open the Diatonic Apps note", "open the interview event", "make the panel full screen" or "hide the sidebar" and it moves the window you asked from. These are the **Interface** tools (`ui_open_app`, `ui_open_item`, `ui_set_layout`): they only change what is displayed, read nothing and need no approval. Requests that begin with show/open/go to (and ask for no data, numbers or follow-up) are sent to the model with just these three tools, which keeps the prompt tiny and the choice obvious.

**The agent bar.** When an app fills the window (Calendar and Mail do by default) the chat column is hidden, so a bar at the bottom carries the conversation: type or use the mic, read the latest reply, and approve or deny actions without leaving the app. In split view the normal composer is the bar. Layout words the agent understands: *split* (chat and panel side by side), *focus* (panel fills the window, bar stays), *chat* (no panel), and sidebar show/hide.

**"This" means what's open.** When an email, note or file is open, a chip above the message box shows it, and a request that says *this*, *it* or *that* ("summarize this email", "reply to it") tells the agent exactly which one. Click the chip to stop sharing it.

Only the window that started the run is moved.

### Ask the agent to change the interface

"Make the accent green", "bigger text", "make the sidebar 200 pixels wide", "add a button to the top bar that says Hello", "add a panel with a clock", "undo that". The agent picks the smallest tool that does it:

| Tool | For | Approval |
|---|---|---|
| `ui_set_theme` | accent, light/dark, density, font, text size, corners, chat width, chat-list width, which app icons show in the rail | no (undoable) |
| `ui_add_button` | a top-bar button that opens/closes an app, or asks the agent a prompt (data, not code) | no (undoable) |
| `ui_add_style` | custom CSS for one part of the page | no (undoable); CSS can't load anything from the network or touch the approval cards |
| `ui_add_script` | new behaviour, e.g. a button | **yes, the card shows the code** |
| `ui_add_panel` | a whole new panel with a rail icon | **yes, the card shows the code** |
| `ui_revert`, `ui_remove_mod`, `ui_list_mods` | undo, delete, list | no |

Changes are stored in `~/.diatonic/ui/mods.json` with an undo history, shown under Settings → Interface → "Changes the agent made" (turn each off, remove it, or undo the last one), and offered as an Undo button when they happen. Scripts get a small `diatonic` object (`h`, `api`, `toast`, `addButton`, `toggle`, `open`, `close`, `isOpen`, `every`, `agent.send`, `on`); whatever a script adds is removed when it is changed or removed, so edits apply without a reload. Added buttons appear in the top bar and in the agent bar, so they work while an app fills the window. Approval cards for code say so if the code doesn't parse. Open the page with `?safe=1` to start with every mod off.

### Your own apps, no API and no code

"Create an app to track my exam studies: subjects, topics for each subject, mark topics complete, add notes" gives you an app with its own rail icon and panel (sections, checklists with progress, details and notes in place), and tools the agent uses from any chat ("add Physics with topics Optics and Thermodynamics", "mark Optics done", "what's left in Physics?"). A data app is a definition of lists and typed fields (text, notes, number, date, check, choice), nested if needed, stored in Diatonic's database and run by one tested runtime (`diatonic/dataapps.py`):

| Tool | Does | Approval |
|---|---|---|
| `ui_create_app` | name and title | no (undoable; no network, no code) |
| `ui_add_list` | a list and its fields, optionally inside another list | no |
| `ui_add_field`, `ui_remove_field`, `ui_remove_list` | change it later ("also add a priority to topics"); data is kept | no |
| `<app>_add_…`, `<app>_update_…`, `<app>_list_…` | generated per list; items are found by name | no |
| `<app>_delete_item` | delete an item and what's inside it | **always asks** |

### Apps on web APIs, built by the agent

"Implement an app that uses the Open-Meteo API at https://api.open-meteo.com/v1; /forecast takes latitude and longitude" gives you a new app with its own rail icon, a panel to run it, and a tool the agent can use from any chat ("what's the weather in Pune?"). An app like this is **data, not code**: a short definition (base URL, where the key goes, one entry per endpoint, which values to show) run by one tested runtime (`diatonic/apiapps.py`). The model builds it in small checked steps (it picks `ui_connect_api` only when an API or URL is involved; otherwise it makes a data app):

| Tool | Does | Approval |
|---|---|---|
| `ui_connect_api` | name, base URL, how the key is sent | **yes**, the card names the host it may call |
| `ui_add_action` | add or change one endpoint; it is **test-called immediately** and the result's fields are reported back | no (undoable) |
| `ui_set_fields` | which values to show (checked against the real response) | no |
| `ui_remove_action`, `ui_test_app` | | no |

Editing is a one-line change ("also ask for the timezone", "show only temperature and wind") because the model sees every app's current definition. Calls run on the server: HTTPS only, only the app's own host, never a local or private address, no redirects, 300 KB and 15 s limits. The API key is pasted into the app's panel once, kept in the keychain, never shown to the model, and scrubbed from error messages. Endpoints that change data (POST/PUT/PATCH/DELETE) ask for approval every time the agent uses them, and API responses count as untrusted (like email). Not covered: OAuth sign-in, pagination, webhooks; those still need a hand-written app.

**Why it is safe to let a 4B model do this.** A run that has read email (anyone can write an email) can't change the interface or build apps, and neither can a scheduled or background run (those have no screen at all). Scripts and panels never run without you reading the code first, data apps can't reach the network or run code, and API apps can only call the one host you approved.

## Models: local and cloud, switch on the go

Click the model name in the status bar to switch between saved models; the choice applies everywhere (every chat and window, spoken summaries, scheduled jobs). Add models in **Settings → Models**: pick a provider, type the model name (or **Load list** to ask the provider), paste the key once per provider, and **Test** it (one request with a tool, so you know it can act as the agent).

| Provider | Endpoint |
|---|---|
| Ollama (local) | `http://localhost:11434` |
| Google AI Studio (Gemini) | `https://generativelanguage.googleapis.com/v1beta/openai` |
| Groq | `https://api.groq.com/openai/v1` |
| Any other OpenAI-compatible API | your own |

Keys are kept in the keychain, one per API host, never in `config.json` or a prompt. Cloud models are reached through one adapter (`agent/providers/openai.py`) that streams text and tool calls in the same shape as Ollama, retries rate limits, and hides `<think>` reasoning.

**Cloud models and private data.** Mail, Calendar, Files, Notes, Memory, GitHub and the apps you build are *private* (change it per app in Settings → Apps). With a cloud model, any private tool asks first ("Allow once", "Allow for this chat", "Deny"), scheduled runs don't get private tools at all, remembered facts and `@file` contents aren't sent, earlier answers in the chat that used private data are replaced with a note, and on-screen titles (an email subject) are left out.

## Make it yours: quick actions, `/` commands and interface

**Saved prompts and script buttons** (Settings → Actions, or **+ Add** on the bar above the message box). Each one can be a chip on the bar, a `/command`, a keyboard shortcut, or all three.

- **Saved prompt:** text that is dropped into the message box (so you can tweak it) or sent straight away, optionally in a fresh chat. Placeholders: `{{input}}` (whatever you type after the command), `{{date}}`, `{{time}}`, `{{day}}`, `{{clipboard}}`. Example: `/summarize <paste text>` fills in `Summarize in 3 bullets: …`. A chip whose prompt needs `{{input}}` asks for it first.
- **Script button:** runs a shell command from a saved action and streams its output into the chat as a live card (with Stop, Copy and *Send output to the agent*). Your input arrives as `$1` and `$INPUT`; it is never pasted into the command text, so it can't inject code. Optional working folder, timeout and "ask before running". Scripts need **Settings → Automation → Allow scripts** (the same switch as scheduled script jobs; off by default). The server only ever runs a *saved* action by id, never a command sent by the browser, and the agent has no tool to create or run these.
- **`/` menu:** type `/` for your actions plus built-ins: `/new`, `/settings [tab]`, `/theme dark|light|system`, `/speak on|off`, `/panel <app>`, `/sidebar`, `/stop`, `/actions`, `/help`. Arrow keys to move, **Tab** to complete, **Enter** to run (`/say hello` passes "hello" as the argument), **Esc** to close.
- **On the bar:** drag chips to reorder, right-click to edit, press a chip's shortcut from anywhere. **Add examples** loads a few starters.

**Interface** (Settings → Interface), all applied live: theme, accent colour, density, text size, font, corner roundness, chat width, show/hide the sidebar (⌘B), quick-action bar, status bar, spoken-summary button and empty-chat suggestions, reorder or hide apps in the left rail, and a custom-CSS box for anything else (`--accent`, `--bg`, `--surface`, … are all variables). **Export** saves your appearance and actions to a JSON file; **Import** restores it (validated first, so a bad file changes nothing).
