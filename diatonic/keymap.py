"""The user's keymap: key bindings kept in one JSON file in the Diatonic home folder.

The page merges this file over its built-in defaults, so the file only holds what the user changed:
{"leader": "Meta+;", "vim": false, "bindings": [{"key": "g g", "command": "nav.top", "when": "!input", "args": {...}}]}
A binding whose command is null removes that key. The page re-reads the file when its window gains focus, so editing
it by hand works too. Key syntax is checked by the page (a bad entry is reported and skipped); this module only keeps
the file well-formed and bounded.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .config import home

MAX_BINDINGS = 500
MAX_TEXT = 120
EMPTY = {"leader": "", "vim": False, "bindings": []}


class KeymapError(ValueError):
    pass


def path() -> Path:
    return home() / "keymap.json"


def clean(data) -> dict:
    """The keymap as the file stores it, or a KeymapError saying what is wrong."""
    if not isinstance(data, dict):
        raise KeymapError("The keymap must be an object.")
    leader = data.get("leader") or ""
    if not isinstance(leader, str) or len(leader) > MAX_TEXT:
        raise KeymapError("leader must be a key like Meta+;")
    rows = data.get("bindings") or []
    if not isinstance(rows, list) or len(rows) > MAX_BINDINGS:
        raise KeymapError(f"bindings must be a list of at most {MAX_BINDINGS}.")
    out = []
    for i, b in enumerate(rows, 1):
        if not isinstance(b, dict):
            raise KeymapError(f"Binding {i} must be an object.")
        key, command, when = b.get("key"), b.get("command"), b.get("when")
        if not isinstance(key, str) or not key.strip() or len(key) > MAX_TEXT:
            raise KeymapError(f"Binding {i} needs a key, like \"Meta+k\" or \"g g\".")
        if command is not None and (not isinstance(command, str) or not command or len(command) > MAX_TEXT):
            raise KeymapError(f"Binding {i} needs a command id (or null to remove the key).")
        if when is not None and (not isinstance(when, str) or len(when) > MAX_TEXT):
            raise KeymapError(f"Binding {i}: when must be text like \"!input\".")
        row = {"key": key.strip(), "command": command}
        if when:
            row["when"] = when
        args = b.get("args")
        if args is not None:
            if not isinstance(args, dict) or len(json.dumps(args)) > 500:
                raise KeymapError(f"Binding {i}: args must be a small object.")
            row["args"] = args
        out.append(row)
    return {"leader": leader.strip(), "vim": bool(data.get("vim")), "bindings": out}


def load() -> dict:
    """What's in the file. A missing or unreadable file means no changes (a typo never locks the user out)."""
    try:
        return clean(json.loads(path().read_text()))
    except (OSError, ValueError):
        return dict(EMPTY, bindings=[])


def save(data) -> dict:
    data = clean(data)
    p = path()
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".keymap-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, p)
    except OSError:
        Path(tmp).unlink(missing_ok=True)
        raise
    return data
