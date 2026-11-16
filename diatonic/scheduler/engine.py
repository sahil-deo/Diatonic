"""The clock: sleeps until the next schedule is due and runs it. Survives restarts."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from ..agent import session
from ..agent.guards import clip, collapse_repeats
from ..voice import summarizer
from . import timeparse
from .notify import Notifier
from .scripts import run_script
from .store import Store

log = logging.getLogger("diatonic.scheduler")
LATE_AFTER = 90  # seconds past due before a run counts as "missed"

SCHEDULED_NOTE = ("\n\nThis is an automated scheduled run. Nobody is available to answer questions. "
                  "Do the task with your tools, then report the result in a few short lines.")


class Scheduler:
    def __init__(self, ctx, registry, runs, notifier: Notifier, store: Store):
        self.ctx, self.registry, self.runs, self.notifier, self.store = ctx, registry, runs, notifier, store
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}
        self._prompt_lock = asyncio.Lock()  # one scheduled prompt at a time: the model is the bottleneck

    # ---- lifecycle -----------------------------------------------------
    def start(self) -> None:
        self.store.mark_stale_runs()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._running.values()) if t]
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    def poke(self) -> None:
        self._wake.set()

    def changed(self) -> None:
        self.poke()
        self.ctx.broadcast({"type": "app_changed", "app": "scheduler"})

    # ---- creating ------------------------------------------------------
    def create(self, *, name: str, kind: str, when: str, payload: dict, allow_writes: bool = False,
               created_by: str = "user") -> dict:
        now = self.ctx.now()
        spec = timeparse.parse_schedule(when, now)
        nxt = timeparse.next_run(spec, now)
        row = self.store.add(name=name[:120], kind=kind, spec=spec, payload=payload,
                             next_run=nxt.timestamp() if nxt else None, allow_writes=allow_writes, created_by=created_by)
        self.changed()
        return row

    def edit(self, sid: int, *, name: str | None = None, when: str | None = None, payload: dict | None = None) -> dict:
        """Change a reminder or job. A new `when` is parsed like a new one and the next run is recalculated."""
        s = self.store.get(sid)
        if s is None:
            raise KeyError(sid)
        fields: dict = {}
        if name is not None and name.strip():
            fields["name"] = name.strip()[:120]
        if when is not None and when.strip():
            now = self.ctx.now()
            spec = timeparse.parse_schedule(when, now)          # raises ParseError with a helpful message
            nxt = timeparse.next_run(spec, now)
            fields.update(spec=spec, next_run=nxt.timestamp() if nxt else None)
        if payload:
            fields["payload"] = {**s["payload"], **payload}
        if fields:
            self.store.update(sid, **fields)
            self.changed()
        return self.store.get(sid)

    def describe_next(self, s: dict) -> str:
        if s["next_run"] is None:
            return "—"
        return timeparse.human(datetime.fromtimestamp(s["next_run"], self.ctx.tz), self.ctx.now())

    # ---- the loop ------------------------------------------------------
    async def _loop(self) -> None:
        while True:
            try:
                now = time.time()
                for s in self.store.due(now):
                    self._dispatch(s, late=now - s["next_run"] > LATE_AFTER)
                nxt = self.store.next_due_time()
                timeout = 30 if nxt is None else max(0.05, min(30.0, nxt - time.time()))
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout)
                except asyncio.TimeoutError:
                    pass
                self._wake.clear()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("scheduler loop error")
                await asyncio.sleep(5)

    def _advance(self, s: dict) -> None:
        """Move next_run to the next future occurrence (missed ones are coalesced into this single run)."""
        nxt = timeparse.next_run(s["spec"], self.ctx.now())
        if nxt is None:
            self.store.update(s["id"], next_run=None, state="done")
        else:
            self.store.update(s["id"], next_run=nxt.timestamp())

    def _dispatch(self, s: dict, *, late: bool = False, manual: bool = False) -> bool:
        running = self._running.get(s["id"])
        if running and not running.done():
            if not manual:
                self._advance(s)
            rid = self.store.start_run(s["id"], late)
            self.store.finish_run(rid, s["id"], "skipped", "Previous run was still going.")
            return False
        if not manual:
            self._advance(s)
        task = asyncio.create_task(self._run(s, late))
        self._running[s["id"]] = task
        task.add_done_callback(lambda t, sid=s["id"]: self._running.pop(sid, None))
        self.poke()
        return True

    def run_now(self, sid: int) -> bool:
        s = self.store.get(sid)
        return bool(s) and self._dispatch(s, manual=True)

    # ---- running one schedule -----------------------------------------
    async def _run(self, s: dict, late: bool) -> None:
        rid = self.store.start_run(s["id"], late)
        status, output, conv = "ok", "", None
        try:
            if s["kind"] == "notify":
                text = s["payload"].get("text", s["name"])
                when = datetime.fromtimestamp(s["next_run"] or time.time(), self.ctx.tz)
                body = text + (f"\n(missed earlier; it was due {timeparse.human(when, self.ctx.now())})" if late else "")
                await self.notifier.send("Reminder", body, kind="reminder",
                                         data={"schedule_id": s["id"], "text": text}, speak_text=f"Reminder: {text}")
                output = text
            elif s["kind"] == "prompt":
                status, output, conv = await self._run_prompt(s)
            elif s["kind"] == "macro":
                macro_id = s["payload"].get("macro_id") or s["payload"].get("id") or s["name"]
                self.ctx.broadcast({"type": "run_macro", "macro_id": macro_id, "schedule_id": s["id"]})
                output = f"Triggered macro {macro_id}"
            elif s["kind"] == "script":
                status, output = await run_script(self.ctx, s)
                mode = s["payload"].get("notify_on", "failure")
                if mode == "always" or (mode == "failure" and status != "ok"):
                    await self.notifier.send(f"{s['name']}: {'finished' if status == 'ok' else status}",
                                             clip(output.strip(), 400), kind="job",
                                             data={"schedule_id": s["id"]},
                                             speak_text=None if status == "ok" else f"The job {s['name']} failed.")
        except asyncio.CancelledError:
            self.store.finish_run(rid, s["id"], "interrupted", "Stopped.")
            raise
        except Exception as e:
            log.exception("schedule %s failed", s["id"])
            status, output = "error", f"{type(e).__name__}: {e}"
            await self.notifier.send(f"{s['name']} failed", output[:300], kind="job", data={"schedule_id": s["id"]})
        self.store.finish_run(rid, s["id"], status, output, conv)
        self.ctx.broadcast({"type": "app_changed", "app": "scheduler"})

    async def _run_prompt(self, s: dict) -> tuple[str, str, str | None]:
        stamp = self.ctx.now().strftime("%b %d %H:%M")
        async with self._prompt_lock:
            conv = self.ctx.db.create_conversation(f"⏰ {s['name']} · {stamp}")
            token = session.JOB.set({"id": s["id"], "commands": list(s["payload"].get("commands") or [])})   # copied into the run's task
            run = self.runs.start(
                self.ctx, self.registry, conv, s["payload"]["prompt"], False, readonly=not s["allow_writes"],
                approval_timeout=float(self.ctx.config.data["scheduler"]["approval_timeout"]),
                extra_system=SCHEDULED_NOTE)
            session.JOB.reset(token)
            await run.task
        final = next((e["text"] for e in reversed(run.events) if e["type"] == "message"), "")
        err = next((e["message"] for e in reversed(run.events) if e["type"] == "error"), None)
        self._prune_prompt_chats(s["name"])
        ok = bool(final) and not err
        body = clip(summarizer_text(final or err or "No result."), 400)
        await self.notifier.send(s["name"] if ok else f"{s['name']} failed", body, kind="job",
                                 data={"schedule_id": s["id"], "conversation_id": conv["id"]},
                                 speak_text=summarizer.clean_for_speech(final, 25) if ok else f"The job {s['name']} failed.")
        return ("ok" if ok else "error"), final or err or "", conv["id"]

    def _prune_prompt_chats(self, name: str) -> None:
        keep = int(self.ctx.config.data["scheduler"]["keep_prompt_chats"])
        mine = [c for c in self.ctx.db.list_conversations(500) if c["title"].startswith(f"⏰ {name} ·")]
        for c in mine[keep:]:
            self.ctx.db.delete_conversation(c["id"])


def summarizer_text(text: str) -> str:
    return collapse_repeats(text).replace("**", "").replace("`", "").strip()
