"""Data apps: personal apps with no API and no code. Built by the agent in a couple of calls, used by name."""
import asyncio

import pytest
from starlette.testclient import TestClient

from diatonic.agent import loop
from diatonic.agent.runs import RunManager
from diatonic.agent.session import RUN_CLIENT
from diatonic.apps.base import ToolError
from diatonic.apps.ui import create as create_ui
from diatonic.dataapps import DataSpecError, check_data_manifest, coerce, parse_fields
from diatonic.mods import ModStore
from datetime import date


def test_fields_are_read_the_way_a_small_model_writes_them():
    f = parse_fields("done: checkbox, exam_date: date, priority: choice(high, medium, low), notes: long text")
    assert [x["name"] for x in f] == ["name", "done", "exam_date", "priority", "notes"]          # a name field is added first
    assert [x["type"] for x in f] == ["text", "check", "date", "choice", "notes"] and f[3]["options"] == ["high", "medium", "low"]
    for bad in ["x: colour", "p: choice()", "a, a"]:
        with pytest.raises(DataSpecError):
            parse_fields(bad)
    with pytest.raises(DataSpecError, match="parent"):
        check_data_manifest({"lists": {"topics": {"parent": "subjects"}}})


def test_values_are_checked_against_their_type():
    t = date(2026, 11, 15)
    assert coerce({"name": "d", "type": "check"}, "done", t) is True and coerce({"name": "d", "type": "check"}, "not done", t) is False
    assert coerce({"name": "d", "type": "date"}, "tomorrow", t) == "2026-11-16" and coerce({"name": "n", "type": "number"}, "3", t) == 3
    assert coerce({"name": "p", "type": "choice", "options": ["High", "Low"]}, "high", t) == "High"
    with pytest.raises(ToolError):
        coerce({"name": "p", "type": "choice", "options": ["High", "Low"]}, "urgent", t)


@pytest.fixture
def made(env, tmp_path):
    ctx, reg, app, db = env
    ctx.mods = ModStore(tmp_path)
    runs = RunManager()
    events = []
    ctx.send_ui, ctx.registry, ctx.broadcast = runs.send_ui, reg, events.append
    ctx.mods.on_change = reg.sync_api_apps
    ui = create_ui(ctx); reg.register(ui)
    return ctx, reg, ui, events


def tool(reg, app, name):
    return next(t for t in reg.apps[app].tools() if t.name == name)


def test_build_and_use_an_exam_tracker(made):
    ctx, reg, ui, events = made

    async def go():
        RUN_CLIENT.set("tab")
        await ui.create_app({"name": "exams", "title": "Exam Tracker"})
        assert reg.apps["exams"].panel and reg.apps["exams"].tools() == []
        await ui.add_list({"app": "exams", "name": "subjects", "fields": "name*: text, exam_date: date, notes: notes"})
        r = await ui.add_list({"app": "Exam Tracker", "name": "topics", "parent": "subject", "fields": "name*, done: check, notes: notes"})
        assert "exams_add_topic" in r and "inside subjects" in r
        add_s, add_t, upd_t = tool(reg, "exams", "add_subject"), tool(reg, "exams", "add_topic"), tool(reg, "exams", "update_topic")
        assert add_t.parameters["required"] == ["subject", "name"] and add_t.parameters["properties"]["done"]["type"] == "boolean"
        await add_s.handler({"name": "Physics", "exam_date": "2026-12-05"})
        await add_s.handler({"name": "Chemistry"})
        for t in ("Optics", "Thermodynamics"):
            await add_t.handler({"subject": "physics", "name": t})
        await add_t.handler({"subject": "Chem", "name": "Organic reactions"})
        assert "already there" in await add_t.handler({"subject": "Physics", "name": "optics"})            # no duplicates
        await upd_t.handler({"topic": "optic", "done": "true", "notes": "revise lens formula"})            # found by name, loosely
        listing = await tool(reg, "exams", "list_subjects").handler({})
        phys = next(s for s in listing if s["name"] == "Physics")
        assert phys["topics_done"] == "1/2" and phys["exam_date"] == "2026-12-05"
        left = await tool(reg, "exams", "list_topics").handler({"subject": "Physics", "only": "open"})
        assert [t["name"] for t in left] == ["Thermodynamics"]
        everywhere = await tool(reg, "exams", "list_topics").handler({})
        assert {(t["name"], t["in"]) for t in everywhere} >= {("Optics", "Physics"), ("Organic reactions", "Chemistry")}
        with pytest.raises(ToolError, match="Did you mean"):
            await upd_t.handler({"topic": "Thermodinamics stuff", "done": True})
        # editing the app: one call, data kept
        r = await ui.add_field({"app": "exams", "list": "topic", "field": "priority: choice(high, low)"})
        assert "priority:choice" in r and "done:check" in r
        with pytest.raises(ToolError, match="item's name"):
            await ui.remove_field({"app": "exams", "list": "topics", "field": "name"})
        await tool(reg, "exams", "update_topic").handler({"topic": "Thermodynamics", "priority": "high"})
        assert len(ctx.db.items("exams", "topics")) == 3
        assert any(e["type"] == "app_changed" and e["app"] == "exams" for e in events)                    # an open panel refreshes
    asyncio.run(go())


def test_deleting_asks_first_and_takes_whats_inside(made):
    ctx, reg, ui, events = made

    async def go():
        RUN_CLIENT.set("tab")
        await ui.create_app({"name": "exams"})
        await ui.add_list({"app": "exams", "name": "subjects", "fields": "name*"})
        await ui.add_list({"app": "exams", "name": "topics", "parent": "subjects", "fields": "name*, done: check"})
        await tool(reg, "exams", "add_subject").handler({"name": "Physics"})
        await tool(reg, "exams", "add_topic").handler({"subject": "Physics", "name": "Optics"})
        d = tool(reg, "exams", "delete_item")
        assert d.needs_approval and not reg.is_always_allowed(reg.apps["exams"], d)                         # always a card
        assert d.describe({"list": "subjects", "name": "phys"}) == "Delete the subject 'Physics' and the 1 item(s) inside it"
        assert "No subject" in d.precheck({"list": "subjects", "name": "Biology"})                          # no card for nothing
        await d.handler({"list": "subjects", "name": "Physics"})
        assert ctx.db.items("exams") == []
    asyncio.run(go())


def test_creating_needs_no_approval_and_routing_picks_data_not_api(made):
    ctx, reg, ui, events = made
    for name in ("create_app", "add_list"):
        assert reg.is_always_allowed(ui, next(t for t in ui.tools() if t.name == name))
    text = "Create a app to track my exam studies, I should be able to add subject, add topics for each subject, mark each topic as complete or incomplete, add notes"
    tools, _ = loop.select_tools(reg, text, set())
    assert "ui_create_app" in tools and "ui_add_list" in tools and "ui_connect_api" not in tools
    tools, _ = loop.select_tools(reg, "make an app for the weather api at https://api.open-meteo.com/v1", set())
    assert "ui_connect_api" in tools and "ui_create_app" not in tools


def test_the_panel_api(made, tmp_path):
    ctx, reg, ui, events = made
    from diatonic.server import create_app
    app = create_app(ctx.config, ctx.db, allowed_hosts={"testserver"})
    sctx, sreg = app.state.ctx, app.state.registry
    sctx.mods = ModStore(tmp_path / "m"); sctx.mods.on_change = sreg.sync_api_apps
    sctx.mods.put("data", "reads", '{"title": "Reading", "lists": {"books": {"fields": "name*, done: check, rating: number"}}}')
    with TestClient(app) as c:
        b = c.post("/api/dapps/reads/books", json={"data": {"name": "Dune", "rating": "5"}}).json()
        assert b["data"] == {"name": "Dune", "rating": 5}
        assert c.patch(f"/api/dapps/reads/items/{b['id']}", json={"data": {"done": True}}).json()["data"]["done"] is True
        assert c.post("/api/dapps/reads/books", json={"data": {"rating": "x"}}).status_code == 400
        assert c.get("/api/dapps/reads").json()["items"][0]["data"]["name"] == "Dune"
        assert c.delete(f"/api/dapps/reads/items/{b['id']}").json() == {"deleted": 1}
        assert c.get("/api/apps").json() and any(a["id"] == "reads" and a["module"] == "/apps/_data.js" for a in c.get("/api/apps").json())
