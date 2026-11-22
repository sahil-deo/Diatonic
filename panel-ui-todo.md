# Panel-based UI: design and plan

Goal: replace the agent-first shell (rail, conversation sidebar, one chat, one panel slot) with a fully customizable panel workspace. Everything is a panel (including the agent), panels split and resize freely, several panels of the same app can coexist, and everything is reachable from the keyboard.

## Decisions (made 2026-11-15)

- **Tabs and tiling both.** Each leaf of the layout is a tab stack.
- **Keyboard model: leader key plus a few direct chords** (my choice), with an opt-in vim-style mode.
- **Browser tab only** (no PWA/Tauri wrapper), so browser-reserved keys (⌘W, ⌘T, ⌘N, ⌘1-9) are unavailable.
- **Desktop only for now.** Narrow-window collapse to tabs is a later nicety.

## Where the current UI blocks this

- `panel.js` has a single `#panel`, a single `state.panelApp`, and module-level `current` and `seq`.
- Chat is special-cased: the dock, `screenContext`, and `set_layout`'s three modes (`split`, `focus`, `chat`) all assume it.
- Keys are hard-coded in one `if` chain in `main.js`.
- The palette is a substring filter over a flat list.
- Splitting and multi-instance panels need a different core, not extra CSS.

## Core design

### 1. Layout tree
- A node is a `split` (direction, sizes, children) or a `stack` (tabs, one active).
- A tab is a panel instance: `{id, app, params, state}`.
- The tree serializes to JSON: saved as named layouts, restored on reload, editable by the agent.
- Tabs also fix the cluttered apps rail: open 30 apps without 30 permanent icons.

### 2. Everything is a panel
- Chat becomes an app with a `conversationId` param, so two chats can sit side by side.
- The conversation list, notifications, settings, home tiles and terminal are panels too. The bottom dock goes away.
- Panel contract: `render(el, ctx)` gains an instance id, params, persisted per-instance state, a resize callback, and the ability to contribute commands and status items.
- Multi-instance requires panels to stop keeping module-level state. This is the real refactor cost (not yet audited).
- Use container queries so a panel works at 300px and at 1200px.

### 3. One command registry (build first)
- A command is `{id, title, args, run, when}`.
- Palette, keymap, status bar pins, macros, agent tools and custom panels all invoke the same commands, e.g. `panel.split {dir: "right", app: "mail"}`.
- "Put mail on the left and my calendar top-right" becomes a thin agent tool over the registry.
- It doubles as the documented SDK for agent-built panels (see `hackable-todo.md`).

## Features

### Launcher instead of the rail
- Primary route is the command bar.
- A new or split leaf shows an empty launcher (search, recents, pinned apps), like a browser new-tab page.
- The rail becomes optional and off by default.

### Status bar
- Keep it. Zones: left, center, right.
- Items: existing statuses, pinned commands or apps, and panel-contributed items with badges (e.g. unread mail).
- Pin via the palette or right-click. Click runs the command; modifier-click opens it in a split.

### Command bar (⌘K)
- **Plain text:** fuzzy search with frecency over apps, panels, conversations, commands, automations, layouts.
- **`/`:** command grammar, e.g. `/open files`, `/run <automation>`, `/split right mail`, `/layout dev`.
- **Second shortcut (e.g. ⌘J):** opens straight into ask-the-agent mode, avoiding guesswork about whether "mail" is an app or a prompt. Tab toggles modes.
- **Where an ask opens:** Enter replaces the focused chat, ⌘Enter opens a new chat in a split, ⌥Enter runs it in the background and notifies on completion.
- **`/run`:** merges scheduler jobs, saved macros and shell scripts; commands with arguments show a small form.

### Keymap
- `keymap.json` in the Diatonic home folder, hot-reloaded, mirrored in a Settings editor with conflict detection.
- Bindings: `key -> command + args + when`, e.g. `"when": "!input && panel:files"`.
- Leader key (e.g. ⌘;) with a which-key popup showing the next keys; a few direct chords on top.
- The terminal panel passes everything through except the leader.

### Panel movement
- `focus.left/right/up/down` (spatial, by rect), `panel.move.*`, `panel.swap`, `panel.split.right/down`, `panel.resize.*`, `panel.zoom` (maximize toggle), `panel.close`, `tab.next/prev`.
- Numbered or Vimium-style "jump to panel" hint mode.

### Keyboard scrolling
- Normal/insert split: with a panel focused and not in a text field, `j/k` scroll, `ctrl-d/u` half-page, `gg/G` top/bottom, `space` pages.
- `Esc` leaves a text field back to the panel; `i` or Enter enters it.
- List panels (mail, files, todos) add `j/k` item selection, Enter to open, `/` to filter.
- Default is arrows plus PgUp/PgDn; vim-style keys are a setting.

### More ideas
- **Saved layouts** ("Inbox zero", "Dev"), switchable from the palette, per-window restore. Macros can set one up; the scheduler can run it at 8am.
- **Macros:** declarative JSON sequences of commands (optional wait/if), recordable, savable by the agent ("save this layout as Dev"), runnable from the scheduler. Script macros need approval.
- **Linked panels:** a chat panel follows another panel ("sees that mail"), with a visible chip. Replaces the single `screenContext`.
- **Layout undo/redo and reopen closed panel (⌘⇧T):** reuse the existing mods history.
- **Drag between panels:** a file or email dropped on a chat or todo list becomes a reference.
- **Mouse polish:** edge drop zones when dragging a tab, double-click a divider to equalize, header menu (split, move, duplicate, pin).
- **Scratchpad:** toggleable floating quick-chat or quake-style terminal. No free-floating windows at first.
- **Detach to a new window:** feasible since state lives on the server and streams over SSE.
- **Later:** collapse the tree to tabs on narrow windows (today's 820px behavior).

## Risks

- **Prompt injection:** an agent-editable keymap or macro is an attack surface (a poisoned email could rebind a key to a shell macro). Extend the existing "refused in any run that has read mail" rule to keymap, macro and layout edits. Script macros need an approval card; plain layout changes can be free and undoable.
- **Build or borrow:** the repo is vanilla JS with no build step. Write the tree and renderer ourselves (roughly 500 lines of CSS grid plus pointer events) because the agent must manipulate it. `dockview-core` (framework-agnostic) could save the drag-and-drop work if that becomes a time sink; not evaluated.
- **Performance:** hidden tabs keep state but stop working; throttle resize observers.
- **Persistence:** store the layout in server config with a versioned schema and migration, localStorage as fallback.


