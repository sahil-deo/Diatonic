"""The catalog: one searchable list of tools, commands and (later) components, skills and ctx methods, built from the live registries."""
import asyncio

import pytest
from starlette.testclient import TestClient

from diatonic.apps.registry import Registry
from diatonic.catalog import KINDS, Entry, tool_example
from diatonic.config import Config
from diatonic.context import Context
from diatonic.db import Database
from diatonic.server import create_app


@pytest.fixture
def web(tmp_path):
    with TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})) as c:
        yield c


def test_every_built_in_tool_has_a_doc_and_an_example(tmp_path):
    ctx = Context(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"))
    ctx.registry = Registry(ctx).discover()
    tools = [e for e in ctx.catalog.entries() if e.kind == "tool"]
    assert len(tools) > 50
    for e in tools:
        assert e.doc.strip(), f"{e.name} has no description"
        assert e.example.startswith(e.name + "("), f"{e.name} has no example"


def test_search_ranks_by_name_then_doc_and_finds_each_kind(env):
    ctx, reg, app, db = env
    cat = ctx.catalog
    ctx.registry = reg
    assert cat.search("nuke")[0].name == "fake_nuke"
    assert cat.search("dangerous")[0].name == "fake_nuke"          # found by its description
    assert cat.search("xyzzy plugh") == []
    assert cat.set_commands([{"id": "panel.close", "title": "Close app panel", "group": "Apps"}, {"nope": 1}, "x"]) == 1
    assert cat.search("close panel", "command")[0].name == "panel.close"
    cat.add_source(lambda: [Entry("component", "ctx.ui.zapbox", "A zappable box.", example="ctx.ui.zapbox({})"), Entry("skill", "zapping", "How to zap a gizmo."),
                            Entry("ctx-api", "ctx.zap", "Zap the widget.", example="ctx.zap()")])
    for kind, word in (("component", "zappable"), ("skill", "gizmo"), ("ctx-api", "widget")):
        assert [e.kind for e in cat.search(word, kind)] == [kind]
    assert set(KINDS) == {"tool", "command", "component", "skill", "ctx-api"}


def test_example_uses_required_arguments_and_enums():
    ex = tool_example("a_b", {"properties": {"n": {"type": "integer"}, "mode": {"enum": ["fast", "slow"]}, "x": {}}, "required": ["n", "mode"]})
    assert ex == "a_b(n=1, mode='fast')"


def test_describe_returns_the_whole_entry(env):
    ctx, reg, app, db = env
    ctx.registry = reg
    e = ctx.catalog.describe("fake_nuke")
    assert e.effects == ["destroy", "write"] and e.args == [{"name": "id", "type": "integer", "doc": "", "required": True}] and e.example == "fake_nuke(id=1)"
    assert ctx.catalog.describe("fake_nope") is None


def test_a_private_apps_entries_are_hidden_when_asked(env):
    ctx, reg, app, db = env
    ctx.registry = reg
    assert ctx.catalog.search("nuke", hidden={"fake"}) == [] and ctx.catalog.describe("fake_nuke", hidden={"fake"}) is None


def test_the_agent_tools_hide_private_apps_from_a_cloud_model_until_allowed(env, monkeypatch):
    from diatonic.apps.ui import create as create_ui
    ctx, reg, app, db = env
    ctx.registry = reg
    reg.register(ui := create_ui(ctx))
    app.private = True
    assert "fake_nuke" in str(asyncio.run(ui.platform_search({"query": "nuke"})))
    monkeypatch.setattr(ctx, "cloud", lambda: True)
    assert asyncio.run(ui.platform_search({"query": "nuke"})) == "Nothing matches. Try other words."
    ctx.cloud_grants[(None, ctx.config.data["agent"]["model"])] = {"fake"}
    assert "fake_nuke" in str(asyncio.run(ui.platform_search({"query": "nuke"})))


def test_search_results_are_short(env):
    from diatonic.apps.ui import create as create_ui
    ctx, reg, app, db = env
    ctx.registry = reg
    reg.register(ui := create_ui(ctx))
    out = asyncio.run(ui.platform_search({"query": "app"}))
    assert len(out) <= 8 and len("\n".join(out)) < 2000          # a few hundred tokens at most
    assert asyncio.run(ui.platform_describe({"name": "ui_open_app"}))["example"].startswith("ui_open_app(")


def test_the_http_api_searches_and_takes_the_pages_commands(web):
    assert web.post("/api/catalog/commands", json=[{"id": "chat.new", "title": "New chat", "group": "Chat"}]).json() == {"ok": True, "count": 1}
    assert web.get("/api/catalog?q=new chat&kind=command").json()["results"][0]["name"] == "chat.new"
    assert web.get("/api/catalog?q=todo").json()["results"]
    assert web.get("/api/catalog?name=chat.new").json()["example"] == 'command("chat.new")'
    assert web.get("/api/catalog?name=nothing").status_code == 404
    assert web.get("/api/catalog?kind=bogus").status_code == 400
