"""Tool apps: an app made of tools Diatonic already has, preset for one job.

"A Google Meet scheduler" is Calendar's create/update/delete/list tools with `add_meet` always on and only the fields a
meeting needs. A tool app is just that definition: each action names an existing tool, fixes some of its arguments, and
exposes the rest. One runtime gives it a panel (a form per action, results as a list with per-item buttons) and agent
tools. Accounts, approvals (deleting still asks) and the untrusted-content rules all come from the underlying tool.
"""
from __future__ import annotations

import json
import re

from .apps.base import App, Tool, ToolError

NAME = re.compile(r"[a-z][a-z0-9_]{0,30}")
MAX_ACTIONS = 10
REF_PARAMS = ("event", "email", "ref", "id", "item", "note", "path", "number", "issue")   # params that take an item from a list


class ToolSpecError(ValueError):
    pass


def parse_fixed(v) -> dict:
    """Preset arguments as an object or as text a small model writes: 'add_meet=true, calendar=Work'."""
    if isinstance(v, dict):
        return v
    out = {}
    for part in re.split(r"\s*,\s*", str(v or "").strip()):
        if not part:
            continue
        if re.fullmatch(r"[A-Za-z_]\w*\s*[=:]?\s*", part):   # 'calendar=' with nothing after it: no preset
            continue
        m = re.fullmatch(r"([A-Za-z_]\w*)\s*[=:]\s*(.+)", part)
        if not m:
            raise ToolSpecError(f"Can't read '{part}'. Write presets like add_meet=true, calendar=Work")
        val = m.group(2).strip().strip("'\"")
        out[m.group(1)] = True if val.lower() == "true" else False if val.lower() == "false" else int(val) if val.isdigit() else val
    return out


def check_tool_manifest(m) -> dict:
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            raise ToolSpecError("The app definition isn't valid JSON.")
    if not isinstance(m, dict):
        raise ToolSpecError("The app definition must be an object.")
    actions = {}
    for name, a in (m.get("actions") or {}).items():
        if not NAME.fullmatch(name):
            raise ToolSpecError(f"Action name '{name}' must be lowercase letters, digits or _ (e.g. schedule).")
        tool = str(a.get("tool") or "")
        if not re.fullmatch(r"[a-z][a-z0-9-]*_[a-z0-9_]+", tool):
            raise ToolSpecError(f"'{tool}' isn't a tool name. Use a full name like gcal_create_event.")
        actions[name] = {"title": str(a.get("title") or name.replace("_", " ").capitalize())[:60], "tool": tool,
                         "fixed": parse_fixed(a.get("fixed") or {}), "params": [str(p) for p in (a.get("params") or [])][:15],
                         "description": str(a.get("description") or "")[:200]}
    if len(actions) > MAX_ACTIONS:
        raise ToolSpecError(f"An app can have at most {MAX_ACTIONS} actions.")
    return {"title": str(m.get("title") or "My app")[:40], "description": str(m.get("description") or "")[:200],
            "apps": [str(x) for x in (m.get("apps") or [])][:6], "actions": actions}


def summary(name: str, m: dict) -> str:
    acts = "; ".join(f"{n} = {a['tool']}" + (f" with {', '.join(f'{k}={v}' for k, v in a['fixed'].items())}" if a["fixed"] else "")
                     for n, a in m["actions"].items()) or "no actions yet"
    return f"{name} ('{m['title']}', built on {', '.join(m['apps']) or '?'}): {acts}"


def catalog(registry, app_ids: list[str]) -> list[str]:
    """What an app could be built from: each tool of the named apps, with its parameters (* = required)."""
    out = []
    for aid in app_ids:
        app = registry.apps.get(aid)
        if app is None or getattr(app, "tool_app", False):
            continue
        for t in app.tools():
            req = set(t.parameters.get("required", []))
            params = ", ".join(f"{p}{'*' if p in req else ''}" for p in t.parameters.get("properties", {}))
            out.append(f"{aid}_{t.name}({params}){' [asks first]' if t.risk == 'destructive' else ''}: {t.description[:110]}")
    return out


# What a request asks an app to do, by the verb a tool's name starts with. Any other verb (archive, star, send...) counts
# when the request uses it.
VERBS = {"create": r"\b(?:creat|schedul|book|new\b|add (?!guest)|make|set ?up|plan)", "list": r"\b(?:list|show|see|view|upcoming|browse|all my)\b",
         "update": r"\b(?:chang|updat|edit|reschedul|move|modif|renam|guests?\b|invit|attendee|time\b)",
         "delete": r"\b(?:delet|cancel|remov)", "search": r"\b(?:search|find|look up)\b",
         "respond": r"\b(?:accept|decline)\w*\s+(?:the |an? |my )?invit|\brsvp\b|\brespond"}
ORDER = ("create", "list", "update", "respond", "delete")
FREE_TEXT = {"summary", "title", "description", "body", "subject", "text", "notes", "location", "message"}
ABOUT_APP = re.compile(r"\b(?:create|make|build|add)\s+(?:me\s+)?(?:an?|the|my|new)?\s*(?:[\w-]+\s+){0,4}?(?:app|panel|tool)\b")


def hint_presets(tool, request: str) -> dict:
    """Presets the tool itself recommends for this request (Calendar: an app about Meet adds a Meet link to every meeting)."""
    out = {}
    for rx, preset in ((tool.build or {}).get("when") or {}).items():
        if re.search(rx, request or "", re.I):
            out.update(preset)
    return out


def make_action(tool, request: str) -> dict:
    """An action for `tool` with the inputs and presets its app recommends."""
    b, props = tool.build or {}, tool.parameters.get("properties", {})
    fixed = hint_presets(tool, request)
    params = list(b["inputs"]) if "inputs" in b else [k for k in props if k not in ("account", "calendar", "max_results")]
    params += [r for r in tool.parameters.get("required", []) if r not in params and r not in fixed]
    return {"title": b.get("title") or tool.title, "tool": "", "fixed": fixed, "params": [p for p in params if p in props and p not in fixed], "description": ""}


def plan(registry, bases: list[str], request: str) -> dict:
    """The actions a request asks for, from the base apps' tools: 'creates meetings, adds guests, lets me change the time,
    deletes meetings and lists them' -> create_event, update_event (with add_guests), delete_event, list_events."""
    text = ABOUT_APP.sub(" ", (request or "").lower())     # 'create a ... app' is about the app, not about creating things
    out = {}
    for aid in bases:
        app = registry.apps.get(aid)
        if app is None:
            continue
        tools = app.tools()
        verb = lambda t: t.name.split("_")[0]
        noun = next((t.name.split("_", 1)[1] for t in tools if verb(t) == "create" and "_" in t.name), "")
        wanted = {v for v in VERBS if re.search(VERBS[v], text)} | {"list"}          # an app always shows what's there
        wanted |= {verb(t) for t in tools if verb(t) not in VERBS and re.search(rf"\b{re.escape(verb(t)[:5])}", text)}
        for v in sorted(wanted, key=lambda v: ORDER.index(v) if v in ORDER else len(ORDER)):
            cands = [t for t in tools if verb(t) == v]
            t = next((t for t in cands if noun and t.name.split("_", 1)[-1].startswith(noun)), cands[0] if cands else None)
            if t is None:
                continue
            a = make_action(t, request)
            a["tool"] = f"{aid}_{t.name}"
            key = re.sub(r"[^a-z0-9]+", "_", a["title"].lower()).strip("_") or v
            out[key if key not in out else f"{key}_{aid}"] = a
    return out


def resolve(registry, tool: str):
    """The underlying (app, tool) for a full tool name, or None. Only built-in apps: a tool app can't be built on another."""
    for a in registry.enabled():
        if getattr(a, "tool_app", False) or not tool.startswith(a.id + "_"):
            continue
        t = next((t for t in a.tools() if t.name == tool[len(a.id) + 1:]), None)
        if t:
            return a, t
    return None


def _coerce(spec: dict, v):
    """Form text into what the tool expects: 'a@x.com, b@y.com' for a list, 'true' for a checkbox, '30' for a number."""
    t = spec.get("type")
    if t == "array" and isinstance(v, str):
        return [x.strip() for x in re.split(r"[,;\n]+", v) if x.strip()]
    if t == "boolean" and isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "on", "1")
    if t == "integer" and isinstance(v, str) and v.strip().lstrip("-").isdigit():
        return int(v)
    return v


class ToolApp(App):
    icon = "box"
    panel = True
    tool_app = True
    module = "/apps/_tools.js"      # one generic panel for every tool app

    def __init__(self, ctx, name: str, manifest: dict):
        super().__init__(ctx)
        self.id, self.m = name, manifest
        self.name = manifest["title"]
        self.description = manifest["description"] or "Made by the agent from Diatonic's own tools."
        words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", manifest["title"] + " " + name.replace("_", " "))}
        self.keywords = sorted(words | set(manifest["actions"]))
        self.default_allow = []

    def _under(self, action: str):
        a = self.m["actions"].get(action)
        if a is None:
            raise ToolError(f"{self.name} has no action '{action}'. Actions: {', '.join(self.m['actions'])}.")
        hit = resolve(self.ctx.registry, a["tool"]) if getattr(self.ctx, "registry", None) else None
        if hit is None:
            raise ToolError(f"{self.name} › {a['title']} uses {a['tool']}, which isn't available (is its app enabled?).")
        return a, hit[0], hit[1]

    def schema(self, action: str) -> dict:
        """The underlying tool's parameters minus the preset ones (and only the listed ones, if any are listed)."""
        a, _, t = self._under(action)
        props = {k: v for k, v in t.parameters.get("properties", {}).items() if k not in a["fixed"] and (not a["params"] or k in a["params"])}
        req = [r for r in t.parameters.get("required", []) if r in props]
        return {"properties": props, "required": req}

    async def run(self, action: str, args: dict):
        a, app, t = self._under(action)
        props = self.schema(action)["properties"]
        call = {**{k: _coerce(props[k], v) for k, v in (args or {}).items() if k in props and v not in (None, "", [])}, **a["fixed"]}
        missing = [r for r in t.parameters.get("required", []) if r not in call]
        if missing:
            raise ToolError(f"{a['title']} needs {', '.join(missing)}.", bad_args=True)
        return await t.handler(call)

    @property
    def private(self) -> bool:            # as private as what it's built on
        return any(getattr(self.ctx.registry.apps.get(x), "private", False) for x in self.m["apps"]) if getattr(self.ctx, "registry", None) else True

    @property
    def untrusted_tools(self) -> list[str]:
        out = []
        for n, a in self.m["actions"].items():
            hit = resolve(self.ctx.registry, a["tool"]) if getattr(self.ctx, "registry", None) else None
            if hit and hit[1].name in hit[0].untrusted_tools:
                out.append(n)
        return out

    def tools(self) -> list[Tool]:
        out = []
        for n, a in self.m["actions"].items():
            try:
                _, app, t = self._under(n)
            except ToolError:
                continue
            out.append(Tool(n, f"{self.name}: {a['title']}. {a['description'] or t.description}"[:300], self.schema(n),
                            (lambda name: lambda args: self.run(name, args))(n), t.risk, title=a["title"],
                            describe=(lambda tt, aa: (lambda args: tt.describe({**args, **aa['fixed']}) if tt.describe else aa['title']))(t, a),
                            preview=t.preview, precheck=t.precheck))
        return out

    async def snapshot(self, n: int) -> dict:
        """The first action that needs no input (e.g. "upcoming meetings") as a home tile."""
        for name, a in self.m["actions"].items():
            try:
                sch = self.schema(name)
                _, _, t = self._under(name)
            except ToolError:
                continue
            if sch["required"] or t.risk != "read":
                continue
            r = await self.run(name, {})
            rows = next((v for v in (r.values() if isinstance(r, dict) else [r]) if isinstance(v, list)), []) if r else []
            label = lambda x: str(x.get("title") or x.get("summary") or x.get("subject") or x.get("name") or x.get("ref") or x)[:120]
            sub = lambda x: " · ".join(str(x[k]) for k in ("start", "date", "from", "due") if x.get(k))[:120] or None
            return {"items": [{"text": label(x), "sub": sub(x)} for x in rows[:n] if isinstance(x, dict)], "empty": f"Nothing from {a['title'].lower()}."}
        return {"items": [], "empty": "Open the app to use it."}
