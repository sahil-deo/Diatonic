"""Reminders and scheduled jobs (notify / prompt / script)."""
from __future__ import annotations

import re
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
    keywords = ["remind", "reminder", "reminders", "cron", "job", "jobs", "every", "daily", "weekly", "hourly", "reschedule",
                "monthly", "alarm", "timer", "snooze", "scheduled", "recurring"]
    default_allow = ["remind_me", "pause_job", "resume_job", "run_job_now"]

    # ---- helpers -------------------------------------------------------
    @property
    def sched(self):
        if self.ctx.scheduler is None:
            raise ToolError("The scheduler isn't running.")
        return self.ctx.scheduler

    async def snapshot(self, n: int) -> dict:
        items = sorted((s for s in self.sched.store.list() if s["state"] == "active" and s["next_run"]), key=lambda s: s["next_run"])
        return {"items": [{"text": s["name"], "sub": self.sched.describe_next(s), "badge": "reminder" if s["kind"] == "notify" else s["kind"]}
                          for s in items[:n]], "empty": "Nothing scheduled."}

    def _fmt(self, s: dict) -> dict:
        return {"id": s["id"], "name": s["name"], "kind": s["kind"], "when": s["spec"].get("text"),
                "next": self.sched.describe_next(s), "state": s["state"], "last": s["last_status"],
                "writes_allowed": bool(s["allow_writes"]) or None}

    @staticmethod
    def _bare_hour(when: str | None, s: dict) -> str | None:
        """'at 7' has no am/pm and is read by a fixed rule (1-7 = pm). Make the model say the real time so the user can catch it."""
        m = re.search(r"\b(?:at|@)\s*(\d{1,2})(?::\d{2})?\b(?!\s*(?:am|pm|a\.m|p\.m|o'?clock in the))", (when or "").lower())
        if not m or int(m.group(1)) > 12:
            return None
        return (f"'{when}' has no am/pm and was read as {s['when']} (next: {s['next']}). Tell the user the exact time "
                f"you set, and that they can say am or pm if that's wrong.")

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
                                 "prompt": {"type": "string", "description": "What the assistant should do each time"},
                                 "commands": {"type": "array", "items": {"type": "string"},
                                              "description": "Exact shell commands (beyond read-only ones) the job may run unattended, e.g. git pull"}},
                  "required": ["name", "when", "prompt"]}, self.schedule_prompt, WRITE,
                 describe=lambda a: f"Schedule “{a.get('name')}” ({a.get('when')}): {str(a.get('prompt'))[:120]}"
                                    + (f". It may run, unattended: {'; '.join(a['commands'])}" if a.get("commands") else ""),
                 ask=lambda a: True),   # always a card: it can pre-approve commands for unattended runs
            Tool("schedule_script", "Run a shell command on a schedule. Notifies on failure.",
                 {"properties": {"name": {"type": "string"}, "when": WHEN,
                                 "command": {"type": "string", "description": "Shell command to run"}},
                  "required": ["name", "when", "command"]}, self.schedule_script, DESTRUCTIVE,
                 describe=lambda a: f"Run on a schedule ({a.get('when')}): $ {a.get('command')}",
                 precheck=lambda a: None if self.ctx.config.data["scheduler"]["allow_scripts"] else
                 "Script jobs are turned off. The user can enable them in Settings → Automation."),
            Tool("list_jobs", "List scheduled jobs (prompt and script).", {}, self.list_jobs),
            Tool("update_job", "Change a reminder or job (ids from list_reminders / list_jobs): its time ('when', in plain words), "
                 "a reminder's text, a job's name, a prompt job's prompt, or a script job's command. Only pass what changes. "
                 "When the user asks to move or change one, find its id with list_reminders/list_jobs and call this straight away: "
                 "don't ask them to confirm (they'll see the result, and changing it back is one more request).",
                 {"properties": {"id": SID, "when": WHEN, "text": {"type": "string"}, "name": {"type": "string"},
                                 "prompt": {"type": "string"}, "command": {"type": "string"}}, "required": ["id"]},
                 self.update_job, WRITE, title="Update", ask=self._update_needs_card,
                 describe=lambda a: "Change item " + str(a.get("id")) + ": " + ", ".join(f"{k} → {str(v)[:120]}" for k, v in a.items() if k != "id" and v)),
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
        f = self._fmt(s)
        return {"reminder": f, "note": self._bare_hour(a["when"], f) or "Confirm this time to the user."}

    async def list_reminders(self, a):
        return [self._fmt(s) for s in self.sched.store.list("notify")] or "No reminders."

    async def cancel_reminder(self, a):
        s = self._get(a["id"], ("notify",))
        self.sched.store.delete(s["id"])
        self.sched.changed()
        return {"cancelled": s["name"]}

    async def schedule_prompt(self, a):
        cmds = [c.strip() for c in (a.get("commands") or []) if str(c).strip()][:20]
        s = self._create(name=a["name"], kind="prompt", when=a["when"], payload={"prompt": a["prompt"], "commands": cmds})
        return {"job": self._fmt(s), "note": "Runs read-only" + (f", and may run these exact shell commands: {', '.join(cmds)}" if cmds else "")
                + ". Writes can be enabled for this job in the Scheduler panel."}

    async def schedule_script(self, a):
        if not self.ctx.config.data["scheduler"]["allow_scripts"]:
            raise ToolError("Script jobs are turned off. The user can enable them in Settings → Automation.")
        s = self._create(name=a["name"], kind="script", when=a["when"], payload={"command": a["command"]})
        return {"job": self._fmt(s)}

    async def update_job(self, a):
        s = self._get(a["id"])
        payload = {}
        if a.get("text") and s["kind"] == "notify":
            payload["text"] = a["text"]
        if a.get("prompt") and s["kind"] == "prompt":
            payload["prompt"] = a["prompt"]
        if a.get("command") and s["kind"] == "script":
            payload["command"] = a["command"]
        name = a.get("name") or (a.get("text") if s["kind"] == "notify" else None)
        if not (name or a.get("when") or payload):
            raise ToolError("Say what to change: when, text (reminders), name, prompt (prompt jobs) or command (script jobs).", bad_args=True)
        try:
            s = self.sched.edit(s["id"], name=name, when=a.get("when"), payload=payload or None)
        except timeparse.ParseError as e:
            raise ToolError(str(e), bad_args=True)
        f = self._fmt(s)
        return {"updated": f, "note": self._bare_hour(a.get("when"), f)}

    def _update_needs_card(self, a: dict) -> bool:
        """Moving or renaming a reminder is like setting one (no card); changing what a job runs is not."""
        try:
            s = self._get(a.get("id"))
        except (ToolError, TypeError, ValueError):
            return False                     # the handler reports the bad id
        return s["kind"] != "notify" and bool(a.get("prompt") or a.get("command") or s["kind"] == "script")

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
            f["payload"] = {"prompt": s["payload"].get("prompt"), "command": s["payload"].get("command"), "commands": s["payload"].get("commands") or []}
            f["allow_writes"] = bool(s["allow_writes"])
            jobs.append(f)
        return JSONResponse({"reminders": reminders, "jobs": jobs, "shell_enabled": bool(self.ctx.config.data.get("shell", {}).get("agent")),
                             "scripts_enabled": self.ctx.config.data["scheduler"]["allow_scripts"]})

    async def create_item(self, request: Request):
        b = await request.json()
        kind = b.get("kind", "notify")
        try:
            if kind == "notify":
                s = self.sched.create(name=b["text"], kind="notify", when=b["when"], payload={"text": b["text"]})
            elif kind == "prompt":
                b["name"] = (b.get("name") or "").strip() or " ".join(str(b["prompt"]).split())[:50]   # never a nameless job
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
        elif action == "commands" and s["kind"] == "prompt":   # pre-approved shell commands: only the UI (or an approved card) sets them
            cmds = [c.strip() for c in str(b.get("value") or "").splitlines() if c.strip()][:20]
            self.sched.store.update(sid, payload={**s["payload"], "commands": cmds})
            self.sched.changed()
        elif action == "edit":
            payload = {}
            if s["kind"] == "notify" and b.get("text"):
                payload["text"] = b["text"]
            if s["kind"] == "prompt" and b.get("prompt"):
                payload["prompt"] = b["prompt"]
            if s["kind"] == "script" and b.get("command"):
                payload["command"] = b["command"]
            try:
                self.sched.edit(sid, name=b.get("name") or (b.get("text") if s["kind"] == "notify" else None), when=b.get("when"), payload=payload or None)
            except timeparse.ParseError as e:
                return JSONResponse({"error": str(e)}, 400)
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
