"""ctx.store: the small server-side database every file app gets (a key/value area and named tables of rows), kept per app id.

It lives in the main SQLite file, so it survives closing the panel and the browser, and it is removed together with the app. It is
plain data: it never runs code and never reaches other apps' rows, so it needs no approval.
"""
from __future__ import annotations

import json
import time

from .apps.base import ToolError

MAX_VALUE = 256_000        # bytes of JSON in one key or one row
MAX_ROWS_PER_TABLE = 20_000
MAX_BATCH = 1_000
MAX_NAME = 64
TABLE_SCHEMA = """
CREATE TABLE IF NOT EXISTS app_kv (
  app TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY (app, key));
CREATE TABLE IF NOT EXISTS app_rows (
  app TEXT NOT NULL, tbl TEXT NOT NULL, id INTEGER NOT NULL, data TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
  PRIMARY KEY (app, tbl, id));
CREATE TABLE IF NOT EXISTS app_seq (app TEXT NOT NULL, tbl TEXT NOT NULL, last INTEGER NOT NULL, PRIMARY KEY (app, tbl));
"""


def _name(value, what: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > MAX_NAME:
        raise ToolError(f"The {what} must be 1 to {MAX_NAME} characters.")
    return text


def _dump(value, what: str) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        raise ToolError(f"The {what} cannot be saved: it must be plain JSON (text, numbers, true/false, lists, objects).")
    if len(text.encode()) > MAX_VALUE:
        raise ToolError(f"The {what} is too large ({MAX_VALUE // 1000} KB at most). Split it into several rows or keys.")
    return text


def _row(r) -> dict:
    return {**json.loads(r["data"]), "id": r["id"]}


class AppStore:
    def __init__(self, db):
        self.conn = db.conn
        self.conn.executescript(TABLE_SCHEMA)

    # ---- key/value -------------------------------------------------------------
    def get(self, app: str, key: str, default=None):
        r = self.conn.execute("SELECT value FROM app_kv WHERE app=? AND key=?", (app, _name(key, "key"))).fetchone()
        return json.loads(r["value"]) if r else default

    def set(self, app: str, key: str, value) -> None:
        self.conn.execute("INSERT INTO app_kv(app, key, value) VALUES(?,?,?) ON CONFLICT(app, key) DO UPDATE SET value=excluded.value",
                          (app, _name(key, "key"), _dump(value, "value")))
        self.conn.commit()

    def keys(self, app: str) -> list[str]:
        return [r["key"] for r in self.conn.execute("SELECT key FROM app_kv WHERE app=? ORDER BY key", (app,))]

    def remove(self, app: str, key: str) -> bool:
        n = self.conn.execute("DELETE FROM app_kv WHERE app=? AND key=?", (app, _name(key, "key"))).rowcount
        self.conn.commit()
        return bool(n)

    # ---- tables ----------------------------------------------------------------
    def add_many(self, app: str, table: str, rows: list) -> list[dict]:
        """Add all rows or none. Each row is an object; it gets a number `id` (1, 2, 3... per table, never reused)."""
        table = _name(table, "table name")
        if not isinstance(rows, list) or not rows or not all(isinstance(r, dict) for r in rows):
            raise ToolError("Give a list of rows, each one an object like {\"name\": \"x\"}.")
        if len(rows) > MAX_BATCH:
            raise ToolError(f"At most {MAX_BATCH} rows in one call. Send the rest in a second call.")
        have = self.conn.execute("SELECT COUNT(*) FROM app_rows WHERE app=? AND tbl=?", (app, table)).fetchone()[0]
        seq = self.conn.execute("SELECT last FROM app_seq WHERE app=? AND tbl=?", (app, table)).fetchone()
        last = seq["last"] if seq else 0
        if have + len(rows) > MAX_ROWS_PER_TABLE:
            raise ToolError(f"The table \"{table}\" would pass {MAX_ROWS_PER_TABLE} rows. Delete old rows first.")
        now, out = time.time(), []
        with self.conn:
            for i, r in enumerate(rows, 1):
                data = {k: v for k, v in r.items() if k != "id"}
                self.conn.execute("INSERT INTO app_rows(app, tbl, id, data, created, updated) VALUES(?,?,?,?,?,?)",
                                  (app, table, last + i, _dump(data, "row"), now, now))
                out.append({**data, "id": last + i})
            self.conn.execute("INSERT INTO app_seq(app, tbl, last) VALUES(?,?,?) ON CONFLICT(app, tbl) DO UPDATE SET last=excluded.last", (app, table, last + len(rows)))
        return out

    def add(self, app: str, table: str, row: dict) -> dict:
        return self.add_many(app, table, [row])[0]

    def update_many(self, app: str, table: str, patches: list) -> list[dict]:
        """Merge each patch (an object with the row's `id` and the fields to change) into its row. A missing id fails the whole call."""
        table = _name(table, "table name")
        if not isinstance(patches, list) or not patches or not all(isinstance(p, dict) and isinstance(p.get("id"), int) for p in patches):
            raise ToolError("Give a list of objects that each have the row's number id, like {\"id\": 3, \"done\": true}.")
        if len(patches) > MAX_BATCH:
            raise ToolError(f"At most {MAX_BATCH} rows in one call.")
        out, now = [], time.time()
        with self.conn:
            for p in patches:
                r = self.conn.execute("SELECT * FROM app_rows WHERE app=? AND tbl=? AND id=?", (app, table, p["id"])).fetchone()
                if r is None:
                    raise ToolError(f"The table \"{table}\" has no row {p['id']}. Nothing was changed.")
                data = {**json.loads(r["data"]), **{k: v for k, v in p.items() if k != "id"}}
                self.conn.execute("UPDATE app_rows SET data=?, updated=? WHERE app=? AND tbl=? AND id=?", (_dump(data, "row"), now, app, table, p["id"]))
                out.append({**data, "id": p["id"]})
        return out

    def update(self, app: str, table: str, row_id: int, patch: dict) -> dict:
        if not isinstance(patch, dict):
            raise ToolError("The change must be an object like {\"done\": true}.")
        return self.update_many(app, table, [{**patch, "id": row_id}])[0]

    def delete(self, app: str, table: str, ids) -> int:
        """Delete one row (a number) or several (a list of numbers). Returns how many were removed. Deleting a whole table: pass ids=\"all\"."""
        table = _name(table, "table name")
        with self.conn:
            if ids == "all":
                return self.conn.execute("DELETE FROM app_rows WHERE app=? AND tbl=?", (app, table)).rowcount
            ids = ids if isinstance(ids, list) else [ids]
            if not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
                raise ToolError("The ids must be numbers, or \"all\" to empty the table.")
            return sum(self.conn.execute("DELETE FROM app_rows WHERE app=? AND tbl=? AND id=?", (app, table, i)).rowcount for i in ids)

    def query(self, app: str, table: str, where: dict | None = None, order: str = "", limit: int = 200, offset: int = 0) -> list[dict]:
        """Rows whose fields equal every value in `where`, sorted by a field (`"-due"` for descending; default: oldest first)."""
        table = _name(table, "table name")
        if where is not None and not isinstance(where, dict):
            raise ToolError("where must be an object like {\"done\": false}.")
        rows = [_row(r) for r in self.conn.execute("SELECT id, data FROM app_rows WHERE app=? AND tbl=? ORDER BY id", (app, table))]
        for k, v in (where or {}).items():
            rows = [r for r in rows if r.get(k) == v]
        field = str(order or "").lstrip("-")
        if field:
            def key(r):
                v = r.get(field)
                return (v is None, isinstance(v, str), v if isinstance(v, (int, float, str)) and not isinstance(v, bool) else str(v))
            have = [r for r in rows if r.get(field) is not None]
            rows = sorted(have, key=key, reverse=order.startswith("-")) + [r for r in rows if r.get(field) is None]
        return rows[max(0, int(offset)):max(0, int(offset)) + max(1, min(int(limit), 1000))]

    def tables(self, app: str) -> dict[str, int]:
        return {r["tbl"]: r["n"] for r in self.conn.execute("SELECT tbl, COUNT(*) n FROM app_rows WHERE app=? GROUP BY tbl ORDER BY tbl", (app,))}

    def drop_app(self, app: str) -> None:
        """Everything the app stored goes when the app is deleted."""
        with self.conn:
            self.conn.execute("DELETE FROM app_kv WHERE app=?", (app,))
            self.conn.execute("DELETE FROM app_rows WHERE app=?", (app,))
            self.conn.execute("DELETE FROM app_seq WHERE app=?", (app,))
