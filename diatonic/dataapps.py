"""Data apps: personal apps (trackers, lists, logs) that are data, not code, and need no network.

A data app is a definition: a few lists, each with typed fields, optionally nested ("topics" inside "subjects").
One runtime, written and tested once, gives every such app storage (one table for all), a panel, and agent tools
generated per list (add_topic, update_topic, list_topics, ...). The agent builds one with a couple of small,
checked calls, and items are always found by name, so a small model never handles ids.
"""
from __future__ import annotations

import difflib
import json
import re
from datetime import date, timedelta

from .apps.base import DESTRUCTIVE, READ, WRITE, App, Tool, ToolError

TYPES = ("text", "notes", "number", "date", "check", "choice")
ALIASES = {"string": "text", "str": "text", "longtext": "notes", "long text": "notes", "note": "notes", "textarea": "notes",
           "int": "number", "integer": "number", "float": "number", "bool": "check", "boolean": "check", "checkbox": "check",
           "done": "check", "select": "choice", "enum": "choice", "datetime": "date", "day": "date"}
LIST = re.compile(r"[a-z][a-z0-9_]{1,24}")
MAX_LISTS, MAX_FIELDS, MAX_DEPTH = 6, 12, 3


class DataSpecError(ValueError):
    pass


def singular(name: str) -> str:
    return name[:-3] + "y" if name.endswith("ies") else name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def parse_fields(spec) -> list[dict]:
    """Fields as a small model writes them: 'name*: text, exam_date: date, done: check, priority: choice(high, medium, low)'.

    `*` = required. The first field is the item's name (a 'name: text' field is added if there is none).
    """
    if isinstance(spec, list):
        parts = [f if isinstance(f, dict) else {"raw": str(f)} for f in spec]
    else:
        parts, depth, cur = [], 0, ""
        for ch in str(spec or ""):          # split on commas outside parentheses: choice(a, b) stays whole
            depth += ch == "("
            depth -= ch == ")"
            if ch == "," and depth == 0:
                parts.append({"raw": cur}); cur = ""
            else:
                cur += ch
        parts.append({"raw": cur})
    out = []
    for p in parts:
        if "raw" in p:
            raw = p["raw"].strip()
            if not raw:
                continue
            m = re.fullmatch(r"([A-Za-z][\w ]*?)\s*(\*)?\s*(?::\s*(.+))?", raw)
            if not m:
                raise DataSpecError(f"Can't read the field '{raw}'. Write it like: exam_date: date")
            p = {"name": m.group(1), "required": bool(m.group(2)), "type": (m.group(3) or "text").strip()}
        name = re.sub(r"\W+", "_", str(p.get("name") or "").strip().lower()).strip("_")
        typ = str(p.get("type") or "text").strip().lower()
        options = list(p.get("options") or [])
        cm = re.fullmatch(r"(?:choice|select|enum|one of)\s*\((.*)\)", typ)
        if cm:
            typ, options = "choice", [o.strip() for o in re.split(r"[|,/]", cm.group(1)) if o.strip()]
        typ = ALIASES.get(typ, typ)
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,30}", name):
            raise DataSpecError(f"'{p.get('name')}' isn't a valid field name.")
        if typ not in TYPES:
            raise DataSpecError(f"'{typ}' isn't a field type. Use one of: {', '.join(TYPES)} (choice(a, b, c) for a fixed list).")
        if typ == "choice" and not options:
            raise DataSpecError(f"{name}: a choice needs its options, e.g. choice(high, medium, low).")
        out.append({"name": name, "type": typ, "required": bool(p.get("required")), "options": options[:12]})
    if not out or out[0]["type"] != "text":
        out.insert(0, {"name": "name", "type": "text", "required": True, "options": []})
    out[0]["required"] = True
    names = [f["name"] for f in out]
    if len(set(names)) != len(names):
        raise DataSpecError("Two fields have the same name.")
    if len(out) > MAX_FIELDS:
        raise DataSpecError(f"A list can have at most {MAX_FIELDS} fields.")
    return out


def check_data_manifest(m) -> dict:
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            raise DataSpecError("The app definition isn't valid JSON.")
    if not isinstance(m, dict):
        raise DataSpecError("The app definition must be an object.")
    lists = {}
    for name, l in (m.get("lists") or {}).items():
        if not LIST.fullmatch(name):
            raise DataSpecError(f"List name '{name}' must be lowercase letters, digits or _ (e.g. subjects).")
        parent = l.get("parent") or None
        if parent and parent not in lists:
            raise DataSpecError(f"The parent list '{parent}' must be added first." if parent not in (m.get("lists") or {})
                                else "Add the parent list before the lists inside it.")
        depth, p = 1, parent
        while p:
            depth += 1
            p = lists[p]["parent"]
        if depth > MAX_DEPTH:
            raise DataSpecError(f"Lists can nest at most {MAX_DEPTH} deep.")
        item = re.sub(r"\W+", "_", str(l.get("item") or singular(name)).lower()).strip("_") or name
        lists[name] = {"item": item, "parent": parent, "fields": parse_fields(l.get("fields") or [])}
    if len(lists) > MAX_LISTS:
        raise DataSpecError(f"An app can have at most {MAX_LISTS} lists.")
    items = [l["item"] for l in lists.values()]
    if len(set(items)) != len(items):
        raise DataSpecError("Two lists have the same item name; give one an explicit item name.")
    return {"title": str(m.get("title") or "My app")[:40], "description": str(m.get("description") or "")[:200], "lists": lists}


def summary(name: str, m: dict) -> str:
    """One line per app for tool descriptions, so the model edits what exists."""
    def f(x):
        t = f"choice({'|'.join(x['options'])})" if x["type"] == "choice" else x["type"]
        return f"{x['name']}{'*' if x['required'] else ''}: {t}"
    ls = "; ".join(f"{n}{' (in ' + l['parent'] + ')' if l['parent'] else ''} = {', '.join(f(x) for x in l['fields'])}" for n, l in m["lists"].items())
    return f"{name} ('{m['title']}'): {ls or 'no lists yet'}"


def coerce(field: dict, value, today: date):
    """A value as typed by a person or a small model, checked against the field type."""
    if value is None or value == "":
        return None
    t = field["type"]
    if t == "check":
        if isinstance(value, bool):
            return value
        v = str(value).strip().lower()
        if v in ("true", "yes", "y", "1", "done", "complete", "completed", "checked", "on"):
            return True
        if v in ("false", "no", "n", "0", "not done", "incomplete", "open", "unchecked", "off", "todo", "pending"):
            return False
        raise ToolError(f"{field['name']} is a yes/no field (true or false).", bad_args=True)
    if t == "number":
        try:
            n = float(str(value).strip())
            return int(n) if n.is_integer() else n
        except ValueError:
            raise ToolError(f"{field['name']} must be a number.", bad_args=True)
    if t == "date":
        v = str(value).strip().lower()
        named = {"today": 0, "tomorrow": 1, "yesterday": -1}
        if v in named:
            return (today + timedelta(days=named[v])).isoformat()
        try:
            return date.fromisoformat(v[:10]).isoformat()
        except ValueError:
            raise ToolError(f"{field['name']} must be a date like 2026-12-05.", bad_args=True)
    if t == "choice":
        hit = next((o for o in field["options"] if o.lower() == str(value).strip().lower()), None)
        if hit is None:
            raise ToolError(f"{field['name']} must be one of: {', '.join(field['options'])}.", bad_args=True)
        return hit
    return str(value)[:5000 if t == "notes" else 300]


class DataApp(App):
    icon = "check"
    panel = True
    data_app = True
    private = True   # the user's own records
    module = "/apps/_data.js"     # one generic panel for every data app

    def __init__(self, ctx, name: str, manifest: dict):
        super().__init__(ctx)
        self.id, self.m = name, manifest
        self.name = manifest["title"]
        self.description = manifest["description"] or "Made by the agent. Stored on this computer."
        words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", manifest["title"])}
        for ln, l in manifest["lists"].items():
            words |= {ln, l["item"], ln.replace("_", " "), l["item"].replace("_", " ")}
        self.keywords = sorted(w for w in words if len(w) > 3)
        self.default_allow = [t for ln, l in manifest["lists"].items() for t in (f"add_{l['item']}", f"update_{l['item']}")]

    # ---- finding things by name ----------------------------------------
    def _label(self, it: dict) -> str:
        return str(it["data"].get(self.m["lists"][it["list"]]["fields"][0]["name"]) or "")

    def find(self, lst: str, name: str, parent: str | None = None) -> dict:
        l = self.m["lists"][lst]
        rows = self.ctx.db.items(self.id, lst)
        if parent and l["parent"]:
            p = self.find(l["parent"], parent)
            rows = [r for r in rows if r["parent"] == p["id"]]
        want = str(name or "").strip().lower()
        labels = {r["id"]: self._label(r).lower() for r in rows}
        for test in (lambda s: s == want, lambda s: s.startswith(want), lambda s: want in s):
            hits = [r for r in rows if want and test(labels[r["id"]])]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                where = lambda r: f" (in {self._label(self.ctx.db.item(self.id, r['parent']))})" if r["parent"] else ""
                raise ToolError(f"More than one {l['item']} matches '{name}': " + ", ".join(self._label(r) + where(r) for r in hits[:8])
                                + (f". Say which {self.m['lists'][l['parent']]['item']}." if l["parent"] else "."), bad_args=True)
        close = difflib.get_close_matches(want, list(labels.values()), 3, 0.5)
        raise ToolError(f"No {l['item']} called '{name}'." + (f" Did you mean: {', '.join(close)}?" if close else
                        f" There are: {', '.join(list(labels.values())[:12]) or 'none yet'}."), bad_args=True)

    # ---- the generated tools -------------------------------------------
    def _values(self, l: dict, a: dict, partial: bool) -> dict:
        today = self.ctx.now().date()
        out = {}
        for f in l["fields"]:
            key = "new_name" if partial and f is l["fields"][0] else f["name"]
            if key in a and a[key] not in (None, ""):
                out[f["name"]] = coerce(f, a[key], today)
            elif not partial and f["required"]:
                raise ToolError(f"{f['name']} is required.", bad_args=True)
        return out

    def _changed(self) -> None:
        self.ctx.broadcast({"type": "app_changed", "app": self.id})

    async def add(self, lst: str, a: dict):
        l = self.m["lists"][lst]
        parent = None
        if l["parent"]:
            pl = self.m["lists"][l["parent"]]
            if not a.get(pl["item"]):
                raise ToolError(f"Say which {pl['item']} it goes in.", bad_args=True)
            parent = self.find(l["parent"], a[pl["item"]])["id"]
        data = self._values(l, a, partial=False)
        dup = [r for r in self.ctx.db.items(self.id, lst) if r["parent"] == parent and self._label(r).lower() == data[l["fields"][0]["name"]].lower()]
        if dup:
            return f"'{self._label(dup[0])}' is already there; nothing added."
        self.ctx.db.add_item(self.id, lst, data, parent)
        self._changed()
        return {"added": l["item"], **data}

    async def update(self, lst: str, a: dict):
        l = self.m["lists"][lst]
        pl = self.m["lists"][l["parent"]]["item"] if l["parent"] else None
        it = self.find(lst, a.get(l["item"]), a.get(pl) if pl else None)
        changes = self._values(l, a, partial=True)
        if not changes:
            raise ToolError("Nothing to change: give the fields to set.", bad_args=True)
        self.ctx.db.update_item(self.id, it["id"], {**it["data"], **changes})
        self._changed()
        return {"updated": self._label(it), **changes}

    def _tree(self, lst: str, rows: list[dict], parent_id) -> list[dict]:
        kids = [n for n, l in self.m["lists"].items() if l["parent"] == lst]
        out = []
        for r in (x for x in rows if x["list"] == lst and x["parent"] == parent_id):
            d = {k: v for k, v in r["data"].items() if v not in (None, "")}
            for k in kids:
                sub = self._tree(k, rows, r["id"])
                check = next((f["name"] for f in self.m["lists"][k]["fields"] if f["type"] == "check"), None)
                d[k] = sub
                if check and sub:
                    d[f"{k}_done"] = f"{sum(1 for s in sub if s.get(check))}/{len(sub)}"
            out.append(d)
        return out

    async def list_(self, lst: str, a: dict):
        l = self.m["lists"][lst]
        rows = self.ctx.db.items(self.id)
        if l["parent"] and a.get(self.m["lists"][l["parent"]]["item"]):
            p = self.find(l["parent"], a[self.m["lists"][l["parent"]]["item"]])
            res = self._tree(lst, rows, p["id"])
        else:
            res = []
            for pid in sorted({r["parent"] for r in rows if r["list"] == lst}, key=lambda x: x or 0) or [None]:
                where = {"in": self._label(self.ctx.db.item(self.id, pid))} if pid else {}
                res += [{**x, **where} for x in self._tree(lst, rows, pid)]
        check = next((f["name"] for f in l["fields"] if f["type"] == "check"), None)
        if check and a.get("only") in ("done", "open"):
            res = [r for r in res if bool(r.get(check)) == (a["only"] == "done")]
        return res or f"No {lst} yet."

    async def delete(self, a: dict):
        lst = a.get("list")
        if lst not in self.m["lists"]:
            raise ToolError(f"list must be one of: {', '.join(self.m['lists'])}.", bad_args=True)
        l = self.m["lists"][lst]
        it = self.find(lst, a.get("name"), a.get("in"))
        n = self.ctx.db.delete_item(self.id, it["id"])
        self._changed()
        return {"deleted": self._label(it), "with_items_inside": n - 1 or None}

    def _describe_delete(self, a: dict) -> str:
        try:
            it = self.find(a["list"], a.get("name"), a.get("in"))
            inside = len([r for r in self.ctx.db.items(self.id) if r["parent"] == it["id"]])
            return f"Delete the {self.m['lists'][a['list']]['item']} '{self._label(it)}'" + (f" and the {inside} item(s) inside it" if inside else "")
        except Exception:
            return f"Delete '{a.get('name')}' from {self.name}"

    def _delete_precheck(self, a: dict) -> str | None:
        """Find it before asking: no approval card for something that isn't there."""
        if a.get("list") not in self.m["lists"]:
            return f"list must be one of: {', '.join(self.m['lists'])}."
        try:
            self.find(a["list"], a.get("name"), a.get("in"))
        except ToolError as e:
            return str(e)
        return None

    def tools(self) -> list[Tool]:
        def prop(f):
            t = {"text": "string", "notes": "string", "date": "string", "choice": "string", "number": "number", "check": "boolean"}[f["type"]]
            p = {"type": t}
            if f["type"] == "choice":
                p["enum"] = f["options"]
            if f["type"] == "date":
                p["description"] = "YYYY-MM-DD"
            return p
        out = []
        for ln, l in self.m["lists"].items():
            item, label = l["item"], l["fields"][0]["name"]
            parent = self.m["lists"][l["parent"]]["item"] if l["parent"] else None
            where = {parent: {"type": "string", "description": f"Name of the {parent}"}} if parent else {}
            fields = {f["name"]: prop(f) for f in l["fields"]}
            out.append(Tool(f"add_{item}", f"{self.name}: add a {item}" + (f" to a {parent}" if parent else "") + ".",
                            {"properties": {**where, **fields}, "required": ([parent] if parent else []) + [f["name"] for f in l["fields"] if f["required"]]},
                            (lambda n: lambda a: self.add(n, a))(ln), WRITE, title=f"Add {item}",
                            describe=(lambda it: lambda a: f"Add {it} '{a.get(label)}'")(item)))
            upd = {f["name"] if f is not l["fields"][0] else "new_name": prop(f) for f in l["fields"]}
            out.append(Tool(f"update_{item}", f"{self.name}: change a {item} found by its name (e.g. mark it done, add notes, rename with new_name).",
                            {"properties": {item: {"type": "string", "description": f"Its current name"}, **where, **upd}, "required": [item]},
                            (lambda n: lambda a: self.update(n, a))(ln), WRITE, title=f"Update {item}"))
            lp = dict(where)
            if any(f["type"] == "check" for f in l["fields"]):
                lp["only"] = {"type": "string", "enum": ["done", "open"]}
            out.append(Tool(f"list_{ln}", f"{self.name}: list the {ln}" + (f" (of one {parent}, or all)" if parent else "") + ", with what's inside each.",
                            {"properties": lp}, (lambda n: lambda a: self.list_(n, a))(ln), READ, title=f"List {ln}"))
        if self.m["lists"]:
            out.append(Tool("delete_item", f"{self.name}: delete one item (and what's inside it) by name. The user is asked first.",
                            {"properties": {"list": {"type": "string", "enum": list(self.m["lists"])}, "name": {"type": "string"},
                                            "in": {"type": "string", "description": "Name of the item it's inside, if ambiguous"}}, "required": ["list", "name"]},
                            self.delete, DESTRUCTIVE, title="Delete item", describe=self._describe_delete, precheck=self._delete_precheck))
        return out
