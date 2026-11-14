"""Notes as plain markdown files in a folder you own (default ~/Documents/Diatonic Notes)."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..base import WRITE, App, Tool, ToolError

DEFAULT_DIR = "~/Documents/Diatonic Notes"
NOTE = {"type": "string", "description": "Note title or file name"}
_FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", title, flags=re.U).strip()
    s = re.sub(r"\s+", " ", s)
    return (s or "Untitled")[:80]


def parse(text: str) -> tuple[dict, str]:
    """Split optional front matter (title/tags/created) from the body."""
    meta, body = {}, text
    m = _FM.match(text)
    if m:
        body = text[m.end():]
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return meta, body


def dump(meta: dict, body: str) -> str:
    head = "\n".join(f"{k}: {v}" for k, v in meta.items() if v)
    return f"---\n{head}\n---\n{body.lstrip(chr(10))}" if head else body


class NotesApp(App):
    id = "notes"
    name = "Notes"
    icon = "file-text"
    description = "Markdown notes saved as plain files you can open in any editor."
    panel = True
    keywords = ["note", "notes", "jot", "notebook", "write down", "journal", "memo"]
    default_allow = ["create_note", "append_note"]
    settings = [{"key": "folder", "label": "Notes folder", "secret": False, "placeholder": DEFAULT_DIR,
                 "help": "Plain .md files. Point this at an existing folder to use your own notes."}]

    # ---- storage -------------------------------------------------------
    @property
    def root(self) -> Path:
        p = Path(self.setting("folder", DEFAULT_DIR)).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _path(self, slug: str) -> Path:
        p = (self.root / f"{slug}.md").resolve()
        if self.root.resolve() not in p.parents:
            raise ToolError("That name isn't allowed.")
        return p

    def _all(self) -> list[dict]:
        out = []
        for p in self.root.glob("*.md"):
            try:
                meta, body = parse(p.read_text(errors="replace"))
            except OSError:
                continue
            tags = [t.strip() for t in meta.get("tags", "").split(",") if t.strip()]
            out.append({"slug": p.stem, "title": meta.get("title") or p.stem, "tags": tags, "body": body,
                        "modified": p.stat().st_mtime, "meta": meta})
        return sorted(out, key=lambda n: -n["modified"])

    def _resolve(self, ref: str) -> dict:
        notes, q = self._all(), ref.strip().lower()
        for test in (lambda n: n["slug"].lower() == q, lambda n: n["title"].lower() == q,
                     lambda n: n["title"].lower().startswith(q), lambda n: q in n["title"].lower()):
            hits = [n for n in notes if test(n)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                raise ToolError("More than one note matches: " + ", ".join(f"“{n['title']}”" for n in hits[:6]) + ". Be more specific.")
        raise ToolError(f"No note called “{ref}”.")

    @staticmethod
    def _brief(n: dict, with_body: bool = False, snippet: str | None = None) -> dict:
        d = {"title": n["title"], "tags": n["tags"] or None,
             "modified": datetime.fromtimestamp(n["modified"]).strftime("%a %d %b %H:%M")}
        if snippet:
            d["match"] = snippet
        if with_body:
            d["body"] = n["body"][:4000]
        return d

    def _changed(self) -> None:
        self.ctx.broadcast({"type": "app_changed", "app": "notes"})

    def _write(self, slug: str, meta: dict, body: str) -> Path:
        p = self._path(slug)
        p.write_text(dump(meta, body))
        return p

    # ---- tools ---------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool("create_note", "Create a new personal note (not a file in a project folder).",
                 {"properties": {"title": {"type": "string"}, "body": {"type": "string", "description": "Markdown"},
                                 "tags": {"type": "array", "items": {"type": "string"}}}, "required": ["title", "body"]},
                 self.create_note, WRITE, describe=lambda a: f"Create note “{a.get('title')}”"),
            Tool("append_note", "Add text to the end of an existing note.",
                 {"properties": {"note": NOTE, "text": {"type": "string"}}, "required": ["note", "text"]},
                 self.append_note, WRITE, describe=lambda a: f"Add to note “{a.get('note')}”"),
            Tool("get_note", "Read a note.", {"properties": {"note": NOTE}, "required": ["note"]}, self.get_note),
            Tool("search_notes", "Search note titles, tags and text.",
                 {"properties": {"query": {"type": "string"}}, "required": ["query"]}, self.search_notes),
            Tool("list_notes", "List notes, newest first.",
                 {"properties": {"tag": {"type": "string"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
                 self.list_notes),
            Tool("delete_note", "Move a note to the trash folder (recoverable).",
                 {"properties": {"note": NOTE}, "required": ["note"]}, self.delete_note, WRITE,
                 describe=lambda a: f"Delete note “{a.get('note')}”"),
        ]

    async def create_note(self, a):
        slug, i = slugify(a["title"]), 2
        while self._path(slug).exists():
            slug = f"{slugify(a['title'])} {i}"
            i += 1
        meta = {"title": a["title"].strip(), "tags": ", ".join(a.get("tags", [])), "created": datetime.now().isoformat(timespec="minutes")}
        self._write(slug, meta, a["body"])
        self._changed()
        return {"created": a["title"], "file": f"{slug}.md"}

    async def append_note(self, a):
        n = self._resolve(a["note"])
        body = n["body"].rstrip("\n") + "\n\n" + a["text"].strip() + "\n"
        self._write(n["slug"], n["meta"], body)
        self._changed()
        return {"appended_to": n["title"]}

    async def get_note(self, a):
        return self._brief(self._resolve(a["note"]), with_body=True)

    async def search_notes(self, a):
        q = a["query"].lower().strip()
        words = [w for w in re.split(r"\s+", q) if w]
        scored = []
        for n in self._all():
            hay_t, hay_b = n["title"].lower(), n["body"].lower()
            tags = " ".join(n["tags"]).lower()
            if not all(w in hay_t or w in tags or w in hay_b for w in words):
                continue
            score = sum(3 * (w in hay_t) + 2 * (w in tags) + (w in hay_b) for w in words)
            i = hay_b.find(words[0]) if words else -1
            snip = n["body"][max(0, i - 40): i + 100].replace("\n", " ").strip() if i >= 0 else None
            scored.append((score, n, snip))
        scored.sort(key=lambda x: (-x[0], -x[1]["modified"]))
        return [self._brief(n, snippet=s) for _, n, s in scored[:10]] or "No notes match."

    async def list_notes(self, a):
        notes = [n for n in self._all() if not a.get("tag") or a["tag"].lower() in [t.lower() for t in n["tags"]]]
        return [self._brief(n) for n in notes[: a.get("limit", 15)]] or "No notes yet."

    async def delete_note(self, a):
        n = self._resolve(a["note"])
        trash = self.root / ".trash"
        trash.mkdir(exist_ok=True)
        self._path(n["slug"]).rename(trash / f"{n['slug']}-{int(time.time())}.md")
        self._changed()
        return {"deleted": n["title"], "recoverable_in": ".trash"}

    # ---- panel routes ----------------------------------------------------
    async def list_(self, request: Request):
        q = request.query_params.get("q", "").strip()
        if q:
            res = await self.search_notes({"query": q})
            titles = {r["title"] for r in res} if isinstance(res, list) else set()
            notes = [n for n in self._all() if n["title"] in titles]
        else:
            notes = self._all()
        return JSONResponse({"folder": str(self.root), "notes": [
            {"slug": n["slug"], "title": n["title"], "tags": n["tags"], "modified": n["modified"],
             "preview": n["body"].strip()[:120]} for n in notes]})

    async def read(self, request: Request):
        n = self._resolve(request.path_params["slug"])
        return JSONResponse({"slug": n["slug"], "title": n["title"], "tags": n["tags"], "body": n["body"], "modified": n["modified"]})

    async def save(self, request: Request):
        b = await request.json()
        slug = request.path_params.get("slug")
        if slug:  # update
            n = self._resolve(slug)
            meta = {**n["meta"], "title": b.get("title", n["title"]), "tags": ", ".join(b.get("tags", n["tags"]))}
            self._write(n["slug"], meta, b.get("body", n["body"]))
            return JSONResponse({"slug": n["slug"]})
        r = await self.create_note({"title": b.get("title") or "Untitled", "body": b.get("body", ""), "tags": b.get("tags", [])})
        return JSONResponse({"slug": r["file"][:-3]})

    async def remove(self, request: Request):
        await self.delete_note({"note": request.path_params["slug"]})
        return JSONResponse({"ok": True})

    def routes(self):
        return [Route("/notes", self.list_), Route("/notes", self.save, methods=["POST"]),
                Route("/notes/{slug}", self.read), Route("/notes/{slug}", self.save, methods=["PUT"]),
                Route("/notes/{slug}", self.remove, methods=["DELETE"])]
