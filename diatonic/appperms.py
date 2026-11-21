"""What a file app may use beyond showing its own panel: notifications, timers, web domains and tools that change things.

The app declares it in manifest.json (`"permissions": {"notify": true, "timer": true, "fetch": ["youtube.com"], "tools": ["todos_add_todo"]}`).
The user approves the whole list once; the approved copy is kept in the user's settings, not in the app's files, because the agent can
write the manifest. If the manifest later asks for more than was approved, the user is asked again. Using something that is not declared
is refused with a message that says what to declare.
"""
from __future__ import annotations

import asyncio
import json
import re
import uuid

from .agent import approvals
from .apps.base import ToolError

ASK_TIMEOUT = 120
HOST = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")
_locks: dict[str, asyncio.Lock] = {}


def normalise(raw) -> dict:
    """The permissions as one fixed shape. Raises ValueError with a sentence the agent can act on."""
    if raw in (None, {}):
        return {"notify": False, "timer": False, "fetch": [], "tools": []}
    if not isinstance(raw, dict):
        raise ValueError('"permissions" must be an object like {"notify": true, "fetch": ["example.com"]}.')
    if extra := set(raw) - {"notify", "timer", "fetch", "tools"}:
        raise ValueError(f"Unknown permission {sorted(extra)[0]!r}. The ones that exist are notify, timer, fetch and tools.")
    for key in ("notify", "timer"):
        if not isinstance(raw.get(key, False), bool):
            raise ValueError(f'"{key}" must be true or false.')
    fetch, tools = raw.get("fetch", []), raw.get("tools", [])
    if not isinstance(fetch, list) or not all(isinstance(d, str) for d in fetch):
        raise ValueError('"fetch" must be a list of domain names, for example ["youtube.com"].')
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        raise ValueError('"tools" must be a list of tool names, for example ["todos_add_todo"].')
    hosts = sorted({re.sub(r"^https?://|/.*$", "", d.strip().lower()) for d in fetch})
    if bad := next((h for h in hosts if not HOST.match(h)), None):
        raise ValueError(f"{bad!r} is not a domain name. Use the name only, for example \"youtube.com\".")
    return {"notify": bool(raw.get("notify")), "timer": bool(raw.get("timer")), "fetch": hosts, "tools": sorted({t.strip() for t in tools if t.strip()})}


def declared(ctx, app_id: str) -> dict:
    try:
        manifest = json.loads(ctx.fileapps.read_file(app_id, "manifest.json"))
        return normalise(manifest.get("permissions"))
    except Exception:
        return normalise(None)


def approved(ctx, app_id: str) -> dict:
    try:
        return normalise(ctx.config.app(app_id).get("permissions_ok"))
    except ValueError:
        return normalise(None)


def items(perms: dict) -> list[str]:
    return (["show notifications"] if perms["notify"] else []) + (["run timers"] if perms["timer"] else []) \
        + [f"read {d}" for d in perms["fetch"]] + [f"change things with {t}" for t in perms["tools"]]


def _covered(want: dict, have: dict) -> bool:
    return (not want["notify"] or have["notify"]) and (not want["timer"] or have["timer"]) \
        and set(want["fetch"]) <= set(have["fetch"]) and set(want["tools"]) <= set(have["tools"])


def _refuse(perm: str, value: str | None) -> ToolError:
    if perm in ("notify", "timer"):
        return ToolError(f'This app has not declared that it uses {perm}. Add "permissions": {{"{perm}": true}} to its manifest.json.')
    key = "fetch" if perm == "fetch" else "tools"
    return ToolError(f'This app has not declared "{value}". Add it to manifest.json, for example "permissions": {{"{key}": ["{value}"]}}.')


async def require(ctx, app_id: str, perm: str, value: str | None = None) -> None:
    """Return when the app may use `perm` (`value` is the domain or tool name); raise ToolError when it may not."""
    want = declared(ctx, app_id)
    if not (want[perm] if perm in ("notify", "timer") else value in want[perm]):
        raise _refuse(perm, value)
    async with _locks.setdefault(app_id, asyncio.Lock()):
        have = approved(ctx, app_id)
        if _covered(want, have):
            return
        name = next((a["name"] for a in ctx.fileapps.list_apps() if a["id"] == app_id), app_id)
        aid = "perm_" + uuid.uuid4().hex[:12]
        fut = approvals.request(aid)
        ctx.broadcast({"type": "perm_ask", "id": aid, "app": app_id, "name": name, "items": items(want), "new": [i for i in items(want) if i not in items(have)]})
        try:
            ok = await asyncio.wait_for(fut, ASK_TIMEOUT)
        except asyncio.TimeoutError:
            ok = False
        finally:
            approvals.discard(aid)
            ctx.broadcast({"type": "perm_ask_done", "id": aid})
        if not ok:
            raise ToolError(f"You did not allow {name} to use: {', '.join(items(want))}. Use the app again to be asked once more.")
        ctx.config.app(app_id)["permissions_ok"] = want
        ctx.config.save()
