"""Interface mods the agent makes on request: named CSS, scripts and whole panels, with an undo history.

Stored in one JSON file in the Diatonic home folder. Styles are filtered (no network, no touching the approval UI).
Scripts and panels are arbitrary code, so the tools that create them always need an approval card, and the store
itself only ever holds what the page loads back from it.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
KINDS = ("style", "script", "panel", "button", "app", "data")
MAX_CODE = 20_000
MAX_CHANGES = 40
# CSS may not fetch anything, and may not restyle the approval UI (a hidden Deny button is as good as a forged one).
_CSS_BAD = re.compile(r"@import|url\s*\(|expression\s*\(|javascript:|behaviou?r\s*:|-moz-binding|approval|a-btns|dock-asks", re.I)


class ModError(ValueError):
    pass


def check_css(css: str) -> None:
    if len(css) > MAX_CODE:
        raise ModError(f"CSS is limited to {MAX_CODE:,} characters.")
    m = _CSS_BAD.search(css)
    if m:
        raise ModError(f"CSS can't contain '{m.group(0)}': styles can't load anything from the network or change the approval cards.")


def check_button(spec: str) -> None:
    """A button is data, not code: {"label", "app"} toggles a panel, {"label", "prompt"} asks the agent."""
    try:
        b = json.loads(spec)
    except ValueError:
        raise ModError("A button is JSON like {\"label\": \"Files\", \"app\": \"files\"}.")
    if not isinstance(b, dict) or not str(b.get("label", "")).strip() or bool(b.get("app")) == bool(b.get("prompt")):
        raise ModError("A button needs a label and exactly one of: app (to open/close) or prompt (to ask the agent).")


class ModStore:
    def __init__(self, folder: Path):
        self.path = Path(folder) / "mods.json"
        self._lock = threading.Lock()
        self.data = {"mods": {}, "changes": []}
        self.on_change = lambda: None   # set by the server: API apps become real apps when their manifest changes
        try:
            self.data.update(json.loads(self.path.read_text()))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        try:
            self.on_change()
        except Exception:
            pass
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=1))
            tmp.replace(self.path)

    def _log(self, entry: dict) -> None:
        self.data["changes"] = (self.data["changes"] + [{**entry, "at": time.time()}])[-MAX_CHANGES:]

    # ---- reading -------------------------------------------------------
    def list(self) -> list[dict]:
        return [{"name": n, **m} for n, m in sorted(self.data["mods"].items(), key=lambda kv: kv[1].get("updated", 0))]

    def get(self, name: str) -> dict | None:
        m = self.data["mods"].get(name)
        return {"name": name, **m} if m else None

    # ---- changing ------------------------------------------------------
    def put(self, kind: str, name: str, code: str, title: str = "", *, enabled: bool = True) -> dict:
        name = (name or "").strip().lower().replace(" ", "-").replace("_", "-")
        if kind not in KINDS:
            raise ModError(f"kind must be one of {', '.join(KINDS)}.")
        if not NAME.fullmatch(name):
            raise ModError("name must be 1-40 lowercase letters, digits or dashes, e.g. wide-sidebar.")
        code = (code or "").strip()
        if not code:
            raise ModError("There is no code.")
        if kind == "style":
            check_css(code)
        elif kind == "button":
            check_button(code)
        elif kind == "data":
            from .dataapps import DataSpecError, check_data_manifest
            try:
                code = json.dumps(check_data_manifest(code))
            except DataSpecError as e:
                raise ModError(str(e))
        elif kind == "app":
            from .apiapps import AppSpecError, check_manifest
            try:
                code = json.dumps(check_manifest(code))
            except AppSpecError as e:
                raise ModError(str(e))
        elif len(code) > MAX_CODE:
            raise ModError(f"Code is limited to {MAX_CODE:,} characters.")
        before = self.data["mods"].get(name)
        if before and before["kind"] != kind:
            raise ModError(f"'{name}' already exists as a {before['kind']}. Pick another name.")
        mod = {"kind": kind, "title": (title or name)[:60], "code": code, "enabled": enabled,
               "version": (before or {}).get("version", 0) + 1, "updated": time.time()}
        self.data["mods"][name] = mod
        self._log({"kind": "mod", "name": name, "before": before, "what": f"{'updated' if before else 'added'} {kind} '{name}'"})
        self._save()
        return {"name": name, **mod}

    def remove(self, name: str) -> dict:
        before = self.data["mods"].pop(name, None)
        if before is None:
            raise ModError(f"There is no mod called '{name}'.")
        self._log({"kind": "mod", "name": name, "before": before, "what": f"removed {before['kind']} '{name}'"})
        self._save()
        return {"name": name, **before}

    def set_code(self, name: str, code: str) -> None:
        """Bookkeeping update (e.g. remembering a response's shape): not a user-visible change, so no undo entry."""
        if name in self.data["mods"]:
            self.data["mods"][name]["code"] = code
            self._save()

    def set_enabled(self, name: str, on: bool) -> None:
        if name not in self.data["mods"]:
            raise ModError(f"There is no mod called '{name}'.")
        self.data["mods"][name]["enabled"] = bool(on)
        self._save()

    def log_theme(self, before: dict, after: dict) -> None:
        self._log({"kind": "theme", "before": before, "after": after, "what": "changed the theme (" + ", ".join(after) + ")"})
        self._save()

    def revert(self, config) -> str:
        """Undo the latest change (a mod or a theme change). Returns what was undone."""
        if not self.data["changes"]:
            raise ModError("Nothing to undo.")
        ch = self.data["changes"].pop()
        if ch["kind"] == "theme":
            config.update({"appearance": ch["before"]})
        elif ch["before"] is None:
            self.data["mods"].pop(ch["name"], None)
        else:
            self.data["mods"][ch["name"]] = ch["before"]
        self._save()
        return "undid: " + ch["what"]
