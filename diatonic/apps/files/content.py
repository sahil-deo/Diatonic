"""File kinds, safe text reading, numbered views, edits and diffs."""
from __future__ import annotations

import difflib
import io
import os
import time
from pathlib import Path

MAX_VIEW_BYTES = 1_000_000     # what the viewer will load as text
MAX_EDIT_BYTES = 1_000_000     # larger files can be read in pieces but not edited
MAX_CREATE_CHARS = 200_000
READ_CHARS = 6000              # per read_file call (about 1.5k tokens; prompt processing is the slow part)

MARKDOWN = {".md", ".markdown", ".mdx"}
IMAGES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".webp": "image/webp",
          ".svg": "image/svg+xml", ".bmp": "image/bmp", ".ico": "image/x-icon"}
LANG = {".py": "python", ".js": "javascript", ".mjs": "javascript", ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript",
        ".tsx": "typescript", ".json": "json", ".jsonc": "json", ".html": "html", ".htm": "html", ".xml": "xml", ".svg": "xml",
        ".css": "css", ".scss": "css", ".sh": "shell", ".bash": "shell", ".zsh": "shell", ".yml": "yaml", ".yaml": "yaml",
        ".toml": "toml", ".ini": "toml", ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".swift": "swift",
        ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby", ".php": "php",
        ".sql": "sql", ".lua": "lua", ".r": "r", ".diff": "diff", ".patch": "diff", ".md": "markdown", ".markdown": "markdown",
        ".txt": "text", ".csv": "text", ".log": "text"}
NAME_LANG = {"Dockerfile": "shell", "Makefile": "shell", ".gitignore": "text", ".zshrc": "shell", ".bashrc": "shell"}


def lang_of(path: Path) -> str:
    return NAME_LANG.get(path.name) or LANG.get(path.suffix.lower(), "text")


def kind_of(path: Path) -> str:
    """markdown | pdf | image | text | binary (text is decided by sniffing, not only the extension)."""
    ext = path.suffix.lower()
    if ext in MARKDOWN:
        return "markdown"
    if ext == ".pdf":
        return "pdf"
    if ext in IMAGES:
        return "image"
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return "binary"
    if b"\0" in head:
        return "binary"
    try:
        head.decode("utf-8")
    except UnicodeDecodeError:
        # a multibyte character may be cut at the 8 KB boundary; tolerate up to 3 trailing bytes
        try:
            head[:-3].decode("utf-8")
        except UnicodeDecodeError:
            return "binary"
    return "text"


def read_text(path: Path, limit: int = MAX_VIEW_BYTES) -> tuple[str, bool]:
    """(text, truncated). newline='' keeps CRLF intact so edits don't rewrite line endings."""
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        text = f.read(limit + 1)
    return (text[:limit], True) if len(text) > limit else (text, False)


def number_lines(lines: list[str], start: int) -> str:
    return "\n".join(f"{start + i:>4}| {ln.rstrip(chr(10)).rstrip(chr(13))}" for i, ln in enumerate(lines))


def window(text: str, start: int | None, end: int | None, max_chars: int = READ_CHARS) -> dict:
    """A numbered slice of `text` no larger than max_chars, with paging hints."""
    lines = text.splitlines(keepends=True)
    total = len(lines)
    s = max(1, start or 1)
    e = min(total, end) if end else total
    if s > total and total:
        return {"error": f"The file has only {total} lines."}
    out, used, last = [], 0, s - 1
    for i in range(s - 1, e):
        piece = len(lines[i]) + 7
        if out and used + piece > max_chars:
            break
        out.append(lines[i])
        used += piece
        last = i + 1
    r = {"lines_total": total, "from": s, "to": last, "content": number_lines(out, s)}
    if last < e:
        r["truncated"] = f"Stopped at line {last} to keep this short. Call again with start_line={last + 1} for more."
    return r


def pdf_text(path: Path, max_pages: int = 3, max_chars: int = 5000, budget_s: float = 4.0) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError:
        return {"error": "PDF text extraction needs the pypdf package."}
    t0, out, pages = time.monotonic(), [], 0
    try:
        reader = PdfReader(str(path))
        n = len(reader.pages)
        for i in range(min(n, max_pages)):
            if time.monotonic() - t0 > budget_s:
                break
            out.append(f"--- page {i + 1} ---\n{(reader.pages[i].extract_text() or '').strip()}")
            pages += 1
            if sum(len(x) for x in out) > max_chars:
                break
    except Exception as e:
        return {"error": f"Couldn't read this PDF ({type(e).__name__})."}
    text = "\n".join(out)
    r = {"pages_total": n, "pages_read": pages, "content": text[:max_chars]}
    if len(text) > max_chars or pages < n:
        r["truncated"] = f"Showing the first {pages} of {n} pages."
    if not text.strip():
        r["note"] = "No extractable text (it may be a scan)."
    return r


# ---------------- edits ----------------
def eol_of(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def apply_line_edit(text: str, start: int, end: int, new_text: str) -> str:
    """Replace lines start..end (1-based, inclusive) with new_text. end == start-1 inserts before `start`."""
    lines = text.splitlines(keepends=True)
    n = len(lines)
    if not (1 <= start <= n + 1):
        raise ValueError(f"start_line must be between 1 and {n + 1} (the file has {n} lines).")
    if not (start - 1 <= end <= n):
        raise ValueError(f"end_line must be between {start - 1} and {n}. Use end_line = start_line - 1 to insert before a line.")
    eol = eol_of(text)
    repl = new_text.replace("\r\n", "\n").replace("\n", eol) if new_text else ""
    if repl and not repl.endswith(eol) and (end < n or text.endswith(("\n", "\r\n"))):
        repl += eol      # keep following lines on their own line
    return "".join(lines[: start - 1]) + repl + "".join(lines[end:])


def unified(old: str, new: str, name: str, context: int = 2, max_lines: int = 80) -> str:
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), f"a/{name}", f"b/{name}", n=context, lineterm=""))
    if len(diff) > max_lines:
        diff = diff[:max_lines] + [f"… {len(diff) - max_lines} more diff lines"]
    return "\n".join(diff)


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def syntax_warning(path: Path, new_text: str) -> str | None:
    """A one-line warning if the change leaves a Python or JSON file unparseable (None if fine or not checked)."""
    ext = path.suffix.lower()
    try:
        if ext == ".py":
            import ast
            ast.parse(new_text)
        elif ext == ".json":
            import json
            json.loads(new_text)
    except SyntaxError as e:
        return f"Python syntax error after this change (line {e.lineno}: {e.msg})"
    except ValueError as e:
        return f"Invalid JSON after this change ({e})"
    return None
