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
            PRAGMA foreign_keys = ON;
            """
        )

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

    def apps_used(self, cid: str) -> set[str]:
        used: set[str] = set()
        for r in self.conn.execute("SELECT meta FROM messages WHERE conversation_id=? AND role='assistant'", (cid,)):
            for t in json.loads(r["meta"]).get("tools", []):
                if t.get("app"):
                    used.add(t["app"])
        return used
