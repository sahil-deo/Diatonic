"""Presentations as Markdown slide decks stored as plain files (default ~/Documents/Diatonic Presentations)."""
from __future__ import annotations

import re
import time
from datetime import datetime
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from ..base import WRITE, App, Tool, ToolError

DEFAULT_DIR = "~/Documents/Diatonic Presentations"
_FM = re.compile(r"\A---\n(.*?)\n---\n?", re.S)

IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
}


def slugify(title: str) -> str:
    s = re.sub(r"[^\w\- ]+", "", title, flags=re.U).strip().lower()
    s = re.sub(r"\s+", "-", s)
    return (s or "untitled")[:80]


_KEY_LINE = re.compile(r"^[A-Za-z_][\w-]*\s*:")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split optional front matter (title, theme, author, etc.) from body.

    A block only counts when every line is `key: value`, so a deck that opens with a `---` slide break is left alone. Decks saved by
    older versions of the editor can have the same block several times in a row; they are folded into one (the first value wins).
    """
    meta, body = {}, text
    while (m := _FM.match(body)):
        lines = [l for l in m.group(1).splitlines() if l.strip()]
        if not lines or not all(_KEY_LINE.match(l) for l in lines):
            break
        body = body[m.end():]
        for line in lines:
            k, v = line.split(":", 1)
            meta.setdefault(k.strip(), v.strip())
    return meta, body


def dump_markdown(meta: dict, body: str) -> str:
    head = "\n".join(f"{k}: {v}" for k, v in meta.items() if v)
    return f"---\n{head}\n---\n{body.lstrip(chr(10))}" if head else body


def split_slides(raw_text: str) -> tuple[dict, list[dict]]:
    """Parse frontmatter and split raw markdown into individual slide objects.
    
    Slide boundary is a line starting with --- outside of fenced code blocks.
    Notes on a slide can be given via <!-- note: ... --> or Note: ... at the bottom.
    """
    meta, body = parse_frontmatter(raw_text)
    lines = body.splitlines()
    slides_raw: list[list[str]] = [[]]
    in_code = False
    fence_char = ""

    for line in lines:
        stripped = line.strip()
        # Toggle code block state
        if stripped.startswith("```") or stripped.startswith("~~~"):
            char = stripped[:3]
            if not in_code:
                in_code = True
                fence_char = char
            elif char == fence_char:
                in_code = False
                fence_char = ""
            slides_raw[-1].append(line)
            continue

        if not in_code and stripped in ("---", "***"):
            # New slide
            slides_raw.append([])
        else:
            slides_raw[-1].append(line)

    has_separator = any(line.strip() in ("---", "***") for line in lines)
    if not has_separator and len(slides_raw) == 1:
        # Check if we can split by major headings (# or ##) when no explicit slide breaks exist
        heading_splits: list[list[str]] = []
        in_c = False
        f_char = ""
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```") or stripped.startswith("~~~"):
                c = stripped[:3]
                if not in_c:
                    in_c = True
                    f_char = c
                elif c == f_char:
                    in_c = False
                    f_char = ""
                if not heading_splits:
                    heading_splits.append([])
                heading_splits[-1].append(line)
                continue
            if not in_c and re.match(r"^#{1,2}\s+", stripped):
                heading_splits.append([line])
            else:
                if not heading_splits:
                    heading_splits.append([])
                heading_splits[-1].append(line)
        if len(heading_splits) > 1:
            slides_raw = heading_splits

    slides = []
    for i, slide_lines in enumerate(slides_raw):
        content = "\n".join(slide_lines).strip()
        if not content and len(slides_raw) > 1 and i == len(slides_raw) - 1:
            continue  # ignore trailing empty slide

        # Extract title (first heading)
        slide_title = ""
        for sl in slide_lines:
            hm = re.match(r"^#{1,6}\s+(.*)", sl.strip())
            if hm:
                slide_title = hm.group(1).strip()
                break

        # Extract speaker notes
        notes = ""
        note_match = re.search(r"<!--\s*notes?:\s*(.*?)\s*-->", content, re.I | re.S)
        if note_match:
            notes = note_match.group(1).strip()
            content = content[:note_match.start()] + content[note_match.end():]
        else:
            # Check for trailing Note: or Notes: paragraph
            np_match = re.search(r"\n+(?:Notes?|Speaker Notes?):\s*(.*)\Z", content, re.I | re.S)
            if np_match:
                notes = np_match.group(1).strip()
                content = content[:np_match.start()]

        slides.append({
            "index": len(slides) + 1,
            "title": slide_title or f"Slide {len(slides) + 1}",
            "content": content.strip(),
            "notes": notes,
        })

    if not slides:
        slides = [{"index": 1, "title": "Slide 1", "content": "# Untitled\n\nAdd content here.", "notes": ""}]

    return meta, slides


class PresentationsApp(App):
    private = True
    id = "presentations"
    name = "Presentations"
    icon = "presentation"
    description = "Markdown slide presentations you can edit and present in full-screen slideshow mode."
    panel = True
    keywords = ["presentation", "presentations", "slides", "slide", "slideshow", "deck", "pitch deck", "present"]
    default_allow = ["create_presentation", "update_presentation"]
    settings = [
        {"key": "folder", "label": "Presentations folder", "secret": False, "placeholder": DEFAULT_DIR,
         "help": "Plain .md files with --- slide separators. Point to an existing folder to use your own presentations."}
    ]

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

    def _changed(self) -> None:
        self.ctx.broadcast({"type": "app_changed", "app": self.id})

    def _all(self) -> list[dict]:
        out = []
        for p in self.root.glob("*.md"):
            try:
                raw = p.read_text(errors="replace")
                meta, slides = split_slides(raw)
            except OSError:
                continue
            title = meta.get("title") or (slides[0]["title"] if slides else p.stem)
            out.append({
                "slug": p.stem,
                "title": title,
                "slides_count": len(slides),
                "slides": slides,
                "raw": raw,
                "body": parse_frontmatter(raw)[1],      # what the editor shows: the slides, without the front matter block
                "meta": meta,
                "modified": p.stat().st_mtime,
                "preview": slides[0]["content"][:140] if slides else "",
            })
        return sorted(out, key=lambda d: -d["modified"])

    def _resolve(self, ref: str) -> dict:
        decks = self._all()
        q = ref.strip().lower()
        for test in (
            lambda d: d["slug"].lower() == q,
            lambda d: d["title"].lower() == q,
            lambda d: d["title"].lower().startswith(q),
            lambda d: q in d["title"].lower(),
        ):
            hits = [d for d in decks if test(d)]
            if len(hits) == 1:
                return hits[0]
            if len(hits) > 1:
                matches = ", ".join(f"“{d['title']}”" for d in hits[:6])
                raise ToolError(f"More than one presentation matches: {matches}. Be more specific.")
        raise ToolError(f"No presentation called “{ref}”.")

    def _write(self, slug: str, meta: dict, body: str) -> Path:
        """Save a deck. `body` may still start with a front matter block (a caller that read the whole file): it is folded into `meta`
        here instead of being written a second time, which used to add a title slide at the top on every save."""
        extra, body = parse_frontmatter(body)
        meta = {**extra, **meta}
        p = self._path(slug)
        p.write_text(dump_markdown(meta, body))
        self._changed()
        return p

    async def snapshot(self, n: int) -> dict:
        decks = self._all()
        items = [
            {
                "text": d["title"],
                "sub": f"{d['slides_count']} slide{'s' if d['slides_count'] != 1 else ''} · {datetime.fromtimestamp(d['modified']).strftime('%a %d %b')}",
                "open": {"item": d["title"]},
            }
            for d in decks[:n]
        ]
        return {
            "items": items,
            "stats": [{"label": "decks", "value": len(decks)}],
            "empty": "No presentations yet.",
        }

    # ---- tools ---------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool("list_presentations", "List all presentation slide decks.", {}, self.list_presentations),
            Tool("get_presentation", "Get a presentation's slides, notes, and raw Markdown.",
                 {"properties": {"deck": {"type": "string", "description": "Presentation title or slug"}}, "required": ["deck"]},
                 self.get_presentation),
            Tool("create_presentation", "Create a new Markdown presentation deck with slides separated by '---'.",
                 {"properties": {
                     "title": {"type": "string", "description": "Deck title"},
                     "content": {"type": "string", "description": "Markdown body with slides separated by '---'"},
                 }, "required": ["title", "content"]},
                 self.create_presentation, risk=WRITE),
            Tool("update_presentation", "Update an existing presentation's title or Markdown content.",
                 {"properties": {
                     "deck": {"type": "string", "description": "Presentation title or slug to update"},
                     "title": {"type": "string", "description": "New title"},
                     "content": {"type": "string", "description": "Updated Markdown content"},
                 }, "required": ["deck"]},
                 self.update_presentation, risk=WRITE),
            Tool("delete_presentation", "Move a presentation deck to trash.",
                 {"properties": {"deck": {"type": "string", "description": "Presentation title or slug"}}, "required": ["deck"]},
                 self.delete_presentation, risk=WRITE),
        ]

    async def list_presentations(self, _args: dict):
        decks = self._all()
        return [
            {"title": d["title"], "slug": d["slug"], "slides_count": d["slides_count"],
             "modified": datetime.fromtimestamp(d["modified"]).strftime("%Y-%m-%d %H:%M")}
            for d in decks
        ] or "No presentations found."

    async def get_presentation(self, a: dict):
        deck = self._resolve(a["deck"])
        return {
            "title": deck["title"],
            "slug": deck["slug"],
            "slides_count": deck["slides_count"],
            "slides": deck["slides"],
            "raw": deck["raw"],
        }

    async def create_presentation(self, a: dict):
        title = a.get("title", "").strip() or "Untitled Presentation"
        slug = slugify(title)
        p = self._path(slug)
        if p.exists():
            slug = f"{slug}-{int(time.time())}"
        content = a.get("content", "").strip()
        if not content:
            content = f"# {title}\n\nWelcome to the presentation.\n\n---\n\n## Slide 2\n\n- Key point 1\n- Key point 2"
        self._write(slug, {"title": title}, content)
        return {"created": title, "slug": slug, "file": f"{slug}.md"}

    async def update_presentation(self, a: dict):
        deck = self._resolve(a["deck"])
        title = a.get("title", deck["title"]).strip() or deck["title"]
        content = a.get("content", deck["body"])
        meta = {**deck["meta"], "title": title}
        self._write(deck["slug"], meta, content)
        return {"updated": title, "slug": deck["slug"]}

    async def delete_presentation(self, a: dict):
        deck = self._resolve(a["deck"])
        trash = self.root / ".trash"
        trash.mkdir(exist_ok=True)
        self._path(deck["slug"]).rename(trash / f"{deck['slug']}-{int(time.time())}.md")
        self._changed()
        return {"deleted": deck["title"], "recoverable_in": ".trash"}

    # ---- HTTP routes ---------------------------------------------------
    async def api_list(self, request: Request):
        q = request.query_params.get("q", "").strip().lower()
        decks = self._all()
        if q:
            decks = [d for d in decks if q in d["title"].lower() or q in d["raw"].lower()]
        return JSONResponse({
            "folder": str(self.root),
            "decks": [
                {
                    "slug": d["slug"],
                    "title": d["title"],
                    "slides_count": d["slides_count"],
                    "modified": d["modified"],
                    "preview": d["preview"],
                }
                for d in decks
            ],
        })

    async def api_get(self, request: Request):
        deck = self._resolve(request.path_params["slug"])
        return JSONResponse({
            "slug": deck["slug"],
            "title": deck["title"],
            "raw": deck["body"],      # the editor's text: slides only. The title and other settings live in `meta`
            "slides_count": deck["slides_count"],
            "slides": deck["slides"],
            "meta": deck["meta"],
            "modified": deck["modified"],
        })

    async def api_save(self, request: Request):
        body = await request.json()
        slug = request.path_params.get("slug")
        if slug:
            deck = self._resolve(slug)
            title = body.get("title", deck["title"]).strip() or deck["title"]
            content = body.get("content", deck["body"])
            meta = {**deck["meta"], "title": title}
            self._write(deck["slug"], meta, content)
            return JSONResponse({"slug": deck["slug"], "title": title})
        
        # New deck
        title = body.get("title", "").strip() or "Untitled"
        content = body.get("content", f"# {title}\n\n---\n\n## Next Slide")
        r = await self.create_presentation({"title": title, "content": content})
        return JSONResponse({"slug": r["slug"], "title": title})

    async def api_delete(self, request: Request):
        slug = request.path_params["slug"]
        await self.delete_presentation({"deck": slug})
        return JSONResponse({"ok": True})

    async def api_parse(self, request: Request):
        """Parse arbitrary Markdown text or a file path into slides on the fly."""
        body = await request.json()
        raw = body.get("content", "")
        file_path = body.get("path")
        if file_path:
            p = Path(file_path).expanduser().resolve()
            if not p.is_file():
                return JSONResponse({"error": f"File not found: {file_path}"}, 404)
            raw = p.read_text(errors="replace")
        meta, slides = split_slides(raw)
        title = meta.get("title") or (slides[0]["title"] if slides else (Path(file_path).stem if file_path else "Presentation"))
        return JSONResponse({
            "title": title,
            "slides": slides,
            "slides_count": len(slides),
            "meta": meta,
        })

    async def api_asset(self, request: Request):
        """Serve an image asset referenced by a presentation."""
        raw_path = request.query_params.get("path", "").strip()
        if not raw_path:
            return JSONResponse({"error": "Missing image path"}, 400)

        cand = Path(raw_path).expanduser()
        real: Path | None = None
        if cand.is_absolute() and cand.is_file():
            try:
                cand_res = cand.resolve()
                if cand_res.is_file():
                    real = cand_res
            except (OSError, RuntimeError):
                pass

        if not real:
            for base in (self.root, self.root / "images", self.root / "assets"):
                target = (base / raw_path).resolve()
                if self.root.resolve() in target.parents or target == self.root.resolve():
                    if target.is_file():
                        real = target
                        break

        if not real or not real.is_file():
            return JSONResponse({"error": f"Image not found: {raw_path}"}, 404)

        suffix = real.suffix.lower()
        media_type = IMAGE_TYPES.get(suffix, "application/octet-stream")
        headers = {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "max-age=3600",
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
            "Content-Disposition": "inline",
        }
        return FileResponse(real, media_type=media_type, headers=headers)

    async def api_upload(self, request: Request):
        """Upload an image to the presentations images directory."""
        import base64
        body = await request.json()
        name = body.get("name", "").strip() or f"image-{int(time.time())}.png"
        data_b64 = body.get("data", "")
        if not data_b64:
            return JSONResponse({"error": "Missing image data"}, 400)

        if "," in data_b64:
            data_b64 = data_b64.split(",", 1)[1]

        try:
            raw_bytes = base64.b64decode(data_b64)
        except Exception as e:
            return JSONResponse({"error": f"Invalid base64: {e}"}, 400)

        images_dir = self.root / "images"
        images_dir.mkdir(parents=True, exist_ok=True)

        safe_name = re.sub(r"[^\w\-.]+", "_", Path(name).name)
        stem = Path(safe_name).stem
        ext = Path(safe_name).suffix.lower() or ".png"
        dest = images_dir / f"{stem}{ext}"
        if dest.exists():
            dest = images_dir / f"{stem}-{int(time.time())}{ext}"

        dest.write_bytes(raw_bytes)
        rel_path = f"images/{dest.name}"
        return JSONResponse({
            "name": dest.name,
            "path": rel_path,
            "url": f"/api/apps/presentations/asset?path={rel_path}",
        })

    def routes(self):
        return [
            Route("/decks", self.api_list),
            Route("/decks", self.api_save, methods=["POST"]),
            Route("/decks/{slug}", self.api_get),
            Route("/decks/{slug}", self.api_save, methods=["PUT"]),
            Route("/decks/{slug}", self.api_delete, methods=["DELETE"]),
            Route("/parse", self.api_parse, methods=["POST"]),
            Route("/asset", self.api_asset),
            Route("/upload", self.api_upload, methods=["POST"]),
        ]
