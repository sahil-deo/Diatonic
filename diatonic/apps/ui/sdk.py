"""The few words every build request carries in the write_app_file tool description. The rest is in the skills (`skills/app-creator.md`)
and the recipes (`recipes/`), which the agent reads on demand, so every word here costs context on each build request."""
from __future__ import annotations

# Everything else on `ctx` is looked up with ui_platform_search / ui_platform_describe (diatonic/ctxapi.py); a worked example is a recipe.
FILE_HINT = (
    "index.js must be: export async function render(el, ctx) { ... } and fill `el`; keep everything inside render (no module-level variables, no imports). "
    "ctx.h(tag, {attrs, onclick}, ...children) builds an element. Always await ctx calls. Before writing, read ui_read_skill(\"app-creator\") and the closest recipe "
    "(ui_read_skill(\"recipes\")); look up the exact calls with ui_platform_search then ui_platform_describe. The manifest must declare what the app uses. "
    "Give the whole file, finished, not a sketch.")

SUMMARY = {"type": "string", "description": "One plain sentence for the user: what this shows or does"}
