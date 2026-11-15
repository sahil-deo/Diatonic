"""Home-screen tiles: each app's snapshot, the tile endpoint, the layout setting and the agent's set_home tool."""
import asyncio
import time

import pytest
from starlette.testclient import TestClient

from diatonic.actions import clean_appearance
from diatonic.agent.session import RUN_CLIENT
from diatonic.apps.base import ToolError
from diatonic.apps.todos import create as todos
from diatonic.apps.ui import create as create_ui
from diatonic.mods import ModStore


def test_layout_setting_is_validated():
    assert clean_appearance({"home": {"layout": "two_cols", "slots": ["todos", "gmail"]}})["home"] == {"layout": "two_cols", "slots": ["todos", "gmail"]}
    for bad in [{"layout": "three"}, {"layout": "grid4", "slots": ["a"] * 5}, "grid4"]:
        with pytest.raises(ValueError):
            clean_appearance({"home": bad})


def test_todos_tile_puts_overdue_first_and_counts(env):
    ctx, reg, app, db = env
    t = todos(ctx)

    async def go():
        await next(x for x in t.tools() if x.name == "add_todo").handler({"title": "Pay rent", "due": "tomorrow 9am"})
        await next(x for x in t.tools() if x.name == "add_todo").handler({"title": "Read paper"})
        ctx.db.conn.execute("UPDATE todos SET due=? WHERE title='Pay rent'", (time.time() - 86400,)); ctx.db.conn.commit()   # now overdue
        return await t.snapshot(3)
    s = asyncio.run(go())
    assert [i["text"] for i in s["items"]] == ["Pay rent", "Read paper"] and s["items"][0]["tone"] == "warn"
    assert {x["label"]: x["value"] for x in s["stats"]} == {"open": 2, "overdue": 1}


def test_tile_endpoint_lists_caches_and_contains_failures(env):
    ctx, reg, app, db = env
    from diatonic.server import create_app
    web = create_app(ctx.config, ctx.db, allowed_hosts={"testserver"})
    sreg = web.state.registry
    calls = {"n": 0}

    async def flaky(n):
        calls["n"] += 1
        raise RuntimeError("boom")

    async def counted(n):
        calls["n"] += 1
        return {"items": [{"text": f"item {calls['n']}"}]}
    with TestClient(web) as c:
        assert {"todos", "scheduler", "telemetry", "notes"} <= {x["id"] for x in c.get("/api/home").json()}
        assert "files" not in {x["id"] for x in c.get("/api/home").json()}          # no snapshot, no tile
        sreg.apps["notes"].snapshot = counted
        first = c.get("/api/home/notes?n=3").json()
        assert c.get("/api/home/notes?n=3").json() == first and calls["n"] == 1     # cached for a minute
        assert c.get("/api/home/notes?n=3&fresh=1").json() != first                  # unless asked fresh (refresh button, data changed)
        sreg.apps["notes"].snapshot = flaky
        assert "RuntimeError" in c.get("/api/home/notes?n=5").json()["error"]       # a broken tile doesn't break the page
        assert c.get("/api/home/files").status_code == 404


def test_the_agent_can_set_the_home_screen(env, tmp_path):
    ctx, reg, app, db = env
    ctx.mods = ModStore(tmp_path)
    ctx.registry, ctx.broadcast = reg, (lambda ev: None)
    t = todos(ctx); reg.register(t)
    ui = create_ui(ctx); reg.register(ui)
    from diatonic.apps.base import App

    class Viewer(App):   # has a panel but no home tile
        id, name, panel = "viewer", "Viewer", True
    reg.register(Viewer(ctx))

    async def go():
        RUN_CLIENT.set("tab")
        r = await ui.set_home({"layout": "side by side", "apps": ["to-dos"]})
        assert "To-dos as 2 tiles side by side" in r and ctx.config.data["appearance"]["home"] == {"layout": "two_cols", "slots": ["todos"]}
        with pytest.raises(ToolError, match="can't be shown"):
            await ui.set_home({"apps": ["viewer"]})
        ctx.mods.revert(ctx.config)
        assert ctx.config.data["appearance"]["home"]["layout"] == "grid4"
    asyncio.run(go())
    from diatonic.agent import loop
    for text in ["put my todos on the home screen", "put my GitHub issues and the exam tracker on the home screen, stacked"]:
        tools, _ = loop.select_tools(reg, text, set())
        assert "ui_set_home" in tools and "ui_add_button" not in tools, text
