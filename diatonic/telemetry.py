"""Observability for agent runs.

Cost model, so this stays cheap on a slow machine:
  * during a run a `Trace` only appends small dicts to a list and reads the monotonic clock;
  * token counts and durations come free from Ollama's final stream chunk (no extra calls);
  * ONE INSERT per run, at the end, in a try/except so a telemetry fault can never affect a run;
  * nothing is computed until someone opens the panel; old rows are pruned in batches.
"""
from __future__ import annotations

import json
import statistics
import time

STEP_CAP = 40
KEEP_ROWS = 5000
PRUNE_EVERY = 50


class NullTrace:
    """Used when telemetry is off: every method is a no-op, so the loop needs no conditionals."""

    def routing(self, *a, **k): pass
    def model(self, *a, **k): pass
    def tool(self, *a, **k): pass
    def guard(self, *a, **k): pass
    def finish(self, *a, **k): return {}


class Trace:
    __slots__ = ("store", "t0", "started", "conv_id", "request", "source", "model_name", "apps", "tools_offered",
                 "steps", "guards", "n_model", "n_tool", "n_tool_err")

    def __init__(self, store: "Telemetry", conv_id: str, request: str, source: str, model: str):
        self.store, self.t0, self.started = store, time.monotonic(), time.time()
        self.conv_id, self.source, self.model_name = conv_id, source, model
        self.request = request[:160] if store.cfg["store_text"] else ""
        self.apps, self.tools_offered, self.steps, self.guards = [], 0, [], []
        self.n_model = self.n_tool = self.n_tool_err = 0

    def routing(self, apps: list[str], tools_offered: int) -> None:
        self.apps, self.tools_offered = apps, tools_offered

    def model(self, ms: float, usage: dict | None, ttft_ms: float | None, looped: bool = False) -> None:
        u = usage or {}
        self.n_model += 1
        if len(self.steps) < STEP_CAP:
            self.steps.append({"k": "model", "ms": int(ms), "ttft": None if ttft_ms is None else int(ttft_ms),
                               "p": u.get("prompt", 0), "c": u.get("completion", 0), "pf": u.get("prefill_ms", 0),
                               "gen": u.get("gen_ms", 0), "load": u.get("load_ms", 0), **({"loop": 1} if looped else {})})

    def tool(self, name: str, ms: float, ok: bool) -> None:
        self.n_tool += 1
        self.n_tool_err += 0 if ok else 1
        if len(self.steps) < STEP_CAP:
            self.steps.append({"k": "tool", "n": name, "ms": int(ms), "ok": int(bool(ok))})

    def guard(self, kind: str) -> None:
        if len(self.guards) < 20:
            self.guards.append(kind)

    def finish(self, status: str, error: str | None = None, answer_chars: int = 0) -> dict:
        total = int((time.monotonic() - self.t0) * 1000)
        m = [s for s in self.steps if s["k"] == "model"]
        row = {
            "started_at": self.started, "conv_id": self.conv_id, "source": self.source, "model": self.model_name,
            "request": self.request, "apps": json.dumps(self.apps), "tools_offered": self.tools_offered,
            "status": status, "error": (error or "")[:300] or None, "total_ms": total,
            "ttft_ms": m[0]["ttft"] if m else None, "model_ms": sum(s["ms"] for s in m),
            "prefill_ms": sum(s["pf"] for s in m), "gen_ms": sum(s["gen"] for s in m), "load_ms": sum(s["load"] for s in m),
            "tool_ms": sum(s["ms"] for s in self.steps if s["k"] == "tool"), "model_calls": self.n_model,
            "tool_calls": self.n_tool, "tool_errors": self.n_tool_err, "prompt_tokens": sum(s["p"] for s in m),
            "completion_tokens": sum(s["c"] for s in m), "answer_chars": answer_chars,
            "guards": json.dumps(self.guards), "steps": json.dumps(self.steps, separators=(",", ":")),
        }
        self.store.record(row)
        return {"ms": total, "ttft_ms": row["ttft_ms"], "prompt_tokens": row["prompt_tokens"],
                "cache_pct": self.store.cache_pct(row["prompt_tokens"], row["prefill_ms"]),
                "completion_tokens": row["completion_tokens"], "model_calls": self.n_model, "tool_calls": self.n_tool}


def _pct(vals: list[float], p: float) -> float | None:
    if not vals:
        return None
    v = sorted(vals)
    return v[min(len(v) - 1, int(round((len(v) - 1) * p)))]


class Telemetry:
    def __init__(self, db, config):
        self.c, self.config, self._writes = db.conn, config, 0

    @property
    def cfg(self) -> dict:
        return self.config.data["telemetry"]

    def begin(self, conv_id: str, request: str, source: str, model: str):
        return Trace(self, conv_id, request, source, model) if self.cfg["enabled"] else NullTrace()

    def cache_pct(self, prompt_tokens: int, prefill_ms: int) -> int | None:
        """Estimated share of the prompt served from Ollama's cache.

        Ollama reports the full prompt size but the time to evaluate only what wasn't cached, so a call whose
        effective speed is far above an uncached baseline must have reused a prefix. Approximate by design."""
        if not prompt_tokens or prompt_tokens < 200:
            return None
        base = float(self.cfg.get("prefill_baseline", 250))
        evaluated = (max(prefill_ms, 1) / 1000.0) * base
        return max(0, min(100, int(round(100 * (1 - evaluated / prompt_tokens)))))

    # ---- writing -------------------------------------------------------
    def record(self, row: dict) -> None:
        try:
            cols = ", ".join(row)
            self.c.execute(f"INSERT INTO telemetry_runs ({cols}) VALUES ({','.join('?' * len(row))})", tuple(row.values()))
            self.c.commit()
            self._writes += 1
            if self._writes % PRUNE_EVERY == 0:
                self.prune()
        except Exception:
            pass  # observability must never break a run

    def prune(self) -> None:
        cutoff = time.time() - int(self.cfg["retention_days"]) * 86400
        self.c.execute("DELETE FROM telemetry_runs WHERE started_at < ?", (cutoff,))
        self.c.execute("DELETE FROM telemetry_runs WHERE id NOT IN (SELECT id FROM telemetry_runs ORDER BY id DESC LIMIT ?)", (KEEP_ROWS,))
        self.c.commit()

    def clear(self) -> None:
        self.c.execute("DELETE FROM telemetry_runs")
        self.c.commit()

    # ---- reading (only when the panel/tool asks) -------------------------
    def _rows(self, days: float) -> list[dict]:
        return [dict(r) for r in self.c.execute("SELECT * FROM telemetry_runs WHERE started_at >= ? ORDER BY id", (time.time() - days * 86400,))]

    def summary(self, days: float = 1) -> dict:
        rows = self._rows(days)
        out: dict = {"days": days, "runs": len(rows)}
        if not rows:
            return out
        ok = [r for r in rows if r["status"] == "ok"]
        tot = [r["total_ms"] for r in rows if r["total_ms"] is not None]
        ttft = [r["ttft_ms"] for r in rows if r["ttft_ms"] is not None]
        gen_tok, gen_ms = sum(r["completion_tokens"] or 0 for r in rows), sum(r["gen_ms"] or 0 for r in rows)
        pt = sum(r["prompt_tokens"] or 0 for r in rows)
        # cache estimate per model call, weighted by prompt size
        cached = total = 0
        guards: dict[str, int] = {}
        tools: dict[str, dict] = {}
        for r in rows:
            for g in json.loads(r["guards"]):
                guards[g] = guards.get(g, 0) + 1
            for s in json.loads(r["steps"]):
                if s["k"] == "model" and s["p"] >= 200:
                    pct = self.cache_pct(s["p"], s["pf"]) or 0
                    cached += s["p"] * pct / 100
                    total += s["p"]
                elif s["k"] == "tool":
                    t = tools.setdefault(s["n"], {"name": s["n"], "calls": 0, "errors": 0, "ms": 0})
                    t["calls"] += 1
                    t["errors"] += 0 if s["ok"] else 1
                    t["ms"] += s["ms"]
        for t in tools.values():
            t["avg_ms"] = int(t["ms"] / t["calls"])
        out.update({
            "ok": len(ok), "errors": sum(r["status"] == "error" for r in rows), "cancelled": sum(r["status"] == "cancelled" for r in rows),
            "p50_ms": _pct(tot, .5), "p90_ms": _pct(tot, .9), "ttft_p50_ms": _pct(ttft, .5),
            "gen_tok_s": round(gen_tok / (gen_ms / 1000), 1) if gen_ms else None,
            "avg_prompt_tokens": int(pt / max(1, sum(r["model_calls"] or 0 for r in rows))),
            "cache_pct": int(100 * cached / total) if total else None,
            "tool_calls": sum(r["tool_calls"] or 0 for r in rows), "tool_errors": sum(r["tool_errors"] or 0 for r in rows),
            "model_calls": sum(r["model_calls"] or 0 for r in rows), "completion_tokens": gen_tok,
            "share": {k: sum(r[k] or 0 for r in rows) for k in ("prefill_ms", "gen_ms", "tool_ms", "load_ms", "total_ms")},
            "guards": sorted(({"kind": k, "count": v} for k, v in guards.items()), key=lambda x: -x["count"]),
            "tools": sorted(tools.values(), key=lambda t: -t["calls"]),
            "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in {r["source"] for r in rows}},
        })
        recent = rows[-40:]
        out["series"] = [{"id": r["id"], "total": r["total_ms"], "prefill": r["prefill_ms"], "gen": r["gen_ms"], "tool": r["tool_ms"],
                          "status": r["status"], "at": r["started_at"]} for r in recent]
        return out

    def runs(self, days: float = 7, limit: int = 30, sort: str = "recent", status: str | None = None) -> list[dict]:
        order = {"recent": "id DESC", "slowest": "total_ms DESC"}.get(sort, "id DESC")
        q, args = "SELECT * FROM telemetry_runs WHERE started_at >= ?", [time.time() - days * 86400]
        if status == "problems":
            q += " AND (status != 'ok' OR tool_errors > 0 OR guards != '[]')"
        q += f" ORDER BY {order} LIMIT ?"
        args.append(limit)
        out = []
        for r in self.c.execute(q, args):
            d = dict(r)
            d.pop("steps"), d.pop("apps"), d.pop("guards")
            d["guards"] = json.loads(r["guards"])
            d["cache_pct"] = self.cache_pct(r["prompt_tokens"] or 0, r["prefill_ms"] or 0)
            out.append(d)
        return out

    def run(self, rid: int) -> dict | None:
        r = self.c.execute("SELECT * FROM telemetry_runs WHERE id=?", (rid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["apps"], d["guards"], d["steps"] = json.loads(r["apps"]), json.loads(r["guards"]), json.loads(r["steps"])
        for s in d["steps"]:
            if s["k"] == "model":
                s["cache_pct"] = self.cache_pct(s["p"], s["pf"])
        return d
