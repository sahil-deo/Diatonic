"""Building apps: the tools that write an app's files, and the builder session that keeps a conversation on one app.

A mixin for the Interface app (apps/ui/app.py). The first request to build or change an app starts a session: from then on the agent
gets the small fixed toolset in BUILDER_TOOLS, and the same app on every turn, until the user moves on to something else.
"""
from __future__ import annotations

import asyncio
import difflib
import hashlib
import re
import time
import uuid
from dataclasses import dataclass

from ...agent import session
from ...agent.session import RUN_CLIENT
from ...catalog import KINDS
from ...fileapps import FileAppError
from ... import instructions
from ..base import DESTRUCTIVE, WRITE, Tool, ToolError
from .sdk import FILE_HINT, SUMMARY

VERIFY_WAIT = 6.0   # seconds to wait for the open window to run new code in its sandbox (it gives up after 4)

# The whole toolset of a builder session. No routing by words: these, and nothing else, for as long as the session lasts.
BUILDER_TOOLS = ("platform_search", "platform_describe", "read_skill", "list_app_files", "read_app_file", "create_file_app", "write_app_file",
                 "patch_app_file", "read_app_logs", "open_app", "delete_file_app")
# Tools whose use in a conversation means it has been building: open_app is left out because it is also plain navigation.
BUILDING = ("ui_create_file_app", "ui_write_app_file", "ui_patch_app_file", "ui_read_app_file", "ui_list_app_files", "ui_read_app_logs")
# Things a person asks for by what they do ("make a stopwatch"), not by calling them an app.
THINGS = r"(?:app|apps|panel|tab|calculator|converter|timer|stopwatch|clock|counter|game|generator|quiz|checklist|planner|tracker|viewer|editor|preview(?:er)?)"
MAKE_A = r"\b(?:create|make|build|add|implement)\b(?:\s+\w+){0,5}?\s+" + THINGS + r"\b"
# "A panel with a text box on top...", "an app for tracking habits": a description of an app with no verb in front of it.
NEW_APP = re.compile(r"^\W*an? (?:\w+ ){0,2}(?:app|panel)\b|\bapp (?:for|to|that)\b|\b(?:new|own|custom|my own) (?:app|panel|tab)\b")
# A request to change an app that already exists, when the conversation is not building yet.
CHANGE = re.compile(r"\b(?:update|edit|change|modify|fix|tweak|adjust|rename|improve|extend|redo|rewrite|restyle|make (?:the|it|that)|bigger|smaller|wider|colou?r|"
                    r"dark mode|light mode|broken|bug|not working|doesn'?t work|add|remove|delete|uninstall|trash|get rid of)\b", re.I)
# "delete the youtube app": a request to get rid of a whole app (not "remove a field from my tracker app")
DELETE_APP = re.compile(r"\b(?:delete|remove|uninstall|trash|get rid of)\b(?:\s+(?!field|fields|list|button|action|endpoint|style|script)\w+[-\w]*){0,5}?\s+apps?\b", re.I)
DELETE_VERB = re.compile(r"\b(?:delete|remove|uninstall|trash|get rid of)\b", re.I)
# "update the placeholder app to work": it opens with a change verb, so it is about an app that exists, even though "app to" reads like a description.
CHANGE_FIRST = re.compile(r"^\W*(?:please\s+)?(?:update|edit|change|modify|fix|finish|complete|tweak|adjust|rewrite|redo)\b")
# An error the user pasted from the app's console or screen.
ERROR_PASTE = re.compile(r"\b(?:Uncaught|TypeError|ReferenceError|SyntaxError|RangeError|is not defined|is not a function|Cannot read propert\w+|Failed to fetch)\b"
                         r"|\bat \S+ \(.+:\d+:\d+\)")
ENDS = re.compile(r"^\W*(?:thanks?|thank you|done|that'?s (?:all|it|enough)|stop building|exit|never ?mind|no more changes)\b", re.I)


@dataclass
class Builder:
    """A conversation pinned to building: the app it works on (None until one exists) and what the agent is told for this turn."""
    app_id: str | None
    note: str


class BuilderMixin:
    """Needs self.ctx, self._guard_mod, self._blocked, self._done and the _verifying and _verified sets from UiApp."""

    # ---- the builder session -----------------------------------------------
    def builder_tools(self) -> list[Tool]:
        return [t for t in self.tools() if t.name in BUILDER_TOOLS]

    def _file_apps(self) -> list[dict]:
        return self.ctx.fileapps.list_apps() if getattr(self.ctx, "fileapps", None) else []

    def _named_app(self, low: str, apps: list[dict]) -> str | None:
        for a in apps:
            if any(n and re.search(rf"\b{re.escape(n)}\b", low) for n in (a["id"], a["name"].lower())):
                return a["id"]
        return None

    def _other_app_named(self, low: str) -> bool:
        """The request is about one of the user's own apps (mail, calendar...), not about the app being built."""
        for a in self.ctx.registry.enabled():
            if a.id == self.id or getattr(a, "file_app", False):
                continue
            if any(re.search(rf"\b{re.escape(k)}(?:s|es)?\b", low) for k in a.keywords if k not in ("open", "show")) or any(re.search(p, low) for p in a.keyword_patterns):
                return True
        return False

    def builder_session(self, conv_id: str, text: str, prior_tools, screen: dict | None, navigation: bool = False) -> Builder | None:
        """Is this turn part of building an app? The first request to build or change one starts a session; it carries on, on the same
        app, until the user says they are done or asks for something that is plainly about another app."""
        low = (text or "").lower()
        apps = self._file_apps()
        named = self._named_app(low, apps)
        pasted = bool(ERROR_PASTE.search(text or "")) and bool(apps)
        deleting = bool(DELETE_APP.search(low)) or bool(named and DELETE_VERB.search(low))
        started = (self.builds_something(text) or deleting or pasted
                   or bool(named and not navigation and CHANGE.search(low)))
        if not started:
            if not any(n in prior_tools for n in BUILDING) or ENDS.match(text or "") or (self._other_app_named(low) and not CHANGE.search(low)):
                return None
        app_id = None if self.builds_something(text) and not named else (named or self._pinned(conv_id, screen, apps))
        return Builder(app_id, self._builder_note(conv_id, app_id, pasted, apps))

    def _pinned(self, conv_id: str, screen: dict | None, apps: list[dict]) -> str | None:
        """The app this conversation is working on: the one open on screen, else the last one a builder tool touched."""
        ids = {a["id"] for a in apps}
        if isinstance(screen, dict) and screen.get("app") in ids:
            return screen["app"]
        for args in self.ctx.db.recent_tool_args(conv_id, BUILDING):
            for key in ("app_id", "id", "name"):
                if args.get(key):
                    try:
                        return self._file_app_id(args[key])
                    except FileAppError:
                        break
        return None

    def _builder_note(self, conv_id: str, app_id: str | None, pasted: bool, apps: list[dict]) -> str:
        """What the agent is told this turn: which app it is on, and any errors that app has logged since the last turn."""
        if app_id is None:
            note = ("\n\nBuilder session: the user wants a new app. Read ui_read_skill recipes and the closest recipe, then create it with ui_create_file_app and "
                    "write index.js. Look up at most two things first: the steps are limited, and the app is not built until index.js is written. "
                    "Existing apps: " + (", ".join(a["id"] for a in apps) or "none") + ".")
        elif self._is_placeholder(app_id):
            note = (f"\n\nBuilder session on the app '{app_id}', which is still an empty placeholder. Build it now: read the closest recipe (ui_read_skill recipes), "
                    f"then write the whole app with ui_write_app_file (path index.js), and do not create another app.")
        else:
            note = (f"\n\nBuilder session on the app '{app_id}'. Every request in this chat is about that app until the user moves on: read what is there "
                    f"(ui_list_app_files, ui_read_app_file), change it with ui_patch_app_file, and do not create another app.")
        if pasted:
            note += " The user pasted an error from the app: find its cause in the code and fix it."
        if app_id:
            seen = self.ctx.builder_seen.get((conv_id, app_id), 0.0)
            fresh = [e for e in self.ctx.fileapps.get_logs(app_id) if e["timestamp"] > seen]
            if fresh:
                note += f"\nErrors the app logged since your last turn ({app_id}):\n" + "\n".join(f"- [{e['source']}] {e['error']}" for e in fresh[-5:])
            self.ctx.builder_seen[(conv_id, app_id)] = time.time()
        return note

    def _is_placeholder(self, app_id: str) -> bool:
        """index.js is still the starter file create_file_app wrote, so nothing has been built yet."""
        try:
            return "custom-app-container" in self.ctx.fileapps.read_file(app_id, "index.js")
        except (FileAppError, ValueError):
            return False

    # ---- file-based custom apps in ~/.diatonic/apps/<id>/ ----------------
    async def create_file_app(self, a: dict):
        self._guard_mod()
        app_id = a.get("id") or a.get("name")
        try:
            manifest = self.ctx.fileapps.create_app(app_id, a.get("name", ""), a.get("description", ""), a.get("icon", "box"))
        except (FileAppError, ValueError) as e:
            exists = self._file_app_id(app_id) if self.ctx.fileapps.exists(self._slug(app_id)) else None
            if exists and self._is_placeholder(exists):   # a second create call is the model forgetting to write: say what to do instead
                raise ToolError(f"'{exists}' already exists and is still an empty placeholder. Do not create it again: call write_app_file with app_id '{exists}', "
                                "path 'index.js' and the full code.", bad_args=True)
            raise ToolError(str(e), bad_args=True)
        # Not _done(): that says the user can see a finished thing. This is a placeholder, and a small model reports it as the app.
        return (f"Created an EMPTY placeholder app '{manifest['name']}' (id {manifest['id']}). It does nothing yet and is NOT built. "
                f"Next call write_app_file with app_id '{manifest['id']}', path 'index.js' and the full code. Do not tell the user it is done before that succeeds.")

    async def delete_file_app(self, a: dict):
        self._guard_mod()
        app_id = a.get("app_id") or a.get("id") or a.get("name")
        if not app_id:
            raise ToolError("Provide the app_id of the custom app to delete.", bad_args=True)
        try:
            app_id = self._file_app_id(app_id)
            self.ctx.fileapps.delete_app(app_id)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        return f"Deleted custom app '{app_id}' and its files. It is gone from the interface."

    def _file_app_id(self, name) -> str:
        """The id of a custom app from what the model called it ('YouTube Search' for youtube-search); an error that lists what exists if it is not one of them."""
        apps = self.ctx.fileapps.list_apps()
        slug = self._slug(name)
        for pick in (lambda a: a["id"] == slug, lambda a: slug in (self._slug(a["name"]), a["name"].lower()),
                     lambda a: bool(slug) and (slug in a["id"] or a["id"] in slug)):
            hits = [a for a in apps if pick(a)]
            if len(hits) == 1:
                return hits[0]["id"]
        raise FileAppError(f"There is no custom app called '{name}'. Existing: " + (", ".join(a["id"] for a in apps) or "none") + ".")

    async def _check_file(self, app_id, path, content: str) -> None:
        """Everything that can reject a write, run before the approval card and again before the write: file rules, then the sandbox test of index.js."""
        try:
            self.ctx.fileapps.check_content(str(path or ""), content)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        if path == "index.js":
            await self._verify("file_app", {"code": content, "app_id": app_id})

    async def write_app_file(self, a: dict):
        self._guard_mod()
        app_id, path, content = a.get("app_id"), a.get("path"), a.get("content", "")
        await self._check_file(app_id, path, content)
        try:
            res = self.ctx.fileapps.write_file(app_id, path, content)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        if self._is_placeholder(app_id):   # not _done(): saving manifest.json or style.css first is not building the app
            return (f"Saved '{path}' in custom app '{app_id}' ({res['bytes']} bytes), but index.js is still the empty placeholder, so the app does nothing yet. "
                    f"Next call write_app_file with app_id '{app_id}', path 'index.js' and the full code.")
        return self._done(f"Saved '{path}' in custom app '{app_id}' ({res['bytes']} bytes)")

    async def _write_file_preview(self, a: dict) -> str:
        app_id, path, content = a.get("app_id"), a.get("path"), a.get("content", "")
        await self._check_file(app_id, path, content)
        try:
            old = self.ctx.fileapps.read_file(app_id, path)
            return self._diff(old, content)
        except (FileAppError, ValueError):
            return "\n".join("+ " + line for line in content.splitlines())

    def _patched(self, a: dict) -> tuple[str, str]:
        """The file as it is and as it would be after the patch; a clear error if `old` is not in it exactly once."""
        app_id, path = a.get("app_id"), a.get("path")
        old, new = str(a.get("old") or ""), str(a.get("new") if a.get("new") is not None else "")
        try:
            cur = self.ctx.fileapps.read_file(app_id, path)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        at = self._locate(cur, old) if old.strip() else None
        if at is None:
            n = cur.count(old) if old else 0
            if n > 1:
                raise ToolError(f"old appears {n} times in {path}. Include more of the surrounding code so it matches once.", bad_args=True)
            close = difflib.get_close_matches(old.strip().splitlines()[0].strip() if old.strip() else "", [l.strip() for l in cur.splitlines() if l.strip()], n=3, cutoff=0.5)
            raise ToolError(f"old is not in {path}. " + (("The closest lines are: " + " | ".join(close) + ". ") if close else "")
                            + "Call read_app_file and copy a line from it.", bad_args=True)
        return cur, cur[:at[0]] + new + cur[at[1]:]

    async def patch_app_file(self, a: dict):
        self._guard_mod()
        app_id, path = a.get("app_id"), a.get("path")
        cur, updated = self._patched(a)
        await self._check_file(app_id, path, updated)
        try:
            self.ctx.fileapps.write_file(app_id, path, updated)
        except (FileAppError, ValueError) as e:
            raise ToolError(str(e), bad_args=True)
        left = self._left_behind(str(a.get("old") or ""), str(a.get("new") or ""), updated)
        if left:   # not _done(): its "reply with one short sentence" would be what the model follows
            return f"Patched '{path}' in custom app '{app_id}', but the change is NOT finished. {left}"
        return self._done(f"Patched '{path}' in custom app '{app_id}'")

    @staticmethod
    def _left_behind(old: str, new: str, updated: str) -> str:
        """Numbers and words the patch took out that still appear elsewhere in the file ('10 * 60' written, '25:00' still shown): a small model fixes one place and stops."""
        gone = {t for t in re.findall(r"\d+|[A-Za-z_]{4,}", old) if t not in set(re.findall(r"\d+|[A-Za-z_]{4,}", new))}
        lines = [l.strip() for l in updated.splitlines() if l.strip() and l.strip() not in new]
        hits = [l[:110] for l in lines if any(re.search(rf"(?<![\w.]){re.escape(t)}(?![\w])", l) for t in gone)][:3]
        return ("Still in the file, from what you replaced: " + " | ".join(hits) + ". Call patch_app_file again for each one that should change too (the screen must show the new value), then reply.") if hits else ""

    async def _patch_file_preview(self, a: dict) -> str:
        cur, updated = self._patched(a)
        await self._check_file(a.get("app_id"), a.get("path"), updated)
        return self._diff(cur, updated)

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
            hint = "Use export async function render(el, ctx) and ctx.h." if kind == "file_app" else "Use only `diatonic`, `el` and `ctx` as described."
            return f"The code failed when it was run: {errors[0]}. Fix the code and call the tool again with all of it. {hint}"
        if kind == "file_app" and not r.get("rendered") and not r.get("usedApi"):
            return ("The panel showed nothing. Make sure your exported render(el, ctx) builds elements and appends them to el, "
                    "for example: el.append(ctx.h('div', {}, 'Hello')).")
        return None

    def _panel_tools(self) -> list[str]:
        """Names a panel may pass to ctx.tool (read-only tools of enabled apps, as <app>_<tool>), so a wrong guess fails the test run, not the user."""
        return sorted(f"{a.id}_{t.name}" for a in self.ctx.registry.enabled() if a.id not in ("shell", "ui") for t in a.tools() if not t.changes)

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
        tools = self._panel_tools()
        app_id = a.get("app_id")
        if app_id and hasattr(self.ctx, "fileapps"):
            for name in self.ctx.fileapps.declared_tool_names(app_id):   # read from the files, never imported: tools.py is Python the user has not approved yet
                tools += [f"{app_id}_{name}", name]
        try:
            if not self.ctx.send_ui(client, {"type": "ui", "action": "verify", "id": rid, "kind": kind, "code": code,
                                             "tools": tools}):
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

    @staticmethod
    def _code_preview(a: dict, key: str) -> str:
        return "\n".join("+ " + line for line in str(a.get(key, "")).splitlines())

    @staticmethod
    def _calls_server(a: dict) -> str:
        return " (it talks to the server)" if re.search(r"\bapi\b|fetch\s*\(|/api/|ctx\.(?:tool|data)\b", str(a.get("code", ""))) else ""

    def builds_something(self, text: str) -> bool:
        """Is this a request for a new app? "Make a panel that lists my to-dos" is, "show my to-dos" is not."""
        low = (text or "").lower()
        return bool(re.search(MAKE_A, low) or NEW_APP.search(low)) and not re.search(r"^\W*(?:show|open|list|what|how)\b", low) and not CHANGE_FIRST.match(low)

    def _hidden_from_cloud(self) -> set[str]:
        """Apps whose entries this run may not see: private ones, when the model is in the cloud and the user has not allowed them for this chat."""
        ctx, reg = self.ctx, self.ctx.registry
        if reg is None or not ctx.cloud():
            return set()
        acfg = ctx.config.data["agent"]
        grants = ctx.cloud_grants.get(((session.CALL.get() or (None,))[0], acfg.get("active_model") or acfg["model"]), set())
        return {a.id for a in reg.apps.values() if reg.is_private(a)} - grants

    async def platform_search(self, a: dict):
        kind = a.get("kind") or None
        if kind and kind not in KINDS:
            raise ToolError(f"kind must be one of: {', '.join(KINDS)}.", bad_args=True)
        found = self.ctx.catalog.search(str(a.get("query") or ""), kind, hidden=self._hidden_from_cloud())
        return [f"{e.signature}: {e.line}" + (f" [{e.kind}]" if e.kind != "tool" else "") for e in found] or "Nothing matches. Try other words."

    async def platform_describe(self, a: dict):
        e = self.ctx.catalog.describe(str(a.get("name") or ""), hidden=self._hidden_from_cloud())
        if e is None:
            raise ToolError(f"Nothing is called '{a.get('name')}'. Use platform_search to find the exact name.", bad_args=True)
        return e.full()

    async def read_skill(self, a: dict):
        s = instructions.get_skill(str(a.get("name") or ""))
        if s is None:
            raise ToolError(f"No skill is called '{a.get('name')}'. Available: {', '.join(x.name for x in instructions.skills())}.", bad_args=True)
        return s.body

    def file_app_tools(self) -> list[Tool]:
        return [
            Tool("create_file_app", "Create a file-based app with its own directory, manifest.json, index.js, and style.css in ~/.diatonic/apps/<id>/.",
                 {"properties": {"id": {"type": "string", "description": "App id slug, e.g. pomodoro-timer"},
                                 "name": {"type": "string", "description": "Display name of the app"},
                                 "description": {"type": "string", "description": "Short description of what the app does"},
                                 "icon": {"type": "string", "description": "Icon name from icons.js (e.g. clock, box, chat)"}},
                  "required": ["id", "name"]},
                 self.create_file_app, WRITE, title="Create file app", precheck=self._blocked,
                 describe=lambda a: f"Create custom app folder '{a.get('name') or a.get('id')}'"),
            Tool("delete_file_app", "Permanently delete a custom app folder from ~/.diatonic/apps/<id>/.",
                 {"properties": {"app_id": {"type": "string", "description": "App id slug to delete (e.g. youtube-search)"}},
                  "required": ["app_id"]},
                 self.delete_file_app, DESTRUCTIVE, title="Delete custom app", precheck=self._blocked,
                 describe=lambda a: f"Permanently delete custom app '{a.get('app_id')}' and all its files"),
            Tool("write_app_file", "Write the whole of a file in a custom app (create_file_app first). This is how an app gets built: until index.js is written the app is an empty placeholder. "
                 "For style.css: scope styles to your container (not body or .btn). " + FILE_HINT,
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

    def lookup_tools(self) -> list[Tool]:
        return [
            Tool("platform_search",
                 "Look up what the platform can do: tools, window commands, components, skills and the app API. Give a few words, e.g. 'notify', 'timer', "
                 "'delete note'. Returns names with one line each; call platform_describe for the full entry.",
                 {"properties": {"query": {"type": "string", "description": "A few words about what you want to do"},
                                 "kind": {"type": "string", "enum": list(KINDS), "description": "Only this kind of entry"}},
                  "required": ["query"]}, self.platform_search, title="Search the platform", describe=lambda a: f"Look up “{a.get('query')}”"),
            Tool("platform_describe",
                 "The full entry for one name from platform_search: what it does, every argument, the effects it has and an example call.",
                 {"properties": {"name": {"type": "string", "description": "An exact name from platform_search"}}, "required": ["name"]},
                 self.platform_describe, title="Describe a platform entry", describe=lambda a: f"Describe {a.get('name')}"),
            Tool("read_skill",
                 "Read a skill: step-by-step instructions, or a recipe (a small working app to copy). Names come from ui_platform_search (kind skill). "
                 "Start with app-creator when building an app.",
                 {"properties": {"name": {"type": "string", "description": "A skill name, e.g. app-creator, recipes, recipe-timer"}}, "required": ["name"]},
                 self.read_skill, title="Read a skill", describe=lambda a: f"Read the skill {a.get('name')}"),
        ]
