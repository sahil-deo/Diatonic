"""Files: browse and read files (code, markdown, PDF), and let the agent read, search, create, edit and delete them.

Everything is confined to folders the user shares (see paths.py). Anything that changes a file asks for approval
with a diff, keeps a backup of the previous version, and deletes move to the Trash.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import sys
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ...config import home
from ..base import DESTRUCTIVE, WRITE, App, Tool, ToolError
from . import content as C
from .paths import PathError, Sandbox, validate_root

PATH = {"type": "string", "description": "File path, relative to the shared folder or absolute"}
MENTION = re.compile(r'(?<![\w/@])@(?:"([^"]+)"|(\S+))')
RAW_TYPES = {**C.IMAGES, ".pdf": "application/pdf"}
BACKUPS = 200


class FilesApp(App):
    private = True   # a cloud model must ask before reading or changing it
    id = "files"
    name = "Files"
    icon = "folder"
    description = "Browse and read files (code, Markdown, PDF) in folders you share; ask the agent to edit, create or delete them."
    panel = True
    keywords = ["file", "files", "folder", "folders", "directory", "directories", "readme", "pdf", "markdown", "source", "codebase"]
    keyword_patterns = [r"\b[\w.-]+\.(?:py|js|ts|tsx|jsx|json|md|txt|pdf|html|css|yml|yaml|toml|sh|go|rs|java|c|cpp|h|rb|php|sql|csv|log|xml|swift|kt)\b"]
    default_allow: list[str] = []   # every change shows a diff and asks

    # ---- sandbox ---------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return self.ctx.app_cfg(self.id)["settings"]

    def sandbox(self) -> Sandbox:
        return Sandbox(list(self.cfg.get("roots") or []), bool(self.cfg.get("allow_sensitive")))

    def locate(self, text: str) -> tuple[Path, str]:
        """Find a file or folder from what a person (or a small model) wrote: an exact path, or a near miss like
        'Developers/Agentic' for ~/Developer/Agentic, or just a name somewhere in the shared folders."""
        sb = self.sandbox()

        def ok(p: Path) -> Path | None:   # every candidate passes the same checks as an exact path (shared, not blocked)
            try:
                return sb.resolve(str(p), must_exist=True)
            except PathError:
                return None
        raw = str(text or "").strip().strip("`\"'").lstrip("@")
        try:
            real = sb.resolve(raw, must_exist=True)
            return real, sb.display(real)
        except PathError:
            pass
        norm = lambda s: re.sub(r"[\s_\-]+", "", s.lower()).rstrip("s")
        parts = [p for p in re.split(r"[\\/]+", re.sub(r"^~[\\/]?", "", raw)) if p and p != "."]
        if not parts:
            raise ToolError("Say which file or folder.")
        for root in sb.roots:
            segs = parts[1:] if norm(parts[0]) == norm(root.name) and len(parts) > 1 else parts   # 'Developer/x' inside the Developer root
            cur = root
            for seg in segs:
                try:
                    cur = next(c for c in sorted(cur.iterdir()) if norm(c.name) == norm(seg))
                except (StopIteration, OSError):
                    cur = None
                    break
            if cur is not None and (real := ok(cur)):
                return real, sb.display(real)
        want, seen = norm(parts[-1]), 0          # last resort: that name anywhere (folders too), shallowest first
        for root in sb.roots:
            for dirpath, dirs, files in os.walk(root):
                dirs[:] = sorted(d for d in dirs if not sb.skipped(d, True) and not d.startswith("."))
                for n in dirs + files:
                    seen += 1
                    if norm(n) == want or (len(want) > 3 and want in norm(n)):
                        if (real := ok(Path(dirpath) / n)):
                            return real, sb.display(real)
                if seen > 5000:
                    break
        raise ToolError(f"Nothing called '{text}' in the shared folders ({', '.join(r.name for r in sb.roots)}).")

    def _p(self, path: str, *, must_exist: bool = True, forgive: bool = False) -> Path:
        """Resolve a path in the shared folders. `forgive` (reading only, never for changes) accepts near misses
        like 'Developers/Agentic' for 'Developer/Agentic'."""
        try:
            return self.sandbox().resolve(path, must_exist=must_exist)
        except PathError as e:
            if forgive and must_exist and "doesn't exist" in str(e):   # never a way around "blocked" or "outside"
                try:
                    return self.locate(path)[0]
                except ToolError:
                    pass
            raise ToolError(str(e))

    def _changed(self, real: Path) -> None:
        self.ctx.broadcast({"type": "app_changed", "app": "files", "path": str(real)})

    # ---- backups and trash ------------------------------------------------
    @staticmethod
    def _hist_dir() -> Path:
        d = home() / "file-history"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def backup(self, real: Path) -> None:
        if not real.is_file():
            return
        tag = hashlib.sha1(str(real).encode()).hexdigest()[:12]
        shutil.copy2(real, self._hist_dir() / f"{tag}-{int(time.time() * 1000)}-{real.name}")
        old = sorted(self._hist_dir().iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
        for p in old[BACKUPS:]:
            p.unlink(missing_ok=True)

    def history(self, real: Path) -> list[dict]:
        tag = hashlib.sha1(str(real).encode()).hexdigest()[:12]
        rows = []
        for p in sorted(self._hist_dir().glob(f"{tag}-*"), reverse=True):
            ms = int(p.name.split("-", 2)[1])
            rows.append({"id": p.name, "at": ms / 1000, "size": p.stat().st_size})
        return rows

    @staticmethod
    def trash(real: Path) -> Path:
        base = Path.home() / ".Trash" if sys.platform == "darwin" and (Path.home() / ".Trash").is_dir() else home() / "trash"
        base.mkdir(parents=True, exist_ok=True)
        dest = base / real.name
        if dest.exists():
            dest = base / f"{real.stem} {time.strftime('%H.%M.%S')}{real.suffix}"
        shutil.move(str(real), str(dest))
        return dest

    # ---- tools -----------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool("list_files", "List files and folders. Junk folders (node_modules, .git…) are hidden.",
                 {"properties": {"path": {"type": "string", "description": "Folder; default is the shared folder"},
                                 "depth": {"type": "integer", "minimum": 1, "maximum": 3}}}, self.list_files),
            Tool("read_file", "Read a text file or PDF with line numbers. Long files come in pieces: use start_line to continue.",
                 {"properties": {"path": PATH, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 1}},
                  "required": ["path"]}, self.read_file),
            Tool("search_files", "Find files by name and lines by text across the shared folders.",
                 {"properties": {"query": {"type": "string"}, "path": {"type": "string", "description": "Limit to this folder"}},
                  "required": ["query"]}, self.search_files),
            Tool("create_file", "Create a NEW file at a path in the shared folders (code, text, Markdown…). Not for personal notes. Fails if it exists.",
                 {"properties": {"path": PATH, "content": {"type": "string"}}, "required": ["path", "content"]}, self.create_file, WRITE,
                 describe=lambda a: f"Create file {a.get('path')} ({len(str(a.get('content', '')).splitlines())} lines)", preview=self.preview_create),
            Tool("edit_file", "Replace a range of lines (numbers from read_file). start_line..end_line inclusive are replaced by new_text; "
                 "end_line = start_line - 1 inserts before start_line. Read the file first.",
                 {"properties": {"path": PATH, "start_line": {"type": "integer", "minimum": 1}, "end_line": {"type": "integer", "minimum": 0},
                                 "new_text": {"type": "string"}}, "required": ["path", "start_line", "end_line", "new_text"]},
                 self.edit_file, WRITE, describe=lambda a: f"Edit {a.get('path')} lines {a.get('start_line')}–{a.get('end_line')}", preview=self.preview_edit),
            Tool("replace_text", "Replace exact text in a file. The old text must appear exactly once (or set replace_all).",
                 {"properties": {"path": PATH, "old": {"type": "string"}, "new": {"type": "string"}, "replace_all": {"type": "boolean"}},
                  "required": ["path", "old", "new"]}, self.replace_text, WRITE,
                 describe=lambda a: f"Replace text in {a.get('path')}", preview=self.preview_replace),
            Tool("delete_file", "Delete a file (moved to the Trash, so it can be recovered). Files only.",
                 {"properties": {"path": PATH}, "required": ["path"]}, self.delete_file, DESTRUCTIVE,
                 describe=lambda a: f"Move {a.get('path')} to the Trash", preview=self.preview_delete),
        ]

    async def list_files(self, a):
        sb = self.sandbox()
        real = self._p(a.get("path") or ".", forgive=True)
        if not real.is_dir():
            raise ToolError(f"{a.get('path')} is a file, not a folder.")
        depth, out = int(a.get("depth") or 1), []

        def walk(d: Path, level: int):
            try:
                entries = sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for e in entries:
                if len(out) >= 120:
                    return
                if sb.skipped(e.name, e.is_dir()) or e.name.startswith(".") and e.name not in (".env.example",):
                    continue
                try:
                    from .paths import is_denied
                    rp = Path(os.path.realpath(e))
                    if sb.root_of(rp) is None or (not sb.allow_sensitive and is_denied(rp)):
                        continue      # a symlink that leaves the shared folders isn't listed
                except OSError:
                    continue
                out.append(("  " * level) + (e.name + "/" if e.is_dir() else f"{e.name}  {C.human_size(e.stat().st_size)}"))
                if e.is_dir() and level + 1 < depth:
                    walk(e, level + 1)

        await asyncio.get_running_loop().run_in_executor(None, walk, real, 0)
        return {"folder": sb.display(real) or ".", "entries": out, "note": "Truncated at 120 entries." if len(out) >= 120 else None}

    async def read_file(self, a):
        real = self._p(a["path"], forgive=True)
        if real.is_dir():
            raise ToolError(f"{a['path']} is a folder. Use list_files.")
        kind = C.kind_of(real)
        name = self.sandbox().display(real)
        if kind == "pdf":
            r = await asyncio.get_running_loop().run_in_executor(None, C.pdf_text, real)
            if "error" in r:
                raise ToolError(r["error"])
            return {"path": name, **r}
        if kind == "image":
            return {"path": name, "note": f"An image ({C.human_size(real.stat().st_size)}). I can't see images, only name and size."}
        if kind == "binary":
            raise ToolError(f"{name} is a binary file ({C.human_size(real.stat().st_size)}); it can't be read as text.")
        text, cut = C.read_text(real)
        w = C.window(text, a.get("start_line"), a.get("end_line"))
        if "error" in w:
            raise ToolError(w["error"])
        if cut:
            w["note"] = "The file is larger than 1 MB; only the first megabyte is available."
        return {"path": name, **w}

    async def search_files(self, a):
        sb = self.sandbox()
        base = self._p(a.get("path") or ".", forgive=True)
        q = a["query"].strip().lower()
        if not q:
            raise ToolError("Give something to search for.")

        def scan():
            names, hits, scanned = [], [], 0
            from .paths import is_denied
            for dirpath, dirs, files in os.walk(base):
                dirs[:] = [d for d in dirs if not sb.skipped(d, True) and not d.startswith(".")]
                for f in files:
                    if sb.skipped(f, False) or f.startswith("."):
                        continue
                    p = Path(dirpath) / f
                    rp = Path(os.path.realpath(p))
                    if sb.root_of(rp) is None or (not sb.allow_sensitive and is_denied(rp)):
                        continue      # never follow a symlink out of the sandbox
                    scanned += 1
                    if scanned > 3000:
                        return names, hits, True
                    if q in f.lower() and len(names) < 15:
                        names.append(sb.display(Path(os.path.realpath(p))))
                    try:
                        if p.stat().st_size > 300_000 or len(hits) >= 25 or C.kind_of(p) not in ("text", "markdown"):
                            continue
                        with open(p, "r", encoding="utf-8", errors="replace") as fh:
                            for i, line in enumerate(fh, 1):
                                if q in line.lower():
                                    hits.append(f"{sb.display(Path(os.path.realpath(p)))}:{i}: {line.strip()[:120]}")
                                    if len(hits) >= 25:
                                        break
                    except OSError:
                        continue
            return names, hits, False

        names, hits, partial = await asyncio.get_running_loop().run_in_executor(None, scan)
        if not names and not hits:
            return "No matches."
        return {"files_named": names or None, "matching_lines": hits or None, "note": "Stopped after scanning 3,000 files." if partial else None}

    # ---- changing files ------------------------------------------------------
    async def create_file(self, a):
        real = self._p(a["path"], must_exist=False)
        if real.exists():
            raise ToolError(f"{a['path']} already exists. Use edit_file or replace_text to change it.")
        if len(a["content"]) > C.MAX_CREATE_CHARS:
            raise ToolError(f"That's too long to create in one go (limit {C.MAX_CREATE_CHARS:,} characters).")
        real.parent.mkdir(parents=True, exist_ok=True)
        C.atomic_write(real, a["content"])
        self._changed(real)
        return {"created": self.sandbox().display(real), "lines": len(a["content"].splitlines())}

    def _editable(self, path: str) -> tuple[Path, str]:
        real = self._p(path)
        if not real.is_file():
            raise ToolError(f"{path} isn't a file.")
        if real.stat().st_size > C.MAX_EDIT_BYTES:
            raise ToolError("That file is over 1 MB; edit it with your own editor.")
        if C.kind_of(real) not in ("text", "markdown"):
            raise ToolError(f"{path} isn't a text file.")
        return real, C.read_text(real)[0]

    async def edit_file(self, a):
        real, text = self._editable(a["path"])
        try:
            new = C.apply_line_edit(text, int(a["start_line"]), int(a["end_line"]), a["new_text"])
        except ValueError as e:
            raise ToolError(str(e))
        self.backup(real)
        C.atomic_write(real, new)
        self._changed(real)
        s = int(a["start_line"])
        region = C.window(new, max(1, s - 1), s + max(0, len(a["new_text"].splitlines())), 1500)
        warn = None if C.syntax_warning(real, text) else C.syntax_warning(real, new)
        return {"edited": self.sandbox().display(real), "lines_before": len(text.splitlines()), "lines_after": len(new.splitlines()),
                "now": region.get("content"), "warning": warn}

    def _replace(self, text: str, a: dict) -> tuple[str, int]:
        old = a["old"]
        if not old:
            raise ToolError("'old' can't be empty.")
        n = text.count(old)
        if n == 0:
            raise ToolError("That text wasn't found. Read the file again and copy it exactly, or use edit_file with line numbers.")
        if n > 1 and not a.get("replace_all"):
            where = [str(text[:m.start()].count("\n") + 1) for m in re.finditer(re.escape(old), text)][:6]
            raise ToolError(f"That text appears {n} times (lines {', '.join(where)}). Include more context so it is unique, or set replace_all.")
        return text.replace(old, a["new"]) if a.get("replace_all") else text.replace(old, a["new"], 1), n if a.get("replace_all") else 1

    async def replace_text(self, a):
        real, text = self._editable(a["path"])
        new, n = self._replace(text, a)
        self.backup(real)
        C.atomic_write(real, new)
        self._changed(real)
        return {"replaced": n, "file": self.sandbox().display(real)}

    async def delete_file(self, a):
        real = self._p(a["path"])
        if real.is_dir():
            raise ToolError("Only files can be deleted, not folders.")
        sb = self.sandbox()
        if real in sb.roots:
            raise ToolError("That's a shared folder itself.")
        name = sb.display(real)
        dest = self.trash(real)
        self._changed(real)
        return {"deleted": name, "recoverable_in": f"the Trash ({dest.name})"}

    # ---- approval previews (a diff, computed before anything changes) ------------
    def preview_create(self, a):
        real = self._p(a["path"], must_exist=False)
        if real.exists():
            return "(this file already exists, so the call will fail)"
        body = str(a.get("content", ""))
        shown = body.splitlines()[:30]
        return "\n".join(f"+{ln}" for ln in shown) + (f"\n… {len(body.splitlines()) - 30} more lines" if len(body.splitlines()) > 30 else "")

    @staticmethod
    def _with_warning(real: Path, old: str, new: str) -> str:
        warn = None if C.syntax_warning(real, old) else C.syntax_warning(real, new)   # only flag changes that break a valid file
        return (f"⚠ {warn}\n" if warn else "") + C.unified(old, new, real.name)

    def preview_edit(self, a):
        real, text = self._editable(a["path"])
        return self._with_warning(real, text, C.apply_line_edit(text, int(a["start_line"]), int(a["end_line"]), a["new_text"]))

    def preview_replace(self, a):
        real, text = self._editable(a["path"])
        return self._with_warning(real, text, self._replace(text, a)[0])

    def preview_delete(self, a):
        real = self._p(a["path"])
        kind = C.kind_of(real)
        extra = f", {len(C.read_text(real)[0].splitlines())} lines" if kind in ("text", "markdown") and real.stat().st_size < C.MAX_VIEW_BYTES else ""
        return f"-{self.sandbox().display(real)}  ({C.human_size(real.stat().st_size)}{extra})\nMoved to the Trash; you can put it back from there."

    # ---- @mentions in chat -------------------------------------------------------
    def expand_mentions(self, text: str) -> tuple[str, list[dict]]:
        """Resolve @path / @"path with spaces" / @path:10-25 in a message into a compact, line-numbered attachment."""
        refs, blocks, notes, budget = [], [], [], 6000
        if not self.sandbox().roots:
            return "", []
        for m in list(MENTION.finditer(text))[:4]:
            raw = (m.group(1) or m.group(2)).rstrip(".,;:!?)")
            start = end = None
            span = re.match(r"^(.*?):(\d+)(?:-(\d+))?$", raw)
            if span and not m.group(1):
                raw, start, end = span.group(1), int(span.group(2)), int(span.group(3) or span.group(2))
            try:
                real = self.sandbox().resolve(raw, must_exist=True)
            except PathError as e:
                notes.append(f"Couldn't attach @{raw}: {e}")
                continue
            name = self.sandbox().display(real)
            if real.is_dir():
                ls = sorted(p.name + ("/" if p.is_dir() else "") for p in real.iterdir() if not p.name.startswith(".") and not Sandbox.skipped(p.name, p.is_dir()))[:30]
                blocks.append(f"### {name}/ (folder)\n" + "\n".join(ls))
                refs.append({"path": name, "kind": "folder"})
                continue
            kind = C.kind_of(real)
            if kind == "pdf":
                r = C.pdf_text(real, max_pages=2, max_chars=min(3000, budget), budget_s=2.0)
                body = r.get("content") or r.get("error") or r.get("note", "")
                head = f"### {name} (PDF, pages {r.get('pages_read', 0)} of {r.get('pages_total', '?')})"
            elif kind in ("text", "markdown"):
                txt, _ = C.read_text(real, 400_000)
                w = C.window(txt, start, end, min(3000, max(500, budget)))
                if "error" in w:
                    notes.append(f"Couldn't attach @{raw}: {w['error']}")
                    continue
                body = w["content"]
                head = f"### {name} (lines {w['from']}–{w['to']} of {w['lines_total']})"
                if "truncated" in w:
                    head += f". Truncated; use read_file(start_line={w['to'] + 1}) for more."
            else:
                blocks.append(f"### {name}\n({kind} file, {C.human_size(real.stat().st_size)}; can't be read as text)")
                refs.append({"path": name, "kind": kind})
                continue
            budget -= len(body)
            blocks.append(f"{head}\n{body}")
            refs.append({"path": name, "kind": kind, "chars": len(body)})
            if budget <= 0:
                notes.append("(Further referenced files were left out to keep the prompt short; use read_file.)")
                break
        if not blocks and not notes:
            return "", []
        return "\n\n[Files the user referenced. Line numbers are shown for edit_file.]\n" + "\n\n".join(blocks + notes), refs

    # ---- viewer / panel routes ------------------------------------------------------
    def _entry(self, p: Path, sb: Sandbox) -> dict:
        st = p.stat()
        is_dir = p.is_dir()
        return {"name": p.name, "path": str(p), "dir": is_dir, "size": None if is_dir else st.st_size, "mtime": st.st_mtime,
                "kind": "folder" if is_dir else C.kind_of(p) if st.st_size < 50_000_000 else "binary"}

    async def r_state(self, request: Request):
        sb = self.sandbox()
        suggest = [str(Path.home() / d) for d in ("Documents", "Desktop", "Developer", "Projects", "Downloads") if (Path.home() / d).is_dir()]
        return JSONResponse({"roots": [{"path": str(r), "name": r.name} for r in sb.roots], "configured": list(self.cfg.get("roots") or []),
                             "allow_sensitive": sb.allow_sensitive, "home": str(Path.home()), "suggestions": [s for s in suggest if s not in map(str, sb.roots)]})

    async def r_add_root(self, request: Request):
        b = await request.json()
        try:
            real = validate_root(str(b.get("path", "")))
        except PathError as e:
            return JSONResponse({"error": str(e)}, 400)
        roots = list(self.cfg.get("roots") or [])
        if str(real) not in roots:
            roots.append(str(real))
        self.cfg["roots"] = roots
        self.ctx.config.save()
        return await self.r_state(request)

    async def r_remove_root(self, request: Request):
        path = request.query_params.get("path", "")
        self.cfg["roots"] = [r for r in (self.cfg.get("roots") or []) if r != path]
        self.ctx.config.save()
        return await self.r_state(request)

    async def r_set_sensitive(self, request: Request):
        self.cfg["allow_sensitive"] = bool((await request.json()).get("value"))
        self.ctx.config.save()
        return await self.r_state(request)

    async def r_ls(self, request: Request):
        sb = self.sandbox()
        path = request.query_params.get("path", "")
        hidden = request.query_params.get("hidden") == "1"
        if not path:
            return JSONResponse({"path": "", "crumbs": [], "entries": [{**self._entry(r, sb), "name": r.name} for r in sb.roots]})
        try:
            real = sb.resolve(path, must_exist=True)
        except PathError as e:
            return JSONResponse({"error": str(e)}, 403)
        if not real.is_dir():
            return JSONResponse({"error": "Not a folder."}, 400)
        from .paths import is_denied
        entries = []
        for p in real.iterdir():
            if not hidden and (p.name.startswith(".") or Sandbox.skipped(p.name, p.is_dir())):
                continue
            try:
                if not sb.allow_sensitive and is_denied(Path(os.path.realpath(p))):
                    continue
                if sb.root_of(Path(os.path.realpath(p))) is None:   # a symlink pointing out of the sandbox is not listed
                    continue
                entries.append(self._entry(p, sb))
            except OSError:
                continue
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        root = sb.root_of(real)
        crumbs = [{"name": root.name, "path": str(root)}]
        for part in real.relative_to(root).parts:
            crumbs.append({"name": part, "path": str(Path(crumbs[-1]["path"]) / part)})
        return JSONResponse({"path": str(real), "crumbs": crumbs, "entries": entries[:1000], "truncated": len(entries) > 1000})

    async def r_file(self, request: Request):
        try:
            real = self.sandbox().resolve(request.query_params.get("path", ""), must_exist=True)
        except PathError as e:
            return JSONResponse({"error": str(e)}, 403)
        if not real.is_file():
            return JSONResponse({"error": "Not a file."}, 400)
        st, kind = real.stat(), C.kind_of(real)
        out = {"path": str(real), "name": real.name, "display": self.sandbox().display(real), "kind": kind, "lang": C.lang_of(real),
               "size": st.st_size, "mtime": st.st_mtime, "dir": str(real.parent)}
        if kind in ("text", "markdown"):
            text, cut = C.read_text(real)
            out.update(content=text, truncated=cut, lines=text.count("\n") + (0 if text.endswith("\n") or not text else 1))
        return JSONResponse(out)

    async def r_raw(self, request: Request):
        try:
            real = self.sandbox().resolve(request.query_params.get("path", ""), must_exist=True)
        except PathError as e:
            return JSONResponse({"error": str(e)}, 403)
        if not real.is_file():
            return JSONResponse({"error": "Not a file."}, 400)
        ctype = RAW_TYPES.get(real.suffix.lower())
        # Same-origin serving of arbitrary files would let a stray .html run script against this API, so only
        # PDFs and images are served inline; images get a CSP that disables scripts (SVG); all else downloads.
        headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "no-cache"}
        if ctype and ctype.startswith("image/"):
            headers["Content-Security-Policy"] = "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        if ctype:
            return FileResponse(real, media_type=ctype, headers={**headers, "Content-Disposition": "inline"})
        return FileResponse(real, media_type="application/octet-stream", headers={**headers, "Content-Disposition": f'attachment; filename="{real.name}"'})

    async def r_pdf_text(self, request: Request):
        """Extracted text of a PDF (the same text the agent gets); a fallback for browsers without an inline viewer."""
        try:
            real = self.sandbox().resolve(request.query_params.get("path", ""), must_exist=True)
        except PathError as e:
            return JSONResponse({"error": str(e)}, 403)
        if real.suffix.lower() != ".pdf":
            return JSONResponse({"error": "Not a PDF."}, 400)
        pages = max(1, min(int(request.query_params.get("pages", 5)), 60))
        r = await asyncio.get_running_loop().run_in_executor(None, lambda: C.pdf_text(real, max_pages=pages, max_chars=80000, budget_s=6.0))
        return JSONResponse(r, 400 if "error" in r else 200)

    async def r_find(self, request: Request):
        q = request.query_params.get("q", "").strip().lower()
        limit = min(int(request.query_params.get("limit", 12)), 30)
        sb = self.sandbox()
        if not sb.roots:
            return JSONResponse([])

        def search():
            from .paths import is_denied
            scored, seen = [], 0
            for root in sb.roots:
                for dirpath, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if not Sandbox.skipped(d, True) and not d.startswith(".")]
                    for f in files + [d + "/" for d in dirs]:
                        if f.startswith(".") or Sandbox.skipped(f.rstrip("/"), f.endswith("/")):
                            continue
                        seen += 1
                        if seen > 40000:
                            return scored
                        p = Path(dirpath) / f.rstrip("/")
                        rel = p.relative_to(root).as_posix() + ("/" if f.endswith("/") else "")
                        score = _fuzzy(q, rel.lower())
                        rp = Path(os.path.realpath(p))
                        if score is not None and sb.root_of(rp) is not None and (sb.allow_sensitive or not is_denied(rp)):
                            scored.append((score, str(p), rel, f.endswith("/"), root.name))
            return scored

        res = await asyncio.get_running_loop().run_in_executor(None, search)
        res.sort(key=lambda x: (x[0], len(x[2])))
        multi = len(sb.roots) > 1
        return JSONResponse([{"path": p, "rel": (f"{rn}/{rel}" if multi else rel), "dir": d} for _, p, rel, d, rn in res[:limit]])

    async def r_history(self, request: Request):
        try:
            real = self.sandbox().resolve(request.query_params.get("path", ""))
        except PathError as e:
            return JSONResponse({"error": str(e)}, 403)
        return JSONResponse(self.history(real))

    async def r_restore(self, request: Request):
        b = await request.json()
        try:
            real = self.sandbox().resolve(str(b.get("path", "")))
        except PathError as e:
            return JSONResponse({"error": str(e)}, 403)
        tag = hashlib.sha1(str(real).encode()).hexdigest()[:12]
        bid = str(b.get("id", ""))
        src = self._hist_dir() / bid
        if "/" in bid or not bid.startswith(tag + "-") or not src.is_file():
            return JSONResponse({"error": "No such backup."}, 404)
        self.backup(real)                        # restoring is itself undoable
        shutil.copy2(src, real)
        self._changed(real)
        return JSONResponse({"ok": True})

    async def r_dirs(self, request: Request):
        """Folder picker for adding a shared folder: directory names under the home folder only."""
        home_dir = Path.home().resolve()
        p = Path(os.path.realpath(os.path.expanduser(request.query_params.get("path") or "~")))
        if p != home_dir and home_dir not in p.parents:
            p = home_dir
        dirs = []
        try:
            for e in sorted(p.iterdir(), key=lambda x: x.name.lower()):
                if e.is_dir() and not e.name.startswith(".") and e.name not in ("Library",):
                    dirs.append({"name": e.name, "path": str(e)})
        except OSError:
            pass
        return JSONResponse({"path": str(p), "parent": str(p.parent) if p != home_dir else None, "dirs": dirs[:300]})

    async def r_open(self, request: Request):
        """Open a shared file or folder in Finder or VS Code (the panel's "Open in" menu). Only paths inside the shared
        folders, only from Diatonic's own page, and no shell: the path is passed as one argument."""
        if request.headers.get("x-diatonic") != "1":
            return JSONResponse({"error": "forbidden"}, 403)
        b = await request.json()
        try:
            real = self.sandbox().resolve(str(b.get("path") or ""), must_exist=True)
        except PathError as e:
            return JSONResponse({"error": str(e)}, 400)
        where = b.get("app")
        if where == "finder":
            argv = ["open", str(real)] if real.is_dir() else ["open", "-R", str(real)]   # a file: show it selected in its folder
        elif where == "code":
            code = shutil.which("code", path=os.environ.get("PATH", "") + ":/usr/local/bin:/opt/homebrew/bin")
            argv = [code, str(real)] if code else ["open", "-a", "Visual Studio Code", str(real)]
        else:
            return JSONResponse({"error": "app must be finder or code"}, 400)
        try:
            p = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                                                     stderr=asyncio.subprocess.PIPE, start_new_session=True)
            _, err = await asyncio.wait_for(p.communicate(), 15)
        except (OSError, asyncio.TimeoutError) as e:
            return JSONResponse({"error": f"Couldn't open it: {type(e).__name__}"}, 500)
        if p.returncode:
            msg = err.decode(errors="replace").strip()[:200] or f"exit {p.returncode}"
            return JSONResponse({"error": "VS Code isn't installed (or its `code` command isn't)." if where == "code" else msg}, 500)
        return JSONResponse({"ok": True})

    def routes(self):
        return [Route("/open", self.r_open, methods=["POST"]), Route("/state", self.r_state), Route("/roots", self.r_add_root, methods=["POST"]),
                Route("/roots", self.r_remove_root, methods=["DELETE"]), Route("/sensitive", self.r_set_sensitive, methods=["POST"]),
                Route("/ls", self.r_ls), Route("/file", self.r_file), Route("/raw", self.r_raw), Route("/find", self.r_find), Route("/pdf-text", self.r_pdf_text),
                Route("/history", self.r_history), Route("/restore", self.r_restore, methods=["POST"]), Route("/dirs", self.r_dirs)]


def _fuzzy(q: str, text: str) -> int | None:
    """Subsequence match; lower is better. Filename hits beat directory hits."""
    if not q:
        return 0
    base = text.rsplit("/", 2)
    name = base[-1] if base[-1] else (base[-2] + "/")
    if q in name.lower():
        return 0 if name.lower().startswith(q) else 1
    if q in text:
        return 2
    i = 0
    for ch in text:
        if i < len(q) and ch == q[i]:
            i += 1
    return 3 if i == len(q) else None
