"""Tool effects: every approval, panel and untrusted-content decision reads `effects`, not the old risk label."""
import asyncio

import pytest
from conftest import ScriptedProvider
from starlette.testclient import TestClient

from diatonic.agent import approvals, loop
from diatonic.agent.runs import RunManager
from diatonic.apps.base import DESTRUCTIVE, READ, WRITE, App, Tool
from diatonic.apps.todos.app import TodosApp
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app


def call(tool, /, **args):
    return {"tool_calls": [{"name": tool, "arguments": args}]}


async def _noop(a):
    return {"ok": True}


def test_effects_default_from_the_risk_label():
    assert Tool("a", "", {}, _noop).effects == {"read"}
    assert Tool("b", "", {}, _noop, WRITE).effects == {"write"}
    assert Tool("c", "", {}, _noop, DESTRUCTIVE).effects == {"write", "destroy"}


def test_explicit_effects_set_the_label_and_unknown_ones_are_refused():
    assert Tool("a", "", {}, _noop, effects={"read", "external"}).risk == READ
    assert Tool("b", "", {}, _noop, effects={"execute"}).risk == WRITE
    assert Tool("c", "", {}, _noop, effects={"destroy"}).risk == DESTRUCTIVE
    with pytest.raises(ValueError):
        Tool("d", "", {}, _noop, effects={"teleport"})


def test_only_changing_effects_need_approval():
    assert not Tool("a", "", {}, _noop, effects={"read", "external", "display"}).needs_approval
    for e in ("write", "destroy", "execute", "spend"):
        assert Tool("b", "", {}, _noop, effects={e}).needs_approval


def test_a_tool_that_spends_asks_first_with_no_risk_label(env, monkeypatch):
    ctx, reg, app, db = env
    calls = []

    async def pay(a):
        calls.append(a)
        return {"paid": True}

    monkeypatch.setattr(app, "tools", lambda: [Tool("pay", "pay", {}, pay, effects={"spend"})])
    cards = []

    async def go():
        monkeypatch.setattr(loop, "make_provider", lambda c: ScriptedProvider([[call("fake_pay")], [{"content": "no"}]]))
        async for ev in loop.run_agent(ctx, reg, db.create_conversation()["id"], "pay it"):
            if ev["type"] == "tool_call":
                cards.append(ev)
                asyncio.get_running_loop().call_later(0.01, approvals.resolve, ev["id"], False)

    asyncio.run(go())
    assert cards[0]["needs_approval"] and not calls


def test_a_panel_cannot_call_a_tool_that_executes_even_if_it_calls_itself_a_read(tmp_path, monkeypatch):
    monkeypatch.setattr(TodosApp, "tools", lambda self: [Tool("peek", "p", {}, _noop, READ, effects={"execute"}), Tool("look", "l", {}, _noop)])
    with TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})) as c:
        assert c.post("/api/panel/tools/todos/peek", json={}).status_code == 403
        assert c.post("/api/panel/tools/todos/look", json={}).status_code == 200


class Mail(App):
    id, name = "gmail", "Mail"
    untrusted_tools = ["read_email"]

    def tools(self):
        return [Tool("read_email", "read", {}, _noop)]


class Paint(App):
    id, name = "paint", "Paint"

    def tools(self):
        return [Tool("stripe", "changes the look", {}, _noop, effects={"display"})]


def test_a_run_that_read_mail_cannot_use_a_tool_that_changes_the_interface(env, monkeypatch):
    ctx, reg, app, db = env
    reg.register(Mail(ctx))
    reg.register(Paint(ctx))
    runs = RunManager()
    ctx.send_ui, ctx.registry = runs.send_ui, reg

    async def go():
        monkeypatch.setattr(loop, "make_provider", lambda c: ScriptedProvider([[call("gmail_read_email")], [call("paint_stripe")], [{"content": "x"}]]))
        run = runs.start(ctx, reg, db.create_conversation("S"), "read my email and paint it", False, client="tab-A")
        while not run.task.done():
            await asyncio.sleep(0.01)
        return [e for e in run.events if e["type"] == "tool_result"]

    results = asyncio.run(go())
    assert results[0]["ok"] and not results[1]["ok"] and "can't change the interface" in results[1]["preview"]
