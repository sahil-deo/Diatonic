"""User-defined and agent-defined macros: declarative JSON command sequences.

Stored in macros.json in the Diatonic home folder:
[
  {
    "id": "macro_dev",
    "name": "Dev",
    "description": "Side-by-side chat and files",
    "steps": [
      {"command": "layout.preset.side-by-side"},
      {"command": "app.open", "args": {"id": "chat"}},
      {"wait": 100},
      {"command": "app.open", "args": {"id": "files"}}
    ]
  }
]
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from .config import home

MAX_MACROS = 50
MAX_STEPS = 50
MAX_TEXT = 120
MAX_DESC = 300
ID_RE = re.compile(r"^[a-zA-Z0-9_\-:]+$")


class MacroError(ValueError):
    pass


def path() -> Path:
    return home() / "macros.json"


def clean_macro(raw) -> dict:
    if not isinstance(raw, dict):
        raise MacroError("A macro must be an object.")
    mid = raw.get("id")
    if not isinstance(mid, str) or not mid.strip() or len(mid) > MAX_TEXT or not ID_RE.match(mid.strip()):
        raise MacroError("Macro id must be letters, numbers, hyphens or underscores (at most 120 chars).")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_TEXT:
        raise MacroError("Macro name must be text of at most 120 characters.")
    desc = raw.get("description") or ""
    if not isinstance(desc, str) or len(desc) > MAX_DESC:
        raise MacroError(f"Macro description must be text of at most {MAX_DESC} characters.")

    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list):
        raise MacroError("Macro steps must be a list.")
    if len(raw_steps) > MAX_STEPS:
        raise MacroError(f"A macro can have at most {MAX_STEPS} steps.")

    clean_steps = []
    for i, s in enumerate(raw_steps, 1):
        if not isinstance(s, dict):
            raise MacroError(f"Step {i} must be an object.")
        cmd = s.get("command")
        wait = s.get("wait")
        cond = s.get("if")
        args = s.get("args")

        if cmd is None and wait is None:
            raise MacroError(f"Step {i} needs either a command or a wait.")
        if cmd is not None and (not isinstance(cmd, str) or not cmd.strip() or len(cmd) > MAX_TEXT):
            raise MacroError(f"Step {i} command must be text of at most {MAX_TEXT} characters.")
        if wait is not None:
            if not isinstance(wait, (int, float)) or wait < 0 or wait > 60000:
                raise MacroError(f"Step {i} wait must be between 0 and 60000 milliseconds.")
        if cond is not None:
            if not isinstance(cond, str) or len(cond) > MAX_TEXT:
                raise MacroError(f"Step {i} condition must be text of at most {MAX_TEXT} characters.")
        if args is not None:
            if not isinstance(args, dict):
                raise MacroError(f"Step {i} args must be an object.")
            if len(json.dumps(args)) > 2000:
                raise MacroError(f"Step {i} args is too large.")

        step = {}
        if cmd is not None:
            step["command"] = cmd.strip()
        if args is not None:
            step["args"] = args
        if wait is not None:
            step["wait"] = int(wait)
        if cond is not None:
            step["if"] = cond.strip()
        clean_steps.append(step)

    return {
        "id": mid.strip(),
        "name": name.strip(),
        "description": desc.strip(),
        "steps": clean_steps,
    }


def clean_macros(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise MacroError("Macros must be a list of macro objects.")
    if len(raw) > MAX_MACROS:
        raise MacroError(f"At most {MAX_MACROS} macros allowed.")
    seen_ids = set()
    out = []
    for item in raw:
        cleaned = clean_macro(item)
        if cleaned["id"] in seen_ids:
            raise MacroError(f"Duplicate macro id '{cleaned['id']}'.")
        seen_ids.add(cleaned["id"])
        out.append(cleaned)
    return out


def load() -> list[dict]:
    p = path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return clean_macros(data)
    except Exception:
        return []


def save(data) -> list[dict]:
    cleaned = clean_macros(data)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False, encoding="utf-8") as tf:
        json.dump(cleaned, tf, indent=2)
        tf.write("\n")
        tmp_name = tf.name
    os.replace(tmp_name, p)
    return cleaned
