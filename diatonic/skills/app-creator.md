---
name: app-creator
description: How to build or change an app: the shape of index.js, the rules, how to look things up and how to read errors.
---
Build an app as files, in this order: 1) ui_read_skill("recipes") and then the closest recipe (a timer app has one), 2) ui_create_file_app, 3) ui_write_app_file with path index.js.
Until index.js is written the app is an empty placeholder and does nothing. The steps are limited: at most two lookups, then create and write. Skip manifest.json and style.css unless they must change.

Shape of index.js:
- `export async function render(el, ctx) { ... }` fills `el`. Keep everything inside render: no module-level variables, no imports.
- Build with `ctx.ui` components (card, list, form, stat, button...) and `ctx.h(tag, attrs, ...children)`. Style with the `ui-` classes and tokens such as var(--accent), never colours.
- Always await ctx calls. Clean up listeners and timers with `ctx.onClose`.

Rules:
- Start from a recipe. ui_read_skill("recipes") lists them; read the closest one and change it. Don't write from nothing.
- Look things up, never guess: ui_platform_search ("timer", "save data", "fetch"), then ui_platform_describe for the exact call. Components are kind "component".
- Save data with ctx.store, not localStorage. Reach the web with ctx.fetch, not fetch. Timers and notifications are ctx.timer and ctx.notify.
- The manifest must declare what the app uses (permissions). The user is asked once to allow it.
- Write whole files, finished. To change an app, ui_read_app_file first, then ui_patch_app_file with the exact old text; don't rewrite it.
- Bulk data goes in one call (ctx.store.addMany), not one call per row.

When something fails: call ui_read_app_logs, fix the one thing it names, and patch. Do not say the app works until ui_open_app shows it with no errors in the log.
