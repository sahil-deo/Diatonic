"""Background runs: every chat runs as its own task, independent of any browser connection.

Events are tagged with a per-run sequence number, buffered for replay, and fanned out to
subscribers (the UI's single global event stream). Switching chats or closing the tab never
interrupts a run; the UI re-attaches by replaying the buffer.
"""
from __future__ import annotations

import asyncio
import time

from . import loop as agent_loop
from .session import RUN_CLIENT


class RunBusy(Exception):
    pass


class Run:
    def __init__(self, conv_id: str, title: str, client: str | None = None):
        self.conv_id, self.title, self.client = conv_id, title, client
        self.events: list[dict] = []
        self.seq = 0
        self.status = "running"  # running | waiting (for approval)
        self.task: asyncio.Task | None = None
        self.started = time.time()

    def snapshot(self) -> dict:
        return {"conversation_id": self.conv_id, "title": self.title, "status": self.status,
                "seq": self.seq, "started": self.started}

    def replay(self) -> dict:
        return {**self.snapshot(), "events": list(self.events)}


class RunManager:
    def __init__(self):
        self.runs: dict[str, Run] = {}
        self._subs: set[asyncio.Queue] = set()
        self._client_of: dict[asyncio.Queue, str] = {}
        self.focus: str | None = None   # the one UI that should speak events nobody in particular started

    # ---- subscribers ---------------------------------------------------
    def subscribe(self, client: str | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        if client:
            self._client_of[q] = client
            if self.focus is None:
                self.focus = client
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)
        gone = self._client_of.pop(q, None)
        if gone and self.focus == gone and gone not in self._client_of.values():
            self.focus = next(reversed(list(self._client_of.values())), None)   # hand over to the newest remaining UI

    def set_focus(self, client: str) -> None:
        if client in self._client_of.values():
            self.focus = client

    def snapshot(self) -> list[dict]:
        return [r.snapshot() for r in self.runs.values()]

    def broadcast(self, ev: dict) -> None:
        """Push a non-run event (notifications, data changes) to every connected UI."""
        if ev.get("type") == "notification":
            ev["speak_for"] = self.focus   # every open tab gets the event, but only this one may read it aloud
        for q in list(self._subs):
            q.put_nowait(ev)

    def send_ui(self, client: str, ev: dict) -> bool:
        """Push a screen-control event to one window only. False if that window is gone."""
        sent = False
        for q, c in list(self._client_of.items()):
            if c == client and q in self._subs:
                q.put_nowait({**ev, "target": client}); sent = True
        return sent

    # ---- lifecycle -----------------------------------------------------
    def start(self, ctx, registry, conv: dict, text: str, speak: bool, **opts) -> Run:
        if conv["id"] in self.runs:
            raise RunBusy(conv["id"])
        client = opts.pop("client", None)
        run = Run(conv["id"], conv["title"], client)
        self.runs[run.conv_id] = run
        self._publish(run, {"type": "run_start", "title": run.title})
        run.task = asyncio.create_task(self._drive(run, ctx, registry, text, speak, opts))
        return run

    def cancel(self, conv_id: str) -> bool:
        run = self.runs.get(conv_id)
        if run and run.task and not run.task.done():
            run.task.cancel()
            return True
        return False

    async def shutdown(self) -> None:
        tasks = [r.task for r in self.runs.values() if r.task]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _drive(self, run: Run, ctx, registry, text: str, speak: bool, opts: dict) -> None:
        finished = False
        RUN_CLIENT.set(run.client)   # tools that act on the screen need to know which window asked
        try:
            async for ev in agent_loop.run_agent(ctx, registry, run.conv_id, text, speak=speak, **opts):
                finished = finished or ev["type"] == "done"
                if ev["type"] == "done":
                    self.runs.pop(run.conv_id, None)  # before publishing, so late readers see the DB
                self._publish(run, ev)
        except asyncio.CancelledError:
            self._publish(run, {"type": "cancelled"})
        except Exception as e:  # never leave the UI waiting
            self._publish(run, {"type": "error", "message": f"{type(e).__name__}: {e}"})
        finally:
            if not finished:
                self.runs.pop(run.conv_id, None)
                self._publish(run, {"type": "done"})

    def _publish(self, run: Run, ev: dict) -> None:
        t = ev["type"]
        if t == "tool_call" and ev.get("needs_approval"):
            run.status = "waiting"
        elif t in ("tool_status", "tool_result") and run.status == "waiting":
            run.status = "running"
        if t in ("summary", "notify"):   # spoken output: only the UI that started the run (else the focused one) speaks it
            ev = {**ev, "speak_for": run.client or self.focus}
        run.seq += 1
        ev = {**ev, "seq": run.seq, "conversation_id": run.conv_id, "status": run.status}
        run.events.append(ev)
        for q in list(self._subs):
            q.put_nowait(ev)
