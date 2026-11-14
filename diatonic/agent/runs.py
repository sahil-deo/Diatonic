"""Background runs: every chat runs as its own task, independent of any browser connection.

Events are tagged with a per-run sequence number, buffered for replay, and fanned out to
subscribers (the UI's single global event stream). Switching chats or closing the tab never
interrupts a run; the UI re-attaches by replaying the buffer.
"""
from __future__ import annotations

import asyncio
import time

from . import loop as agent_loop


class RunBusy(Exception):
    pass


class Run:
    def __init__(self, conv_id: str, title: str):
        self.conv_id, self.title = conv_id, title
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

    # ---- subscribers ---------------------------------------------------
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def snapshot(self) -> list[dict]:
        return [r.snapshot() for r in self.runs.values()]

    # ---- lifecycle -----------------------------------------------------
    def start(self, ctx, registry, conv: dict, text: str, speak: bool) -> Run:
        if conv["id"] in self.runs:
            raise RunBusy(conv["id"])
        run = Run(conv["id"], conv["title"])
        self.runs[run.conv_id] = run
        self._publish(run, {"type": "run_start", "title": run.title})
        run.task = asyncio.create_task(self._drive(run, ctx, registry, text, speak))
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

    async def _drive(self, run: Run, ctx, registry, text: str, speak: bool) -> None:
        finished = False
        try:
            async for ev in agent_loop.run_agent(ctx, registry, run.conv_id, text, speak=speak):
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
        run.seq += 1
        ev = {**ev, "seq": run.seq, "conversation_id": run.conv_id, "status": run.status}
        run.events.append(ev)
        for q in list(self._subs):
            q.put_nowait(ev)
