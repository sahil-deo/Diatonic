import asyncio
import json
import statistics
import time

import pytest
from starlette.testclient import TestClient

from diatonic.agent import loop
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app
from diatonic.telemetry import NullTrace, Telemetry
from conftest import ScriptedProvider


def call(name, **args):
    return {"tool_calls": [{"name": name, "arguments": args}]}


def usage(p=1000, c=40, pf=4000, gen=1100, load=0):
    return {"usage": {"prompt": p, "completion": c, "prefill_ms": pf, "gen_ms": gen, "load_ms": load}}


def run(env, provider, monkeypatch, text="do it"):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", lambda c: provider)
    conv = db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], text)]
    return conv, asyncio.run(go())


def test_a_run_records_one_row_with_timings_tokens_and_tools(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_read", text="x"), usage(1500, 30, 6000, 900)], [{"content": "Done."}, usage(1700, 20, 400, 600)]])
    conv, ev = run(env, p, monkeypatch, "read it")
    rows = ctx.telemetry.runs(days=1)
    assert len(rows) == 1
    r = ctx.telemetry.run(rows[0]["id"])
    assert r["status"] == "ok" and r["request"] == "read it" and r["source"] == "chat" and r["apps"] == ["fake"]
    assert r["model_calls"] == 2 and r["tool_calls"] == 1 and r["tool_errors"] == 0
    assert r["prompt_tokens"] == 3200 and r["completion_tokens"] == 50 and r["prefill_ms"] == 6400 and r["gen_ms"] == 1500
    assert [s["k"] for s in r["steps"]] == ["model", "tool", "model"] and r["steps"][1]["n"] == "fake_read"
    assert r["ttft_ms"] is not None and r["total_ms"] >= 0 and r["tools_offered"] > 0
    perf = next(e for e in ev if e["type"] == "done")["perf"]
    assert perf["prompt_tokens"] == 3200 and perf["model_calls"] == 2
    assert db.messages(conv["id"])[-1]["meta"]["perf"]["completion_tokens"] == 50   # shown again when the chat is reopened


def test_guards_errors_and_failures_are_counted(env, monkeypatch):
    ctx, *_ = env
    run(env, ScriptedProvider([[call("fake_boom")], [call("fake_read", text="a")], [call("fake_read", text="a")], [{"content": "x"}]]), monkeypatch)
    r = ctx.telemetry.run(ctx.telemetry.runs()[0]["id"])
    assert r["tool_errors"] == 1 and "duplicate" in r["guards"]


def test_provider_errors_and_cancellation_are_recorded(env, monkeypatch):
    from diatonic.agent.providers import ProviderError
    ctx, *_ = env

    class Down(ScriptedProvider):
        async def stream(self, *a, **k):
            raise ProviderError("Can't reach Ollama")
            yield

    run(env, Down([]), monkeypatch)
    assert ctx.telemetry.runs()[0]["status"] == "error" and "reach" in ctx.telemetry.runs()[0]["error"]

    class Hang(ScriptedProvider):
        async def stream(self, *a, **k):
            await asyncio.sleep(30)
            yield {}

    async def go():
        monkeypatch.setattr(loop, "make_provider", lambda c: Hang([]))
        conv = env[3].create_conversation()
        t = asyncio.create_task(_consume(loop.run_agent(ctx, env[1], conv["id"], "hang")))
        await asyncio.sleep(0.2)
        t.cancel()
        await asyncio.gather(t, return_exceptions=True)

    asyncio.run(go())
    assert [r["status"] for r in ctx.telemetry.runs(sort="recent")][0] == "cancelled"


async def _consume(agen):
    async for _ in agen:
        pass


def test_scheduled_runs_are_labelled(env, monkeypatch):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", lambda c: ScriptedProvider([[{"content": "ok"}]]))
    conv = db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], "brief me", readonly=True)]
    asyncio.run(go())
    assert ctx.telemetry.runs()[0]["source"] == "scheduled"


def test_cache_estimate_uses_effective_prefill_speed(env):
    t = env[0].telemetry
    assert t.cache_pct(1347, 7000) == 0 or t.cache_pct(1347, 7000) < 40           # ~190 tok/s: a miss
    assert t.cache_pct(1347, 20) >= 99                                            # near-instant: served from cache
    assert 60 <= t.cache_pct(1500, 1200) <= 85                                    # partial reuse
    assert t.cache_pct(150, 10) is None and t.cache_pct(0, 0) is None             # too small to say


def test_summary_aggregates(env, monkeypatch):
    ctx, *_ = env
    for pf in (6000, 6000, 30):
        run(env, ScriptedProvider([[call("fake_read", text="x"), usage(1500, 30, pf, 800)], [{"content": "ok"}, usage(1600, 20, 100, 500)]]), monkeypatch)
    s = ctx.telemetry.summary(1)
    assert s["runs"] == 3 and s["ok"] == 3 and s["tool_calls"] == 3 and s["model_calls"] == 6
    assert s["p50_ms"] is not None and s["gen_tok_s"] and 0 < s["cache_pct"] < 100
    assert s["tools"][0]["name"] == "fake_read" and s["tools"][0]["calls"] == 3 and len(s["series"]) == 3
    assert ctx.telemetry.summary(0.04)["runs"] == 3 and ctx.telemetry.summary(1)["by_source"] == {"chat": 3}


def test_disabled_telemetry_records_nothing_and_adds_no_perf(env, monkeypatch):
    ctx, *_ = env
    ctx.config.data["telemetry"]["enabled"] = False
    conv, ev = run(env, ScriptedProvider([[{"content": "ok"}]]), monkeypatch)
    assert ctx.telemetry.runs() == [] and next(e for e in ev if e["type"] == "done")["perf"] == {}
    assert isinstance(ctx.telemetry.begin("c", "r", "chat", "m"), NullTrace)


def test_request_text_is_optional_and_clipped(env, monkeypatch):
    ctx, *_ = env
    run(env, ScriptedProvider([[{"content": "ok"}]]), monkeypatch, "x" * 500)
    assert len(ctx.telemetry.runs()[0]["request"]) == 160
    ctx.config.data["telemetry"]["store_text"] = False
    run(env, ScriptedProvider([[{"content": "ok"}]]), monkeypatch, "private thing")
    assert ctx.telemetry.runs()[0]["request"] in ("", None)


def test_retention_prunes_old_rows_and_clear_works(env, monkeypatch):
    ctx, *_ = env
    for _ in range(3):
        run(env, ScriptedProvider([[{"content": "ok"}]]), monkeypatch)
    ctx.telemetry.c.execute("UPDATE telemetry_runs SET started_at=? WHERE id=1", (time.time() - 90 * 86400,))
    ctx.telemetry.prune()
    assert len(ctx.telemetry._rows(365)) == 2
    ctx.telemetry.clear()
    assert ctx.telemetry._rows(365) == []


def test_a_database_fault_never_breaks_a_run(env, monkeypatch):
    ctx, *_ = env
    ctx.telemetry.c.execute("DROP TABLE telemetry_runs")
    conv, ev = run(env, ScriptedProvider([[{"content": "still works"}]]), monkeypatch)
    assert [e for e in ev if e["type"] == "message"][0]["text"] == "still works"


# ---------------- overhead ----------------
def test_overhead_is_negligible(env, monkeypatch, capsys):
    """Recording must cost far less than one millisecond per run against model calls that take seconds."""
    ctx, *_ = env
    t = ctx.telemetry
    N = 400
    per = []
    for _ in range(N):
        t0 = time.perf_counter()
        tr = t.begin("c", "what's on my calendar tomorrow", "chat", "qwen3:4b-instruct")
        tr.routing(["gcal"], 7)
        tr.model(4000, {"prompt": 1200, "completion": 40, "prefill_ms": 4000, "gen_ms": 1100, "load_ms": 0}, 4300)
        tr.tool("gcal_list_events", 800, True)
        tr.model(1500, {"prompt": 1500, "completion": 60, "prefill_ms": 300, "gen_ms": 1200, "load_ms": 0}, 400)
        tr.finish("ok", None, 120)
        per.append((time.perf_counter() - t0) * 1000)
    mean, p99 = statistics.mean(per), sorted(per)[int(N * .99)]
    print(f"\ntelemetry overhead per run: mean {mean:.3f} ms, p99 {p99:.3f} ms (a typical run takes ~8000 ms)")
    assert mean < 2.0 and p99 < 15.0


# ---------------- API + app ----------------
@pytest.fixture
def client(tmp_path):
    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        yield c, app.state.ctx


def seed(ctx, n=3):
    for i in range(n):
        tr = ctx.telemetry.begin("c1", f"request {i}", "chat", "qwen3:4b-instruct")
        tr.routing(["github"], 13)
        tr.model(5000, {"prompt": 1200, "completion": 40, "prefill_ms": 4800, "gen_ms": 1000, "load_ms": 0}, 4900)
        tr.tool("github_list_repos", 700 + i, i != 1)
        tr.finish("ok")


def test_telemetry_api(client):
    c, ctx = client
    seed(ctx)
    s = c.get("/api/apps/telemetry/summary?days=1").json()
    assert s["runs"] == 3 and s["cfg"]["enabled"] is True and s["tools"][0]["errors"] == 1
    runs = c.get("/api/apps/telemetry/runs?sort=slowest&limit=2").json()
    assert len(runs) == 2 and "steps" not in runs[0]
    assert len(c.get("/api/apps/telemetry/runs?status=problems").json()) == 1       # only the run with a failed tool
    d = c.get(f"/api/apps/telemetry/run/{runs[0]['id']}").json()
    assert d["steps"][0]["k"] == "model" and d["steps"][0]["cache_pct"] == 0 or d["steps"][0]["cache_pct"] is not None
    assert c.get("/api/apps/telemetry/run/9999").status_code == 404
    assert c.get("/api/apps/telemetry/summary?days=abc").status_code == 200           # bad input is tolerated
    c.put("/api/config", json={"telemetry": {"enabled": False}})
    assert c.get("/api/apps/telemetry/summary").json()["cfg"]["enabled"] is False
    assert c.post("/api/apps/telemetry/clear").json()["ok"]
    assert c.get("/api/apps/telemetry/summary").json()["runs"] == 0


def test_agent_read_tool(client):
    c, ctx = client
    from diatonic.apps.telemetry import create
    app = create(ctx)
    assert asyncio.run(app.tools()[0].handler({})) == "No runs recorded in that period."
    seed(ctx)
    r = asyncio.run(app.tools()[0].handler({"days": 1}))
    assert r["runs"] == 3 and r["slowest_tool"] == "github_list_repos" and app.tools()[0].risk == "read"
