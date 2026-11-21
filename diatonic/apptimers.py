"""ctx.timer: countdowns that belong to an app but run on the server, so they keep going when the panel or the browser is closed.

A timer is a row (state running, paused or done) plus one asyncio task that sleeps until it ends. When one ends the user gets a
notification and every open window gets a `timer:done` event the panel can listen to with ctx.timer.onDone. Running timers are
picked up again when the server starts; one that ended while the server was off fires at once.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .apps.base import ToolError

log = logging.getLogger(__name__)
MIN_SECONDS = 1
MAX_SECONDS = 7 * 86400
MAX_PER_APP = 50
SCHEMA = """
CREATE TABLE IF NOT EXISTS app_timers (
  id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT NOT NULL, label TEXT NOT NULL, seconds REAL NOT NULL,
  state TEXT NOT NULL, ends_at REAL, remaining REAL NOT NULL, created REAL NOT NULL);
"""


class AppTimers:
    def __init__(self, ctx, db):
        self.ctx, self.conn = ctx, db.conn
        self.conn.executescript(SCHEMA)
        self.tasks: dict[int, asyncio.Task] = {}

    def start_all(self) -> None:
        """Called once the server runs: schedule every timer that was running when it last stopped."""
        for r in self.conn.execute("SELECT id FROM app_timers WHERE state='running'").fetchall():
            self._schedule(r["id"])

    async def stop(self) -> None:
        for t in self.tasks.values():
            t.cancel()
        self.tasks.clear()

    def _row(self, app: str, tid) -> dict:
        r = self.conn.execute("SELECT * FROM app_timers WHERE id=? AND app=?", (tid if isinstance(tid, int) else -1, app)).fetchone()
        if r is None:
            raise ToolError(f"This app has no timer {tid}. Use ctx.timer.list() to see its timers.")
        return dict(r)

    @staticmethod
    def public(r: dict) -> dict:
        left = max(0.0, r["ends_at"] - time.time()) if r["state"] == "running" else (0.0 if r["state"] == "done" else r["remaining"])
        return {"id": r["id"], "label": r["label"], "seconds": r["seconds"], "state": r["state"], "remaining": round(left, 3), "ends_at": r["ends_at"] if r["state"] == "running" else None}

    def _schedule(self, tid: int) -> None:
        if old := self.tasks.pop(tid, None):
            old.cancel()
        r = self.conn.execute("SELECT ends_at FROM app_timers WHERE id=?", (tid,)).fetchone()
        self.tasks[tid] = asyncio.get_running_loop().create_task(self._wait(tid, max(0.0, r["ends_at"] - time.time())))

    async def _wait(self, tid: int, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            await self._finish(tid)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("timer %s", tid)

    async def _finish(self, tid: int) -> None:
        self.tasks.pop(tid, None)
        r = self.conn.execute("SELECT * FROM app_timers WHERE id=? AND state='running'", (tid,)).fetchone()
        if r is None:
            return
        self.conn.execute("UPDATE app_timers SET state='done', remaining=0, ends_at=NULL WHERE id=?", (tid,))
        self.conn.commit()
        self.ctx.broadcast({"type": "app_event", "app": r["app"], "event": "timer:done", "data": {"id": tid, "label": r["label"]}})
        if self.ctx.notifier:
            await self.ctx.notifier.send(r["label"] or "Timer", "Time is up.", kind="reminder", data={"app": r["app"], "timer": tid})

    # ---- what an app can do ------------------------------------------------------
    def start(self, app: str, seconds, label: str = "") -> dict:
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not MIN_SECONDS <= seconds <= MAX_SECONDS:
            raise ToolError(f"seconds must be a number from {MIN_SECONDS} to {MAX_SECONDS} (7 days). Convert minutes and hours to seconds first.")
        if self.conn.execute("SELECT COUNT(*) FROM app_timers WHERE app=?", (app,)).fetchone()[0] >= MAX_PER_APP:
            raise ToolError(f"An app can have {MAX_PER_APP} timers at most. Cancel the finished ones with ctx.timer.cancel(id).")
        now = time.time()
        cur = self.conn.execute("INSERT INTO app_timers(app, label, seconds, state, ends_at, remaining, created) VALUES(?,?,?,?,?,?,?)",
                                (app, str(label or "")[:80], seconds, "running", now + seconds, seconds, now))
        self.conn.commit()
        self._schedule(cur.lastrowid)
        return self.public(self._row(app, cur.lastrowid))

    def pause(self, app: str, tid) -> dict:
        r = self._row(app, tid)
        if r["state"] != "running":
            raise ToolError(f"Timer {tid} is not running, so it cannot be paused.")
        if t := self.tasks.pop(tid, None):
            t.cancel()
        self.conn.execute("UPDATE app_timers SET state='paused', remaining=?, ends_at=NULL WHERE id=?", (max(0.0, r["ends_at"] - time.time()), tid))
        self.conn.commit()
        return self.public(self._row(app, tid))

    def resume(self, app: str, tid) -> dict:
        r = self._row(app, tid)
        if r["state"] != "paused":
            raise ToolError(f"Timer {tid} is not paused, so it cannot be resumed.")
        self.conn.execute("UPDATE app_timers SET state='running', ends_at=? WHERE id=?", (time.time() + r["remaining"], tid))
        self.conn.commit()
        self._schedule(tid)
        return self.public(self._row(app, tid))

    def cancel(self, app: str, tid) -> bool:
        """Remove a timer in any state (running, paused or finished)."""
        self._row(app, tid)
        if t := self.tasks.pop(tid, None):
            t.cancel()
        self.conn.execute("DELETE FROM app_timers WHERE id=?", (tid,))
        self.conn.commit()
        return True

    def list(self, app: str) -> list[dict]:
        return [self.public(dict(r)) for r in self.conn.execute("SELECT * FROM app_timers WHERE app=? ORDER BY id", (app,))]

    def drop_app(self, app: str) -> None:
        for r in self.conn.execute("SELECT id FROM app_timers WHERE app=?", (app,)).fetchall():
            if t := self.tasks.pop(r["id"], None):
                t.cancel()
        self.conn.execute("DELETE FROM app_timers WHERE app=?", (app,))
        self.conn.commit()
