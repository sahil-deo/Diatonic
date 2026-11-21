import asyncio
import time
from datetime import datetime

import pytest

from diatonic.apps.base import ToolError
from diatonic.apps.notes import create as make_notes
from diatonic.apps.scheduler import create as make_scheduler
from diatonic.apps.todos import create as make_todos


def tool(app, tool_name):
    return next(t for t in app.tools() if t.name == tool_name)


def call(app, tool_name, /, **args):
    return asyncio.run(tool(app, tool_name).handler(args))


# ---------------- scheduler app ----------------
def test_remind_me_resolves_time_and_confirms(sched):
    ctx, reg, eng, store, runs, events = sched
    app = make_scheduler(ctx)
    r = call(app, "remind_me", text="call mom", when="tomorrow 9am")
    assert r["reminder"]["next"] == "tomorrow 09:00" and r["reminder"]["kind"] == "notify"
    assert call(app, "list_reminders")[0]["name"] == "call mom"
    assert any(e.get("type") == "app_changed" for e in events)  # panels refresh when the agent changes things
    with pytest.raises(ToolError, match="understand"):
        call(app, "remind_me", text="x", when="whenever")
    call(app, "cancel_reminder", id=r["reminder"]["id"])
    assert call(app, "list_reminders") == "No reminders."


def test_job_tools_and_risk_levels(sched):
    ctx, reg, eng, store, runs, events = sched
    app = make_scheduler(ctx)
    risks = {t.name: t.risk for t in app.tools()}
    assert risks["remind_me"] == "write" and risks["schedule_script"] == "destructive" and risks["list_jobs"] == "read"
    assert "remind_me" in app.default_allow and "schedule_script" not in app.default_allow
    j = call(app, "schedule_prompt", name="Briefing", when="every weekday at 8am", prompt="What's on today?")
    assert j["job"]["writes_allowed"] is None and j["job"]["next"]
    jid = j["job"]["id"]
    call(app, "pause_job", id=jid)
    assert call(app, "list_jobs")[0]["state"] == "paused"
    assert call(app, "resume_job", id=jid)["next"]
    call(app, "delete_job", id=jid)
    with pytest.raises(ToolError, match="No such item"):
        call(app, "pause_job", id=jid)


def test_agent_cannot_schedule_scripts_unless_enabled(sched):
    ctx, reg, eng, store, runs, events = sched
    app = make_scheduler(ctx)
    with pytest.raises(ToolError, match="turned off"):
        call(app, "schedule_script", name="x", when="in 5 minutes", command="echo hi")
    ctx.config.data["scheduler"]["allow_scripts"] = True
    assert call(app, "schedule_script", name="x", when="in 5 minutes", command="echo hi")["job"]["kind"] == "script"


def test_agent_has_no_way_to_grant_writes_or_enable_scripts(sched):
    ctx, *_ = sched
    names = {t.name for t in make_scheduler(ctx).tools()}
    assert not any("write" in n or "allow" in n or "enable" in n for n in names)


# ---------------- todos ----------------
def test_todos_lifecycle(sched):
    ctx, reg, eng, store, runs, events = sched
    app = make_todos(ctx)
    a = call(app, "add_todo", title="Buy milk", due="tomorrow 5pm", priority="high")
    assert a["added"]["due"] == "tomorrow 17:00" and a["added"]["priority"] == "high"
    call(app, "add_todo", title="Read paper")
    titles = [t["title"] for t in call(app, "list_todos")]
    assert titles == ["Buy milk", "Read paper"]  # dated first
    assert call(app, "list_todos", due="today") == "No matching to-dos."  # both are due later than today
    call(app, "complete_todo", id=a["added"]["id"])
    assert [t["title"] for t in call(app, "list_todos")] == ["Read paper"]
    assert [t["title"] for t in call(app, "list_todos", status="done")] == ["Buy milk"]
    call(app, "reopen_todo", id=a["added"]["id"])
    call(app, "update_todo", id=a["added"]["id"], title="Buy oat milk", priority="low")
    assert call(app, "list_todos", status="all")[0]["title"] == "Buy oat milk"
    call(app, "delete_todo", id=a["added"]["id"])
    with pytest.raises(ToolError):
        call(app, "complete_todo", id=a["added"]["id"])


def test_todo_with_reminder_creates_and_cleans_up_its_reminder(sched):
    ctx, reg, eng, store, runs, events = sched
    app = make_todos(ctx)
    a = call(app, "add_todo", title="Send invoice", due="tomorrow 9am", remind=True)
    assert a["reminder"] and len(store.list("notify")) == 1
    call(app, "complete_todo", id=a["added"]["id"])  # done: no longer nags
    assert store.list("notify") == []


def test_todo_due_must_be_a_single_time(sched):
    ctx, *_ = sched
    with pytest.raises(ToolError, match="repeating"):
        call(make_todos(ctx), "add_todo", title="x", due="every day")


def test_overdue_filter(sched):
    ctx, *_ = sched
    app = make_todos(ctx)
    r = call(app, "add_todo", title="Late one", due="in 5 minutes")
    ctx.db.conn.execute("UPDATE todos SET due=? WHERE id=?", (time.time() - 60, r["added"]["id"]))
    ctx.db.conn.commit()
    o = call(app, "list_todos", due="overdue")
    assert o[0]["title"] == "Late one" and o[0]["overdue"] is True


# ---------------- notes ----------------
@pytest.fixture
def notes(sched, tmp_path):
    ctx, *_ = sched
    ctx.app_cfg("notes")["settings"]["folder"] = str(tmp_path / "notes")
    return make_notes(ctx), tmp_path / "notes"


def test_notes_are_plain_markdown_files(notes):
    app, root = notes
    call(app, "create_note", title="Trip ideas", body="Visit **Kyoto**.\nTry ramen.", tags=["travel", "japan"])
    f = root / "Trip ideas.md"
    assert f.exists()
    text = f.read_text()
    assert text.startswith("---\n") and "tags: travel, japan" in text and "Visit **Kyoto**." in text
    call(app, "append_note", note="trip", text="Book flights")  # fuzzy title
    assert "Book flights" in f.read_text()
    got = call(app, "get_note", note="Trip ideas")
    assert got["tags"] == ["travel", "japan"] and "Book flights" in got["body"]


def test_notes_search_list_and_duplicates(notes):
    app, root = notes
    call(app, "create_note", title="Groceries", body="milk, eggs, bread")
    call(app, "create_note", title="Groceries", body="second list")
    assert (root / "Groceries 2.md").exists()
    call(app, "create_note", title="Ideas", body="an app for bread baking", tags=["work"])
    res = call(app, "search_notes", query="bread")
    assert {r["title"] for r in res} == {"Groceries", "Ideas"} and "bread" in res[0]["match"]
    assert call(app, "search_notes", query="zzz") == "No notes match."
    assert [n["title"] for n in call(app, "list_notes", tag="work")] == ["Ideas"]
    with pytest.raises(ToolError, match="More than one"):
        call(app, "get_note", note="Groc")


def test_notes_delete_is_recoverable_and_paths_are_confined(notes):
    app, root = notes
    call(app, "create_note", title="Temp", body="x")
    call(app, "delete_note", note="Temp")
    assert not (root / "Temp.md").exists() and list((root / ".trash").glob("Temp-*.md"))
    call(app, "create_note", title="../../etc/passwd", body="nope")
    assert not any(p.name == "passwd.md" for p in root.parent.rglob("*.md") if root not in p.parents)
    assert all(root in p.resolve().parents for p in root.glob("*.md"))


def test_notes_read_files_written_elsewhere(notes):
    app, root = notes
    root.mkdir(parents=True, exist_ok=True)
    (root / "Handwritten.md").write_text("# Title\n\nJust a plain file, no front matter. #tag")
    assert call(app, "get_note", note="handwritten")["body"].startswith("# Title")
    assert call(app, "search_notes", query="plain")[0]["title"] == "Handwritten"


# ---------------- service + notification API ----------------
def test_launchd_plist_is_correct_and_survives_crashes():
    import plistlib
    from diatonic import service
    p = plistlib.loads(plistlib.dumps(service.build_plist(python="/x/python", port=5005)))
    assert p["ProgramArguments"] == ["/x/python", "-m", "diatonic", "run", "--port", "5005"]
    assert p["RunAtLoad"] is True and p["KeepAlive"] is True and p["Label"] == "com.diatonic.server"
    assert "/opt/homebrew/bin" in p["EnvironmentVariables"]["PATH"]


def test_notification_api_inbox_snooze_and_state(tmp_path):
    from starlette.testclient import TestClient
    from diatonic.config import Config
    from diatonic.db import Database
    from diatonic.server import create_app

    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        assert c.post("/api/notifications/test").status_code == 200
        inbox = c.get("/api/notifications").json()
        assert inbox["unread"] == 1 and inbox["items"][0]["title"] == "Test notification"
        nid = inbox["items"][0]["id"]
        assert c.post(f"/api/notifications/{nid}/snooze", json={"minutes": 10}).json()["ok"]
        assert c.get("/api/notifications").json()["unread"] == 0  # snoozing marks it read
        pr = c.get("/api/apps/scheduler/panel").json()
        assert len(pr["reminders"]) == 1 and pr["reminders"][0]["next"].startswith(("today", "tomorrow"))
        assert c.post("/api/apps/scheduler/create", json={"kind": "notify", "text": "x", "when": "gibberish"}).status_code == 400
        assert c.post("/api/apps/scheduler/create", json={"kind": "script", "name": "s", "when": "in 5 minutes", "command": "ls"}).status_code == 400  # scripts off
        c.post("/api/notifications/clear")
        assert c.get("/api/notifications").json()["items"] == []
        state = c.get("/api/state").json()
        ids = {a["id"] for a in state["apps"]}
        assert {"scheduler", "todos", "notes", "github", "gcal"} <= ids
        sch = next(a for a in state["apps"] if a["id"] == "scheduler")
        always = {t["name"] for t in sch["tools"] if t["always_allow"]}
        assert always == {"remind_me", "pause_job", "resume_job", "run_job_now"}  # sensible defaults, user-changeable


def test_panel_cannot_grant_writes_via_agent_tools_but_ui_can(tmp_path):
    from starlette.testclient import TestClient
    from diatonic.config import Config
    from diatonic.db import Database
    from diatonic.server import create_app

    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        j = c.post("/api/apps/scheduler/create", json={"kind": "prompt", "name": "B", "when": "every day at 8am", "prompt": "hi"}).json()
        assert c.post("/api/apps/scheduler/action", json={"id": j["id"], "action": "allow_writes", "value": True}).status_code == 200
        panel = c.get("/api/apps/scheduler/panel").json()
        assert panel["jobs"][0]["allow_writes"] is True


def test_script_job_that_cannot_run_is_rejected_before_asking_for_approval(sched, monkeypatch):
    from diatonic.agent import loop
    from conftest import ScriptedProvider
    ctx, reg, eng, store, runs, events = sched
    reg.register(make_scheduler(ctx))
    p = ScriptedProvider([[{"tool_calls": [{"name": "scheduler_schedule_script",
                                            "arguments": {"name": "x", "when": "in 5 minutes", "command": "ls"}}]}],
                          [{"content": "Scripts are off."}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = ctx.db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], "schedule a script job to run ls")]

    ev = asyncio.run(go())
    tc = next(e for e in ev if e["type"] == "tool_call")
    assert tc["needs_approval"] is False and not [e for e in ev if e["type"] == "notify"]
    assert "turned off" in next(e for e in ev if e["type"] == "tool_result")["preview"]
