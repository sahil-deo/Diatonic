"""What the model is told, kept in files: the system prompt sections (`prompts/`) and the skills it can read on demand (`skills/`).

Both can be overridden from the user's folder (`~/.diatonic/prompts/NAME.md`, `~/.diatonic/skills/NAME.md`): a file with the same name
replaces the built-in one. Recipes in `recipes/` are served as skills named `recipe-<id>`, so the model reads them the same way.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .catalog import Entry
from .config import home

HERE = Path(__file__).parent
SECTIONS = ("identity", "scope", "tools", "discovery", "building")   # the fixed order of the system prompt
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")


def _user(kind: str, name: str) -> Path:
    return home() / kind / f"{name}.md"


def sections() -> list[dict]:
    """The system prompt sections in order. An empty user file switches a section off."""
    out = []
    for name in SECTIONS:
        mine = _user("prompts", name)
        text = (mine if mine.is_file() else HERE / "prompts" / f"{name}.md").read_text(encoding="utf-8").strip()
        out.append({"name": name, "text": text, "source": "yours" if mine.is_file() else "built in", "chars": len(text)})
    return out


def system_text() -> str:
    return "\n".join(s["text"] for s in sections() if s["text"])


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source: str   # "built in", "recipe" or "yours"


def _parse(text: str, fallback: str) -> tuple[str, str, str]:
    """name, description and body of a skill file; the header is two `key: value` lines between `---` lines."""
    m = re.match(r"---\n(.*?)\n---\n?(.*)\Z", text.strip() + "\n", re.S)
    head, body = (m.group(1), m.group(2)) if m else ("", text)
    meta = dict(line.split(":", 1) for line in head.splitlines() if ":" in line)
    return meta.get("name", fallback).strip(), meta.get("description", "").strip(), body.strip()


def _recipes() -> list[Skill]:
    out = []
    for d in sorted(p for p in (HERE / "recipes").iterdir() if p.is_dir()):
        try:
            man = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
            files = [(f.name, f.read_text(encoding="utf-8")) for f in sorted(d.iterdir()) if f.is_file()]
        except (OSError, ValueError):
            continue
        body = "\n\n".join(f"{n}:\n{t.strip()}" for n, t in files)
        out.append(Skill(f"recipe-{d.name}", man.get("description", ""), body, "recipe"))
    return out


def skills() -> list[Skill]:
    found: dict[str, Skill] = {}
    recipes = _recipes()
    for src, folder in (("built in", HERE / "skills"), ("yours", home() / "skills")):
        for f in sorted(folder.glob("*.md")):
            try:
                name, desc, body = _parse(f.read_text(encoding="utf-8"), f.stem)
            except OSError:
                continue
            if NAME.match(name):
                found[name] = Skill(name, desc, body, src)
    if "recipes" in found:
        found["recipes"].body = found["recipes"].body.replace("{{recipes}}", "\n".join(f"- {r.name}: {r.description}" for r in recipes))
    for r in recipes:
        found.setdefault(r.name, r)
    return list(found.values())


def get_skill(name: str) -> Skill | None:
    return next((s for s in skills() if s.name == name.strip().lstrip("/").lower()), None)


def entries() -> list[Entry]:
    return [Entry("skill", s.name, s.description or s.name, example=f'ui_read_skill("{s.name}")', domain="skills") for s in skills()]


def split_slash(text: str) -> tuple[Skill | None, str]:
    """'/app-creator make a timer' -> (that skill, 'make a timer'). Anything that is not a known skill is left alone."""
    m = re.match(r"/([a-z0-9-]+)(?:\s+(.*))?\Z", text.strip(), re.S)
    s = get_skill(m.group(1)) if m else None
    return (s, (m.group(2) or "").strip()) if s else (None, text)
