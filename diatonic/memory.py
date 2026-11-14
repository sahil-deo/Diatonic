"""Small, bounded, cross-chat memory.

Two kinds of entries, both short:
  fact    something about the user or their setup ("my default repo is sahil-deo/Mid-Map"),
          scope `global` or `app:<id>`. Added to the system prompt, within a strict budget.
  lesson  a tool quirk the harness observed ("github_list_issues: per_page=…"),
          scope `tool:<name>`. Appended to that one tool's description, so it costs nothing
          unless the tool is actually offered.

Everything is capped, de-duplicated and decays, so it can't grow into a burden on a small model.
"""
from __future__ import annotations

import difflib
import re
import time

MAX_TEXT = 200
MAX_TOTAL = 80          # rows overall
MAX_FACTS = 30          # user/agent facts
DECAY_DAYS = 90         # auto-learned lessons nobody used for this long are forgotten
_NORM = re.compile(r"[\W_]+")


def _norm(s: str) -> str:
    return _NORM.sub(" ", s.lower()).strip()


def valid_scope(scope: str, app_ids: set[str] | None = None) -> bool:
    if scope == "global":
        return True
    kind, _, name = scope.partition(":")
    return kind in ("app", "tool") and bool(name) and (app_ids is None or kind == "tool" or name in app_ids)


class MemoryStore:
    def __init__(self, db, config):
        self.c, self.config = db.conn, config

    @property
    def cfg(self) -> dict:
        return self.config.data["memory"]

    # ---- writing -------------------------------------------------------
    def add(self, text: str, *, scope: str = "global", kind: str = "fact", source: str = "user",
            key: str | None = None, pinned: bool = False) -> tuple[dict, str]:
        """Returns (row, status) where status is created | duplicate | updated. Raises ValueError if refused."""
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("Nothing to remember.")
        if len(text) > MAX_TEXT:
            raise ValueError(f"Keep it under {MAX_TEXT} characters; memories should be short facts.")
        if not valid_scope(scope):
            raise ValueError(f"Bad scope '{scope}'. Use global, app:<id> or tool:<name>.")
        now = time.time()
        if key:
            r = self.c.execute("SELECT * FROM memories WHERE key=? AND scope=?", (key, scope)).fetchone()
            if r:
                self.c.execute("UPDATE memories SET text=?, uses=uses+1, updated_at=? WHERE id=?", (text, now, r["id"]))
                self.c.commit()
                return self.get(r["id"]), "updated"
        for r in ([] if key else self.c.execute("SELECT * FROM memories WHERE scope=? AND kind=?", (scope, kind))):  # a key is the identity
            a, b = _norm(r["text"]), _norm(text)
            if a == b or difflib.SequenceMatcher(None, a, b).ratio() >= 0.9:
                self.c.execute("UPDATE memories SET updated_at=?, pinned=MAX(pinned,?) WHERE id=?", (now, int(pinned), r["id"]))
                self.c.commit()
                return self.get(r["id"]), "duplicate"
        if kind == "fact" and self.c.execute("SELECT COUNT(*) n FROM memories WHERE kind='fact'").fetchone()["n"] >= MAX_FACTS:
            raise ValueError(f"Memory is full ({MAX_FACTS} facts). Delete one in Settings → Memory first.")
        cur = self.c.execute(
            "INSERT INTO memories (kind, scope, key, text, source, pinned, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (kind, scope, key, text, source, int(pinned), now, now))
        self.c.commit()
        self._evict()
        return self.get(cur.lastrowid), "created"

    def update(self, mid: int, **f) -> dict | None:
        allowed = {k: v for k, v in f.items() if k in ("text", "scope", "pinned", "enabled")}
        if "text" in allowed:
            allowed["text"] = re.sub(r"\s+", " ", str(allowed["text"])).strip()[:MAX_TEXT]
            if not allowed["text"]:
                raise ValueError("Text can't be empty.")
        if "scope" in allowed and not valid_scope(allowed["scope"]):
            raise ValueError("Bad scope.")
        for k in ("pinned", "enabled"):
            if k in allowed:
                allowed[k] = int(bool(allowed[k]))
        if allowed:
            sets = ", ".join(f"{k}=?" for k in allowed)
            self.c.execute(f"UPDATE memories SET {sets}, updated_at=? WHERE id=?", (*allowed.values(), time.time(), mid))
            self.c.commit()
        return self.get(mid)

    def delete(self, mid: int) -> bool:
        n = self.c.execute("DELETE FROM memories WHERE id=?", (mid,)).rowcount
        self.c.commit()
        return bool(n)

    def forget_matching(self, text: str) -> dict | None:
        """Delete the single best match for `text` (by id, substring or similarity)."""
        q = _norm(text)
        best, score = None, 0.0
        for r in self.c.execute("SELECT * FROM memories WHERE kind='fact'"):
            n = _norm(r["text"])
            s = 1.0 if q and q in n else difflib.SequenceMatcher(None, q, n).ratio()
            if s > score:
                best, score = r, s
        if best is None or score < 0.5:
            return None
        row = dict(best)
        self.delete(best["id"])
        return row

    def _evict(self) -> None:
        """Keep the table bounded: drop the least valuable unpinned rows, lessons before facts."""
        n = self.c.execute("SELECT COUNT(*) n FROM memories").fetchone()["n"]
        if n <= MAX_TOTAL:
            return
        extra = n - MAX_TOTAL
        ids = [r["id"] for r in self.c.execute(
            "SELECT id FROM memories WHERE pinned=0 ORDER BY (kind='fact'), uses, updated_at LIMIT ?", (extra,))]
        self.c.executemany("DELETE FROM memories WHERE id=?", [(i,) for i in ids])
        self.c.commit()

    def prune(self) -> int:
        """Forget auto-learned lessons that haven't been touched for a long time."""
        cutoff = time.time() - DECAY_DAYS * 86400
        n = self.c.execute("DELETE FROM memories WHERE source='auto' AND pinned=0 AND COALESCE(last_used, updated_at) < ?",
                           (cutoff,)).rowcount
        self.c.commit()
        return n

    # ---- reading -------------------------------------------------------
    def get(self, mid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM memories WHERE id=?", (mid,)).fetchone()
        return dict(r) if r else None

    def all(self) -> list[dict]:
        rows = self.c.execute("SELECT * FROM memories ORDER BY pinned DESC, (kind='lesson'), updated_at DESC")
        return [dict(r) for r in rows]

    def relevant(self, app_ids: set[str]) -> list[dict]:
        """Facts worth adding to this request's prompt: global plus those for the apps in play, within budget."""
        if not self.cfg["enabled"]:
            return []
        scopes = ["global", *(f"app:{a}" for a in sorted(app_ids))]
        marks = ",".join("?" * len(scopes))
        rows = self.c.execute(
            f"SELECT * FROM memories WHERE kind='fact' AND enabled=1 AND scope IN ({marks}) "
            "ORDER BY pinned DESC, (scope='global') DESC, updated_at DESC", scopes).fetchall()
        out, used = [], 0
        for r in rows:
            if len(out) >= int(self.cfg["max_items"]) or (out and used + len(r["text"]) > int(self.cfg["budget_chars"])):
                break
            out.append(dict(r))
            used += len(r["text"])
        if out:
            now = time.time()
            self.c.executemany("UPDATE memories SET uses=uses+1, last_used=? WHERE id=?", [(now, r["id"]) for r in out])
            self.c.commit()
        return out

    def tool_notes(self, tool_name: str, limit: int = 2) -> list[str]:
        if not self.cfg["enabled"]:
            return []
        rows = self.c.execute("SELECT id, text FROM memories WHERE kind='lesson' AND enabled=1 AND scope=? "
                              "ORDER BY pinned DESC, uses DESC, updated_at DESC LIMIT ?", (f"tool:{tool_name}", limit)).fetchall()
        if rows:
            self.c.executemany("UPDATE memories SET last_used=? WHERE id=?", [(time.time(), r["id"]) for r in rows])
            self.c.commit()
        return [r["text"] for r in rows]


def facts_block(facts: list[dict]) -> str:
    return ("\n\nKnown about the user (trust these):\n" + "\n".join(f"- {f['text']}" for f in facts)) if facts else ""


class LessonTracker:
    """Per run: notices 'rejected arguments, then the same tool succeeded with a different value'.

    Deterministic on purpose: no model call, and only numeric / boolean / enum parameters can become
    lessons, so a lesson can never smuggle in free text from a web page or an issue.
    """

    def __init__(self):
        self.failed: dict[str, list[tuple[dict, str]]] = {}

    def on_failure(self, name: str, args: dict, error: str) -> None:
        self.failed.setdefault(name, []).append((dict(args), error))

    def on_success(self, name: str, args: dict, schema: dict, title: str) -> tuple[str, str] | None:
        props = schema.get("properties", {})
        best = None
        for old, err in self.failed.get(name, []):
            changed = [k for k in props if k in old and k in args and old[k] != args[k]
                       and (props[k].get("type") in ("integer", "number", "boolean") or "enum" in props[k])]
            others = [k for k in set(old) | set(args) if k not in changed and old.get(k) != args.get(k)]
            if 1 <= len(changed) <= 2 and not others and (best is None or len(changed) < len(best[0])):
                best = (changed, old, err)
        if not best:
            return None
        changed, old, err = best
        why = re.sub(r"^Error:\s*", "", err).split("\n")[0][:90]
        parts = "; ".join(f"{k}={old[k]!r} was rejected, {k}={args[k]!r} works" for k in changed)
        self.failed[name] = []
        return f"{title}: {parts} ({why}).", f"{name}:{','.join(sorted(changed))}"
