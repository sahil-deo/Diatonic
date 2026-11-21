"""The interface as a tool: the agent can put an app, a mail, a note or a file on screen, change the layout and change how things look.

The tools that change the look are in looks.py and those that build apps are in builder.py. These tools only move the view in the window
that started the run. They read nothing and change no data, so they never ask for approval. A background run (scheduled, or whose window
was closed) has no screen to move.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date

from ...agent import session
from ...agent.session import RUN_CLIENT
from ..base import App, Tool, ToolError
from .builder import BuilderMixin
from .looks import LooksMixin

# What each panel can actually do with open_app's options; anything else is reported back as ignored, never claimed.
ACCEPTS = {"gcal": {"view", "date"}, "gmail": {"view", "query", "account"}, "notes": {"query"}, "todos": {"view"}, "files": {"path"}, "presentations": {"query", "item"}}
VIEWS = {
    "gcal": ["day", "week", "month", "agenda"],
    "gmail": ["inbox", "starred", "sent", "drafts", "spam", "trash"],
    "todos": ["open", "done"],
}
NAV_TOOLS = ("open_app", "open_item", "set_layout", "pin_app", "unpin_app", "save_macro", "run_macro", "list_macros", "delete_macro", "save_layout", "switch_layout", "list_layouts", "delete_layout", "arrange_panels", "undo_layout")
HOME = re.compile(r"\b(?:home ?screen|home ?page|start ?screen|dashboard|widgets?|tiles?)\b")
LOOK_TOOLS = ("set_home", "set_theme", "add_style", "add_button", "add_script", "list_mods", "remove_mod", "revert")
# Words that mean "change the interface itself" (vs. just showing something): only then are the look tools offered.
CUSTOM = re.compile(r"\b(?:theme|colou?r|accent|font|css|style|button|widget|panel|tab|rail|icon|side ?bar|top ?bar|bigger|smaller|wider|"
                    r"narrower|compact|rounded|undo|revert|script|toggle|customi[sz]e|dark mode|light mode|text size|interface|mods?|"
                    r"home ?screen|home ?page|start ?screen|dashboard|widgets?|tiles?)\b", re.I)
MODES = {
    "zoom": "maximize/zoom the panel to fill the window without closing other panels (full screen)",
    "unzoom": "restore all panels to previous layout",
    "split": "chat and panel side by side",
    "single": "single panel only (closes other panels)",
    "focus": "maximize/zoom the panel to fill the window without closing other panels",
    "chat": "open the chat",
}
# Each mode is a command the app already has, so the agent, a key and the palette all do the same thing.
MODE_COMMANDS = {
    "zoom": {"command": "panel.zoom"},
    "unzoom": {"command": "panel.unzoom"},
    "split": {"command": "panel.split.right", "args": {"app": "launcher"}},
    "single": {"command": "layout.preset.single"},
    "focus": {"command": "panel.zoom"},
    "chat": {"command": "app.open", "args": {"id": "chat", "focus": False}},
}


class UiApp(LooksMixin, BuilderMixin, App):
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
        app_name = str(a.get("app") or "").strip()
        app_id = self._resolve(app_name).id if app_name else None
        if mode:
            cmd = dict(MODE_COMMANDS[mode])
            if app_id:
                if mode in ("zoom", "focus"):
                    self._open(app_id, {})
                cmd["args"] = {**cmd.get("args", {}), "app": app_id}
            self._send(cmd)
        if side:
            self._send({"command": "sidebar.set", "args": {"mode": side}})
        details = [f"{k} {v}" for k, v in (("mode", mode), ("app", app_id), ("sidebar", side)) if v]
        return self._done("layout changed (" + ", ".join(details) + ")")

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
                     "arrange_panels", "undo_layout"]
    untrusted_note = session.UNTRUSTED_NOTE

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

    def tools_for(self, text: str, prior_tools=frozenset()) -> list[Tool]:
        """Showing and layout always; the look tools when the request is about the look. Building has its own toolset (builder_session)."""
        t = (text or "").lower()
        if HOME.search(t):   # "put X on the home screen" is about tiles
            return [x for x in self.tools() if x.name in {"set_home", "revert", *NAV_TOOLS}]
        want = set(NAV_TOOLS) | (set(LOOK_TOOLS) if CUSTOM.search(t) else set())
        return [x for x in self.tools() if x.name in want]

    def tools(self) -> list[Tool]:
        out = self._tools()
        for t in out:
            if t.precheck == self._blocked:   # the tools that change the interface itself: see session.UNTRUSTED_NOTE
                t.add_effects("display")
        return out

    def _tools(self) -> list[Tool]:
        apps = ", ".join(self._apps()) if getattr(self.ctx, "registry", None) else "calendar, mail, notes, files, todos"
        return [
            *self.looks_tools(),
            *self.file_app_tools(),
            *self.lookup_tools(),
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
                 "Change how the window is arranged. "
                 "mode: zoom (maximize/full-screen the panel to fill the window without closing other panels; use unzoom to restore), "
                 "unzoom (restore all panels to previous layout), "
                 "split (add a new panel beside the current one), "
                 "single (keep only one panel and close the rest), "
                 "focus (same as zoom), "
                 "chat (open the chat). "
                 "app: optional app id or name to zoom or layout (e.g. 'gmail', 'notes'). "
                 "sidebar: show or hide the chat list.",
                 {"properties": {"mode": {"type": "string", "enum": list(MODES)},
                                 "app": {"type": "string", "description": "Optional app to zoom or open in layout, e.g. 'gmail', 'notes'"},
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
                     "steps": {"type": "array", "items": {"type": "object"}, "description": "List of step objects: [{'command': 'app.open', 'args': {'id': 'files'}}, {'wait': 100}]"}},
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
                     "apps": {"type": "array", "items": {"type": "string"}, "description": "Optional list of apps to include: ['chat', 'files']"}},
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
