"""Panels reach the harness (read-only tools, the user's tracker apps) and the agent edits panels in place, shown as a diff."""
import pytest
from starlette.testclient import TestClient
from test_mods import call, setup
from test_verify import GOOD, OK, chat

from diatonic.agent.loop import select_tools
from diatonic.apps.ui import sdk
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})) as c:
        yield c


# ---------- a panel reads through tools ----------
def test_a_panel_can_run_a_read_only_tool(client):
    client.post("/api/apps/todos/create", json={"title": "buy milk"}, headers=ORIGIN)
    r = client.post("/api/panel/tools/todos/list_todos", json={}, headers=ORIGIN)
    assert r.status_code == 200 and "buy milk" in r.text


def test_a_panel_cannot_run_tools_that_change_things(client):
    r = client.post("/api/panel/tools/todos/add_todo", json={"title": "x"}, headers=ORIGIN)
    assert r.status_code == 403 and "ask the agent" in r.json()["error"]
    assert client.post("/api/panel/tools/todos/delete_todo", json={"id": 1}, headers=ORIGIN).status_code == 403


@pytest.mark.parametrize("path", ["shell/run", "ui/open_app", "nope/x", "todos/nope"])
def test_some_things_are_never_available_to_panels(client, path):
    assert client.post(f"/api/panel/tools/{path}", json={}, headers=ORIGIN).status_code == 404


def test_bad_arguments_are_refused_and_foreign_origins_blocked(client):
    assert client.post("/api/panel/tools/todos/list_todos", json={"status": 5}, headers=ORIGIN).status_code == 400
    assert client.post("/api/panel/tools/todos/list_todos", json={}, headers={"origin": "http://evil.com"}).status_code == 403


# ---------- editing a panel in place ----------
CODE = "const b = diatonic.h('button', {style: 'color: red'}, 'Go');\nel.append(b);"


def seed(ctx, tmp_path=None):
    ctx.mods.put("panel", "timer", CODE, "Timer")


def run(env, tmp_path, monkeypatch, script, answers=(OK,)):
    ctx, *_ = setup(env, tmp_path)
    seed(ctx)
    return chat_with_seed(env, tmp_path, monkeypatch, script, answers)


def chat_with_seed(env, tmp_path, monkeypatch, script, answers):
    # chat() builds its own context from `env`, so seed the store it will use
    import test_verify
    orig = test_verify.setup

    def seeded(e, t):
        out = orig(e, t)
        seed(out[0])
        return out
    monkeypatch.setattr(test_verify, "setup", seeded)
    return chat(env, tmp_path, monkeypatch, script, list(answers))


def test_edit_panel_replaces_one_piece_and_shows_a_diff(env, tmp_path, monkeypatch):
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name="timer", old="color: red", new="color: green")], [{"content": "ok"}]], [OK])
    card = [e for e in ev if e.get("type") == "tool_call"][0]
    assert card["needs_approval"] and "-const b" in card["preview"] and "+const b" in card["preview"] and "green" in card["preview"]
    assert ctx.mods.get("timer")["code"] == CODE.replace("red", "green") and asked[0]["code"].count("green") == 1   # the new code was test-run first


@pytest.mark.parametrize("old,why", [("not in the code", "not in the code"), ("e", "appears")])
def test_edit_panel_refuses_a_piece_that_does_not_match_exactly_once(env, tmp_path, monkeypatch, old, why):
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name="timer", old=old, new="x")], [{"content": "no"}]], [])
    res = [e for e in ev if e.get("type") == "tool_result"][0]
    assert not res["ok"] and why in res["preview"] and ctx.mods.get("timer")["code"] == CODE and not asked   # nothing to test, nothing stored


def test_an_edit_that_breaks_the_panel_is_sent_back(env, tmp_path, monkeypatch):
    bad = {"errors": ["x is not defined"], "rendered": True, "usedApi": False}
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name="timer", old="'Go'", new="x")], [{"content": "no"}]], [bad])
    assert ctx.mods.get("timer")["code"] == CODE and "x is not defined" in [e for e in ev if e.get("type") == "tool_result"][0]["preview"]


def test_rewriting_an_existing_panel_shows_only_what_differs(env, tmp_path, monkeypatch):
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_add_panel", name="timer", title="Timer", code=CODE + "\nel.append('more');")], [{"content": "ok"}]], [OK])
    card = [e for e in ev if e.get("type") == "tool_call"][0]
    assert card["preview"].startswith("Changes to the existing panel") and "+el.append('more');" in card["preview"]
    assert "+const b" not in card["preview"] and "-const b" not in card["preview"]       # unchanged lines are context, not additions


def test_list_mods_with_a_name_returns_the_code_to_copy_from(env, tmp_path, monkeypatch):
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_list_mods", name="timer")], [{"content": "ok"}]], [])
    result = [e for e in ev if e.get("type") == "tool_result"][0]
    assert result["ok"] and "diatonic.h('button'" in result["preview"]


# ---------- the tool is offered when it makes sense ----------
def test_edit_is_offered_only_when_there_is_something_to_edit(env, tmp_path):
    ctx, reg, db, runs, events = setup(env, tmp_path)
    ask = "make the timer button green"
    assert "ui_edit_panel" not in select_tools(reg, ask, set())[0]
    seed(ctx)
    assert "ui_edit_panel" in select_tools(reg, ask, set())[0]
    assert "ui_edit_panel" not in select_tools(reg, "show my calendar", set())[0]


def test_the_sdk_mentions_the_new_helpers_and_still_fits():
    text = sdk.SDK
    assert "ctx.tool(" in text and "ctx.data(" in text
    assert len(sdk.SDK + sdk.examples()) < 2400


def test_the_verification_request_lists_what_a_panel_may_call(env, tmp_path, monkeypatch):
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[call("ui_add_panel", name="p", title="P", code=GOOD)], [{"content": "ok"}]], [OK])
    assert "fake_read" in asked[0]["tools"] and "fake_nuke" not in asked[0]["tools"] and not any(t.startswith(("ui_", "shell_")) for t in asked[0]["tools"])
    assert asked[0]["trackers"] == []


# ---------- what the approval card says, and what a panel can read ----------
def test_the_approval_card_leads_with_the_models_one_sentence_summary(env, tmp_path, monkeypatch):
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[call("ui_add_panel", name="p", title="Timer", code=GOOD, summary="A 25 minute countdown with start and reset.")], [{"content": "ok"}]], [OK])
    card = [e for e in ev if e.get("type") == "tool_call"][0]
    assert card["describe"].startswith("Add a new panel 'Timer'") and card["describe"].endswith(": A 25 minute countdown with start and reset.")
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[call("ui_add_panel", name="q", title="Plain", code=GOOD)], [{"content": "ok"}]], [OK])
    assert [e for e in ev if e.get("type") == "tool_call"][0]["describe"] == "Add a new panel 'Plain'"          # no summary: nothing invented


def test_a_panel_can_read_reminders_and_jobs_but_not_change_them(client):
    assert client.post("/api/panel/tools/scheduler/list_reminders", json={}, headers=ORIGIN).status_code == 200
    assert client.post("/api/panel/tools/scheduler/list_jobs", json={}, headers=ORIGIN).status_code == 200
    assert client.post("/api/panel/tools/scheduler/delete_job", json={"id": 1}, headers=ORIGIN).status_code == 403


def test_changing_something_already_built_offers_only_the_tools_for_changing_it(env, tmp_path):
    ctx, reg, db, runs, events = setup(env, tmp_path)
    seed(ctx)
    tools, _ = select_tools(reg, "Make the timer button green", set())
    ui = {n for n in tools if n.startswith("ui_")}
    assert "ui_edit_panel" in ui and "ui_list_mods" in ui
    assert not {"ui_create_app", "ui_add_field", "ui_add_list"} & ui       # the model once "added a duration field" instead of editing


def test_the_sdk_tells_the_model_what_the_common_tools_return():
    for part in ("todos_list_todos gives [{id, title", "gcal_list_events {events:", "Array.isArray"):
        assert part in sdk.SDK


# ---------- editing forgives the usual copy mistakes ----------
def test_old_text_may_differ_in_spacing_and_line_breaks(env, tmp_path, monkeypatch):
    sloppy = "const b = diatonic.h('button',   {style: 'color: red'},\n 'Go');"          # not how the code is laid out, same code
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name="timer", old=sloppy, new="const b = diatonic.h('button', {}, 'Stop');")], [{"content": "ok"}]], [OK])
    assert ctx.mods.get("timer")["code"] == "const b = diatonic.h('button', {}, 'Stop');\nel.append(b);"


def test_a_missed_copy_says_which_lines_are_closest(env, tmp_path, monkeypatch):
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name="timer", old="el.appendChild(b)", new="x")], [{"content": "no"}]], [])
    msg = [e for e in ev if e.get("type") == "tool_result"][0]["preview"]
    assert "closest lines" in msg and "el.append(b);" in msg and ctx.mods.get("timer")["code"] == CODE


@pytest.mark.parametrize("name", ["timer", "Timer", "pomodoro timer", "the-timer", "pomodoro"])
def test_the_panel_is_found_by_a_loose_name_when_the_match_is_clear(env, tmp_path, monkeypatch, name):
    ctx, asked, ev = chat_with_seed(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name=name, old="color: red", new="color: blue")], [{"content": "ok"}]], [OK])
    assert "color: blue" in ctx.mods.get("timer")["code"]


def test_with_two_panels_a_wrong_name_is_an_error_that_lists_them(env, tmp_path, monkeypatch):
    import test_verify
    orig = test_verify.setup

    def two(e, t):
        out = orig(e, t)
        seed(out[0]); out[0].mods.put("panel", "clock", "el.append('c')", "Clock")
        return out
    monkeypatch.setattr(test_verify, "setup", two)
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[call("ui_edit_panel", name="weather", old="x", new="y")], [{"content": "no"}]], [])
    msg = [e for e in ev if e.get("type") == "tool_result"][0]["preview"]
    assert not asked and "timer" in msg and "clock" in msg
