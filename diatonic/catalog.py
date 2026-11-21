"""One searchable list of everything the platform can do: tools, window commands, components, skills and `ctx` methods.

Entries are generated from the live registries and never written by hand, so the list cannot drift from the code. Tools come from the app
registry, commands are pushed once by the page, and other kinds (components, skills, ctx methods) join through `add_source`. The agent
finds things with `platform_search` and reads one in full with `platform_describe`.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

KINDS = ("tool", "command", "component", "skill", "ctx-api")
MAX_COMMANDS = 500


@dataclass
class Entry:
    kind: str
    name: str
    doc: str
    args: list[dict] = field(default_factory=list)   # [{"name", "type", "doc", "required"}]
    example: str = ""
    effects: list[str] = field(default_factory=list)
    domain: str = ""
    app: str = ""   # the app that owns it; entries of a private app are hidden from a cloud model until the user allows it

    @property
    def signature(self) -> str:
        return f"{self.name}({', '.join(a['name'] + ('' if a.get('required') else '?') for a in self.args)})"

    @property
    def line(self) -> str:
        first = re.split(r"(?<=[.!?])\s", self.doc.strip(), maxsplit=1)[0]
        return first if len(first) <= 110 else first[:107].rstrip() + "..."

    def short(self) -> dict:
        return {"kind": self.kind, "name": self.name, "line": self.line, "signature": self.signature}

    def full(self) -> dict:
        return {**asdict(self), "signature": self.signature}


def _placeholder(spec: dict) -> str:
    if spec.get("enum"):
        return repr(spec["enum"][0])
    return {"integer": "1", "number": "1", "boolean": "true", "array": "[]", "object": "{}"}.get(spec.get("type"), '"text"')


def tool_example(name: str, params: dict) -> str:
    """A call with the required arguments filled with stand-in values, so every tool has an example without anyone writing one."""
    props = params.get("properties", {})
    parts = [f"{k}={_placeholder(props.get(k, {}))}" for k in params.get("required", [])]
    return f"{name}({', '.join(parts)})"


def tool_entry(app, tool) -> Entry:
    props, req = tool.parameters.get("properties", {}), set(tool.parameters.get("required", []))
    name = f"{app.id}_{tool.name}"
    args = [{"name": k, "type": v.get("type", "string"), "doc": v.get("description", ""), "required": k in req} for k, v in props.items()]
    return Entry("tool", name, tool.description, args, tool_example(name, tool.parameters), sorted(tool.effects), tool.domain or app.id, app.id)


def _words(text: str) -> list[str]:
    return [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in re.findall(r"[a-z0-9]+", text.lower())]


def _score(e: Entry, q: list[str], raw: str) -> int:
    name, doc, where = set(_words(e.name)), e.doc.lower(), f"{e.domain} {e.app} {' '.join(e.effects)}".lower()
    score = 10 if raw and raw == e.name.lower() else 0
    for w in q:
        score += (5 if w in name else 0) + (2 if w in doc else 0) + (3 if w in where else 0) + (1 if any(w in a["name"] for a in e.args) else 0)
    return score


class Catalog:
    def __init__(self, ctx):
        self.ctx = ctx
        self.commands: list[dict] = []
        self.sources: list[Callable[[], list[Entry]]] = []

    def add_source(self, fn: Callable[[], list[Entry]]) -> None:
        """Another place entries come from (components, skills...). `fn` is called on every search, so it always reflects the files on disk."""
        self.sources.append(fn)

    def set_commands(self, items) -> int:
        """The page tells the server which commands exist (id, title, group). Anything malformed is dropped."""
        keep = [{"id": str(c["id"])[:80], "title": str(c.get("title") or c["id"])[:120], "group": str(c.get("group") or "")[:40]}
                for c in (items if isinstance(items, list) else []) if isinstance(c, dict) and isinstance(c.get("id"), str) and c["id"]][:MAX_COMMANDS]
        self.commands = keep
        return len(keep)

    def entries(self) -> list[Entry]:
        out: list[Entry] = []
        reg = getattr(self.ctx, "registry", None)
        if reg is not None:
            out += [tool_entry(a, t) for a, t in reg.tool_map().values()]
        out += [Entry("command", c["id"], f"{c['title']}." + (f" ({c['group']})" if c["group"] else ""), example=f'command("{c["id"]}")', domain=c["group"].lower())
                for c in self.commands]
        for fn in self.sources:
            out += fn()
        return out

    def search(self, query: str = "", kind: str | None = None, limit: int = 8, hidden: frozenset | set = frozenset()) -> list[Entry]:
        q = _words(query)
        found = [(s, e) for e in self.entries() if (not kind or e.kind == kind) and e.app not in hidden for s in [_score(e, q, query.strip().lower()) if q else 1] if s > 0]
        found.sort(key=lambda p: (-p[0], p[1].name))
        return [e for _, e in found[:max(1, min(int(limit), 50))]]

    def describe(self, name: str, hidden: frozenset | set = frozenset()) -> Entry | None:
        name = name.strip()
        return next((e for e in self.entries() if e.name == name and e.app not in hidden), None)
