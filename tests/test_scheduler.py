import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from diatonic.agent import loop
from diatonic.scheduler import timeparse
from diatonic.scheduler.notify import in_quiet_hours
from diatonic.scheduler.timeparse import ParseError, next_run, parse_schedule
from conftest import ScriptedProvider

TZ = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 11, 15, 14, 30, tzinfo=TZ)  # a Sunday


def at(text):
    s = parse_schedule(text, NOW)
    return next_run(s, NOW), s


# ---------------- time parsing ----------------
@pytest.mark.parametrize("text,expected", [
    ("in 20 minutes", (14, 50, 15)), ("in an hour", (15, 30, 15)), ("in 2 hours 15 minutes", (16, 45, 15)),
    ("tomorrow 9am", (9, 0, 16)), ("tomorrow at 5", (17, 0, 16)), ("today 5pm", (17, 0, 15)),
    ("at 3pm", (15, 0, 15)), ("at 2pm", (14, 0, 16)),  # 2pm already passed today -> tomorrow
    ("friday 5pm", (17, 0, 20)), ("tonight", (20, 0, 15)), ("tomorrow morning", (9, 0, 16)),
    ("noon tomorrow", (12, 0, 16)), ("2026-11-20 15:00", (15, 0, 20)), ("sunday 10am", (10, 0, 22)),
])
def test_one_off_phrases(text, expected):
    when, spec = at(text)
    assert spec["type"] == "at"
    assert (when.hour, when.minute, when.day) == expected and when.tzinfo is not None


@pytest.mark.parametrize("text,kind,expr", [
    ("every weekday at 9am", "cron", "0 9 * * 1-5"), ("every monday at 8:15", "cron", "15 8 * * 1"),
    ("daily at 7pm", "cron", "0 19 * * *"), ("every day", "cron", "0 9 * * *"),
    ("monthly on the 1st at 8am", "cron", "0 8 1 * *"), ("0 9 * * 1-5", "cron", "0 9 * * 1-5"),
    ("every 30 minutes", "every", None), ("hourly", "every", None),
])
def test_recurring_phrases(text, kind, expr):
    s = parse_schedule(text, NOW)
    assert s["type"] == kind and (expr is None or s["expr"] == expr)
    assert next_run(s, NOW) > NOW


@pytest.mark.parametrize("text", ["blah", "", "in 0 minutes", "every 10 seconds", "2020-01-01 10:00", "99 99 * * *", "at 25:00"])
def test_bad_phrases_give_clear_errors(text):
    with pytest.raises(ParseError):
        parse_schedule(text, NOW)


def test_interval_is_anchored_not_drifting():
    s = parse_schedule("every 30 minutes", NOW)
    n1 = next_run(s, NOW)
    n2 = next_run(s, n1)
    assert (n2 - n1).total_seconds() == 1800 and (n1 - NOW).total_seconds() == 1800
    late = datetime.fromtimestamp(NOW.timestamp() + 1800 * 5 + 10, TZ)  # woke up 5 intervals late
    assert (next_run(s, late).timestamp() - NOW.timestamp()) % 1800 == 0


def test_one_off_is_finished_after_it_fires():
    s = parse_schedule("in 5 minutes", NOW)
    assert next_run(s, datetime.fromtimestamp(NOW.timestamp() + 600, TZ)) is None


def test_cron_across_dst():
    ny = ZoneInfo("America/New_York")
    s = {"type": "cron", "expr": "30 2 * * *", "text": "x"}
    before = datetime(2026, 3, 7, 12, 0, tzinfo=ny)
    runs = []
    t = before
    for _ in range(3):
        t = next_run(s, t)
        runs.append(t)
    assert len(runs) == 3 and all(runs[i] < runs[i + 1] for i in range(2))  # never loops or stalls over the gap


def test_quiet_hours():
    cfg = {"quiet_enabled": True, "quiet_start": "22:00", "quiet_end": "07:00"}
    mk = lambda h: datetime(2026, 11, 15, h, 0, tzinfo=TZ)
    assert in_quiet_hours(cfg, mk(23)) and in_quiet_hours(cfg, mk(3)) and not in_quiet_hours(cfg, mk(12))
    assert not in_quiet_hours({**cfg, "quiet_enabled": False}, mk(23))


# ---------------- engine ----------------
def run_engine(coro_fn):
    async def go():
        await coro_fn()
    asyncio.run(go())


def test_reminder_fires_notifies_and_finishes(sched):
    ctx, reg, eng, store, runs, events = sched

    async def go():
        s = eng.create(name="Call mom", kind="notify", when="in 5 minutes", payload={"text": "Call mom"})
        assert s["state"] == "active"
        past = datetime.fromtimestamp(time.time() - 1, TZ)
        store.update(s["id"], next_run=time.time() - 1, spec={**s["spec"], "at": past.isoformat()})  # make it due
        eng.start()
        eng.poke()
        await asyncio.sleep(0.3)
        await eng.stop()
        done = store.get(s["id"])
        assert done["state"] == "done" and done["last_status"] == "ok" and done["next_run"] is None
        n = [e for e in events if e["type"] == "notification"]
        assert len(n) == 1 and n[0]["body"] == "Call mom" and n[0]["speak_text"] == "Reminder: Call mom"
        assert store.unread_count() == 1

    asyncio.run(go())


def test_recurring_reschedules_and_missed_runs_coalesce(sched):
    ctx, reg, eng, store, runs, events = sched

    async def go():
        s = eng.create(name="Stretch", kind="notify", when="every 30 minutes", payload={"text": "Stretch"})
        store.update(s["id"], next_run=time.time() - 3600 * 5)  # the server was off for 5 hours
        eng.start()
        eng.poke()
        await asyncio.sleep(0.3)
        await eng.stop()
        n = [e for e in events if e["type"] == "notification"]
        assert len(n) == 1 and "missed" in n[0]["body"]        # one late reminder, not ten
        after = store.get(s["id"])
        assert after["state"] == "active" and after["next_run"] > time.time()

    asyncio.run(go())


def test_paused_jobs_do_not_fire(sched):
    ctx, reg, eng, store, runs, events = sched

    async def go():
        s = eng.create(name="x", kind="notify", when="in 5 minutes", payload={"text": "x"})
        store.update(s["id"], next_run=time.time() - 1, state="paused")
        eng.start()
        eng.poke()
        await asyncio.sleep(0.2)
        await eng.stop()
        assert not [e for e in events if e["type"] == "notification"]

    asyncio.run(go())


def test_overlapping_run_is_skipped_not_doubled(sched):
    ctx, reg, eng, store, runs, events = sched

    async def go():
        ctx.config.data["scheduler"]["allow_scripts"] = True
        s = eng.create(name="slow", kind="script", when="every 1 minute", payload={"command": "sleep 1"})
        assert eng.run_now(s["id"]) is True
        assert eng.run_now(s["id"]) is False  # already running
        await asyncio.sleep(1.4)
        statuses = sorted(r["status"] for r in store.runs(s["id"]))
        assert statuses == ["ok", "skipped"]

    asyncio.run(go())


def test_stale_running_rows_are_cleaned_on_start(sched):
    ctx, reg, eng, store, runs, events = sched
    s = eng.create(name="x", kind="notify", when="in 5 minutes", payload={"text": "x"})
    rid = store.start_run(s["id"])
    eng.start_cleanup = None
    store.mark_stale_runs()
    assert store.runs(s["id"])[0]["status"] == "interrupted"


# ---------------- script jobs ----------------
def test_scripts_are_off_by_default(sched):
    ctx, reg, eng, store, runs, events = sched

    async def go():
        s = eng.create(name="echo", kind="script", when="in 5 minutes", payload={"command": "echo hi"})
        eng.run_now(s["id"])
        await asyncio.sleep(0.3)
        r = store.runs(s["id"])[0]
        assert r["status"] == "error" and "turned off" in r["output"]

    asyncio.run(go())


def test_script_success_failure_timeout_and_env(sched):
    ctx, reg, eng, store, runs, events = sched
    ctx.config.data["scheduler"]["allow_scripts"] = True

    async def one(cmd, **payload):
        s = eng.create(name=cmd[:10], kind="script", when="in 5 minutes", payload={"command": cmd, **payload})
        eng.run_now(s["id"])
        for _ in range(60):
            await asyncio.sleep(0.1)
            r = store.runs(s["id"])
            if r and r[0]["status"] != "running":
                return r[0]
        raise AssertionError("did not finish")

    async def go():
        ok = await one("echo hello")
        assert ok["status"] == "ok" and "hello" in ok["output"]
        bad = await one("echo oops; exit 3")
        assert bad["status"] == "error" and "exit code 3" in bad["output"]
        assert [e for e in events if e["type"] == "notification" and "error" in e["title"]]  # failures notify
        secret = await one("echo ${DIATONIC_SECRETS_BACKEND:-unset}")
        assert "unset" in secret["output"]  # parent environment is not leaked
        t0 = time.time()
        to = await one("sleep 30", timeout=0.5)
        assert to["status"] == "timeout" and time.time() - t0 < 10

    asyncio.run(go())


def test_timeout_kills_child_processes_too(sched, tmp_path):
    ctx, reg, eng, store, runs, events = sched
    ctx.config.data["scheduler"]["allow_scripts"] = True
    marker = tmp_path / "alive"

    async def go():
        s = eng.create(name="fork", kind="script", when="in 5 minutes",
                       payload={"command": f"(sleep 2; touch {marker}) & sleep 30", "timeout": 0.5})
        eng.run_now(s["id"])
        await asyncio.sleep(3.2)
        assert not marker.exists()  # the backgrounded child died with the group

    asyncio.run(go())


# ---------------- prompt jobs ----------------
def test_prompt_job_runs_readonly_in_its_own_chat_and_notifies(sched, monkeypatch):
    ctx, reg, eng, store, runs, events = sched
    p = ScriptedProvider([[{"content": "Nothing urgent today."}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    seen = {}
    orig = loop.run_agent

    def spy(*a, **kw):
        seen.update(kw)
        return orig(*a, **kw)

    monkeypatch.setattr("diatonic.agent.runs.agent_loop.run_agent", spy)

    async def go():
        s = eng.create(name="Morning briefing", kind="prompt", when="every day at 8am", payload={"prompt": "Summarize my day"})
        eng.run_now(s["id"])
        await asyncio.sleep(0.5)
        r = store.runs(s["id"])[0]
        assert r["status"] == "ok" and r["conversation_id"]
        assert seen["readonly"] is True and "automated scheduled run" in seen["extra_system"]
        n = [e for e in events if e["type"] == "notification"][0]
        assert n["title"] == "Morning briefing" and "Nothing urgent" in n["body"]
        assert n["data"]["conversation_id"] == r["conversation_id"]
        conv = ctx.db.get_conversation(r["conversation_id"])
        assert conv["title"].startswith("⏰ Morning briefing")

    asyncio.run(go())


def test_readonly_runs_never_see_write_tools(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"tool_calls": [{"name": "fake_nuke", "arguments": {"id": 1}}]}], [{"content": "ok"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], "do it", readonly=True)]

    asyncio.run(go())
    assert app.calls == []  # the destructive tool was not even offered, and calling it anyway does nothing


def test_unattended_approval_times_out_as_denied(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"tool_calls": [{"name": "fake_nuke", "arguments": {"id": 1}}]}], [{"content": "denied, ok"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], "do it", approval_timeout=0.2)]

    ev = asyncio.run(go())
    assert app.calls == [] and [e for e in ev if e["type"] == "tool_status"][0]["status"] == "denied"


def test_unattended_approval_notification_has_approval_id_and_resolves(sched, monkeypatch):
    from diatonic.agent import approvals
    ctx, reg, scheduler, store, runs, events = sched
    app = reg.apps["fake"]
    p = ScriptedProvider([[{"tool_calls": [{"name": "fake_nuke", "arguments": {"id": 1}}]}], [{"content": "approved, done"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = ctx.db.create_conversation()

    async def go():
        gen = loop.run_agent(ctx, reg, conv["id"], "do it", approval_timeout=5.0)
        evs = []
        async for e in gen:
            evs.append(e)
            if e["type"] == "notify" and "approval" in e.get("text", "").lower():
                nots = store.notifications()
                app_not = next((n for n in nots if n["kind"] == "approval"), None)
                assert app_not is not None
                aid = app_not["data"].get("approval_id")
                assert aid is not None
                approvals.resolve(aid, True)
        return evs

    ev = asyncio.run(go())
    assert app.calls == [("nuke", {"id": 1})]
    assert [e for e in ev if e["type"] == "tool_status"][0]["status"] == "approved"


def test_unattended_approval_notification_can_be_rejected(sched, monkeypatch):
    from diatonic.agent import approvals
    ctx, reg, scheduler, store, runs, events = sched
    app = reg.apps["fake"]
    p = ScriptedProvider([[{"tool_calls": [{"name": "fake_nuke", "arguments": {"id": 2}}]}], [{"content": "denied, understood"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = ctx.db.create_conversation()

    async def go():
        gen = loop.run_agent(ctx, reg, conv["id"], "do it", approval_timeout=5.0)
        evs = []
        async for e in gen:
            evs.append(e)
            if e["type"] == "notify" and "approval" in e.get("text", "").lower():
                nots = store.notifications()
                app_not = next((n for n in nots if n["kind"] == "approval"), None)
                assert app_not is not None
                aid = app_not["data"].get("approval_id")
                assert aid is not None
                approvals.resolve(aid, False)
        return evs

    ev = asyncio.run(go())
    assert app.calls == []
    assert [e for e in ev if e["type"] == "tool_status"][0]["status"] == "denied"


def test_old_scheduled_chats_are_pruned(sched, monkeypatch):
    ctx, reg, eng, store, runs, events = sched
    ctx.config.data["scheduler"]["keep_prompt_chats"] = 2
    for i in range(5):
        ctx.db.create_conversation(f"⏰ Brief · day {i}")
    eng._prune_prompt_chats("Brief")
    assert len([c for c in ctx.db.list_conversations() if c["title"].startswith("⏰ Brief")]) == 2


def test_reminders_and_jobs_can_be_edited(sched):
    ctx, reg, scheduler, store, runs, events = sched
    from diatonic.apps.scheduler import create as make_sched
    from diatonic.apps.base import ToolError
    import asyncio as _a
    app = make_sched(ctx)
    r = scheduler.create(name="stretch", kind="notify", when="tomorrow 9am", payload={"text": "stretch"})
    before = r["next_run"]
    tool = next(t for t in app.tools() if t.name == "update_job")
    out = _a.run(tool.handler({"id": r["id"], "when": "tomorrow 10am", "text": "stretch and drink water"}))
    s = store.get(r["id"])
    assert s["next_run"] - before == 3600 and s["payload"]["text"] == "stretch and drink water" and s["name"] == "stretch and drink water"
    assert tool.ask({"id": r["id"], "when": "in 5 minutes"}) is False                    # moving a reminder: no card
    j = scheduler.create(name="briefing", kind="prompt", when="every weekday at 8", payload={"prompt": "brief me"})
    assert tool.ask({"id": j["id"], "prompt": "brief me on mail only"}) is True           # changing what a job does: card
    assert tool.ask({"id": j["id"], "when": "every weekday at 7:30"}) is False
    spec = store.get(j["id"])["spec"]
    with __import__("pytest").raises(ToolError):
        _a.run(tool.handler({"id": j["id"], "when": "whenever you like"}))               # unparseable times are refused, nothing changes
    assert store.get(j["id"])["spec"] == spec


def test_a_bare_hour_is_spelled_out_so_the_user_can_catch_it(sched):
    ctx, reg, scheduler, store, runs, events = sched
    from diatonic.apps.scheduler import create as make_sched
    import asyncio as _a
    app = make_sched(ctx)
    j = scheduler.create(name="briefing", kind="prompt", when="every weekday at 8am", payload={"prompt": "brief me"})
    tool = next(t for t in app.tools() if t.name == "update_job")
    out = _a.run(tool.handler({"id": j["id"], "when": "every weekday at 7"}))
    assert "19:00" in out["note"] and "am or pm" in out["note"]
    assert _a.run(tool.handler({"id": j["id"], "when": "every weekday at 7am"}))["note"] is None
