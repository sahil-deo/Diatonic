"""A simple to-do list with due dates, priorities and optional reminders."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...scheduler import timeparse
from ..base import WRITE, App, Tool, ToolError

PRIORITY = {"none": 0, "low": 1, "medium": 2, "high": 3}
PRIORITY_NAME = {v: k for k, v in PRIORITY.items()}
TID = {"type": "integer", "description": "To-do id from list_todos"}


class TodosApp(App):
    id = "todos"
    name = "To-dos"
    icon = "check-square"
    description = "A checklist the agent can add to, tick off and review."
    panel = True
    keywords = ["todo", "todos", "to-do", "to-dos", "task", "tasks", "checklist", "to do", "to dos"]
    default_allow = ["add_todo", "complete_todo", "update_todo", "reopen_todo"]

    @property
    def c(self):
        return self.ctx.db.conn

    # ---- helpers -------------------------------------------------------
    def _due(self, when: str | None) -> float | None:
        if not when:
            return None
        try:
            spec = timeparse.parse_schedule(when, self.ctx.now())
        except timeparse.ParseError as e:
            raise ToolError(str(e))
        if spec["type"] != "at":
            raise ToolError("A due date must be a single date/time, not a repeating schedule.")
        return datetime.fromisoformat(spec["at"]).timestamp()

    async def snapshot(self, n: int) -> dict:
        rows = list(self.c.execute("SELECT * FROM todos WHERE status='open' ORDER BY (due IS NULL), due, priority DESC, id"))
        overdue = sum(1 for r in rows if r["due"] and r["due"] < time.time())
        items = [self._fmt(r) for r in rows[:n]]
        return {"items": [{"text": t["title"], "sub": t.get("due"), "tone": "warn" if t.get("overdue") else None,
                           "badge": t["priority"] if t["priority"] not in ("normal", "none", None) else None} for t in items],
                "stats": [{"label": "open", "value": len(rows)}] + ([{"label": "overdue", "value": overdue}] if overdue else []),
                "empty": "Nothing to do. Nice."}

    def _fmt(self, r) -> dict:
        d = {"id": r["id"], "title": r["title"], "status": r["status"], "priority": PRIORITY_NAME[r["priority"]]}
        if r["due"]:
            due = datetime.fromtimestamp(r["due"], self.ctx.tz)
            d["due"] = timeparse.human(due, self.ctx.now())
            d["due_iso"] = due.isoformat()
            d["overdue"] = r["status"] == "open" and r["due"] < time.time() or None
        if r["notes"]:
            d["notes"] = r["notes"]
        return d

    def _get(self, tid: int):
        r = self.c.execute("SELECT * FROM todos WHERE id=?", (int(tid),)).fetchone()
        if not r:
            raise ToolError(f"No to-do with id {tid}.")
        return r

    def _changed(self) -> None:
        self.ctx.broadcast({"type": "app_changed", "app": "todos"})

    def _attach_reminder(self, tid: int, title: str, due: float) -> int | None:
        if not self.ctx.scheduler or due <= time.time():
            return None
        s = self.ctx.scheduler.create(name=f"To-do: {title}", kind="notify", when=datetime.fromtimestamp(due, self.ctx.tz).isoformat(),
                                      payload={"text": f"To-do due: {title}"}, created_by="agent")
        return s["id"]

    # ---- tools ---------------------------------------------------------
    def tools(self) -> list[Tool]:
        return [
            Tool("add_todo", "Add a to-do. 'due' is optional natural language. Set remind=true to be notified at the due time.",
                 {"properties": {"title": {"type": "string"}, "due": {"type": "string", "description": "e.g. 'tomorrow 5pm', 'friday'"},
                                 "priority": {"type": "string", "enum": list(PRIORITY)}, "notes": {"type": "string"},
                                 "remind": {"type": "boolean"}}, "required": ["title"]}, self.add_todo, WRITE,
                 describe=lambda a: f"Add to-do “{a.get('title')}”" + (f" due {a['due']}" if a.get("due") else "")),
            Tool("list_todos", "List to-dos. status: open (default), done or all. due: today, overdue, week or all.",
                 {"properties": {"status": {"type": "string", "enum": ["open", "done", "all"]},
                                 "due": {"type": "string", "enum": ["all", "today", "overdue", "week"]},
                                 "limit": {"type": "integer", "minimum": 1, "maximum": 50}}}, self.list_todos),
            Tool("complete_todo", "Mark a to-do done.", {"properties": {"id": TID}, "required": ["id"]}, self.complete_todo, WRITE,
                 describe=lambda a: f"Complete to-do {a.get('id')}"),
            Tool("reopen_todo", "Mark a to-do not done.", {"properties": {"id": TID}, "required": ["id"]}, self.reopen_todo, WRITE),
            Tool("update_todo", "Change a to-do's title, due date, priority or notes.",
                 {"properties": {"id": TID, "title": {"type": "string"}, "due": {"type": "string"},
                                 "priority": {"type": "string", "enum": list(PRIORITY)}, "notes": {"type": "string"}},
                  "required": ["id"]}, self.update_todo, WRITE,
                 describe=lambda a: f"Update to-do {a.get('id')}: " + ", ".join(f"{k}={v}" for k, v in a.items() if k != "id")),
            Tool("delete_todo", "Delete a to-do.", {"properties": {"id": TID}, "required": ["id"]}, self.delete_todo, WRITE,
                 describe=lambda a: f"Delete to-do {a.get('id')}"),
        ]

    async def add_todo(self, a):
        due = self._due(a.get("due"))
        cur = self.c.execute("INSERT INTO todos (title, notes, due, priority, created_at) VALUES (?,?,?,?,?)",
                             (a["title"].strip(), a.get("notes", ""), due, PRIORITY.get(a.get("priority", "none"), 0), time.time()))
        tid = cur.lastrowid
        if a.get("remind") and due:
            rid = self._attach_reminder(tid, a["title"], due)
            self.c.execute("UPDATE todos SET reminder_id=? WHERE id=?", (rid, tid))
        self.c.commit()
        self._changed()
        return {"added": self._fmt(self._get(tid)), "reminder": bool(a.get("remind") and due) or None}

    async def list_todos(self, a):
        q, args = "SELECT * FROM todos WHERE 1=1", []
        status = a.get("status", "open")
        if status != "all":
            q += " AND status=?"
            args.append(status)
        now = self.ctx.now()
        end_today = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=now.tzinfo).timestamp()
        due = a.get("due", "all")
        if due == "today":
            q += " AND due IS NOT NULL AND due<?"
            args.append(end_today)
        elif due == "overdue":
            q += " AND due IS NOT NULL AND due<?"
            args.append(time.time())
        elif due == "week":
            q += " AND due IS NOT NULL AND due<?"
            args.append(end_today + 6 * 86400)
        q += " ORDER BY (due IS NULL), due, priority DESC, id LIMIT ?"
        args.append(a.get("limit", 25))
        return [self._fmt(r) for r in self.c.execute(q, args)] or "No matching to-dos."

    async def complete_todo(self, a):
        r = self._get(a["id"])
        self.c.execute("UPDATE todos SET status='done', completed_at=? WHERE id=?", (time.time(), r["id"]))
        if r["reminder_id"] and self.ctx.scheduler:  # a finished to-do shouldn't nag
            self.ctx.scheduler.store.delete(r["reminder_id"])
            self.ctx.scheduler.changed()
        self.c.commit()
        self._changed()
        return {"completed": r["title"]}

    async def reopen_todo(self, a):
        r = self._get(a["id"])
        self.c.execute("UPDATE todos SET status='open', completed_at=NULL WHERE id=?", (r["id"],))
        self.c.commit()
        self._changed()
        return {"reopened": r["title"]}

    async def update_todo(self, a):
        r = self._get(a["id"])
        sets, args = [], []
        if a.get("title"):
            sets.append("title=?"); args.append(a["title"].strip())
        if "due" in a:
            sets.append("due=?"); args.append(self._due(a["due"]))
        if a.get("priority"):
            sets.append("priority=?"); args.append(PRIORITY.get(a["priority"], 0))
        if "notes" in a:
            sets.append("notes=?"); args.append(a["notes"])
        if not sets:
            raise ToolError("Nothing to change.")
        self.c.execute(f"UPDATE todos SET {', '.join(sets)} WHERE id=?", (*args, r["id"]))
        self.c.commit()
        self._changed()
        return {"updated": self._fmt(self._get(r["id"]))}

    async def delete_todo(self, a):
        r = self._get(a["id"])
        if r["reminder_id"] and self.ctx.scheduler:
            self.ctx.scheduler.store.delete(r["reminder_id"])
            self.ctx.scheduler.changed()
        self.c.execute("DELETE FROM todos WHERE id=?", (r["id"],))
        self.c.commit()
        self._changed()
        return {"deleted": r["title"]}

    # ---- panel routes ----------------------------------------------------
    async def panel_data(self, request: Request):
        show_done = request.query_params.get("done") == "1"
        rows = await self.list_todos({"status": "all" if show_done else "open", "limit": 200})
        return JSONResponse({"todos": rows if isinstance(rows, list) else []})

    async def create_item(self, request: Request):
        b = await request.json()
        try:
            return JSONResponse(await self.add_todo({k: v for k, v in b.items() if v not in ("", None)}))
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def act(self, request: Request):
        b = await request.json()
        fn = {"complete": self.complete_todo, "reopen": self.reopen_todo, "delete": self.delete_todo,
              "update": self.update_todo}.get(b.get("action"))
        if not fn:
            return JSONResponse({"error": "Unknown action"}, 400)
        try:
            return JSONResponse(await fn({k: v for k, v in b.items() if k != "action"}))
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    def routes(self):
        return [Route("/panel", self.panel_data), Route("/create", self.create_item, methods=["POST"]),
                Route("/action", self.act, methods=["POST"])]
