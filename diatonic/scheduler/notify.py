"""Notifications: always stored in the inbox, then pushed to every enabled channel."""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, time as dtime

from .store import Store


def in_quiet_hours(cfg: dict, now: datetime) -> bool:
    if not cfg.get("quiet_enabled"):
        return False
    try:
        s = dtime.fromisoformat(cfg["quiet_start"])
        e = dtime.fromisoformat(cfg["quiet_end"])
    except (ValueError, KeyError):
        return False
    t = now.time()
    return s <= t < e if s <= e else (t >= s or t < e)


class Notifier:
    def __init__(self, ctx, store: Store, broadcast):
        self.ctx, self.store, self.broadcast = ctx, store, broadcast

    @property
    def cfg(self) -> dict:
        return self.ctx.config.data["notifications"]

    async def send(self, title: str, body: str = "", *, kind: str = "info", data: dict | None = None,
                   speak_text: str | None = None) -> dict:
        row = self.store.add_notification(title[:120], body[:1000], kind, data or {})
        quiet = in_quiet_hours(self.cfg, self.ctx.now())
        self.broadcast({"type": "notification", **row, "quiet": quiet, "speak_text": None if quiet else speak_text,
                        "unread": self.store.unread_count()})
        if self.cfg.get("native") and not quiet:
            await self._native(title, body)
        return row

    async def _native(self, title: str, body: str) -> None:
        if sys.platform != "darwin" or os.environ.get("DIATONIC_NO_NATIVE"):
            return
        script = ["on run argv", "display notification (item 1 of argv) with title (item 2 of argv) "
                  "subtitle \"Diatonic\" sound name \"Glass\"", "end run"]
        try:
            p = await asyncio.create_subprocess_exec(
                "osascript", *sum((["-e", ln] for ln in script), []), body[:200] or "—", title[:80],
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await asyncio.wait_for(p.wait(), 5)
        except Exception:
            pass  # a failed banner must never break the schedule
