"""Changing how the interface looks: theme, home screen, styles, top-bar buttons and scripts, all undoable.

A mixin for the Interface app (apps/ui/app.py). Everything here goes through the mods store, so "undo that" works.
"""
from __future__ import annotations

import json
import re

from ...actions import clean_appearance
from ...mods import ModError
from ..base import WRITE, Tool, ToolError
from .sdk import SUMMARY

COLOURS = {"green": "#34d399", "blue": "#7c9cff", "red": "#ef4444", "purple": "#a78bfa", "violet": "#a78bfa", "pink": "#f472b6", "orange": "#fb923c",
           "yellow": "#facc15", "teal": "#14b8a6", "cyan": "#22d3ee", "white": "#ffffff", "gray": "#9ca3af", "grey": "#9ca3af"}


def hex_colour(v) -> str:
    """Accept what a small model says for a colour: a name, 'ff8800', '#f80'."""
    s = str(v or "").strip().lower()
    if s in COLOURS:
        return COLOURS[s]
    s = s.lstrip("#")
    if re.fullmatch(r"[0-9a-f]{3}", s):
        s = "".join(c * 2 for c in s)
    return "#" + s if re.fullmatch(r"[0-9a-f]{6}", s) else str(v or "")


STYLE_MAP = ("Colours are CSS variables on :root: --bg --surface --surface-2 --border --text --muted --accent --radius --font. "
             "Parts: .rail (the strip of app icons on the far left; one icon is .rail-btn[data-app=files]) .sidebar (chat list) .main (chat) "
             ".topbar .messages .msg.user .bubble .msg.assistant .composer .panel (right app panel) .dock (bottom bar) .statusbar. "
             "No url() and no @import.")
SCRIPT_MAP = ("Code is an async function body with `diatonic`: h(tag, attrs, ...kids), addButton({label, onclick}), toggle(appId), "
              "open(appId), close(), isOpen(appId), every(ms, fn), toast(text), agent.send(text), api.get(url)/post(url, body), on('panel', fn). "
              "Example: diatonic.addButton({label: 'Hi', onclick: () => diatonic.toast('hi')});")

class LooksMixin:
    """Needs self.ctx, self._guard_mod, self._blocked, self._changed, self._done and self._resolve from UiApp."""

    async def set_theme(self, a: dict):
        self._guard_mod()
        keys = ("theme", "accent", "density", "font", "font_size", "radius", "width", "sidebar_width", "show_status_bar", "show_suggestions", "show_speak_button", "show_rail")
        try:
            patch = {k: a[k] for k in keys if a.get(k) is not None and a.get(k) != ""}
            hide, show = self._app_list(a.get("hide_apps")), self._app_list(a.get("show_apps"))
            if hide or show:   # icons in the left rail
                cur = list(self.ctx.config.data["appearance"].get("rail_hidden") or [])
                patch["rail_hidden"] = [x for x in dict.fromkeys(cur + hide) if x not in show]
            if "accent" in patch:
                patch["accent"] = hex_colour(patch["accent"])
            clean = clean_appearance(patch)
        except (ValueError, TypeError) as e:
            raise ToolError(str(e), bad_args=True)
        if not clean:
            raise ToolError("Say what to change: theme, accent, density, font, font_size, radius, width, sidebar_width, hide_apps or show_apps.", bad_args=True)
        cur = self.ctx.config.data["appearance"]
        self.ctx.mods.log_theme(json.loads(json.dumps({k: cur.get(k) for k in clean})), clean)   # a copy: undo needs the old values
        self.ctx.config.update({"appearance": clean})
        self._changed("theme: " + ", ".join(f"{k} {v}" for k, v in clean.items()))
        return self._done("theme changed (" + ", ".join(f"{k} {v}" for k, v in clean.items()) + "). The user can say 'undo' to revert it")

    async def set_home(self, a: dict):
        """The home screen: a layout and which app fills each tile."""
        self._guard_mod()
        from ...actions import HOME_LAYOUTS
        cur = json.loads(json.dumps(self.ctx.config.data["appearance"].get("home") or {"layout": "grid4", "slots": []}))   # a copy: undo needs the old value
        layout = str(a.get("layout") or cur["layout"]).strip().lower()
        alias = {"4": "grid4", "four": "grid4", "grid": "grid4", "stacked": "two_rows", "rows": "two_rows", "side by side": "two_cols",
                 "columns": "two_cols", "1": "one", "big": "one", "single": "one"}
        layout = alias.get(layout, layout)
        if layout not in HOME_LAYOUTS:
            raise ToolError(f"layout must be one of: {', '.join(HOME_LAYOUTS)}.", bad_args=True)
        slots = self._app_list(a.get("apps")) if a.get("apps") else cur["slots"]
        tiles = [x.id for x in self.ctx.registry.enabled() if callable(getattr(x, "snapshot", None))]
        bad = [s for s in slots if s not in tiles]
        if bad:
            raise ToolError(f"{', '.join(bad)} can't be shown on the home screen. Apps with a tile: {', '.join(tiles)}.", bad_args=True)
        home = clean_appearance({"home": {"layout": layout, "slots": slots[:HOME_LAYOUTS[layout] or 4]}})["home"]
        self.ctx.mods.log_theme({"home": cur}, {"home": home})
        self.ctx.config.update({"appearance": {"home": home}})
        names = ", ".join(self.ctx.registry.apps[s].name for s in home["slots"] if s in self.ctx.registry.apps) or "suggestions"
        shape = {"grid4": "4 small tiles", "two_rows": "2 stacked tiles", "two_cols": "2 tiles side by side", "one": "1 big tile", "suggestions": "suggestions"}[layout]
        self._changed(f"home screen shows {names} ({shape})")
        return self._done(f"the home screen now shows {names} as {shape}")

    def _app_list(self, v) -> list[str]:
        if not v:
            return []
        items = v if isinstance(v, list) else re.split(r"\s*(?:,|\band\b)\s*", str(v))
        return [self._resolve(x).id for x in items if str(x).strip()]

    async def add_button(self, a: dict):
        spec = {"label": str(a.get("label") or "").strip()[:30]}
        name = a.get("name") or ("btn-" + re.sub(r"[^a-z0-9]+", "-", spec["label"].lower()).strip("-"))[:40]
        old = self.ctx.mods.get(name)
        if old and old["kind"] == "button" and not a.get("app") and not a.get("prompt"):   # relabel only: keep what it did
            prev = json.loads(old["code"])
            spec.update({k: prev[k] for k in ("app", "prompt") if k in prev})
        if a.get("app"):
            spec["app"] = self._resolve(a["app"]).id
        if a.get("prompt"):
            spec["prompt"] = str(a["prompt"])[:500]
        if not a.get("name"):   # 'rename the Files button': a second button doing the same thing is never wanted, so replace it
            same = next((m for m in self.ctx.mods.list() if m["kind"] == "button"
                         and {k: v for k, v in json.loads(m["code"]).items() if k != "label"} == {k: v for k, v in spec.items() if k != "label"}), None)
            if same:
                name = same["name"]
        mod = self._put("button", {"name": name, "code": json.dumps(spec), "title": spec["label"]})
        what = f"opens and closes {spec['app']}" if "app" in spec else "sends its prompt to the agent"
        return self._done(f"button '{spec['label']}' is in the top bar; it {what}")

    def _put(self, kind: str, a: dict, code_key: str = "code"):
        self._guard_mod()
        try:
            mod = self.ctx.mods.put(kind, a.get("name", ""), a.get(code_key, ""), a.get("title", ""))
        except ModError as e:
            raise ToolError(str(e), bad_args=True)
        self._changed(f"{kind} '{mod['name']}'")
        return mod

    async def add_style(self, a: dict):
        mod = self._put("style", {**a, "code": a.get("css", "")})
        return self._done(f"style '{mod['name']}' is applied. The user can say 'undo' to remove it")

    async def add_script(self, a: dict):
        self._guard_mod()
        await self._verify("script", a)
        mod = self._put("script", a)
        return self._done(f"script '{mod['name']}' is installed and running")

    async def list_mods(self, a: dict):
        if a.get("name"):
            m = self.ctx.mods.get(self._slug(a["name"]))
            if m and m["kind"] == "script":
                return {"name": m["name"], "kind": m["kind"], "title": m["title"], "code": m["code"]}
        mods = [{k: m[k] for k in ("name", "kind", "title", "enabled")} for m in self.ctx.mods.list()]
        return mods or "No interface changes have been made yet."

    async def remove_mod(self, a: dict):
        self._guard_mod()
        try:
            self.ctx.mods.remove(str(a.get("name", "")).strip().lower())
        except ModError as e:
            raise ToolError(str(e), bad_args=True)
        self._changed(f"removed {a.get('name')}")
        return self._done(f"'{a.get('name')}' was removed")

    async def revert(self, a: dict):
        self._guard_mod()
        try:
            what = self.ctx.mods.revert(self.ctx.config)
        except ModError as e:
            raise ToolError(str(e))
        self._changed(what)
        return self._done(what)

    def _code_review(self, kind: str):
        async def preview(a: dict) -> str:
            await self._verify(kind, a)
            before = self.ctx.mods.get(self._slug(a.get("name")))
            if before and before["kind"] == kind and before["code"].strip() != str(a.get("code") or "").strip():
                return "Changes to the existing " + kind + ":\n" + self._diff(before["code"], str(a.get("code") or ""))   # an update: show only what differs
            return self._code_preview(a, "code")
        return preview

    def _existing(self) -> str:
        """Names of the changes already made, so 'change that button' reuses a name instead of adding a second one."""
        mods = self.ctx.mods.list()[-12:] if getattr(self.ctx, "mods", None) else []
        if not mods:
            return ""
        return " Existing changes (pass the same name to replace one): " + ", ".join(f"{m['name']} ({m['kind']} '{m['title']}')" for m in mods) + "."

    def looks_tools(self) -> list[Tool]:
        name = {"type": "string", "description": "Short lowercase name with dashes, e.g. wide-sidebar. Reuse an existing name to replace it."}
        existing = self._existing()
        return [
            Tool("set_home", "Choose what the home screen (an empty chat) shows: a layout and an app per tile, e.g. to-dos, mail, GitHub issues, "
                 "telemetry, calendar, reminders, notes, or apps the user built. Layouts: grid4 (4 small), two_rows (2 stacked), "
                 "two_cols (2 side by side), one (1 big), suggestions.",
                 {"properties": {"layout": {"type": "string", "enum": ["grid4", "two_rows", "two_cols", "one", "suggestions"]},
                                 "apps": {"type": "array", "items": {"type": "string"}, "description": "One app per tile, in order"}}},
                 self.set_home, WRITE, title="Set home screen", precheck=self._blocked),
            Tool("set_theme", "Change the look: theme, accent colour, density, font, text size, corners, chat width, chat-list width, "
                 "and which app icons show in the left rail (the 'side bar'). Try this first for look-and-feel requests. Undoable.",
                 {"properties": {"theme": {"type": "string", "enum": ["system", "light", "dark"]}, "accent": {"type": "string", "description": "A colour name (green) or #rrggbb; empty to reset"},
                                 "density": {"type": "string", "enum": ["comfortable", "compact"]}, "font": {"type": "string", "enum": ["system", "sans", "serif", "mono", "rounded"]},
                                 "font_size": {"type": "integer", "description": "11-22"}, "radius": {"type": "string", "enum": ["sharp", "default", "round"]},
                                 "width": {"type": "string", "enum": ["narrow", "default", "wide", "full"], "description": "Chat column"},
                                 "sidebar_width": {"type": "string", "enum": ["narrow", "default", "wide"], "description": "Chat list"},
                                 "hide_apps": {"type": "array", "items": {"type": "string"}, "description": "Rail icons to hide, e.g. [\"files\"]"},
                                 "show_apps": {"type": "array", "items": {"type": "string"}},
                                 "show_status_bar": {"type": "boolean"}, "show_suggestions": {"type": "boolean"}, "show_speak_button": {"type": "boolean"}}},
                 self.set_theme, WRITE, title="Change theme", precheck=self._blocked),
            Tool("add_style", "Custom CSS for one part of the page, when set_theme can't do it. Undoable. " + STYLE_MAP,
                 {"properties": {"name": name, "css": {"type": "string", "description": "The CSS"}, "title": {"type": "string"}}, "required": ["name", "css"]},
                 self.add_style, WRITE, title="Add style", precheck=self._blocked, describe=lambda a: f"Add style '{a.get('name')}'"),
            Tool("add_button", "Top-bar button, no code. app: opens/closes that app ('toggle the file explorer' -> label Files, app files). "
                 "prompt: asks the agent that. Prefer over add_script. Undoable." + existing,
                 {"properties": {"name": name, "label": {"type": "string"}, "app": {"type": "string", "description": "App to open/close, e.g. files, gcal, gmail, notes"},
                                 "prompt": {"type": "string", "description": "Or: what to ask the agent when clicked"}}, "required": ["label"]},
                 # (to only rename a button, pass its name and the new label; it keeps doing what it did)
                 self.add_button, WRITE, title="Add button", precheck=self._blocked, describe=lambda a: f"Add a '{a.get('label')}' button"),
            Tool("add_script", "JavaScript for behaviour add_button can't do. The user approves the code first. " + SCRIPT_MAP + existing,
                 {"properties": {"name": name, "code": {"type": "string", "description": "JavaScript"}, "title": {"type": "string"}, "summary": SUMMARY}, "required": ["name", "code"]},
                 self.add_script, WRITE, title="Add script", precheck=self._blocked,
                 describe=lambda a: f"Run a new script '{a.get('name')}' in the interface{self._calls_server(a)}{self._what(a)}",
                 preview=self._code_review("script")),
            Tool("list_mods", "List the interface changes made so far (styles, scripts, buttons). With the name of a script, also returns its code.",
                 {"properties": {"name": name}}, self.list_mods, title="List changes"),
            Tool("remove_mod", "Remove one interface change by name." + existing, {"properties": {"name": name}, "required": ["name"]},
                 self.remove_mod, WRITE, title="Remove change", precheck=self._blocked),
            Tool("revert", "Undo the most recent interface change (theme, style, script or panel). Use for 'undo that', 'put it back'.", {},
                 self.revert, WRITE, title="Undo change", precheck=self._blocked),
        ]
