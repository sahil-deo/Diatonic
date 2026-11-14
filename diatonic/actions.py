"""User-defined quick actions (saved prompts and script buttons) and interface customization.

Actions live in config.json. Scripts only ever run from a saved action, by id, on an explicit click or
slash command; the browser never sends a command to execute. The same master switch as scheduled script
jobs (`scheduler.allow_scripts`) must be on.
"""
from __future__ import annotations

import asyncio
import os
import re
import time
import uuid

from .config import DEFAULTS
from .scheduler.scripts import HARD_MAX_TIMEOUT, ScriptsDisabled, _env, _kill

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
HOTKEY_RE = re.compile(r"^(?:(?:Control|Alt|Shift|Meta)\+)+[A-Za-z0-9]+$|^F\d{1,2}$")
BUILTIN_COMMANDS = {"new", "settings", "theme", "speak", "panel", "stop", "actions", "help", "sidebar"}
MAX_ACTIONS = 100
MAX_OUTPUT = 200_000


def _s(v, n: int) -> str:
    return str(v if v is not None else "").strip()[:n]


def clean_actions(raw) -> list[dict]:
    """Validate and normalise a full action list. Raises ValueError with a message fit for the UI."""
    if not isinstance(raw, list):
        raise ValueError("Actions must be a list.")
    if len(raw) > MAX_ACTIONS:
        raise ValueError(f"At most {MAX_ACTIONS} actions.")
    out, ids, names, keys = [], set(), set(), set()
    for i, a in enumerate(raw, 1):
        if not isinstance(a, dict):
            raise ValueError(f"Action {i} is malformed.")
        kind = a.get("kind")
        if kind not in ("prompt", "script"):
            raise ValueError(f"Action {i}: kind must be 'prompt' or 'script'.")
        label = _s(a.get("label"), 40)
        if not label:
            raise ValueError(f"Action {i}: give it a label.")
        name = _s(a.get("name"), 32).lower().lstrip("/")
        if name:
            if not NAME_RE.match(name):
                raise ValueError(f"“{label}”: the command name may use a-z, 0-9, - and _ (max 32).")
            if name in BUILTIN_COMMANDS:
                raise ValueError(f"“{label}”: /{name} is a built-in command.")
            if name in names:
                raise ValueError(f"“{label}”: /{name} is used twice.")
            names.add(name)
        shortcut = _s(a.get("shortcut"), 40)
        if shortcut:
            if not HOTKEY_RE.match(shortcut):
                raise ValueError(f"“{label}”: that shortcut isn't valid.")
            if shortcut in keys:
                raise ValueError(f"“{label}”: shortcut {shortcut} is used twice.")
            keys.add(shortcut)
        aid = _s(a.get("id"), 40) or uuid.uuid4().hex[:10]
        if aid in ids:
            aid = uuid.uuid4().hex[:10]
        ids.add(aid)
        item = {"id": aid, "kind": kind, "label": label, "name": name, "icon": _s(a.get("icon"), 8),
                "description": _s(a.get("description"), 120), "bar": bool(a.get("bar", True)),
                "shortcut": shortcut, "confirm": bool(a.get("confirm", False))}
        if kind == "prompt":
            item["prompt"] = _s(a.get("prompt"), 8000)
            if not item["prompt"]:
                raise ValueError(f"“{label}”: the prompt is empty.")
            item.update(send=bool(a.get("send", False)), new_chat=bool(a.get("new_chat", False)))
        else:
            item["command"] = _s(a.get("command"), 4000)
            if not item["command"]:
                raise ValueError(f"“{label}”: the command is empty.")
            try:
                t = int(a.get("timeout") or 0)
            except (TypeError, ValueError):
                raise ValueError(f"“{label}”: timeout must be a number of seconds.")
            item.update(cwd=_s(a.get("cwd"), 300), timeout=max(0, min(t, HARD_MAX_TIMEOUT)))
        out.append(item)
    return out


FONTS = {"system", "sans", "serif", "mono", "rounded"}
ENUMS = {"theme": {"system", "light", "dark"}, "density": {"comfortable", "compact"}, "font": FONTS,
         "radius": {"sharp", "default", "round"}, "width": {"narrow", "default", "wide", "full"},
         "sidebar": {"show", "hide"}}
BOOLS = {"show_status_bar", "show_quick_actions", "show_suggestions", "show_speak_button"}


def clean_appearance(patch) -> dict:
    """Validate a (partial) appearance update. Unknown keys are rejected so typos aren't silently saved."""
    if not isinstance(patch, dict):
        raise ValueError("Appearance must be an object.")
    out: dict = {}
    for k, v in patch.items():
        if k in ENUMS:
            if v not in ENUMS[k]:
                raise ValueError(f"Invalid {k}: {v!r}.")
            out[k] = v
        elif k in BOOLS:
            out[k] = bool(v)
        elif k == "accent":
            if v and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(v)):
                raise ValueError("Accent must be a #rrggbb colour.")
            out[k] = str(v or "")
        elif k == "font_size":
            out[k] = max(11, min(22, int(v)))
        elif k == "custom_css":
            if len(str(v)) > 20000:
                raise ValueError("Custom CSS is limited to 20,000 characters.")
            out[k] = str(v)
        elif k in ("rail_order", "rail_hidden"):
            if not isinstance(v, list) or not all(isinstance(x, str) and len(x) < 40 for x in v) or len(v) > 50:
                raise ValueError(f"{k} must be a list of app ids.")
            out[k] = v
        else:
            raise ValueError(f"Unknown appearance setting '{k}'.")
    return out


def export_bundle(config) -> dict:
    return {"diatonic": 1, "appearance": config.data["appearance"], "actions": config.data["actions"]}


def import_bundle(config, bundle) -> dict:
    """Validate everything first, then apply, so a bad file changes nothing."""
    if not isinstance(bundle, dict) or bundle.get("diatonic") != 1:
        raise ValueError("That doesn't look like a Diatonic customization file.")
    actions = clean_actions(bundle.get("actions", []))
    appearance = clean_appearance({**DEFAULTS["appearance"], **(bundle.get("appearance") or {})})
    config.data["appearance"] = appearance
    config.data["actions"] = actions
    config.save()
    return {"appearance": appearance, "actions": actions}


class ActionRunner:
    """Runs saved script actions, streaming output to every connected UI over the event stream."""

    def __init__(self, ctx):
        self.ctx = ctx
        self.tasks: dict[str, asyncio.Task] = {}
        self.procs: dict[str, asyncio.subprocess.Process] = {}
        self.cancelled: set[str] = set()

    def find(self, action_id: str) -> dict | None:
        return next((a for a in self.ctx.config.data["actions"] if a["id"] == action_id), None)

    def start(self, action: dict, text: str = "") -> str:
        if action["kind"] != "script":
            raise ValueError("Only script actions can be run here.")
        if not self.ctx.config.data["scheduler"]["allow_scripts"]:
            raise ScriptsDisabled()
        rid = uuid.uuid4().hex[:10]
        self.tasks[rid] = asyncio.create_task(self._run(rid, action, text[:4000]))
        return rid

    def cancel(self, rid: str) -> bool:
        p = self.procs.get(rid)
        if p and p.returncode is None:
            _kill(p)
            self.cancelled.add(rid)
            return True
        return False

    async def shutdown(self) -> None:
        for p in list(self.procs.values()):
            _kill(p)
        for t in list(self.tasks.values()):
            t.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    def _emit(self, rid: str, aid: str, kind: str, **kw) -> None:
        self.ctx.broadcast({"type": kind, "run_id": rid, "action_id": aid, **kw})

    async def _run(self, rid: str, action: dict, text: str) -> None:
        cfg = self.ctx.config.data["scheduler"]
        timeout = min(float(action.get("timeout") or cfg["script_timeout"]), HARD_MAX_TIMEOUT)
        cwd = os.path.expanduser(action.get("cwd") or "~")
        if not os.path.isdir(cwd):
            self._emit(rid, action["id"], "script_output", text=f"Working folder not found: {cwd}\n")
            self._emit(rid, action["id"], "script_done", status="error", code=None, ms=0)
            return
        t0, sent, status, code = time.monotonic(), 0, "ok", None
        argv = ["/bin/sh", "-c", action["command"], "sh"] + ([text] if text else [])
        env = {**_env(), "INPUT": text, "DIATONIC_ACTION": action["id"]}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv, cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        except OSError as e:
            self._emit(rid, action["id"], "script_output", text=f"Couldn't start: {e}\n")
            self._emit(rid, action["id"], "script_done", status="error", code=None, ms=0)
            return
        self.procs[rid] = proc
        try:
            async def pump():
                nonlocal sent
                while chunk := await proc.stdout.read(4096):
                    if sent < MAX_OUTPUT:
                        piece = chunk.decode(errors="replace")
                        sent += len(chunk)
                        self._emit(rid, action["id"], "script_output", text=piece)
                        if sent >= MAX_OUTPUT:
                            self._emit(rid, action["id"], "script_output", text="\n… output truncated\n")
            await asyncio.wait_for(pump(), timeout)
            code = await proc.wait()
            status = "ok" if code == 0 else "error"
        except asyncio.TimeoutError:
            _kill(proc)
            status = "timeout"
            self._emit(rid, action["id"], "script_output", text=f"\n[killed after {int(timeout)}s]\n")
        except asyncio.CancelledError:
            _kill(proc)
            status = "cancelled"
        finally:
            if rid in self.cancelled:
                status = "cancelled"
                self.cancelled.discard(rid)
            if proc.returncode is None:
                _kill(proc)
            self.procs.pop(rid, None)
            self._emit(rid, action["id"], "script_done", status=status, code=code,
                       ms=int((time.monotonic() - t0) * 1000))
