"""SQLite storage for conversations and messages."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path


class Database:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY, title TEXT NOT NULL,
              created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
              role TEXT NOT NULL, content TEXT NOT NULL, meta TEXT NOT NULL DEFAULT '{}',
              created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id, id);
            CREATE TABLE IF NOT EXISTS schedules (
              id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
              kind TEXT NOT NULL,                      -- notify | prompt | script
              spec TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}',
              state TEXT NOT NULL DEFAULT 'active',    -- active | paused | done
              next_run REAL, last_run REAL, last_status TEXT,
              allow_writes INTEGER NOT NULL DEFAULT 0, created_by TEXT NOT NULL DEFAULT 'user',
              created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_sched_due ON schedules(state, next_run);
            CREATE TABLE IF NOT EXISTS schedule_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL,
              started REAL NOT NULL, finished REAL, status TEXT NOT NULL,
              output TEXT NOT NULL DEFAULT '', conversation_id TEXT, late INTEGER NOT NULL DEFAULT 0);
            CREATE INDEX IF NOT EXISTS idx_runs_sched ON schedule_runs(schedule_id, id);
            CREATE TABLE IF NOT EXISTS notifications (
              id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
              kind TEXT NOT NULL DEFAULT 'info', data TEXT NOT NULL DEFAULT '{}',
              created_at REAL NOT NULL, read INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS todos (
              id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
              due REAL, priority INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'open',
              reminder_id INTEGER, created_at REAL NOT NULL, completed_at REAL);
            CREATE TABLE IF NOT EXISTS memories (
              id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,   -- fact | lesson
              scope TEXT NOT NULL,            -- global | app:<id> | tool:<app>_<tool>
              key TEXT, text TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'user',   -- user | agent | auto
              pinned INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, uses INTEGER NOT NULL DEFAULT 0,
              created_at REAL NOT NULL, updated_at REAL NOT NULL, last_used REAL);
            CREATE INDEX IF NOT EXISTS idx_mem_scope ON memories(scope, kind);
            CREATE TABLE IF NOT EXISTS telemetry_runs (
              id INTEGER PRIMARY KEY AUTOINCREMENT, started_at REAL NOT NULL, conv_id TEXT, source TEXT NOT NULL DEFAULT 'chat',
              model TEXT, request TEXT, apps TEXT NOT NULL DEFAULT '[]', tools_offered INTEGER NOT NULL DEFAULT 0,
              status TEXT NOT NULL, error TEXT, total_ms INTEGER, ttft_ms INTEGER, model_ms INTEGER, prefill_ms INTEGER,
              gen_ms INTEGER, load_ms INTEGER, tool_ms INTEGER, model_calls INTEGER, tool_calls INTEGER, tool_errors INTEGER,
              prompt_tokens INTEGER, completion_tokens INTEGER, answer_chars INTEGER,
              guards TEXT NOT NULL DEFAULT '[]', steps TEXT NOT NULL DEFAULT '[]');
            CREATE INDEX IF NOT EXISTS idx_tel_started ON telemetry_runs(started_at);
            CREATE TABLE IF NOT EXISTS shell_log (      -- every command the agent ran or tried (the Terminal panel's History)
              id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, source TEXT NOT NULL, conv_id TEXT, cwd TEXT,
              command TEXT NOT NULL, decision TEXT NOT NULL, exit_code INTEGER, seconds REAL, output TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS app_items (     -- rows of the data apps the agent builds (one table for all of them)
              id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT NOT NULL, list TEXT NOT NULL, parent INTEGER,
              data TEXT NOT NULL DEFAULT '{}', pos REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_items_app ON app_items(app, list);
            PRAGMA foreign_keys = ON;
            """
        )

    # ---- shell log -----------------------------------------------------------
    def log_shell(self, **row) -> None:
        cols = ("at", "source", "conv_id", "cwd", "command", "decision", "exit_code", "seconds", "output")
        row = {"at": time.time(), "output": "", **row}
        self.conn.execute(f"INSERT INTO shell_log ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(row.get(c) for c in cols))
        self.conn.execute("DELETE FROM shell_log WHERE id NOT IN (SELECT id FROM shell_log ORDER BY id DESC LIMIT 2000)")
        self.conn.commit()

    def shell_log(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM shell_log ORDER BY id DESC LIMIT ?", (limit,))]

    # ---- data apps ---------------------------------------------------------
    def items(self, app: str, lst: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM app_items WHERE app=?", [app]
        if lst:
            q += " AND list=?"; args.append(lst)
        return [{**dict(r), "data": json.loads(r["data"])} for r in self.conn.execute(q + " ORDER BY pos, id", args)]

    def item(self, app: str, iid: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM app_items WHERE app=? AND id=?", (app, iid)).fetchone()
        return {**dict(r), "data": json.loads(r["data"])} if r else None

    def add_item(self, app: str, lst: str, data: dict, parent: int | None = None) -> dict:
        now = time.time()
        cur = self.conn.execute("INSERT INTO app_items (app, list, parent, data, pos, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
                                (app, lst, parent, json.dumps(data), now, now, now))
        self.conn.commit()
        return self.item(app, cur.lastrowid)

    def update_item(self, app: str, iid: int, data: dict) -> dict | None:
        self.conn.execute("UPDATE app_items SET data=?, updated_at=? WHERE app=? AND id=?", (json.dumps(data), time.time(), app, iid))
        self.conn.commit()
        return self.item(app, iid)

    def delete_item(self, app: str, iid: int) -> int:
        """Delete an item and everything under it. Returns how many rows went."""
        ids, todo = [], [iid]
        while todo:
            ids += todo
            todo = [r["id"] for r in self.conn.execute(f"SELECT id FROM app_items WHERE app=? AND parent IN ({','.join('?' * len(todo))})", (app, *todo))]
        self.conn.execute(f"DELETE FROM app_items WHERE app=? AND id IN ({','.join('?' * len(ids))})", (app, *ids))
        self.conn.commit()
        return len(ids)

    def create_conversation(self, title: str = "New chat") -> dict:
        cid, now = uuid.uuid4().hex[:12], time.time()
        self.conn.execute("INSERT INTO conversations VALUES (?,?,?,?)", (cid, title, now, now))
        self.conn.commit()
        return {"id": cid, "title": title, "created_at": now, "updated_at": now}

    def get_conversation(self, cid: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM conversations WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None

    def list_conversations(self, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def rename(self, cid: str, title: str) -> None:
        self.conn.execute("UPDATE conversations SET title=? WHERE id=?", (title[:120], cid))
        self.conn.commit()

    def delete_conversation(self, cid: str) -> None:
        self.conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
        self.conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
        self.conn.commit()

    def add_message(self, cid: str, role: str, content: str, meta: dict | None = None) -> None:
        now = time.time()
        self.conn.execute(
            "INSERT INTO messages (conversation_id, role, content, meta, created_at) VALUES (?,?,?,?,?)",
            (cid, role, content, json.dumps(meta or {}), now),
        )
        self.conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now, cid))
        self.conn.commit()

    def messages(self, cid: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT role, content, meta, created_at FROM messages WHERE conversation_id=? ORDER BY id",
            (cid,),
        ).fetchall()
        return [{**dict(r), "meta": json.loads(r["meta"])} for r in rows]

    def history(self, cid: str, limit: int = 20) -> list[dict]:
        """Recent user/assistant turns (with their meta, so the loop can replay recent tool traces)."""
        rows = self.conn.execute(
            "SELECT role, content, meta FROM messages WHERE conversation_id=? AND content!='' "
            "ORDER BY id DESC LIMIT ?",
            (cid, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"], "meta": json.loads(r["meta"])} for r in reversed(rows)]

    def tools_used(self, cid: str) -> set[str]:
        used: set[str] = set()
        for r in self.conn.execute("SELECT meta FROM messages WHERE conversation_id=? AND role='assistant'", (cid,)):
            used.update(t["name"] for t in json.loads(r["meta"]).get("tools", []) if t.get("name"))
        return used

    def apps_used(self, cid: str) -> set[str]:
        used: set[str] = set()
        for r in self.conn.execute("SELECT meta FROM messages WHERE conversation_id=? AND role='assistant'", (cid,)):
            for t in json.loads(r["meta"]).get("tools", []):
                if t.get("app"):
                    used.add(t["app"])
        return used
