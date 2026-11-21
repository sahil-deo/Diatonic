# The platform catalog

The agent does not carry a list of everything Diatonic can do in its prompt. It looks things up. The catalog is one searchable list, built from the live code each time it is asked, so it cannot fall out of date.

## What is in it

| Kind | Comes from | Name looks like |
|---|---|---|
| `tool` | the app registry (every enabled app) | `notes_create_note` |
| `command` | the page, which sends its command list once at load (`POST /api/catalog/commands`) | `panel.close` |
| `ctx-api` | `diatonic/ctxapi.py` (`ctx.store`, `ctx.fetch`, `ctx.notify`, `ctx.timer`, `ctx.on`, `ctx.tool` and the basics) | |
| `component` | `web/js/ui/components.json`, through `diatonic/uikit.py` (one entry per UI kit component, example generated from its props) | |
| `skill` | `diatonic/skills/*.md`, your own `~/.diatonic/skills/*.md`, and every recipe as `recipe-<id>`, through `diatonic/instructions.py` | `app-creator`, `recipe-timer` |

Each entry has: `kind`, `name`, `doc`, `args` (name, type, doc, required), one `example`, `effects`, `domain` and `app`. A tool's example is made from its required arguments, so every tool has one. A test fails if a built-in tool has no description.

## Effects

Every tool declares what it can do. `risk` (`read`, `write`, `destructive`) is still accepted and sets the effects when none are given; it is only the short label on approval cards.

| Effect | Meaning | Asks first |
|---|---|---|
| `read` | looks at data | no |
| `write` | changes data | yes |
| `destroy` | deletes data | yes (the card is marked destructive) |
| `execute` | runs commands or code | yes |
| `spend` | costs money or quota | yes |
| `external` | talks to the network or another service | no on its own |
| `display` | changes the interface itself (theme, panels, buttons) | no on its own; refused in a run that has read mail |

Three rules read effects, and nothing else: an approval card is shown when the tool has `write`, `destroy`, `execute` or `spend` (unless the user chose "always allow", or the tool decides for itself with `ask`); a panel may only call tools with none of those; and a run that has read untrusted content (mail, web pages, command output) cannot use a `display` tool.

## Looking things up

- `ui_platform_search(query, kind?)`: a few matches, one line each, as `name(args): what it does`.
- `ui_platform_describe(name)`: the whole entry, with the example.
- `GET /api/catalog?q=timer&kind=tool&limit=8` and `GET /api/catalog?name=notes_create_note` do the same for scripts and tests.

With a cloud model, entries of a private app (mail, files, notes) are hidden until the user allows that app for the chat, the same rule as for its tools.

## For the agent

- Search before you guess a name. Search with the verb and the thing: "delete note", "notify".
- Copy the example, then change the values.
- A tool whose effects include `write`, `destroy`, `execute` or `spend` will ask the user; say what it will do.

## Instructions are files

The system prompt is five short sections in `diatonic/prompts/` (`identity`, `scope`, `tools`, `discovery`, `building`), joined in that order. A file with the same name in `~/.diatonic/prompts/` replaces a section; an empty file switches it off. The clock and what is on screen go on the user message, so the prompt stays the same between turns. The command "Show the system prompt" lists the sections with their sizes.

Skills are `NAME.md` files with a `name` and `description` header, in `diatonic/skills/` or `~/.diatonic/skills/`. The agent finds them with `ui_platform_search` (kind `skill`) and reads one with `ui_read_skill`; the user can type `/NAME` at the start of a message to put a skill into that turn. The recipes in `diatonic/recipes/` are served the same way as `recipe-timer`, `recipe-tracker` and `recipe-search`, and the `recipes` skill lists them.

## For the agent

- Look up, never guess: `ui_platform_search` with a few words, then `ui_platform_describe` for the exact name and arguments.
- Before building or changing an app, read `app-creator`. Then read the closest recipe and change it instead of writing from nothing.
- A skill or recipe that does not exist is an error that lists the real names. Use one of them.
- Ask for what the app needs in its manifest `permissions`; the user is asked once.
