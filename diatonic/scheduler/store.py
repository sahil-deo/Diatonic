"""Persistence for schedules, their run history and the notification inbox."""
from __future__ import annotations

import json
import time


def _row(r) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in ("spec", "payload", "data"):
        if k in d:
            d[k] = json.loads(d[k])
    return d


class Store:
    def __init__(self, db):
        self.c = db.conn

    # ---- schedules -----------------------------------------------------
    def add(self, *, name: str, kind: str, spec: dict, payload: dict, next_run: float | None,
            allow_writes: bool = False, created_by: str = "user") -> dict:
        cur = self.c.execute(
            "INSERT INTO schedules (name, kind, spec, payload, next_run, allow_writes, created_by, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (name, kind, json.dumps(spec), json.dumps(payload), next_run, int(allow_writes), created_by, time.time()))
        self.c.commit()
        return self.get(cur.lastrowid)

    def get(self, sid: int) -> dict | None:
        return _row(self.c.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone())

    def list(self, kind: str | None = None, states: tuple[str, ...] = ("active", "paused")) -> list[dict]:
        q = f"SELECT * FROM schedules WHERE state IN ({','.join('?' * len(states))})"
        args: list = list(states)
        if kind == "job":
            q += " AND kind != 'notify'"
        elif kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY (next_run IS NULL), next_run, id"
        return [_row(r) for r in self.c.execute(q, args).fetchall()]

    def update(self, sid: int, **fields) -> None:
        for k in ("spec", "payload"):
            if k in fields:
                fields[k] = json.dumps(fields[k])
        sets = ", ".join(f"{k}=?" for k in fields)
        self.c.execute(f"UPDATE schedules SET {sets} WHERE id=?", (*fields.values(), sid))
        self.c.commit()

    def delete(self, sid: int) -> None:
        self.c.execute("DELETE FROM schedule_runs WHERE schedule_id=?", (sid,))
        self.c.execute("DELETE FROM schedules WHERE id=?", (sid,))
        self.c.commit()

    def due(self, now: float) -> list[dict]:
        rows = self.c.execute("SELECT * FROM schedules WHERE state='active' AND next_run IS NOT NULL AND next_run<=?"
                              " ORDER BY next_run", (now,)).fetchall()
        return [_row(r) for r in rows]

    def next_due_time(self) -> float | None:
        r = self.c.execute("SELECT MIN(next_run) m FROM schedules WHERE state='active' AND next_run IS NOT NULL").fetchone()
        return r["m"]

    # ---- runs ----------------------------------------------------------
    def start_run(self, sid: int, late: bool = False) -> int:
        cur = self.c.execute("INSERT INTO schedule_runs (schedule_id, started, status, late) VALUES (?,?,?,?)",
                             (sid, time.time(), "running", int(late)))
        self.c.commit()
        return cur.lastrowid

    def finish_run(self, rid: int, sid: int, status: str, output: str = "", conversation_id: str | None = None) -> None:
        now = time.time()
        self.c.execute("UPDATE schedule_runs SET finished=?, status=?, output=?, conversation_id=? WHERE id=?",
                       (now, status, output[-20000:], conversation_id, rid))
        self.c.execute("UPDATE schedules SET last_run=?, last_status=? WHERE id=?", (now, status, sid))
        self.c.execute("DELETE FROM schedule_runs WHERE schedule_id=? AND id NOT IN "
                       "(SELECT id FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT 50)", (sid, sid))
        self.c.commit()

    def runs(self, sid: int, limit: int = 10) -> list[dict]:
        rows = self.c.execute("SELECT * FROM schedule_runs WHERE schedule_id=? ORDER BY id DESC LIMIT ?", (sid, limit))
        return [dict(r) for r in rows]

    def mark_stale_runs(self) -> None:
        """Runs left 'running' by a crash or restart can't still be running."""
        self.c.execute("UPDATE schedule_runs SET status='interrupted', finished=? WHERE status='running'", (time.time(),))
        self.c.commit()

    # ---- notifications -------------------------------------------------
    def add_notification(self, title: str, body: str, kind: str, data: dict) -> dict:
        now = time.time()
        cur = self.c.execute("INSERT INTO notifications (title, body, kind, data, created_at) VALUES (?,?,?,?,?)",
                             (title, body, kind, json.dumps(data), now))
        self.c.execute("DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT 200)")
        self.c.commit()
        return _row(self.c.execute("SELECT * FROM notifications WHERE id=?", (cur.lastrowid,)).fetchone())

    def notifications(self, limit: int = 50) -> list[dict]:
        return [_row(r) for r in self.c.execute("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]

    def notification(self, nid: int) -> dict | None:
        return _row(self.c.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone())

    def mark_read(self, nid: int | None = None) -> None:
        if nid is None:
            self.c.execute("UPDATE notifications SET read=1")
        else:
            self.c.execute("UPDATE notifications SET read=1 WHERE id=?", (nid,))
        self.c.commit()

    def delete_notification(self, nid: int | None = None) -> None:
        self.c.execute("DELETE FROM notifications" + ("" if nid is None else " WHERE id=?"), () if nid is None else (nid,))
        self.c.commit()

    def unread_count(self) -> int:
        return self.c.execute("SELECT COUNT(*) n FROM notifications WHERE read=0").fetchone()["n"]
