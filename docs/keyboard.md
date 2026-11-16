# Keyboard

Everything the interface can do is a **command** with an id (`chat.new`, `panel.close`, `app.open`…). Keys, the command palette, scripts and panels all run commands by id, so a new key never needs new code.

## Basics

| Key | What it does |
|---|---|
| ⌘K | Command palette. Type a few letters (`gcal` finds Google Calendar); things you use often rise to the top. |
| ⌘; | **Leader key.** Press it, then another key. A popup lists what each key does. Works everywhere, including the terminal and text boxes. |
| ⌘, / ⌘B / ⇧⌘O | Settings, sidebar, new chat. |
| `/` | Write a message (when you aren't typing). |
| Esc | Leave the chat box. |
| ↑ ↓ PgUp PgDn Home End Space | Scroll the side you last clicked (the chat or the app panel), when you aren't typing. In a list that offers it (mail, files, notes), ↑ ↓ move the selection and Enter opens it. |

A thin outline shows which side has the keyboard; it appears once you use the keys and goes away on the next click.

Leader keys: `n` new chat · `s` settings · `k` keyboard settings · `b` sidebar · `t` light/dark · `m` switch model · `h` / `l` focus the chat / the app panel · `x` close panel · `z` expand panel · `r` refresh panel · `a` then `f` files, `m` mail, `c` calendar, `n` notes, `t` to-dos, `g` GitHub, `s` terminal, `p` presentations, `j` jobs, `y` telemetry · `w` then `v` / `s` split right / below, `t` reopen a closed tab, `z` zoom the panel, `d` duplicate it, `m` open its menu.

**Vim-style keys** (Settings → Keyboard, or the command "Turn on vim-style keys"): `j` `k` move, Ctrl-D / Ctrl-U half a page, `g g` top, `G` bottom, `i` write a message.

## Changing keys

Settings → Keyboard lists every command with its keys. Click `+` and press the keys (a sequence such as `g g` works: pause when done); click `×` to remove one. The leader key can be changed there too. A key that would clash with another is flagged and nothing is overwritten without asking.

Browsers keep ⌘W, ⌘T, ⌘N, ⌘L, ⌘Q and ⌘1–9 for themselves, so Diatonic can't receive them; that is why the leader key exists.

The changes are saved in `keymap.json` in the Diatonic folder (`~/.diatonic`). You can edit it by hand; it is read again when you come back to the window.

```json
{
  "leader": "Ctrl+Space",
  "vim": false,
  "bindings": [
    { "key": "Mod+j", "command": "chat.new" },
    { "key": "<leader> g", "command": "app.open", "args": { "id": "github" } },
    { "key": "g g", "command": "nav.top", "when": "idle" },
    { "key": "Mod+b", "command": null }
  ]
}
```

- `key`: modifiers `Mod` (⌘ on a Mac, Ctrl elsewhere), `Ctrl`, `Alt`, `Meta`, `Shift` joined with `+`, then the key (`k`, `,`, `ArrowDown`, `Enter`, `Escape`, `Space`, `F5`). Spaces separate the steps of a sequence; `<leader>` stands for the leader key. An upper-case letter on its own means Shift (`G`).
- `command`: a command id, or `null` to remove that key from the built-in keymap.
- `args`: for commands that take some (`app.open` takes `id`, and optionally `params` such as `{ "view": "month" }` and `focus: false` to leave the keyboard where it is; `panel.split.right` and `panel.split.down` take an optional `app` to open without asking; `sidebar.set` takes `mode`: `show` or `hide`; `settings.open` takes `tab`).
- `when`: optional condition from these names, combined with `!`, `&&`, `||` and parentheses: `input` (typing in a text box), `composer` (the chat box), `terminal`, `dialog` (a window is open), `idle` (not typing), `plain` (idle and not on a button or link), `vim`, `panel`, `panel:files`, `app:gmail`, `focus:chat`, `focus:panel`, `list`. Keys with no modifier only fire while you aren't typing, whatever `when` says.
- Your entries win over the built-in ones. A bad entry is reported and skipped; the rest still work.

## In the terminal and text boxes

The terminal gets every key except the leader. In text boxes only keys that can't be typing work (anything with ⌘, ⌃ or ⌥, Esc, function keys).

## For panels and scripts

`ctx.command({id, title, run})` in a panel adds a command (named `<app>.<id>`, removed when the panel closes). `ctx.list({items: () => [elements], open?})` lets ↑ ↓ Enter work on a panel's rows. Scripts get `diatonic.command(...)` and `diatonic.run(id, args)`. See [panels.md](panels.md).
