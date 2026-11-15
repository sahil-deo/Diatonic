"""Connected apps: apps the agent builds on tools Diatonic already has (here a Meet scheduler on Calendar), on the real account."""
import asyncio
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent))
from test_google import HOME, WORK, gx, inspect  # noqa: E402,F401  (gx is a fixture)

from diatonic.agent import session  # noqa: E402
from diatonic.agent.runs import RunManager  # noqa: E402
from diatonic.apps.base import ToolError  # noqa: E402
from diatonic.apps.registry import Registry  # noqa: E402
from diatonic.apps.ui import create as create_ui  # noqa: E402
from diatonic.mods import ModError, ModStore  # noqa: E402
from diatonic.server import create_app  # noqa: E402
from diatonic.toolapps import check_tool_manifest, parse_fixed  # noqa: E402


@pytest.fixture
def made(gx, tmp_path):
    ctx, ga, cal, mail = gx
    ctx.mods = ModStore(tmp_path)
    runs, events = RunManager(), []
    ctx.send_ui, ctx.broadcast = runs.send_ui, events.append
    ctx.registry = reg = Registry(ctx)
    reg.register(cal); reg.register(mail)
    ctx.mods.on_change = reg.sync_api_apps
    ui = create_ui(ctx); reg.register(ui)
    return ctx, reg, ui


def run(coro):
    async def go():
        session.RUN_CLIENT.set("tab")      # a chat run with a window: it may change the interface
        return await coro
    return asyncio.run(go())


def act(reg, app, name, **args):
    return run(next(t for t in reg.apps[app].tools() if t.name == name).handler(args))


def build_meet_app(ui):
    session.REQUEST.set("make an app to schedule google meets on my account")
    out = run(ui.create_connected_app({"name": "meets", "title": "Meet Scheduler", "apps": "calendar"}))
    assert "gcal_create_event(summary*, start*" in out and "gcal_delete_event(event*) [asks first]" in out   # the catalog to build from
    run(ui.add_app_action({"app": "meets", "name": "schedule", "title": "Schedule", "tool": "gcal_create_event",
                           "preset": "add_meet=true, account=work", "inputs": "summary, start, end, attendees"}))
    run(ui.add_app_action({"app": "meets", "name": "upcoming", "title": "Upcoming meetings", "tool": "list_events", "preset": "only_meet=true"}))
    run(ui.add_app_action({"app": "meets", "name": "reschedule", "tool": "gcal_update_event", "inputs": "event, start, end"}))
    run(ui.add_app_action({"app": "meets", "name": "invite", "title": "Add guests", "tool": "gcal_update_event", "inputs": ["event", "add_guests"]}))
    return run(ui.add_app_action({"app": "meets", "name": "cancel", "title": "Cancel", "tool": "gcal_delete_event"}))


def test_presets_are_read_the_way_a_small_model_writes_them():
    assert parse_fixed("add_meet=true, calendar: Work, max_results=5") == {"add_meet": True, "calendar": "Work", "max_results": 5}
    assert check_tool_manifest({"actions": {"go": {"tool": "gcal_list_events"}}})["actions"]["go"]["title"] == "Go"
    for bad in [{"actions": {"Go!": {"tool": "gcal_x"}}}, {"actions": {"go": {"tool": "nothing"}}}]:
        with pytest.raises(ValueError):
            check_tool_manifest(bad)


def test_a_meet_scheduler_built_on_calendar(made):
    ctx, reg, ui = made
    out = build_meet_app(ui)
    assert "asks before it runs" in out
    app = reg.apps["meets"]
    assert app.panel and app.module == "/apps/_tools.js" and app.private          # as private as the calendar it uses
    tools = {t.name: t for t in app.tools()}
    assert set(tools) == {"schedule", "upcoming", "reschedule", "invite", "cancel"}
    assert tools["cancel"].risk == "destructive" and tools["schedule"].risk == "write" and tools["upcoming"].risk == "read"
    assert "add_meet" not in tools["schedule"].parameters["properties"] and tools["schedule"].parameters["required"] == ["summary", "start"]

    made_ev = act(reg, "meets", "schedule", summary="Design sync", start="2030-07-01T10:00:00", attendees="a@x.test, b@y.test")["created"]
    assert made_ev["meet"] and made_ev["ref"].startswith(WORK + "|") and made_ev["guests"] == ["a@x.test", "b@y.test"]
    raw = inspect(WORK).events["primary"][made_ev["ref"].split("|")[2]]
    assert raw["conferenceData"] and raw["attendees"] == [{"email": "a@x.test"}, {"email": "b@y.test"}]

    listed = act(reg, "meets", "upcoming", start="2030-06-30", end="2030-07-03")["events"]
    assert [e["title"] for e in listed] == ["Design sync"] and listed[0]["meet"]

    act(reg, "meets", "invite", event=made_ev["ref"], add_guests="c@z.test")                 # adds, keeps the others
    assert [x["email"] for x in raw["attendees"]] == ["a@x.test", "b@y.test", "c@z.test"]
    moved = act(reg, "meets", "reschedule", event=made_ev["ref"], start="2030-07-02T15:00:00")["updated"]
    assert moved["start"].startswith("2030-07-02T15:00")
    with pytest.raises(ToolError, match="needs event"):
        act(reg, "meets", "cancel")
    act(reg, "meets", "cancel", event=made_ev["ref"])
    assert made_ev["ref"].split("|")[2] not in inspect(WORK).events["primary"]


def test_mistakes_get_errors_that_say_what_to_do(made):
    ctx, reg, ui = made
    build_meet_app(ui)
    with pytest.raises(ToolError, match="isn't a tool of gcal. Choose one of: gcal_list_calendars"):
        run(ui.add_app_action({"app": "meets", "name": "x", "tool": "gmail_send_email"}))
    with pytest.raises(ToolError, match="has no parameter colour"):
        run(ui.add_app_action({"app": "meets", "name": "x", "tool": "gcal_create_event", "inputs": "summary, start, colour"}))
    with pytest.raises(ToolError, match="needs start: add it to inputs"):
        run(ui.add_app_action({"app": "meets", "name": "x", "tool": "gcal_create_event", "inputs": "summary"}))
    with pytest.raises(ToolError, match="isn't one of your apps"):
        run(ui.create_connected_app({"name": "rocket", "apps": "spaceship"}))
    run(ui.remove_app_action({"app": "meets", "name": "invite"}))
    assert "invite" not in {t.name for t in reg.apps["meets"].tools()}


def test_requests_about_accounts_get_a_connected_app_not_a_local_list(made):
    ctx, reg, ui = made
    names = lambda text: {t.name for t in ui.tools_for(text)}
    assert {"create_connected_app", "add_app_action"} <= names("create a google meet scheduler app") and "create_app" not in names("create a google meet scheduler app")
    assert "create_app" in names("make an exam tracker app") and "create_connected_app" not in names("make an exam tracker app")
    session.REQUEST.set("create a google meet scheduler app")
    with pytest.raises(ToolError, match="create_connected_app with apps=gcal"):
        run(ui.create_app({"name": "meet", "title": "Meet Scheduler"}))
    session.REQUEST.set("an app to log my meetings locally")
    run(ui.create_app({"name": "meetlog"}))                                                       # explicitly local: fine
    assert reg.apps["meetlog"].data_app
    assert "(meetlog2) is created" in run(ui.create_connected_app({"name": "meetlog", "apps": "gcal"}))   # the name was taken
    assert reg.apps["meetlog"].data_app and reg.apps["meetlog2"].tool_app
    build_meet_app(ui)
    assert "create_connected_app" in names("add a description field to the meet scheduler")    # named: keep building it


def test_the_panel_runs_actions_and_deleting_needs_confirmation(gx, tmp_path):
    ctx, ga, cal, mail = gx
    app = create_app(ctx.config, ctx.db, allowed_hosts={"testserver"}, http=ctx.http)
    sctx = app.state.ctx
    sctx.google = ga; ga.ctx = sctx
    sctx.mods.put("tools", "meets", '{"title": "Meets", "apps": ["gcal"], "actions": {'
                  '"upcoming": {"tool": "gcal_list_events", "fixed": {"only_meet": true}},'
                  '"schedule": {"tool": "gcal_create_event", "fixed": {"add_meet": true, "account": "home"}, "params": ["summary", "start", "attendees"]},'
                  '"cancel": {"tool": "gcal_delete_event"}}}')
    with TestClient(app) as c:
        info = c.get("/api/tapps/meets").json()
        assert info["actions"]["cancel"] == {**info["actions"]["cancel"], "risk": "destructive", "ref": "event"}
        assert set(info["actions"]["schedule"]["schema"]["properties"]) == {"summary", "start", "attendees"}
        r = c.post("/api/tapps/meets/call/schedule", json={"args": {"summary": "1:1", "start": "2030-08-01T09:00:00", "attendees": "q@x.test"}}).json()
        ref = r["result"]["created"]["ref"]
        assert ref.startswith(HOME + "|") and r["result"]["created"]["meet"]
        assert c.post("/api/tapps/meets/call/schedule", json={"args": {"summary": "no time"}}).json()["error"] == "Schedule needs start."
        assert c.post("/api/tapps/meets/call/cancel", json={"args": {"event": ref}}).status_code == 400   # not confirmed
        assert c.post("/api/tapps/meets/call/cancel", json={"args": {"event": ref}, "confirm": True}).json()["result"] == {"deleted": ref}
        assert c.get("/api/tapps/gcal").status_code == 404
        home = c.get("/api/home/meets").json()
        assert "items" in home
    with pytest.raises(ModError, match="isn't a tool name"):
        sctx.mods.put("tools", "bad", '{"actions": {"go": {"tool": "rm -rf"}}}')


def test_a_small_models_shapes_are_forgiven(made):
    ctx, reg, ui = made
    session.REQUEST.set("an app that books meetings and invites guests")
    run(ui.create_connected_app({"name": "book", "apps": "gcal"}))
    out = run(ui.add_app_action({"app": "book", "name": "list", "tool": "gcal_list_events", "preset": "start=2026-11-15T00:00:00, account="}))
    assert "start left out" in out and reg.apps["book"].m["actions"]["list"]["fixed"] == {}            # a fixed date would go stale
    run(ui.add_app_action({"app": "book", "name": "book", "tool": "gcal_create_event", "preset": "add_meet=true, calendar=",
                           "inputs": "summary=Meeting, start=YYYY-MM-DD"}))
    a = reg.apps["book"].m["actions"]["book"]
    assert a["fixed"] == {"add_meet": True} and a["params"] == ["summary", "start", "attendees"]        # "guests" in the request -> attendees


def test_the_request_decides_the_actions_and_the_tools_decide_the_details(made):
    ctx, reg, ui = made
    session.REQUEST.set("Create a Google Meet scheduler app that creates meetings on my account, adds guests, lets me change the time, "
                        "deletes meetings and lists my meetings.")
    run(ui.create_connected_app({"name": "meet", "apps": "gcal"}))
    acts = reg.apps["meet"].m["actions"]
    assert [a["tool"] for a in acts.values()] == ["gcal_create_event", "gcal_list_events", "gcal_update_event", "gcal_delete_event"]
    assert acts["schedule"]["fixed"] == {"add_meet": True} and "attendees" in acts["schedule"]["params"]   # a Meet app: every meeting gets a link
    assert acts["upcoming"]["fixed"] == {"only_meet": True}
    assert "add_guests" in acts["edit"]["params"] and "attendees" not in acts["edit"]["params"]            # adding guests keeps the others
    session.REQUEST.set("make an app that lists my calendar events")                                        # only what's asked (and a list)
    run(ui.create_connected_app({"name": "cal", "apps": "gcal"}))
    assert [a["tool"] for a in reg.apps["cal"].m["actions"].values()] == ["gcal_list_events"]
    out = run(ui.add_app_action({"app": "meet", "name": "edit", "tool": "gcal_update_event", "preset": "description=Updated", "inputs": "event, attendees"}))
    assert "description left out" in out and reg.apps["meet"].m["actions"]["edit"]["params"] == ["event", "add_guests"]
