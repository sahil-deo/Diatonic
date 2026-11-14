"""Reminders and scheduled jobs (notify / prompt / script)."""
from __future__ import annotations

from datetime import datetime

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...scheduler import timeparse
from ..base import DESTRUCTIVE, WRITE, App, Tool, ToolError

WHEN = {"type": "string", "description": "e.g. 'in 20 minutes', 'tomorrow 9am', 'friday 5pm', 'every weekday at 9'"}
SID = {"type": "integer", "description": "Item id from the list"}


class SchedulerApp(App):
    id = "scheduler"
    name = "Scheduler"
    icon = "clock"
    description = "Reminders, and jobs that run a prompt or a script on a schedule."
    panel = True
    keywords = ["remind", "reminder", "reminders", "cron", "job", "jobs", "every", "daily", "weekly", "hourly",
                "monthly", "alarm", "timer", "snooze", "scheduled", "recurring"]
    default_allow = ["remind_me", "pause_job", "resume_job", "run_job_now"]

    # ---- helpers -------------------------------------------------------
    @property
    def sched(self):
        if self.ctx.scheduler is None:
            raise ToolError("The scheduler isn't running.")
        return self.ctx.scheduler

    def _fmt(self, s: dict) -> dict:
        return {"id": s["id"], "name": s["name"], "kind": s["kind"], "when": s["spec"].get("text"),
                "next": self.sched.describe_next(s), "state": s["state"], "last": s["last_status"],
                "writes_allowed": bool(s["allow_writes"]) or None}

    def _get(self, sid: int, kinds: tuple[str, ...] | None = None) -> dict:
        s = self.sched.store.get(int(sid))
        if not s or (kinds and s["kind"] not in kinds):
            raise ToolError(f"No such item (id {sid}). Use the list tool to see ids.")
        return s

    def _create(self, **kw) -> dict:
        try:
            return self.sched.create(created_by="agent", **kw)
        except timeparse.ParseError as e:
            raise ToolError(str(e))

    # ---- tools ---------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool("remind_me", "Set a reminder. Pass the user's own words for 'when'; the time is resolved for you.",
                 {"properties": {"text": {"type": "string", "description": "What to be reminded of"}, "when": WHEN},
                  "required": ["text", "when"]}, self.remind_me, WRITE,
                 describe=lambda a: f"Remind you “{a.get('text')}” — {a.get('when')}"),
            Tool("list_reminders", "List upcoming reminders.", {}, self.list_reminders),
            Tool("cancel_reminder", "Cancel (delete) a reminder.", {"properties": {"id": SID}, "required": ["id"]},
                 self.cancel_reminder, WRITE, describe=lambda a: f"Cancel reminder {a.get('id')}"),
            Tool("schedule_prompt", "Run an AI prompt on a schedule (e.g. a daily briefing). It runs read-only "
                 "in its own chat and notifies the user with the result.",
                 {"properties": {"name": {"type": "string"}, "when": WHEN,
                                 "prompt": {"type": "string", "description": "What the assistant should do each time"}},
                  "required": ["name", "when", "prompt"]}, self.schedule_prompt, WRITE,
                 describe=lambda a: f"Schedule “{a.get('name')}” ({a.get('when')}): {str(a.get('prompt'))[:120]}"),
            Tool("schedule_script", "Run a shell command on a schedule. Notifies on failure.",
                 {"properties": {"name": {"type": "string"}, "when": WHEN,
                                 "command": {"type": "string", "description": "Shell command to run"}},
                  "required": ["name", "when", "command"]}, self.schedule_script, DESTRUCTIVE,
                 describe=lambda a: f"Run on a schedule ({a.get('when')}): $ {a.get('command')}",
                 precheck=lambda a: None if self.ctx.config.data["scheduler"]["allow_scripts"] else
                 "Script jobs are turned off. The user can enable them in Settings → Automation."),
            Tool("list_jobs", "List scheduled jobs (prompt and script).", {}, self.list_jobs),
            Tool("pause_job", "Pause a job or reminder.", {"properties": {"id": SID}, "required": ["id"]}, self.pause_job, WRITE),
            Tool("resume_job", "Resume a paused job.", {"properties": {"id": SID}, "required": ["id"]}, self.resume_job, WRITE),
            Tool("delete_job", "Delete a job.", {"properties": {"id": SID}, "required": ["id"]},
                 self.delete_job, WRITE, describe=lambda a: f"Delete job {a.get('id')}"),
            Tool("run_job_now", "Run a job immediately.", {"properties": {"id": SID}, "required": ["id"]}, self.run_job_now, WRITE),
            Tool("job_history", "Show a job's recent runs and their output.",
                 {"properties": {"id": SID, "limit": {"type": "integer", "minimum": 1, "maximum": 10}}, "required": ["id"]},
                 self.job_history),
        ]

    async def remind_me(self, a):
        s = self._create(name=a["text"], kind="notify", when=a["when"], payload={"text": a["text"]})
        return {"reminder": self._fmt(s), "note": "Confirm this time to the user."}

    async def list_reminders(self, a):
        return [self._fmt(s) for s in self.sched.store.list("notify")] or "No reminders."

    async def cancel_reminder(self, a):
        s = self._get(a["id"], ("notify",))
        self.sched.store.delete(s["id"])
        self.sched.changed()
        return {"cancelled": s["name"]}

    async def schedule_prompt(self, a):
        s = self._create(name=a["name"], kind="prompt", when=a["when"], payload={"prompt": a["prompt"]})
        return {"job": self._fmt(s), "note": "Runs read-only. Writes can be enabled for this job in the Scheduler panel."}

    async def schedule_script(self, a):
        if not self.ctx.config.data["scheduler"]["allow_scripts"]:
            raise ToolError("Script jobs are turned off. The user can enable them in Settings → Automation.")
        s = self._create(name=a["name"], kind="script", when=a["when"], payload={"command": a["command"]})
        return {"job": self._fmt(s)}

    async def list_jobs(self, a):
        return [self._fmt(s) for s in self.sched.store.list("job")] or "No jobs."

    async def pause_job(self, a):
        s = self._get(a["id"])
        self.sched.store.update(s["id"], state="paused")
        self.sched.changed()
        return {"paused": s["name"]}

    async def resume_job(self, a):
        s = self._get(a["id"])
        now = self.ctx.now()
        nxt = timeparse.next_run(s["spec"], now)
        self.sched.store.update(s["id"], state="active" if nxt else "done", next_run=nxt.timestamp() if nxt else None)
        self.sched.changed()
        return {"resumed": s["name"], "next": self.sched.describe_next(self.sched.store.get(s["id"]))}

    async def delete_job(self, a):
        s = self._get(a["id"], ("prompt", "script"))
        self.sched.store.delete(s["id"])
        self.sched.changed()
        return {"deleted": s["name"]}

    async def run_job_now(self, a):
        s = self._get(a["id"])
        return {"started": self.sched.run_now(s["id"]), "name": s["name"]}

    async def job_history(self, a):
        s = self._get(a["id"])
        runs = self.sched.store.runs(s["id"], a.get("limit", 5))
        return [{"at": datetime.fromtimestamp(r["started"], self.ctx.tz).strftime("%a %d %b %H:%M"),
                 "status": r["status"], "late": bool(r["late"]) or None, "output": (r["output"] or "")[:300]} for r in runs] or "No runs yet."

    # ---- panel / UI routes ----------------------------------------------
    async def panel_data(self, request: Request):
        st = self.sched.store
        reminders = [self._fmt(s) for s in st.list("notify")]
        jobs = []
        for s in st.list("job"):
            f = self._fmt(s)
            f["payload"] = {"prompt": s["payload"].get("prompt"), "command": s["payload"].get("command")}
            f["allow_writes"] = bool(s["allow_writes"])
            jobs.append(f)
        return JSONResponse({"reminders": reminders, "jobs": jobs,
                             "scripts_enabled": self.ctx.config.data["scheduler"]["allow_scripts"]})

    async def create_item(self, request: Request):
        b = await request.json()
        kind = b.get("kind", "notify")
        try:
            if kind == "notify":
                s = self.sched.create(name=b["text"], kind="notify", when=b["when"], payload={"text": b["text"]})
            elif kind == "prompt":
                s = self.sched.create(name=b["name"], kind="prompt", when=b["when"], payload={"prompt": b["prompt"]},
                                      allow_writes=bool(b.get("allow_writes")))
            elif kind == "script":
                if not self.ctx.config.data["scheduler"]["allow_scripts"]:
                    return JSONResponse({"error": "Turn on script jobs in Settings → Automation first."}, 400)
                s = self.sched.create(name=b["name"], kind="script", when=b["when"],
                                      payload={"command": b["command"], "notify_on": b.get("notify_on", "failure")})
            else:
                return JSONResponse({"error": "Unknown kind"}, 400)
        except (timeparse.ParseError, KeyError) as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse(self._fmt(s))

    async def act(self, request: Request):
        b = await request.json()
        sid, action = int(b["id"]), b["action"]
        s = self._get(sid)
        if action == "run":
            self.sched.run_now(sid)
        elif action == "pause":
            await self.pause_job({"id": sid})
        elif action == "resume":
            await self.resume_job({"id": sid})
        elif action == "delete":
            self.sched.store.delete(sid)
            self.sched.changed()
        elif action == "allow_writes" and s["kind"] == "prompt":  # only the UI can grant this, never the agent
            self.sched.store.update(sid, allow_writes=int(bool(b.get("value"))))
            self.sched.changed()
        else:
            return JSONResponse({"error": "Unknown action"}, 400)
        return JSONResponse({"ok": True})

    async def history(self, request: Request):
        sid = int(request.query_params["id"])
        return JSONResponse([{**r} for r in self.sched.store.runs(sid, 10)])

    def routes(self):
        return [Route("/panel", self.panel_data), Route("/create", self.create_item, methods=["POST"]),
                Route("/action", self.act, methods=["POST"]), Route("/history", self.history)]
