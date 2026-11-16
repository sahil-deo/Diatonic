"""The interface as a tool: the agent can put an app, a mail, a note or a file on screen and change the layout.

These tools only move the view in the window that started the run. They read nothing and change no data, so
they never ask for approval. A background run (scheduled, or whose window was closed) has no screen to move.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import re
import uuid
from datetime import date

from ...actions import clean_appearance
from ...agent import session
from ...agent.session import RUN_CLIENT
from ...dataapps import DataSpecError, check_data_manifest, parse_fields, summary as data_summary
from ...apiapps import AppSpecError, call as api_call, check_manifest, key_paths, parse_params, summary as app_summary
from ...mods import ModError
from ...fileapps import FileAppError
from ...toolapps import (FREE_TEXT, REF_PARAMS, ToolSpecError, catalog, check_tool_manifest, hint_presets, parse_fixed, plan,
                         resolve as resolve_tool, summary as tools_summary)
from ..base import WRITE, App, Tool, ToolError
from .sdk import SDK, examples

# What each panel can actually do with open_app's options; anything else is reported back as ignored, never claimed.
ACCEPTS = {"gcal": {"view", "date"}, "gmail": {"view", "query", "account"}, "notes": {"query"}, "todos": {"view"}, "files": {"path"}, "presentations": {"query", "item"}}
VIEWS = {
    "gcal": ["day", "week", "month", "agenda"],
    "gmail": ["inbox", "starred", "sent", "drafts", "spam", "trash"],
    "todos": ["open", "done"],
}
VERIFY_WAIT = 6.0   # seconds to wait for the open window to run new code in its sandbox (it gives up after 4)
SUMMARY = {"type": "string", "description": "One plain sentence for the user: what this shows or does"}
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


NAV_TOOLS = ("open_app", "open_item", "set_layout", "pin_app", "unpin_app", "save_macro", "run_macro", "list_macros", "delete_macro", "save_layout", "switch_layout", "list_layouts", "delete_layout", "arrange_panels", "undo_layout")
STYLE_MAP = ("Colours are CSS variables on :root: --bg --surface --surface-2 --border --text --muted --accent --radius --font. "
             "Parts: .rail (the strip of app icons on the far left; one icon is .rail-btn[data-app=files]) .sidebar (chat list) .main (chat) "
             ".topbar .messages .msg.user .bubble .msg.assistant .composer .panel (right app panel) .dock (bottom bar) .statusbar. "
             "No url() and no @import.")
SCRIPT_MAP = ("Code is an async function body with `diatonic`: h(tag, attrs, ...kids), addButton({label, onclick}), toggle(appId), "
              "open(appId), close(), isOpen(appId), every(ms, fn), toast(text), agent.send(text), api.get(url)/post(url, body), on('panel', fn). "
              "Example: diatonic.addButton({label: 'Hi', onclick: () => diatonic.toast('hi')});")
# Things a person asks for by what they do ("make a stopwatch"), not by calling them an app.
THINGS = r"(?:app|apps|panel|tab|calculator|converter|timer|stopwatch|clock|counter|game|generator|quiz|checklist|planner|tracker|viewer|editor|preview(?:er)?)"
MAKE_A = r"\b(?:create|make|build|add|implement)\b(?:\s+\w+){0,5}?\s+" + THINGS + r"\b"
APP_PATTERNS = [r"\b(?:new|own|custom|my own) (?:app|panel|tab)\b", MAKE_A,
                r"\bapp (?:for|to|that)\b", r"\b(?:api|apis|endpoint|endpoints|integration)\b",
                r"\b(?:update|edit|change|modify|fix|improve|extend|rename)\b.*\b(?:app|panel|tab|field|button|action)\b"]
# Building or changing an app (API apps, panels): offered instead of the look-and-feel tools.
BUILD = re.compile(r"\b(?:api|apis|endpoint|endpoints|integration|app|apps|panel|tab|field|fields|input|tracker|action)\b|" + MAKE_A)
BUILD_TOOLS = ("create_app", "add_list", "add_field", "remove_field", "remove_list", "add_panel", "list_mods", "remove_mod", "revert", "open_app",
               "create_file_app", "write_app_file", "patch_app_file", "read_app_file", "list_app_files", "read_app_logs", "export_file_app", "import_file_app")
API_TOOLS = ("connect_api", "add_action", "set_fields", "remove_action", "test_app", "list_mods", "remove_mod", "revert", "open_app")
API_WORDS = re.compile(r"\b(?:api|apis|endpoint|endpoints|url|base url|integration|api key|json|open-?meteo|openweather)\b|https?://")
EDIT_WORDS = re.compile(r"\b(?:update|edit|change|modify|fix|tweak|adjust|rename|make (?:the|it|that)|bigger|smaller|wider|colou?r|green|red|blue)\b")
API_TOPICS = re.compile(r"\b(?:weather|forecast|news|stocks?|crypto|currency|exchange rates?)\b")   # an app about these needs a web service
BUILDING = ("ui_create_app", "ui_add_list", "ui_add_field", "ui_remove_field", "ui_remove_list")
API_BUILDING = ("ui_connect_api", "ui_add_action", "ui_set_fields")
# Requests about the user's real accounts: an app for them is built on that app's tools, never a local list.
CONNECTED = {"gcal": r"\bgoogle ?meets?\b|\bmeet(?:ings?| links?)\b|\bcalendar\b|\binvites?\b|\bguests?\b",
             "gmail": r"\bg?mail\b|\be-?mails?\b|\binbox\b", "github": r"\bgithub\b|\bpull requests?\b|\brepos?\b"}
LOCAL_ONLY = re.compile(r"\b(?:local(?:ly)?|offline|by hand|manually|just (?:track|log|keep|list|note)|no (?:account|google|api))\b")
PARAM_WORDS = {"attendees": ("guest", "attendee", "invite", "people"), "description": ("description", "agenda", "notes?"), "location": ("location", "where", "room")}
TIME_PARAMS = {"start", "end", "date", "after", "before", "since", "until"}
CONNECTED_TOOLS = ("create_connected_app", "add_app_action", "remove_app_action", "list_mods", "remove_mod", "revert", "open_app")
CONNECTED_BUILDING = ("ui_create_connected_app", "ui_add_app_action", "ui_remove_app_action")
HOME = re.compile(r"\b(?:home ?screen|home ?page|start ?screen|dashboard|widgets?|tiles?)\b")
LOOK_TOOLS = ("set_home", "set_theme", "add_style", "add_button", "list_mods", "remove_mod", "revert", "set_layout")
# Words that mean "change the interface itself" (vs. just showing something): only then are the customising tools offered.
CUSTOM = re.compile(r"\b(?:theme|colou?r|accent|font|css|style|button|widget|panel|tab|rail|icon|side ?bar|top ?bar|bigger|smaller|wider|"
                    r"narrower|compact|rounded|undo|revert|script|toggle|customi[sz]e|dark mode|light mode|text size|interface|app for|new app|mods?|"
                    r"home ?screen|home ?page|start ?screen|dashboard|widgets?|tiles?)\b"
                    r"|\b(?:create|make|build|add)\b.*\b(?:app|panel|tab)\b", re.I)
MODES = {"split": "chat and panel side by side", "focus": "one panel fills the window",
         "chat": "open the chat"}
# Each mode is a command the app already has, so the agent, a key and the palette all do the same thing.
MODE_COMMANDS = {"split": {"command": "panel.split.right", "args": {"app": "launcher"}}, "focus": {"command": "layout.preset.single"},
                 "chat": {"command": "app.open", "args": {"id": "chat", "focus": False}}}


class UiApp(App):
    id = "ui"
    name = "Interface"
    icon = "sliders"
    description = "Lets the agent open apps, mails, notes, files and events on screen and change the layout."
    panel = False
    keywords = ["home screen", "homescreen", "home page", "start screen", "dashboard", "widget", "tile", "panel", "rail", "icon", "sidebar", "side bar", "top bar", "topbar", "tab", "interface", "theme", "colour", "color", "accent", "font", "css", "style", "restyle", "button", "widget", "customize", "customise",
                "dark mode", "light mode", "bigger", "smaller", "wider", "narrower", "compact", "rounded", "undo that", "revert", "text size",
                "show", "open", "go to", "switch to", "bring up", "pull up", "display", "layout", "full screen",
                "fullscreen", "expand", "collapse", "hide", "close the panel", "side by side"]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._verifying: dict[str, asyncio.Future] = {}
        self._verified: set[str] = set()

    # ---- helpers -------------------------------------------------------
    def _apps(self) -> dict[str, App]:
        return {a.id: a for a in self.ctx.registry.enabled() if a.panel}

    def _resolve(self, spec: str) -> App:
        apps = self._apps()
        s = re.sub(r"[^a-z0-9 ]", "", str(spec or "").lower()).strip()
        s = re.sub(r"^(my|the)\s+", "", s)
        if s == "chat":
            class _Chat(App):
                id, name, panel = "chat", "Chat", True
                keywords = ["chat", "agent"]
            return _Chat(self.ctx)
        if s in ("tiles", "tile", "home", "homescreen", "home screen", "dashboard"):
            class _Tiles(App):
                id, name, panel = "tiles", "Home", True
                keywords = ["tiles", "home", "dashboard"]
            return _Tiles(self.ctx)
        stem = lambda w: w[:-1] if w.endswith("s") else w
        for a in apps.values():
            names = {a.id, a.name.lower(), *(k.lower() for k in a.keywords)}
            if s in names or stem(s) in {stem(n) for n in names}:
                return a
        words = {stem(w) for w in s.split() if len(w) > 2} - {"app", "panel", "view", "window"}
        for a in apps.values():   # 'file explorer', 'my mail inbox': any word naming the app is enough
            if words & {stem(n) for n in {a.id, a.name.lower(), *(k.lower() for k in a.keywords)}}:
                return a
        listing = ", ".join(f"{a.id} ({a.name})" for a in apps.values()) or "none"
        raise ToolError(f"There is no app called '{spec}'. Apps with a screen: {listing}.", bad_args=True)

    def _screen(self) -> str:
        client = RUN_CLIENT.get()
        if not client:
            raise ToolError("This run has no window attached (it is a background run), so nothing can be shown. "
                            "Tell the user what you found instead.")
        return client

    def _send(self, ev: dict) -> None:
        if not self.ctx.send_ui(self._screen(), {"type": "ui", **ev}):
            raise ToolError("The window that asked is no longer open.")

    def _open(self, app_id: str, params: dict) -> None:
        """Show an app through the same command a key or the palette runs. focus=False leaves the keyboard where it is."""
        self._send({"command": "app.open", "args": {"id": app_id, "params": params, "focus": False}})

    @staticmethod
    def _done(what: str) -> str:
        # A small model, told something is "on screen", tends to invent what it shows. Say plainly that it has seen nothing.
        return (f"Done: {what}. The user can see it now. You have NOT read its contents, so do not list or describe any. "
                "Reply with one short sentence. Only if the user also asked a question about the data, call that app's own read tool next.")

    # ---- tools ---------------------------------------------------------
    def _locate_file(self, what: str) -> tuple[str, str]:
        files = self.ctx.registry.apps.get("files")
        if files is None or not hasattr(files, "locate"):
            raise ToolError("The Files app isn't available.")
        real, shown = files.locate(what)
        return shown, "folder" if real.is_dir() else "file"

    async def open_app(self, a: dict):
        app = self._resolve(a.get("app"))
        if app.id == "files" and (a.get("path") or a.get("query")):   # 'go to Developer/Agentic': a folder, not a search
            shown, kind = self._locate_file(a.get("path") or a.get("query"))
            self._open("files", {"item": shown, "kind": kind})
            return self._done(f"Files now shows the {kind} {shown}. To tell the user what is in it, call files_list_files")
        ignored = [k for k in ("view", "date", "query", "account", "path") if a.get(k) and k not in ACCEPTS.get(app.id, set())]
        params, skipped = {}, ""
        view = str(a.get("view") or "").lower().strip()
        if view:
            allowed = VIEWS.get(app.id)
            if allowed is not None and view in allowed:
                params["view"] = view
            else:   # still show the app; a small model shouldn't have to retry over a view name
                skipped = f"{app.name} has no view '{view}'" + (f" (views: {', '.join(allowed)})" if allowed else "")
        if a.get("date"):
            if app.id != "gcal":
                raise ToolError("Only the calendar takes a date.", bad_args=True)
            try:
                params["date"] = date.fromisoformat(str(a["date"])[:10]).isoformat()
            except ValueError:
                raise ToolError("date must be YYYY-MM-DD, for example 2026-11-16.", bad_args=True)
        for key in ("query", "account"):
            if a.get(key) and key in ACCEPTS.get(app.id, set()):
                params[key] = str(a[key])[:200]
        if "query" in params:   # the words a small model reaches for, turned into what the search box understands
            params["query"] = {"unread": "is:unread", "starred": "is:starred", "important": "is:important", "latest": "", "newest": "",
                               "recent": "", "all": ""}.get(params["query"].lower().strip(), params["query"])
            if app.id != "gmail" and params["query"] in ("is:unread", "is:starred", "is:important"):
                params.pop("query")
        self._open(app.id, params)
        bits = [f"{k} {v}" for k, v in params.items()]
        notes = ([f"{skipped}, so it shows the default page"] if skipped else []) + \
                ([f"{app.name} can't use {', '.join(ignored)}, so that was NOT applied; tell the user"] if ignored else [])
        return self._done(f"{app.name} is on screen" + (f" ({', '.join(bits)})" if bits else "") + (". Note: " + "; ".join(notes) if notes else ""))

    async def _call(self, app_id: str, tool: str, args: dict):
        app = self.ctx.registry.apps.get(app_id)
        t = next((t for t in app.tools() if t.name == tool), None) if app else None
        if t is None:
            raise ToolError(f"{app_id} can't be searched right now.")
        return await t.handler(args)

    async def _find(self, app: App, what: str) -> tuple[str, str]:
        """Turn what the user said ('latest email from sam', 'Diatonic Apps', 'README') into (ref the panel understands, label)."""
        low = what.lower().strip()
        if app.id in ("gmail", "gcal") and "|" in what:
            return what, what
        if app.id == "gmail":
            newest = low in ("", "latest", "newest", "last", "recent", "most recent") or re.fullmatch(r"(?:my |the )?(?:latest|newest|last|most recent)(?: unread)?(?: e-?mails?| messages?| mail)?", low)
            q = "" if newest else what
            r = await self._call("gmail", "list_emails", {"query": q, "limit": 1, "unread_only": "unread" in low and bool(newest)})
            hit = (r.get("emails") if isinstance(r, dict) else None)
            if not isinstance(hit, list) or not hit:
                raise ToolError(f"No email matches '{what}'. Try a Gmail search such as from:name or subject:word.")
            return hit[0]["ref"], hit[0].get("subject", "that email")
        if app.id == "gcal":
            r = await self._call("gcal", "search_events", {"query": what})
            evs = r.get("events") if isinstance(r, dict) else None
            if not isinstance(evs, list) or not evs:
                raise ToolError(f"No calendar event matches '{what}'.")
            now = self.ctx.now().isoformat()
            ev = next((e for e in evs if str(e.get("start", "")) >= now[:10]), evs[0])   # the next one coming up, else the closest past one
            return ev["ref"], ev.get("title", "that event")
        if app.id == "notes":
            r = await self._call("notes", "search_notes", {"query": what})
            if not isinstance(r, list) or not r:
                raise ToolError(f"No note matches '{what}'.")
            hit = next((n for n in r if n["title"].lower() == low), r[0])
            return hit["title"], hit["title"]
        if app.id == "presentations":
            r = await self._call("presentations", "list_presentations", {})
            if not isinstance(r, list) or not r:
                raise ToolError(f"No presentation matches '{what}'.")
            hit = next((d for d in r if d["title"].lower() == low or d["slug"].lower() == low), None)
            if not hit:
                hit = next((d for d in r if low in d["title"].lower() or low in d["slug"].lower()), r[0])
            return hit["slug"], hit["title"]
        if app.id == "files":
            shown, kind = self._locate_file(what)
            return shown, shown
        raise ToolError(f"{app.name} can't open a single item. Use open_app instead.", bad_args=True)

    async def open_item(self, a: dict):
        app = self._resolve(a.get("app"))
        what = str(a.get("what") or a.get("ref") or "").strip()
        if not what and app.id != "gmail":
            raise ToolError("Say which one: a title, a name, a path, or the ref from an earlier result.", bad_args=True)
        ref, label = await self._find(app, what)
        params = {"item": ref[:500]}
        if app.id == "files":
            params["kind"] = self._locate_file(ref)[1]
        self._open(app.id, params)
        return self._done(f"'{label}' is open in {app.name}")

    async def set_layout(self, a: dict):
        mode = str(a.get("mode") or "").lower().strip()
        if mode and mode not in MODES:
            raise ToolError(f"mode must be one of: {', '.join(MODES)}.", bad_args=True)
        side = str(a.get("sidebar") or "").lower().strip()
        if side and side not in ("show", "hide"):
            raise ToolError("sidebar must be 'show' or 'hide'.", bad_args=True)
        if not mode and not side:
            raise ToolError("Give a mode and/or a sidebar setting.", bad_args=True)
        if mode:
            self._send(MODE_COMMANDS[mode])
        if side:
            self._send({"command": "sidebar.set", "args": {"mode": side}})
        return self._done("layout changed (" + ", ".join(f"{k} {v}" for k, v in (("mode", mode), ("sidebar", side)) if v) + ")")

    async def pin_app(self, a: dict):
        """Pin an app to the status bar."""
        app = self._resolve(a["app"])
        pins = list(self.ctx.config.data["appearance"].get("statusbar_pins") or [
            {"id": "pin:gmail", "label": "Mail", "icon": "mail", "appId": "gmail", "zone": "center"},
            {"id": "pin:shell", "label": "Terminal", "icon": "command", "appId": "shell", "zone": "center"},
            {"id": "pin:files", "label": "Files", "icon": "folder", "appId": "files", "zone": "center"},
        ])
        pin_id = f"pin:{app.id}"
        existing = next((p for p in pins if p.get("appId") == app.id or p.get("id") == pin_id), None)
        zone = str(a.get("zone") or "center")
        if not existing:
            pins.append({"id": pin_id, "label": app.name, "icon": getattr(app, "icon", "box") or "box", "appId": app.id, "zone": zone})
            self.ctx.config.update({"appearance": {"statusbar_pins": pins}})
        return self._done(f"pinned {app.name} to the status bar")

    async def unpin_app(self, a: dict):
        """Unpin an app from the status bar."""
        app = self._resolve(a["app"])
        pins = list(self.ctx.config.data["appearance"].get("statusbar_pins") or [
            {"id": "pin:gmail", "label": "Mail", "icon": "mail", "appId": "gmail", "zone": "center"},
            {"id": "pin:shell", "label": "Terminal", "icon": "command", "appId": "shell", "zone": "center"},
            {"id": "pin:files", "label": "Files", "icon": "folder", "appId": "files", "zone": "center"},
        ])
        pins = [p for p in pins if p.get("appId") != app.id and p.get("id") != f"pin:{app.id}"]
        self.ctx.config.update({"appearance": {"statusbar_pins": pins}})
        return self._done(f"unpinned {app.name} from the status bar")

    async def save_macro(self, a: dict):
        """Save a declarative macro (named sequence of commands)."""
        self._guard_mod()
        from ... import macros
        name = str(a.get("name") or "").strip()
        if not name:
            raise ToolError("Provide a name for the macro.", bad_args=True)
        mid = str(a.get("id") or "").strip()
        if not mid:
            mid = "macro_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        mid = mid[:macros.MAX_TEXT]
        desc = str(a.get("description") or "").strip()[:macros.MAX_DESC]

        raw_steps = a.get("steps")
        if not raw_steps:
            app_id = a.get("app")
            if app_id:
                raw_steps = [{"command": "app.open", "args": {"id": self._resolve(app_id).id}}]
            else:
                raw_steps = [{"command": "layout.preset.side-by-side"}]

        try:
            cleaned = macros.clean_macro({"id": mid, "name": name, "description": desc, "steps": raw_steps})
        except macros.MacroError as e:
            raise ToolError(str(e), bad_args=True)

        all_macros = [m for m in macros.load() if m["id"] != mid]
        all_macros.append(cleaned)
        macros.save(all_macros)
        self.ctx.broadcast({"type": "macros_changed"})
        return self._done(f"saved macro '{cleaned['name']}' with {len(cleaned['steps'])} step(s)")

    async def run_macro(self, a: dict):
        """Trigger execution of a saved macro by name or id."""
        from ... import macros
        target = str(a.get("name") or a.get("id") or "").strip().lower()
        if not target:
            raise ToolError("Provide the name or id of the macro to run.", bad_args=True)
        all_macros = macros.load()
        found = next((m for m in all_macros if m["id"].lower() == target or m["name"].lower() == target), None)
        if not found:
            names = ", ".join(f"'{m['name']}'" for m in all_macros) or "none"
            raise ToolError(f"No macro '{target}'. Available macros: {names}.", bad_args=True)
        self.ctx.broadcast({"type": "run_macro", "macro_id": found["id"]})
        return self._done(f"running macro '{found['name']}'")

    async def list_macros(self, a: dict):
        """List all saved macros."""
        from ... import macros
        all_macros = macros.load()
        if not all_macros:
            return {"macros": [], "summary": "No macros saved yet."}
        res = [{"id": m["id"], "name": m["name"], "description": m["description"], "steps_count": len(m["steps"])} for m in all_macros]
        names = ", ".join(m["name"] for m in all_macros)
        return {"macros": res, "summary": f"Found {len(res)} macro(s): {names}"}

    async def delete_macro(self, a: dict):
        """Delete a saved macro by name or id."""
        self._guard_mod()
        from ... import macros
        target = str(a.get("name") or a.get("id") or "").strip().lower()
        if not target:
            raise ToolError("Provide the name or id of the macro to delete.", bad_args=True)
        all_macros = macros.load()
        found = next((m for m in all_macros if m["id"].lower() == target or m["name"].lower() == target), None)
        if not found:
            raise ToolError(f"No macro '{target}' found.", bad_args=True)
        remaining = [m for m in all_macros if m["id"] != found["id"]]
        macros.save(remaining)
        self.ctx.broadcast({"type": "macros_changed"})
        return self._done(f"deleted macro '{found['name']}'")

    async def save_layout(self, a: dict):
        """Save a named layout layout."""
        self._guard_mod()
        from ... import layouts
        name = str(a.get("name") or "").strip()
        if not name:
            raise ToolError("Provide a name for the layout.", bad_args=True)
        wid = str(a.get("id") or "").strip()
        if not wid:
            wid = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        wid = wid[:layouts.MAX_TEXT]
        desc = str(a.get("description") or "").strip()[:layouts.MAX_DESC]

        raw_tree = a.get("tree")
        if not raw_tree:
            apps = self._app_list(a.get("apps")) if a.get("apps") else ["chat", "files"]
            if len(apps) == 1:
                raw_tree = {"v": 1, "root": {"type": "stack", "id": "s_main", "active": 0, "tabs": [{"id": f"t_{apps[0]}", "app": apps[0], "params": {}}]}}
            else:
                raw_tree = {
                    "v": 1,
                    "root": {
                        "type": "split",
                        "dir": "row",
                        "sizes": [round(1.0 / len(apps), 2)] * len(apps),
                        "children": [
                            {"type": "stack", "id": f"s_{idx}", "active": 0, "tabs": [{"id": f"t_{app}", "app": app, "params": {}}]}
                            for idx, app in enumerate(apps)
                        ]
                    }
                }

        try:
            cleaned = layouts.clean_layout({"id": wid, "name": name, "description": desc, "tree": raw_tree})
        except layouts.LayoutError as e:
            raise ToolError(str(e), bad_args=True)

        all_ws = [w for w in layouts.load() if w["id"] != wid]
        all_ws.append(cleaned)
        layouts.save(all_ws)
        self.ctx.broadcast({"type": "layouts_changed"})
        return self._done(f"saved layout '{cleaned['name']}'")

    async def switch_layout(self, a: dict):
        """Switch to a saved layout by name or id."""
        from ... import layouts
        target = str(a.get("name") or a.get("id") or "").strip().lower()
        if not target:
            raise ToolError("Provide the name or id of the layout to switch to.", bad_args=True)
        all_ws = layouts.load()
        found = next((w for w in all_ws if w["id"].lower() == target or w["name"].lower() == target), None)
        if not found:
            names = ", ".join(f"'{w['name']}'" for w in all_ws) or "none"
            raise ToolError(f"No layout '{target}'. Available layouts: {names}.", bad_args=True)
        self.ctx.broadcast({"type": "switch_layout", "layout_id": found["id"], "tree": found["tree"]})
        return self._done(f"switched to layout '{found['name']}'")

    async def list_layouts(self, a: dict):
        """List all saved layouts."""
        from ... import layouts
        all_ws = layouts.load()
        if not all_ws:
            return {"layouts": [], "summary": "No layouts saved yet."}
        res = [{"id": w["id"], "name": w["name"], "description": w["description"]} for w in all_ws]
        names = ", ".join(w["name"] for w in all_ws)
        return {"layouts": res, "summary": f"Found {len(res)} layout(s): {names}"}

    async def delete_layout(self, a: dict):
        """Delete a saved layout by name or id."""
        self._guard_mod()
        from ... import layouts
        target = str(a.get("name") or a.get("id") or "").strip().lower()
        if not target:
            raise ToolError("Provide the name or id of the layout to delete.", bad_args=True)
        all_ws = layouts.load()
        found = next((w for w in all_ws if w["id"].lower() == target or w["name"].lower() == target), None)
        if not found:
            raise ToolError(f"No layout '{target}' found.", bad_args=True)
        remaining = [w for w in all_ws if w["id"] != found["id"]]
        layouts.save(remaining)
        self.ctx.broadcast({"type": "layouts_changed"})
        return self._done(f"deleted layout '{found['name']}'")

    async def arrange_panels(self, a: dict):
        """Arrange apps spatially on screen (e.g. 'mail left, calendar top-right, chat bottom-right')."""
        from .layout_builder import build_layout_tree
        positions = {}
        direct_keys = ("left", "right", "top", "bottom", "top_left", "top_right", "bottom_left", "bottom_right", "center",
                       "top-left", "top-right", "bottom-left", "bottom-right")
        for k in direct_keys:
            if a.get(k):
                try:
                    resolved = self._resolve(a[k]).id
                    positions[k] = resolved
                except Exception:
                    pass

        placements = a.get("placements")
        if isinstance(placements, list):
            for p in placements:
                if isinstance(p, dict) and p.get("position") and p.get("app"):
                    try:
                        resolved = self._resolve(p["app"]).id
                        positions[p["position"]] = resolved
                    except Exception:
                        pass

        if not positions:
            raise ToolError("Specify where to place apps: left, right, top, bottom, top_right, bottom_right, top_left, bottom_left, or center.", bad_args=True)

        tree = build_layout_tree(positions)
        summary_items = [f"{app} ({pos.replace('_', ' ')})" for pos, app in positions.items()]
        summary = f"arranged layout: {', '.join(summary_items)}"
        if hasattr(self.ctx, "mods") and hasattr(self.ctx.mods, "log_layout"):
            self.ctx.mods.log_layout(before=None, after=tree, what=summary)
        self.ctx.broadcast({"type": "switch_layout", "tree": tree})
        self._changed(summary)
        return self._done(f"{summary}. You can say 'undo' to revert.")

    async def undo_layout(self, a: dict):
        """Undo the most recent layout arrangement."""
        self.ctx.broadcast({"type": "undo_layout"})
        return self._done("reverted layout change")

    # ---- changing the interface itself ---------------------------------
    default_allow = ["set_home", "set_theme", "add_style", "add_button", "list_mods", "remove_mod", "revert", "add_action", "set_fields", "remove_action",
                     "pin_app", "unpin_app", "save_macro", "run_macro", "list_macros", "delete_macro",
                     "save_layout", "switch_layout", "list_layouts", "delete_layout",
                     "arrange_panels", "undo_layout",
                     "create_app", "add_list", "add_field", "remove_field", "remove_list"]   # data apps need no network and run no code, so no card
    untrusted_note = ("This run has read email, whose text anyone can write, so it can't change the interface. "
                      "Ask again in a new chat.")

    def _blocked(self, _a=None) -> str | None:
        if not RUN_CLIENT.get():
            return "This is a background run with no window attached, so it can't change the interface."
        if session.untrusted_sources():
            return self.untrusted_note
        return None

    def _guard_mod(self) -> None:
        if (why := self._blocked()):
            raise ToolError(why)

    def _changed(self, summary: str) -> None:
        self.ctx.broadcast({"type": "mods_changed", "by": RUN_CLIENT.get(), "summary": summary})

    async def set_theme(self, a: dict):
        self._guard_mod()
        keys = ("theme", "accent", "density", "font", "font_size", "radius", "width", "sidebar_width", "show_status_bar", "show_suggestions", "show_speak_button")
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

    async def add_panel(self, a: dict):
        self._guard_mod()
        await self._verify("panel", a)
        mod = self._put("panel", a)
        return self._done(f"panel '{mod['title']}' was added to the left rail")

    async def list_mods(self, a: dict):
        if a.get("name"):
            try:
                m = self._find_code_mod(a["name"])
            except ToolError:
                m = None
            if m:
                return {"name": m["name"], "kind": m["kind"], "title": m["title"], "code": m["code"]}   # to copy a piece of it for edit_panel
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

    # ---- file-based custom apps in ~/.diatonic/apps/<id>/ ----------------
    async def create_file_app(self, a: dict):
        self._guard_mod()
        app_id = a.get("id") or a.get("name")
        try:
            manifest = self.ctx.fileapps.create_app(app_id, a.get("name", ""), a.get("description", ""), a.get("icon", "box"))
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        return self._done(f"Created custom app '{manifest['name']}' in ~/.diatonic/apps/{manifest['id']}/")

    async def write_app_file(self, a: dict):
        self._guard_mod()
        app_id = a.get("app_id")
        path = a.get("path")
        content = a.get("content", "")
        if path == "index.js":
            await self._verify("panel", {"code": content})
        try:
            res = self.ctx.fileapps.write_file(app_id, path, content)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        return self._done(f"Saved '{path}' in custom app '{app_id}' ({res['bytes']} bytes)")

    async def _write_file_preview(self, a: dict) -> str:
        app_id = a.get("app_id")
        path = a.get("path")
        content = a.get("content", "")
        try:
            old = self.ctx.fileapps.read_file(app_id, path)
            return self._diff(old, content)
        except Exception:
            return "\n".join("+ " + line for line in content.splitlines())

    async def patch_app_file(self, a: dict):
        self._guard_mod()
        app_id = a.get("app_id")
        path = a.get("path")
        old = a.get("old", "")
        new = a.get("new", "")
        try:
            cur = self.ctx.fileapps.read_file(app_id, path)
            updated = cur.replace(old, new, 1) if old in cur else cur
            if path == "index.js":
                await self._verify("panel", {"code": updated})
            self.ctx.fileapps.patch_file(app_id, path, old, new)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        return self._done(f"Patched '{path}' in custom app '{app_id}'")

    async def _patch_file_preview(self, a: dict) -> str:
        app_id = a.get("app_id")
        path = a.get("path")
        old = a.get("old", "")
        new = a.get("new", "")
        try:
            cur = self.ctx.fileapps.read_file(app_id, path)
            updated = cur.replace(old, new, 1)
            return self._diff(cur, updated)
        except Exception:
            return f"- {old}\n+ {new}"

    async def read_app_file(self, a: dict):
        app_id = a.get("app_id")
        path = a.get("path")
        try:
            return self.ctx.fileapps.read_file(app_id, path)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)

    async def list_app_files(self, a: dict):
        app_id = a.get("app_id")
        if app_id:
            info = self.ctx.fileapps.get_app(app_id)
            if not info:
                raise ToolError(f"No custom app found with id '{app_id}'.", bad_args=True)
            return info
        return self.ctx.fileapps.list_apps()

    async def read_app_logs(self, a: dict):
        app_id = a.get("app_id")
        logs = self.ctx.fileapps.get_logs(app_id)
        if not logs:
            return f"No errors or runtime logs recorded for '{app_id}'."
        return logs

    async def export_file_app(self, a: dict):
        app_id = a.get("app_id")
        try:
            return self.ctx.fileapps.export_app(app_id)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)

    async def import_file_app(self, a: dict):
        self._guard_mod()
        bundle = a.get("bundle") or a
        overwrite = bool(a.get("overwrite", False))
        try:
            res = self.ctx.fileapps.import_app(bundle, overwrite=overwrite)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        return self._done(f"Imported custom app '{res['id']}' with {len(res['files'])} files")

    def resolve_verify(self, rid: str, result: dict) -> bool:
        """The window's answer for a pending verification (see _verify)."""
        fut = self._verifying.get(rid)
        if fut is None or fut.done():
            return False
        fut.set_result(result if isinstance(result, dict) else {})
        return True

    @staticmethod
    def _verify_problem(kind: str, r: dict) -> str | None:
        errors = [str(e)[:200] for e in (r.get("errors") or []) if e]
        if errors:
            return (f"The code failed when it was run: {errors[0]}. Fix the code and call the tool again with all of it. "
                    "Use only `diatonic`, `el` and `ctx` as described.")
        if kind == "panel" and not r.get("rendered") and not r.get("usedApi"):
            return ("The panel showed nothing. Build elements and add them with el.append(...), "
                    "for example: el.append(diatonic.h('div', {}, 'Hello')).")
        return None

    def _panel_tools(self) -> list[str]:
        """Names a panel may pass to ctx.tool (read-only tools of enabled apps, as <app>_<tool>), so a wrong guess fails the test run, not the user."""
        return sorted(f"{a.id}_{t.name}" for a in self.ctx.registry.enabled() if a.id not in ("shell", "ui") for t in a.tools() if t.risk == "read")

    async def _verify(self, kind: str, a: dict) -> None:
        """Run new panel or script code in a sandbox in the user's open window BEFORE asking them to approve it. If it breaks, the model hears
        why and tries again, so the user is only shown code that at least runs. The sandbox has no network and no access to the page, and
        calls to the server hang (so only what happens before the first one is checked). No window, or no answer: the approval card is the check."""
        client = RUN_CLIENT.get()
        code = str(a.get("code") or "")
        if not client or not code.strip():
            return
        key = hashlib.sha1(f"{kind}\0{code}".encode()).hexdigest()
        if key in self._verified:
            return
        rid = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._verifying[rid] = fut
        try:
            if not self.ctx.send_ui(client, {"type": "ui", "action": "verify", "id": rid, "kind": kind, "code": code,
                                             "tools": self._panel_tools(), "trackers": sorted(self._data_apps())}):
                return
            try:
                result = await asyncio.wait_for(fut, VERIFY_WAIT)
            except asyncio.TimeoutError:
                self._verified.add(key)    # don't wait again when the approved tool runs
                return
        finally:
            self._verifying.pop(rid, None)
        if (problem := self._verify_problem(kind, result)):
            raise ToolError(problem)
        self._verified.add(key)

    @staticmethod
    def _what(a: dict) -> str:
        """The model's one-sentence account of what the code does, shown on the approval card above the code ('' if it gave none)."""
        s = " ".join(str(a.get("summary") or "").split())[:200]
        return f": {s}" if s else ""

    @staticmethod
    def _slug(name) -> str:
        return str(name or "").strip().lower().replace(" ", "-").replace("_", "-")

    @staticmethod
    def _diff(old: str, new: str) -> str:
        """What changes, as the approval card shows it: -removed and +added lines with a little context."""
        lines = difflib.unified_diff(old.strip().splitlines(), new.strip().splitlines(), lineterm="", n=2)
        return "\n".join(l for l in lines if not l.startswith(("---", "+++"))) or "(no change)"

    def _code_review(self, kind: str):
        async def preview(a: dict) -> str:
            await self._verify(kind, a)
            before = self.ctx.mods.get(self._slug(a.get("name")))
            if before and before["kind"] == kind and before["code"].strip() != str(a.get("code") or "").strip():
                return "Changes to the existing " + kind + ":\n" + self._diff(before["code"], str(a.get("code") or ""))   # an update: show only what differs
            return self._code_preview(a, "code")
        return preview

    def _find_code_mod(self, name) -> dict:
        """A panel or script by the name the model gave, forgiving: the exact name, then a name or title that contains it (or is part of it),
        then the only panel or script there is. A small model names things after the request ("pomodoro"), not after what it called them ("timer")."""
        mods = [m for m in self.ctx.mods.list() if m["kind"] in ("panel", "script")]
        slug = self._slug(name)
        for pick in (lambda m: m["name"] == slug,
                     lambda m: slug and (slug in m["name"] or m["name"] in slug or slug in self._slug(m["title"]) or self._slug(m["title"]) in slug)):
            hits = [m for m in mods if pick(m)]
            if len(hits) == 1:
                return hits[0]
        if len(mods) == 1:
            return mods[0]
        raise ToolError("There is no panel or script with that name. Existing: " + (", ".join(f"{m['name']}" for m in mods) or "none") + ". list_mods shows them.", bad_args=True)

    @staticmethod
    def _locate(code: str, old: str) -> tuple[int, int] | None:
        """Where `old` is in `code`: exactly as written, or failing that ignoring differences in spaces, indentation and line breaks (the usual way a
        small model's copy goes wrong). None if it isn't there once."""
        n = code.count(old)
        if n == 1:
            i = code.index(old)
            return i, i + len(old)
        if n > 1:
            return None
        parts = old.split()
        if not parts:
            return None
        hits = list(re.finditer(r"\s*".join(re.escape(p) for p in parts), code))
        return (hits[0].start(), hits[0].end()) if len(hits) == 1 else None

    def _edited(self, a: dict) -> tuple[dict, str]:
        """The panel or script being edited and its code with `old` replaced by `new`; a clear error if `old` doesn't match once."""
        m = self._find_code_mod(a.get("name"))
        old, new = str(a.get("old") or ""), str(a.get("new") if a.get("new") is not None else "")
        if not old.strip():
            raise ToolError("old must be a piece of the current code. list_mods with this name shows the code.", bad_args=True)
        at = self._locate(m["code"], old)
        if at is None:
            n = m["code"].count(old)
            if n > 1:
                raise ToolError(f"old appears {n} times. Include more of the surrounding code so it matches once.", bad_args=True)
            close = difflib.get_close_matches(old.strip().splitlines()[0].strip(), [l.strip() for l in m["code"].splitlines() if l.strip()], n=3, cutoff=0.5)
            raise ToolError("old is not in the code. " + (("The closest lines are: " + " | ".join(close) + ". ") if close else "")
                            + "Copy a line from the code (list_mods with this name shows it) and put the change in new.", bad_args=True)
        return m, m["code"][:at[0]] + new + m["code"][at[1]:]

    async def edit_panel(self, a: dict):
        self._guard_mod()
        m, code = self._edited(a)
        await self._verify(m["kind"], {"code": code})
        mod = self._put(m["kind"], {"name": m["name"], "title": m["title"], "code": code})
        return self._done(f"{m['kind']} '{mod['name']}' was changed")

    async def _edit_preview(self, a: dict) -> str:
        m, code = self._edited(a)
        await self._verify(m["kind"], {"code": code})
        return self._diff(m["code"], code)

    @staticmethod
    def _code_preview(a: dict, key: str) -> str:
        return "\n".join("+ " + line for line in str(a.get(key, "")).splitlines())

    @staticmethod
    def _calls_server(a: dict) -> str:
        return " (it talks to the server)" if re.search(r"\bapi\b|fetch\s*\(|/api/|ctx\.(?:tool|data)\b", str(a.get("code", ""))) else ""

    def _existing(self) -> str:
        """Names of the changes already made, so 'change that button' reuses a name instead of adding a second one."""
        mods = self.ctx.mods.list()[-12:] if getattr(self.ctx, "mods", None) else []
        if not mods:
            return ""
        return " Existing changes (pass the same name to replace one): " + ", ".join(f"{m['name']} ({m['kind']} '{m['title']}')" for m in mods) + "."

    # ---- building API apps: small steps, each checked, the endpoint tested as soon as it's added ----------
    def _api_apps(self) -> dict[str, dict]:
        return {m["name"]: json.loads(m["code"]) for m in (self.ctx.mods.list() if getattr(self.ctx, "mods", None) else []) if m["kind"] == "app"}

    def _manifest(self, name: str) -> tuple[str, dict]:
        apps = self._api_apps()
        n = re.sub(r"[^a-z0-9-]+", "-", str(name or "").lower()).strip("-")
        if n not in apps:   # "the weather app", "Weather": match by title too
            n = next((k for k, m in apps.items() if m["title"].lower() == str(name).lower().strip() or k.startswith(n) and n), n)
        if n not in apps:
            raise ToolError(f"No API app called '{name}'. API apps: {', '.join(apps) or 'none yet (use create_app)'}.", bad_args=True)
        return n, apps[n]

    def _save_app(self, name: str, m: dict, what: str) -> dict:
        try:
            mod = self.ctx.mods.put("app", name, json.dumps(m), m["title"])
        except ModError as e:
            raise ToolError(str(e), bad_args=True)
        self._changed(what)
        return json.loads(mod["code"])

    def _key(self, name: str) -> str | None:
        app = self.ctx.registry.apps.get(name) if getattr(self.ctx, "registry", None) else None
        return app.key() if app is not None and hasattr(app, "key") else None

    async def _try(self, name: str, m: dict, action: str, test: dict | None = None) -> tuple[str, list[str]]:
        """Call a GET action with its defaults plus any test values. Returns (what happened, value paths found)."""
        a = m["actions"][action]
        test = test or {}
        if a["method"] != "GET":
            return "not tested (it changes data, so it only runs when asked)", []
        missing = [p["name"] for p in a["params"] if p["required"] and not p["default"] and p["name"] not in test]
        if missing:
            return f"NOT TESTED: call add_action again with test_with giving example values for {', '.join(missing)}", []
        if m["auth"]["type"] != "none" and not self._key(name):
            return "not tested yet: the API key isn't set. Ask the user to open the app in the left rail and paste the key, then call test_app", []
        try:
            r = await api_call(self.ctx.http, m, action, test, self._key(name))
        except ToolError as e:
            return f"TEST FAILED: {e}. Fix the path, params or key settings and add the action again", []
        paths = key_paths(r["data"])
        return f"test call OK ({r['status']})", paths

    # ---- building data apps (no API, no code) ----------------------------
    def _data_apps(self) -> dict[str, dict]:
        return {m["name"]: json.loads(m["code"]) for m in (self.ctx.mods.list() if getattr(self.ctx, "mods", None) else []) if m["kind"] == "data"}

    def _data(self, name: str) -> tuple[str, dict]:
        apps = self._data_apps()
        n = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
        hit = next((k for k, m in apps.items() if k == n or m["title"].lower().replace(" ", "") == n or n and (k.startswith(n) or n.startswith(k))), None)
        if hit is None:
            raise ToolError(f"No app called '{name}'. Your apps: {', '.join(f'{k} ({m['title']})' for k, m in apps.items()) or 'none yet (use create_app)'}.", bad_args=True)
        return hit, apps[hit]

    def _save_data(self, name: str, m: dict, what: str) -> dict:
        try:
            mod = self.ctx.mods.put("data", name, json.dumps(m), m["title"])
        except ModError as e:
            raise ToolError(str(e), bad_args=True)
        self._changed(what)
        self.ctx.broadcast({"type": "app_changed", "app": name})
        return json.loads(mod["code"])

    def _tools_line(self, name: str) -> str:
        app = self.ctx.registry.apps.get(name)
        return ", ".join(f"{name}_{t.name}" for t in app.tools()) if app else ""

    async def create_app(self, a: dict):
        """A personal app with no API: lists of things with fields (a tracker, a log, a reading list...)."""
        self._guard_mod()
        title = str(a.get("title") or str(a.get("name") or "").replace("_", " ").title()).strip()
        name = re.sub(r"[^a-z0-9]+", "", str(a.get("name") or title).lower())[:20]
        if len(name) < 2:
            raise ToolError("Give the app a short name (lowercase, one word).", bad_args=True)
        req = session.REQUEST.get()
        linked = self.connected_to(req)
        if linked and not LOCAL_ONLY.search(req.lower()):
            names = ", ".join(self.ctx.registry.apps[x].name for x in linked)
            raise ToolError(f"Not a local list app: this request is about {names}, and a local app can't create or change anything there. "
                            f"Use create_connected_app with apps={','.join(linked)} instead, then add_app_action for each thing the user asked for.", bad_args=True)
        clash = self.ctx.registry.apps.get(name)
        if clash is not None and not getattr(clash, "data_app", False):
            raise ToolError(f"'{name}' is already an app; pick another name.", bad_args=True)
        old = self._data_apps().get(name, {})
        try:
            m = check_data_manifest({"title": title or name, "description": a.get("description"), "lists": old.get("lists", {})})
        except DataSpecError as e:
            raise ToolError(str(e), bad_args=True)
        self._save_data(name, m, f"{'updated' if old else 'created'} app '{m['title']}'")
        return (f"App '{m['title']}' ({name}) is {'updated' if old else 'created'}, with its own icon in the left rail. "
                f"Next: add_list once for each kind of thing the user said it holds, named after their words.")

    async def add_list(self, a: dict):
        self._guard_mod()
        name, m = self._data(a.get("app"))
        lst = re.sub(r"[^a-z0-9_]+", "_", str(a.get("name") or "").lower()).strip("_")
        old = m["lists"].get(lst)
        parent = re.sub(r"[^a-z0-9_]+", "_", str(a.get("parent") or "").lower()).strip("_") or (old or {}).get("parent")
        if parent and parent not in m["lists"]:   # 'subject' for 'subjects'
            parent = next((k for k, l in m["lists"].items() if l["item"] == parent), parent)
        lists = dict(m["lists"])
        lists[lst] = {"item": a.get("item") or (old or {}).get("item"), "parent": parent,
                      "fields": a.get("fields") if a.get("fields") else (old or {}).get("fields", [])}
        if old is None:   # keep parents before children
            lists = {k: v for k, v in lists.items() if k != lst} | {lst: lists[lst]}
        try:
            m = check_data_manifest({**m, "lists": lists})
        except DataSpecError as e:
            raise ToolError(str(e), bad_args=True)
        self._save_data(name, m, f"{'changed' if old else 'added'} the list '{lst}' in {m['title']}")
        l = m["lists"][lst]
        return (f"{'Changed' if old else 'Added'} the list '{lst}' in {m['title']}" + (f" (inside {l['parent']})" if l["parent"] else "")
                + f", fields: {', '.join(f['name'] + ':' + f['type'] for f in l['fields'])}. The panel shows it now. "
                f"Your tools for it (don't mention their names to the user): {self._tools_line(name)}.")

    def _list_of(self, m: dict, lst: str) -> str:
        key = re.sub(r"[^a-z0-9_]+", "_", str(lst or "").lower()).strip("_")
        hit = key if key in m["lists"] else next((k for k, l in m["lists"].items() if l["item"] == key or k.startswith(key) and key), None)
        if hit is None:
            raise ToolError(f"{m['title']} has no list '{lst}'. Lists: {', '.join(m['lists'])}.", bad_args=True)
        return hit

    async def add_field(self, a: dict):
        self._guard_mod()
        name, m = self._data(a.get("app"))
        lst = self._list_of(m, a.get("list"))
        try:
            new = parse_fields(str(a.get("field") or ""))[1:] or parse_fields(str(a.get("field") or ""))
        except DataSpecError as e:
            raise ToolError(str(e), bad_args=True)
        fields = [f for f in m["lists"][lst]["fields"] if f["name"] not in {n["name"] for n in new}] + new
        m["lists"][lst]["fields"] = fields
        try:
            m = check_data_manifest(m)
        except DataSpecError as e:
            raise ToolError(str(e), bad_args=True)
        self._save_data(name, m, f"added {', '.join(f['name'] for f in new)} to {lst} in {m['title']}")
        return (f"{lst} now have: {', '.join(f['name'] + ':' + f['type'] for f in m['lists'][lst]['fields'])}. Existing items are kept; "
                f"the user can set it in the app or by asking you. (Don't mention tool names to the user.)")

    async def remove_field(self, a: dict):
        self._guard_mod()
        name, m = self._data(a.get("app"))
        lst = self._list_of(m, a.get("list"))
        f = str(a.get("field") or "").strip().lower()
        fields = m["lists"][lst]["fields"]
        if f == fields[0]["name"]:
            raise ToolError("That's the item's name; it can't be removed.", bad_args=True)
        if f not in {x["name"] for x in fields}:
            raise ToolError(f"{lst} has no field '{f}'. Fields: {', '.join(x['name'] for x in fields)}.", bad_args=True)
        m["lists"][lst]["fields"] = [x for x in fields if x["name"] != f]
        self._save_data(name, m, f"removed {f} from {lst} in {m['title']}")
        return f"Removed {f} from {lst} (values are kept and come back if it's added again or the change is undone)."

    async def remove_list(self, a: dict):
        self._guard_mod()
        name, m = self._data(a.get("app"))
        lst = str(a.get("name") or "")
        if lst not in m["lists"]:
            raise ToolError(f"{m['title']} has no list '{lst}'. Lists: {', '.join(m['lists'])}.", bad_args=True)
        kids = [k for k, l in m["lists"].items() if l["parent"] == lst]
        if kids:
            raise ToolError(f"Remove the lists inside it first: {', '.join(kids)}.", bad_args=True)
        m["lists"].pop(lst)
        self._save_data(name, m, f"removed the list '{lst}' from {m['title']}")
        return f"Removed the list '{lst}' (its items are kept and come back if the list is added again or the change is undone)."

    async def connect_api(self, a: dict):
        self._guard_mod()
        title = str(a.get("title") or str(a.get("name") or "").replace("-", " ").title()).strip()
        name = re.sub(r"[^a-z0-9-]+", "-", str(a.get("name") or title).lower()).strip("-")[:30]
        builtin = self.ctx.registry.apps.get(name)
        if builtin is not None and not getattr(builtin, "api_app", False):
            raise ToolError(f"'{name}' is a built-in app; pick another name.", bad_args=True)
        old = self._api_apps().get(name, {})
        try:
            m = check_manifest({"title": title or name, "base_url": a.get("base_url"), "description": a.get("description"),
                                "auth": {"type": a.get("key_in") or "none", "name": a.get("key_name")}, "actions": old.get("actions", {})})
        except AppSpecError as e:
            raise ToolError(str(e), bad_args=True)
        self._save_app(name, m, f"{'updated' if old else 'created'} app '{m['title']}'")
        need = m["auth"]["type"] != "none"
        return (f"App '{m['title']}' ({name}) is {'updated' if old else 'created'} with its own icon in the left rail. "
                f"Next: add each endpoint with add_action (app '{name}')."
                + (" It needs an API key: tell the user to open the app and paste it there. Never ask for the key in chat." if need else ""))

    async def add_action(self, a: dict):
        self._guard_mod()
        name, m = self._manifest(a.get("app"))
        act = re.sub(r"[^a-z0-9_]+", "_", str(a.get("name") or "").lower()).strip("_")
        old = m["actions"].get(act, {})
        try:
            m["actions"][act] = {"title": a.get("title") or old.get("title") or act.replace("_", " ").capitalize(),
                                 "method": a.get("method") or old.get("method") or "GET", "path": a.get("path") or old.get("path") or "",
                                 "params": parse_params(a["params"]) if a.get("params") is not None else old.get("params", []),
                                 "fields": old.get("fields", [])}
            m = check_manifest(m)
        except AppSpecError as e:
            raise ToolError(str(e), bad_args=True)
        test = dict(re.findall(r"([A-Za-z_][\w.\-]*)\s*[=:]\s*([^,]+)", str(a.get("test_with") or "")))
        result, paths = await self._try(name, m, act, {k: v.strip() for k, v in test.items()})
        if paths:
            m["actions"][act]["sample_keys"] = paths
            m["actions"][act]["fields"] = [f for f in m["actions"][act]["fields"] if f in paths]
        self._save_app(name, m, f"{'changed' if old else 'added'} action '{act}' in {m['title']}")
        tip = (" Values it returns: " + ", ".join(paths[:30]) + ". Choose the ones to show with set_fields.") if paths else ""
        return f"Saved action '{act}' ({m['actions'][act]['method']} {m['actions'][act]['path']}) in {m['title']}: {result}.{tip}"

    async def set_fields(self, a: dict):
        self._guard_mod()
        name, m = self._manifest(a.get("app"))
        act = str(a.get("action") or "")
        if act not in m["actions"]:
            raise ToolError(f"{m['title']} has no action '{act}'. Actions: {', '.join(m['actions'])}.", bad_args=True)
        fields = a.get("fields") if isinstance(a.get("fields"), list) else re.split(r"\s*,\s*", str(a.get("fields") or ""))
        fields = [f.strip() for f in fields if f and f.strip()]
        known = m["actions"][act]["sample_keys"]
        if known:
            import difflib
            bad = [f for f in fields if f not in known]
            if bad:
                hints = {f: difflib.get_close_matches(f, known, 1, 0.4) for f in bad}
                raise ToolError("Not in the response: " + "; ".join(f"{f} (did you mean {h[0]}?)" if h else f for f, h in hints.items())
                                + f". Available: {', '.join(known[:30])}.", bad_args=True)
        m["actions"][act]["fields"] = fields
        self._save_app(name, m, f"chose what {m['title']} shows")
        return f"{m['title']} › {m['actions'][act]['title']} now shows: {', '.join(fields) or 'the whole response'}."

    async def remove_action(self, a: dict):
        self._guard_mod()
        name, m = self._manifest(a.get("app"))
        if m["actions"].pop(str(a.get("action") or ""), None) is None:
            raise ToolError(f"{m['title']} has no action '{a.get('action')}'. Actions: {', '.join(m['actions'])}.", bad_args=True)
        self._save_app(name, m, f"removed an action from {m['title']}")
        return f"Removed. {m['title']} actions now: {', '.join(m['actions']) or 'none'}."

    async def test_app(self, a: dict):
        name, m = self._manifest(a.get("app"))
        if not m["actions"]:
            return f"{m['title']} has no actions yet. Add them with add_action."
        lines = []
        for act in m["actions"]:
            result, paths = await self._try(name, m, act)
            if paths:
                m["actions"][act]["sample_keys"] = paths
            lines.append(f"{act}: {result}")
        self.ctx.mods.put("app", name, json.dumps(m), m["title"]) if any(m["actions"][x]["sample_keys"] for x in m["actions"]) else None
        return "\n".join(lines)

    # ---- apps built on the user's connected apps (Calendar, Mail, GitHub...) -------------------------------
    def connected_to(self, text: str) -> list[str]:
        """Connected apps a request is about ('an app to schedule Google Meets' -> gcal), if they're enabled."""
        t = (text or "").lower()
        on = {a.id for a in self.ctx.registry.enabled()} if getattr(self.ctx, "registry", None) else set()
        return [k for k, rx in CONNECTED.items() if k in on and re.search(rx, t)]

    def _tool_apps(self) -> dict[str, dict]:
        return {m["name"]: json.loads(m["code"]) for m in (self.ctx.mods.list() if getattr(self.ctx, "mods", None) else []) if m["kind"] == "tools"}

    def _toolapp(self, name: str) -> tuple[str, dict]:
        apps = self._tool_apps()
        n = re.sub(r"[^a-z0-9]+", "", str(name or "").lower())
        hit = next((k for k, m in apps.items() if k == n or re.sub(r"[^a-z0-9]+", "", m["title"].lower()) == n or n and (k.startswith(n) or n.startswith(k))), None)
        if hit is None:
            raise ToolError(f"No connected app called '{name}'. Yours: {', '.join(f'{k} ({m['title']})' for k, m in apps.items()) or 'none yet (use create_connected_app)'}.", bad_args=True)
        return hit, apps[hit]

    def _save_tools(self, name: str, m: dict, what: str) -> dict:
        try:
            mod = self.ctx.mods.put("tools", name, json.dumps(m), m["title"])
        except ModError as e:
            raise ToolError(str(e), bad_args=True)
        self._changed(what)
        self.ctx.broadcast({"type": "app_changed", "app": name})
        return json.loads(mod["code"])

    def _bases(self, v) -> list[str]:
        out = []
        for part in (v if isinstance(v, list) else re.split(r"[,;/]+|\band\b", str(v or ""))):
            part = str(part).strip()
            if not part:
                continue
            try:
                a = self._resolve(part)
            except ToolError:
                a = self.ctx.registry.apps.get(part.lower())
            if a is None or getattr(a, "tool_app", False) or any(getattr(a, f, False) for f in ("api_app", "data_app")) or a.id == self.id:
                choices = ", ".join(f"{x.id} ({x.name})" for x in self.ctx.registry.enabled() if x.tools() and x.id != self.id
                                    and not any(getattr(x, f, False) for f in ("api_app", "data_app", "tool_app")))
                raise ToolError(f"'{part}' isn't one of your apps. Build on: {choices}.", bad_args=True)
            out.append(a.id)
        return list(dict.fromkeys(out))

    async def create_connected_app(self, a: dict):
        """An app that works on the user's real accounts by using tools Diatonic already has, preset for one job."""
        self._guard_mod()
        title = str(a.get("title") or str(a.get("name") or "").replace("_", " ").title()).strip()
        name = re.sub(r"[^a-z0-9]+", "", str(a.get("name") or title).lower())[:20]
        if len(name) < 2:
            raise ToolError("Give the app a short name (lowercase, one word).", bad_args=True)
        bases = self._bases(a.get("apps")) or self.connected_to(session.REQUEST.get())
        if not bases:
            raise ToolError("Say which of your apps it builds on (apps), e.g. gcal for Calendar and Meet, gmail for mail.", bad_args=True)
        base, n = name, 2
        while (clash := self.ctx.registry.apps.get(name)) is not None and not getattr(clash, "tool_app", False):
            name = f"{base[:18]}{n}"; n += 1   # the name is taken by another app: use a free one rather than stalling
        old = self._tool_apps().get(name, {})
        actions = old.get("actions") or plan(self.ctx.registry, bases, session.REQUEST.get())   # what the user asked it to do
        try:
            m = check_tool_manifest({"title": title or name, "description": a.get("description"), "apps": bases, "actions": actions})
        except ToolSpecError as e:
            raise ToolError(str(e), bad_args=True)
        self._save_tools(name, m, f"{'updated' if old else 'created'} app '{m['title']}'")
        base_names = ", ".join(self.ctx.registry.apps[b].name for b in bases)
        did = "\n".join(f"- {x['title']}: {x['tool']}" + (f" ({', '.join(f'{k}={v}' for k, v in x['fixed'].items())})" if x["fixed"] else "")
                        + f", asks for {', '.join(x['params']) or 'nothing'}" for x in m["actions"].values())
        return (f"App '{m['title']}' ({name}) is {'updated' if old else 'created'} in the left rail, built on {base_names}: it works on the user's "
                f"real account (same approvals). Its actions:\n{did}\nIf that covers what the user asked, tell them it's ready (in one or two "
                f"sentences, without tool names). Only if something they asked for is missing, call add_app_action with one of these tools "
                f"(* = required):\n" + "\n".join(catalog(self.ctx.registry, bases)))

    def _pick_tool(self, m: dict, tool: str):
        t = re.sub(r"[^a-z0-9_-]+", "", str(tool or "").lower())
        for cand in (t, *(f"{b}_{t}" for b in m["apps"])):
            hit = resolve_tool(self.ctx.registry, cand)
            if hit and hit[0].id in m["apps"]:
                return cand, hit
        raise ToolError(f"'{tool}' isn't a tool of {', '.join(m['apps'])}. Choose one of: "
                        + ", ".join(x.split("(")[0] for x in catalog(self.ctx.registry, m["apps"])) + ".", bad_args=True)

    async def add_app_action(self, a: dict):
        self._guard_mod()
        name, m = self._toolapp(a.get("app"))
        act = re.sub(r"[^a-z0-9_]+", "_", str(a.get("name") or "").lower()).strip("_")[:30]
        if not act or not act[0].isalpha():
            raise ToolError("Give the action a one-word name.", bad_args=True)
        full, (_, t) = self._pick_tool(m, a.get("tool"))
        props, req = t.parameters.get("properties", {}), t.parameters.get("required", [])
        try:
            preset = {**hint_presets(t, session.REQUEST.get()), **parse_fixed(a.get("preset") or {})}
        except ToolSpecError as e:
            raise ToolError(str(e), bad_args=True)
        fixed_text = [k for k in preset if k in FREE_TEXT]   # 'description=Meeting Scheduled' on every meeting is never what anyone wants
        for k in fixed_text:
            preset.pop(k)
        raw = a.get("inputs")
        inputs = [re.sub(r"\s*[=:].*$", "", str(x)).strip() for x in (raw if isinstance(raw, list) else re.split(r"[\s,;]+", str(raw or "")))]
        inputs = list(dict.fromkeys(x for x in inputs if x))   # 'summary=, start=' is how a small model writes 'summary, start'
        timed = [k for k in preset if k in TIME_PARAMS]   # a fixed date goes stale: drop it (lists default to the coming days)
        for k in timed:
            preset.pop(k)
        bad = [k for k in [*preset, *inputs] if k not in props]
        if bad:
            raise ToolError(f"{full} has no parameter {', '.join(bad)}. Its parameters: {', '.join(props)}.", bad_args=True)
        for k, v in list(preset.items()):   # 'true' for a boolean, a list for an array
            if props[k].get("type") == "boolean" and isinstance(v, str):
                preset[k] = v.lower() in ("true", "yes", "1")
            elif props[k].get("type") == "array" and not isinstance(v, list):
                preset[k] = [x.strip() for x in str(v).split(";") if x.strip()]
        if not inputs:
            inputs = list((t.build or {}).get("inputs") or [k for k in props if k not in preset])
        swap = (t.build or {}).get("swap") or {}
        inputs = list(dict.fromkeys(swap.get(k, k) for k in inputs))   # e.g. a form adds guests rather than replacing the list
        req_text = session.REQUEST.get().lower()
        if not any(p in REF_PARAMS for p in req):   # a form that makes something: include what the user mentioned ("guests" -> attendees)
            inputs += [k for k in props if k not in inputs and k not in preset and k not in ("account", "calendar")
                       and any(re.search(rf"\b{w}", req_text) for w in PARAM_WORDS.get(k, (k.rstrip("s"),)))]
        missing = [r for r in req if r not in preset and r not in inputs]
        if missing:
            raise ToolError(f"{full} needs {', '.join(missing)}: add {'it' if len(missing) == 1 else 'them'} to inputs (or preset).", bad_args=True)
        actions = dict(m["actions"])
        old = actions.get(act)
        actions[act] = {"title": a.get("title") or (old or {}).get("title") or act.replace("_", " ").capitalize(), "tool": full,
                        "fixed": preset, "params": [k for k in inputs if k not in preset], "description": a.get("description") or ""}
        try:
            m = check_tool_manifest({**m, "actions": actions})
        except ToolSpecError as e:
            raise ToolError(str(e), bad_args=True)
        self._save_tools(name, m, f"{'changed' if old else 'added'} '{actions[act]['title']}' in {m['title']}")
        x = m["actions"][act]
        others = [k for k in props if k not in x["params"] and k not in x["fixed"] and k not in ("account", "calendar", "max_results")]
        return (f"{'Changed' if old else 'Added'} '{x['title']}' in {m['title']}: {full}"
                + (f" ({', '.join(timed)} left out: a fixed date goes stale, so it shows the coming days)" if timed else "")
                + (f" ({', '.join(fixed_text)} left out: text the user types goes in the form, not a fixed value)" if fixed_text else "")
                + (f" with {', '.join(f'{k}={v}' for k, v in x['fixed'].items())} always" if x["fixed"] else "")
                + f"; its form asks for {', '.join(x['params']) or 'nothing'}." + (" It asks before it runs." if t.risk == "destructive" else "")
                + (f" It could also ask for: {', '.join(others)} (add_app_action again with the same name to include any the user wants)." if others else "")
                + f" Actions so far: {', '.join(m['actions'])}. Add the next one the user asked for, or tell them the app is ready in the left rail.")

    async def remove_app_action(self, a: dict):
        self._guard_mod()
        name, m = self._toolapp(a.get("app"))
        if m["actions"].pop(str(a.get("name") or ""), None) is None:
            raise ToolError(f"{m['title']} has no action '{a.get('name')}'. Actions: {', '.join(m['actions'])}.", bad_args=True)
        self._save_tools(name, m, f"removed an action from {m['title']}")
        return f"Removed. {m['title']} actions now: {', '.join(m['actions']) or 'none'}."

    def connected_tools(self) -> list[Tool]:
        apps = self._tool_apps()
        known = (" Existing ones (edit these, don't recreate them): " + " | ".join(tools_summary(n, m) for n, m in apps.items()) + ".") if apps else ""
        app_ref = {"type": "string", "description": "The app's name"}
        return [
            Tool("create_connected_app", "Create an app that does real things on the user's accounts (Calendar/Google Meet, Gmail, GitHub...) using "
                 "the tools Diatonic already has, e.g. an app to book meetings or triage mail. No code and no API keys. Step 1: name, title and the "
                 "apps it builds on; you get back their tools. Step 2: add_app_action for each thing the user wants it to do." + known,
                 {"properties": {"name": {"type": "string", "description": "Short lowercase id, one word"}, "title": {"type": "string"},
                                 "apps": {"type": "string", "description": "The apps it builds on, e.g. gcal or gmail"},
                                 "description": {"type": "string", "description": "One line: what the app is for"}},
                  "required": ["name", "apps"]}, self.create_connected_app, WRITE, title="Create app", precheck=self._blocked,
                 describe=lambda a: f"Create the app '{a.get('title') or a.get('name')}' on {a.get('apps')}"),
            Tool("add_app_action", "Add or change one action (a button with a form) of a connected app: which tool it runs, the arguments that are "
                 "always the same (preset) and the ones the form asks for (inputs). One call per action." + known,
                 {"properties": {"app": app_ref, "name": {"type": "string", "description": "One word, lowercase"},
                                 "title": {"type": "string", "description": "Button label"},
                                 "tool": {"type": "string", "description": "Full tool name from create_connected_app's list"},
                                 "preset": {"type": "string", "description": "Fixed arguments, key=value, comma separated"},
                                 "inputs": {"type": "string", "description": "Parameters the form asks for, comma separated"}},
                  "required": ["app", "name", "tool"]}, self.add_app_action, WRITE, title="Add action", precheck=self._blocked,
                 describe=lambda a: f"Add '{a.get('title') or a.get('name')}' ({a.get('tool')}) to {a.get('app')}"),
            Tool("remove_app_action", "Remove one action from a connected app.", {"properties": {"app": app_ref, "name": {"type": "string"}}, "required": ["app", "name"]},
                 self.remove_app_action, WRITE, title="Remove action", precheck=self._blocked),
        ]

    def data_tools(self) -> list[Tool]:
        apps = self._data_apps()
        known = (" Existing apps (edit these, don't recreate them): " + " | ".join(data_summary(n, m) for n, m in apps.items()) + ".") if apps else ""
        app_ref = {"type": "string", "description": "The app's name"}
        return [
            Tool("create_app", "Create a personal app that only keeps the user's own notes and lists on this computer (a tracker, log, checklist, "
                 "collection). It can't send, schedule or change anything elsewhere: for meetings, mail or GitHub use create_connected_app. "
                 "Step 1: name and title. Step 2: add_list for each kind of thing it holds. It gets its own panel and tools at once." + known,
                 {"properties": {"name": {"type": "string", "description": "Short lowercase id, one word"}, "title": {"type": "string", "description": "What the user called it"}},
                  "required": ["name"]}, self.create_app, WRITE, title="Create app", precheck=self._blocked),
            Tool("add_list", "Add or change one list in an app (same name replaces its fields; data is kept). fields: comma list, * = required, types: "
                 "text, notes, number, date, check (done/not done), choice(a, b, c). The first field is the item's name. "
                 "parent puts it inside another list (e.g. tasks inside projects, or stops inside trips)." + known,
                 {"properties": {"app": app_ref, "name": {"type": "string", "description": "Plural, in the user's words"},
                                 "fields": {"type": "string", "description": "e.g. name*: text, done: check, notes: notes"},
                                 "parent": {"type": "string", "description": "The list it lives inside, if any"}}, "required": ["app", "name", "fields"]},
                 self.add_list, WRITE, title="Add list", precheck=self._blocked, describe=lambda a: f"Add the list '{a.get('name')}' to {a.get('app')}"),
            Tool("add_field", "Add one field to a list of an app, keeping all items: 'add a priority to tasks' -> list tasks, field "
                 "'priority: choice(high, medium, low)'; 'add a due date to tasks' -> field 'due: date'.",
                 {"properties": {"app": app_ref, "list": {"type": "string"}, "field": {"type": "string", "description": "name: type, e.g. priority: choice(high, low)"}},
                  "required": ["app", "list", "field"]}, self.add_field, WRITE, title="Add field", precheck=self._blocked,
                 describe=lambda a: f"Add {a.get('field')} to {a.get('list')}"),
            Tool("remove_field", "Remove one field from a list (values are kept; undoable).",
                 {"properties": {"app": app_ref, "list": {"type": "string"}, "field": {"type": "string"}}, "required": ["app", "list", "field"]},
                 self.remove_field, WRITE, title="Remove field", precheck=self._blocked),
            Tool("remove_list", "Remove a list from an app (its items are kept, and it can be undone).",
                 {"properties": {"app": app_ref, "name": {"type": "string"}}, "required": ["app", "name"]},
                 self.remove_list, WRITE, title="Remove list", precheck=self._blocked),
        ]

    def build_tools(self) -> list[Tool]:
        apps = self._api_apps()
        known = (" Existing API apps (edit these instead of creating new ones): " + " | ".join(app_summary(n, m) for n, m in apps.items()) + ".") if apps else ""
        app_ref = {"type": "string", "description": "The API app's name"}
        return [
            Tool("connect_api", "Only for an app that calls a web API at a known https:// address (weather, news, prices...): its name, base URL and how the "
                 "API key is sent. Then add_action once per endpoint. For an app that just keeps the user's own data, use create_app. "
                 "Never ask for the key in chat." + known,
                 {"properties": {"name": {"type": "string", "description": "Short lowercase id, e.g. weather"},
                                 "title": {"type": "string"}, "base_url": {"type": "string", "description": "e.g. https://api.weatherapi.com/v1"},
                                 "key_in": {"type": "string", "enum": list(("none", "query", "header", "bearer")), "description": "Where the API key goes"},
                                 "key_name": {"type": "string", "description": "Query parameter or header name for the key, e.g. key"}},
                  "required": ["name", "base_url", "key_in"]},
                 self.connect_api, WRITE, title="Connect API", precheck=self._blocked,
                 describe=lambda a: f"Create the app '{a.get('title') or a.get('name')}', which may call {str(a.get('base_url') or '')[:80]}"),
            Tool("add_action", "Add or change one endpoint of an API app (same name replaces it). It is test-called right away and you get back "
                 "the values it returns. params: comma list, * = required, ': label', '= default', e.g. 'q*: City, days: Days = 1'. "
                 "Always give test_with for required params so it can be tested." + known,
                 {"properties": {"app": app_ref, "name": {"type": "string", "description": "e.g. current"}, "path": {"type": "string", "description": "e.g. /current.json"},
                                 "method": {"type": "string", "enum": ["GET", "POST", "PUT", "PATCH", "DELETE"]}, "params": {"type": "string"},
                                 "test_with": {"type": "string", "description": "Example values for required params, e.g. latitude=18.52, longitude=73.86"},
                                 "title": {"type": "string"}}, "required": ["app", "name"]},
                 self.add_action, WRITE, title="Add action", precheck=self._blocked, describe=lambda a: f"Add '{a.get('name')}' to {a.get('app')}"),
            Tool("set_fields", "Choose which values an action shows (paths from its test result, e.g. location.name, current.temp_c).",
                 {"properties": {"app": app_ref, "action": {"type": "string"}, "fields": {"type": "string", "description": "Comma list"}},
                  "required": ["app", "action", "fields"]}, self.set_fields, WRITE, title="Choose fields", precheck=self._blocked),
            Tool("remove_action", "Remove one endpoint from an API app.", {"properties": {"app": app_ref, "action": {"type": "string"}}, "required": ["app", "action"]},
                 self.remove_action, WRITE, title="Remove action", precheck=self._blocked),
            Tool("test_app", "Test-call every endpoint of an API app (e.g. after the user added the key).", {"properties": {"app": app_ref}, "required": ["app"]},
                 self.test_app, title="Test app"),
        ]

    def mod_tools(self) -> list[Tool]:
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
            Tool("add_panel", "Only for something create_app can't do (a clock, a game, a custom widget): a panel with its own JavaScript. "
                 "Trackers and lists use create_app; web APIs use connect_api. The code is test-run first and you hear about errors; the user then approves it. "
                 "To change a panel that already exists use edit_panel, not a new panel. " + SDK + examples() + existing,
                 {"properties": {"name": name, "title": {"type": "string", "description": "Name shown in the rail"}, "code": {"type": "string", "description": "JavaScript that fills `el`"}, "summary": SUMMARY},
                  "required": ["name", "title", "code"]},
                 self.add_panel, WRITE, title="Add panel", precheck=self._blocked,
                 describe=lambda a: f"Add a new panel '{a.get('title') or a.get('name')}'{self._calls_server(a)}{self._what(a)}",
                 preview=self._code_review("panel")),
            Tool("list_mods", "List the interface changes made so far (styles, scripts, panels). With a name of a panel or script, also returns its code.",
                 {"properties": {"name": name}}, self.list_mods, title="List changes"),
            Tool("edit_panel", "Change a panel or script you already made by replacing one piece of its code: name, old = the exact current text to replace "
                 "(list_mods with the name shows the code), new = what goes there. For small changes ('make the button green'); use add_panel to rewrite it all. "
                 "The code is test-run, then the user approves the change shown as a diff." + existing,
                 {"properties": {"name": name, "old": {"type": "string", "description": "Exact text from the current code"}, "new": {"type": "string", "description": "Replacement text"}, "summary": SUMMARY},
                  "required": ["name", "old", "new"]},
                 self.edit_panel, WRITE, title="Edit panel", precheck=self._blocked,
                 describe=lambda a: f"Change '{a.get('name')}'{self._what(a)}", preview=self._edit_preview),
            Tool("remove_mod", "Remove one interface change by name." + existing, {"properties": {"name": name}, "required": ["name"]},
                 self.remove_mod, WRITE, title="Remove change", precheck=self._blocked),
            Tool("revert", "Undo the most recent interface change (theme, style, script or panel). Use for 'undo that', 'put it back'.", {},
                 self.revert, WRITE, title="Undo change", precheck=self._blocked),
            Tool("create_file_app", "Create a file-based app with its own directory, manifest.json, index.js, and style.css in ~/.diatonic/apps/<id>/.",
                 {"properties": {"id": {"type": "string", "description": "App id slug, e.g. pomodoro-timer"},
                                 "name": {"type": "string", "description": "Display name of the app"},
                                 "description": {"type": "string", "description": "Short description of what the app does"},
                                 "icon": {"type": "string", "description": "Icon name from icons.js (e.g. clock, box, chat)"}},
                  "required": ["id", "name"]},
                 self.create_file_app, WRITE, title="Create file app", precheck=self._blocked,
                 describe=lambda a: f"Create custom app folder '{a.get('name') or a.get('id')}'"),
            Tool("write_app_file", "Write or overwrite a file in a custom app folder (~/.diatonic/apps/<id>/). Use for index.js, style.css, tools.py, etc.",
                 {"properties": {"app_id": {"type": "string", "description": "App id"},
                                 "path": {"type": "string", "description": "Relative file path inside the app folder"},
                                 "content": {"type": "string", "description": "File content"},
                                 "summary": SUMMARY},
                  "required": ["app_id", "path", "content"]},
                 self.write_app_file, WRITE, title="Write app file", precheck=self._blocked,
                 describe=lambda a: f"Write file '{a.get('path')}' in custom app '{a.get('app_id')}'",
                 preview=self._write_file_preview),
            Tool("patch_app_file", "Surgically edit a file in a custom app folder by replacing exact old text with new text.",
                 {"properties": {"app_id": {"type": "string", "description": "App id"},
                                 "path": {"type": "string", "description": "Relative file path inside the app folder"},
                                 "old": {"type": "string", "description": "Exact text to replace"},
                                 "new": {"type": "string", "description": "Replacement text"},
                                 "summary": SUMMARY},
                  "required": ["app_id", "path", "old", "new"]},
                 self.patch_app_file, WRITE, title="Patch app file", precheck=self._blocked,
                 describe=lambda a: f"Patch file '{a.get('path')}' in custom app '{a.get('app_id')}'",
                 preview=self._patch_file_preview),
            Tool("read_app_file", "Read a file from a custom app folder (~/.diatonic/apps/<id>/).",
                 {"properties": {"app_id": {"type": "string", "description": "App id"},
                                 "path": {"type": "string", "description": "Relative file path"}},
                  "required": ["app_id", "path"]},
                 self.read_app_file, title="Read app file"),
            Tool("list_app_files", "List files in a custom app folder or all installed custom apps.",
                 {"properties": {"app_id": {"type": "string", "description": "Optional app id"}}},
                 self.list_app_files, title="List app files"),
            Tool("read_app_logs", "Read client-side and server-side runtime errors and diagnostics for a custom app to debug it.",
                 {"properties": {"app_id": {"type": "string", "description": "App id"}},
                  "required": ["app_id"]},
                 self.read_app_logs, title="Read app logs"),
            Tool("export_file_app", "Export a custom app as a portable JSON bundle.",
                 {"properties": {"app_id": {"type": "string", "description": "App id"}},
                  "required": ["app_id"]},
                 self.export_file_app, title="Export custom app"),
            Tool("import_file_app", "Import a custom app from a portable JSON bundle.",
                 {"properties": {"bundle": {"type": "object", "description": "App bundle JSON"},
                                 "overwrite": {"type": "boolean", "description": "Whether to overwrite existing app"}},
                  "required": ["bundle"]},
                 self.import_file_app, WRITE, title="Import custom app", precheck=self._blocked,
                 describe=lambda a: "Import custom app from bundle"),
        ]

    def _api_names(self, kinds=("app", "panel", "data", "tools", "file_app")) -> list[str]:
        mods = self.ctx.mods.list() if getattr(self.ctx, "mods", None) else []
        names = [w for m in mods if m["kind"] in kinds for w in (m["name"], m["title"].lower())]
        if "file_app" in kinds or "panel" in kinds:
            file_apps = self.ctx.fileapps.list_apps() if getattr(self.ctx, "fileapps", None) else []
            names.extend([w for a in file_apps for w in (a["id"], a["name"].lower())])
        return names

    def tools_for(self, text: str, prior_tools: set[str] | frozenset = frozenset()) -> list[Tool]:
        """Three groups: showing (3 small tools), changing the look, building apps. Offer only what the request is about."""
        t = (text or "").lower()
        want = set(NAV_TOOLS)
        named = lambda kinds: any(n and re.search(rf"\b{re.escape(n)}\b", t) for n in self._api_names(kinds))
        build = BUILD.search(t) or named(("panel", "data")) or any(n in prior_tools for n in BUILDING)   # or still building in this chat
        api = API_WORDS.search(t) or (BUILD.search(t) and API_TOPICS.search(t)) or named(("app",)) or any(n in prior_tools for n in API_BUILDING)
        linked = (build and self.connected_to(t) and not LOCAL_ONLY.search(t)) or named(("tools",)) or any(n in prior_tools for n in CONNECTED_BUILDING)
        if linked and not api:
            want = set(CONNECTED_TOOLS)
        elif api:
            want = set(API_TOOLS)
        elif build:
            want = set(BUILD_TOOLS)
            if self._api_apps() and re.search(r"\b(?:update|edit|change|modify|fix|improve|extend|add|ask for|show)\b", t):
                want |= {"add_action", "set_fields"}   # "update the whther app": could be either kind, so both edit tools
        code_mods = [m for m in self.ctx.mods.list() if m["kind"] in ("panel", "script")] if getattr(self.ctx, "mods", None) else []
        if code_mods and not api and not linked and EDIT_WORDS.search(t):
            words = {w for m in code_mods for w in re.findall(r"[a-z]{4,}", f"{m['name']} {m['title']}".lower())}
            if any(re.search(rf"\b{re.escape(w)}", t) for w in words):      # "change the counter": it names something already built
                want = {"edit_panel", "list_mods", "add_panel", "remove_mod", "revert", "open_app"}   # only ways to change it (not "add a field" tools)
            elif build:
                want |= {"edit_panel", "list_mods", "add_panel"}
        if HOME.search(t):   # "put X on the home screen" is about tiles, even when X is an app being built
            return [x for x in self.tools() if x.name in {"set_home", "revert", *NAV_TOOLS}]
        if CUSTOM.search(t):
            want |= set(LOOK_TOOLS) if not build else {"set_theme", "add_button"}
        return [x for x in self.tools() if x.name in want]

    def builds_something(self, text: str) -> bool:
        """"Make a panel that lists my to-dos": the request is to build, so the other apps' tools (which would just answer) stay out of it."""
        return bool(re.search(MAKE_A, (text or "").lower())) and not re.search(r"^\W*(?:show|open|list|what|how)\b", (text or "").lower())

    @property
    def keyword_patterns(self) -> list[str]:   # plus the names of apps the agent built, so "update the weather app" routes here
        names = [re.escape(n) for n in self._api_names() if n]
        return APP_PATTERNS + ([rf"\b(?:{'|'.join(names)})\b"] if names else [])

    def tools(self) -> list[Tool]:
        apps = ", ".join(self._apps()) if getattr(self.ctx, "registry", None) else "calendar, mail, notes, files, todos"
        return [
            *self.data_tools(),
            *self.connected_tools(),
            *self.build_tools(),
            *self.mod_tools(),
            Tool("open_app",
                 f"Show an app on screen (apps: {apps}): 'show my calendar', 'open mail', 'show unread mail' (query is:unread). "
                 "view/date/query pick the page. For one mail, event, note or file use open_item. Only displays; read data with the app's own tools.",
                 {"properties": {
                     "app": {"type": "string", "description": "App id or name, e.g. gcal, gmail, notes, files, todos"},
                     "view": {"type": "string", "description": "calendar: day|week|month|agenda; mail: inbox|starred|sent|drafts|spam|trash; to-dos: open|done"},
                     "date": {"type": "string", "description": "Calendar only: YYYY-MM-DD to jump to"},
                     "query": {"type": "string", "description": "Mail or notes: a search to show"},
                     "account": {"type": "string", "description": "Mail only: an account email, to show just that mailbox"},
                     "path": {"type": "string", "description": "Files only: a folder or file to show, e.g. Developer/Agentic"}},
                  "required": ["app"]}, self.open_app, title="Show app",
                 describe=lambda a: f"Show {a.get('app', 'an app')}"),
            Tool("open_item",
                 "Open one mail, event, note or file on screen; it finds the item from what the user called it. "
                 "latest email -> gmail 'latest'; email from Sam -> gmail 'from:sam'; README file -> files 'README'; dentist event -> gcal 'dentist'.",
                 {"properties": {"app": {"type": "string", "description": "gmail, gcal, notes or files"},
                                 "what": {"type": "string", "description": "mail: 'latest' or a Gmail search; event: words from its title; note: its title; file: its name or path. A ref from an earlier result also works."}},
                  "required": ["app", "what"]}, self.open_item, title="Open item",
                 describe=lambda a: f"Open {a.get('what') or a.get('ref') or 'an item'}"),
            Tool("set_layout",
                 "Change how the window is arranged. mode: split (add a new panel beside the current one), focus (one panel fills the window: use this for 'full screen' or 'exit split'), "
                 "chat (open the chat). sidebar: show or hide the chat list.",
                 {"properties": {"mode": {"type": "string", "enum": list(MODES)},
                                 "sidebar": {"type": "string", "enum": ["show", "hide"]}}},
                 self.set_layout, title="Change layout"),
            Tool("pin_app",
                 f"Pin an app to the status bar (apps: {apps}): 'pin notes to the status bar', 'pin mail'.",
                 {"properties": {
                     "app": {"type": "string", "description": "App id or name to pin"},
                     "zone": {"type": "string", "enum": ["left", "center", "right"], "description": "Status bar zone (default: center)"}},
                  "required": ["app"]}, self.pin_app, title="Pin app",
                 describe=lambda a: f"Pin {a.get('app', 'app')} to status bar"),
            Tool("unpin_app",
                 f"Unpin an app from the status bar (apps: {apps}): 'unpin notes from the status bar'.",
                 {"properties": {
                     "app": {"type": "string", "description": "App id or name to unpin"}},
                  "required": ["app"]}, self.unpin_app, title="Unpin app",
                 describe=lambda a: f"Unpin {a.get('app', 'app')} from status bar"),
            Tool("save_macro",
                 "Save a macro (a sequence of commands): 'create a macro to open files and chat'.",
                 {"properties": {
                     "name": {"type": "string", "description": "Macro name, e.g. 'Dev' or 'Morning Routine'"},
                     "description": {"type": "string", "description": "Short description of what the macro does"},
                     "id": {"type": "string", "description": "Optional unique id, e.g. 'macro_dev'"},
                     "steps": {"type": "array", "description": "List of step objects: [{'command': 'app.open', 'args': {'id': 'files'}}, {'wait': 100}]"}},
                  "required": ["name"]}, self.save_macro, title="Save macro",
                 describe=lambda a: f"Save macro '{a.get('name', 'macro')}'"),
            Tool("run_macro",
                 "Run a saved macro by name or id: 'run the Dev macro', 'start morning routine'.",
                 {"properties": {
                     "name": {"type": "string", "description": "Name or id of the macro to run"}},
                  "required": ["name"]}, self.run_macro, title="Run macro",
                 describe=lambda a: f"Run macro '{a.get('name', 'macro')}'"),
            Tool("list_macros",
                 "List all saved macros.",
                 {}, self.list_macros, title="List macros"),
            Tool("delete_macro",
                 "Delete a saved macro by name or id.",
                 {"properties": {
                     "name": {"type": "string", "description": "Name or id of the macro to delete"}},
                  "required": ["name"]}, self.delete_macro, title="Delete macro",
                 describe=lambda a: f"Delete macro '{a.get('name', 'macro')}'"),
            Tool("save_layout",
                 "Save the current layout (or a set of apps) under a name: 'save this layout as Dev', 'save an Inbox Zero layout'. Use this, not a macro, to remember how the screen is arranged.",
                 {"properties": {
                     "name": {"type": "string", "description": "Layout name, e.g. 'Dev' or 'Inbox Zero'"},
                     "description": {"type": "string", "description": "Short description of what the layout is for"},
                     "id": {"type": "string", "description": "Optional unique id, e.g. 'dev'"},
                     "apps": {"type": "array", "description": "Optional list of apps to include: ['chat', 'files']"}},
                  "required": ["name"]}, self.save_layout, title="Save layout",
                 describe=lambda a: f"Save layout '{a.get('name', 'layout')}'"),
            Tool("switch_layout",
                 "Switch to a named layout by name or id: 'switch to Dev layout', 'open Inbox Zero'.",
                 {"properties": {
                     "name": {"type": "string", "description": "Name or id of the layout to switch to"}},
                  "required": ["name"]}, self.switch_layout, title="Switch layout",
                 describe=lambda a: f"Switch to layout '{a.get('name', 'layout')}'"),
            Tool("list_layouts",
                 "List all saved named layouts.",
                 {}, self.list_layouts, title="List layouts"),
            Tool("delete_layout",
                 "Delete a saved layout by name or id.",
                 {"properties": {
                     "name": {"type": "string", "description": "Name or id of the layout to delete"}},
                  "required": ["name"]}, self.delete_layout, title="Delete layout",
                 describe=lambda a: f"Delete layout '{a.get('name', 'layout')}'"),
            Tool("arrange_panels",
                 "Arrange apps on the screen: 'mail left, calendar right', 'mail left, calendar top-right, chat bottom-right', "
                 "'three columns with chat, files and terminal', 'grid with mail, calendar, notes, todos'. Free and undoable.",
                 {"properties": {
                     "left": {"type": "string", "description": "App for the left pane"},
                     "right": {"type": "string", "description": "App for the right pane"},
                     "top": {"type": "string", "description": "App for the top pane"},
                     "bottom": {"type": "string", "description": "App for the bottom pane"},
                     "top_left": {"type": "string", "description": "App for the top-left pane"},
                     "top_right": {"type": "string", "description": "App for the top-right pane"},
                     "bottom_left": {"type": "string", "description": "App for the bottom-left pane"},
                     "bottom_right": {"type": "string", "description": "App for the bottom-right pane"},
                     "center": {"type": "string", "description": "App for the center column in a 3-column layout"}}},
                 self.arrange_panels, title="Arrange panels",
                 describe=lambda a: "Arrange screen panels"),
            Tool("undo_layout",
                 "Undo the most recent spatial panel arrangement. Use for 'undo that layout', 'put panels back'.",
                 {}, self.undo_layout, title="Undo layout"),
        ]


def create(ctx) -> App:
    return UiApp(ctx)
