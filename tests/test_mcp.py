"""The MCP server: an outside agent's tool calls go through the same approvals and guards as the chat's own."""
import asyncio

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from diatonic.agent import approvals, session
from diatonic.agent.runs import RunManager
from diatonic.apps.base import READ, App, Tool
from diatonic.mcp import McpHub, endpoint


def hub_for(env):
    ctx, reg, _app, _db = env
    ctx.config.data["mcp"] = {"apps": ["fake", "shell", "ui"]}   # the user has switched these on; shell and ui must still never be offered
    runs = RunManager()
    events = []
    runs.subscribe().put_nowait = events.append   # every published event, in order
    return McpHub(ctx, reg, runs), runs, events


def rpc(method, params=None, mid=1):
    return {"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params is not None else {})}


def test_it_lists_the_tools_of_enabled_apps_with_their_full_names(env):
    hub, _, _ = hub_for(env)
    tok = hub.open("c1")
    names = {t["name"] for t in asyncio.run(hub.handle(tok, rpc("tools/list")))["result"]["tools"]}
    assert {"fake_read", "fake_nuke"} <= names


def test_nothing_is_offered_until_the_user_switches_an_app_on(env):
    ctx, reg, _app, _db = env
    hub = McpHub(ctx, reg, RunManager())
    assert hub.tools() == {}
    tok = hub.open("c1")
    assert asyncio.run(hub.handle(tok, rpc("tools/list")))["result"]["tools"] == []
    assert asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "fake_read", "arguments": {"text": "x"}})))["result"]["isError"] is True


def test_the_terminal_is_not_offered_but_ui_is_available(env):
    ctx, reg, _app, _db = env
    for app_id in ("shell", "ui"):
        a = type("A", (App,), {"id": app_id, "name": app_id, "tools": lambda self: [Tool("x", "x", {}, _noop, READ)]})(ctx)
        reg.register(a)
    hub, _, _ = hub_for(env)
    assert not [n for n in hub.tools() if n.startswith("shell_")]
    assert any(n.startswith("ui_") for n in hub.tools())


def test_ui_tool_receives_run_client_context(env):
    from diatonic.agent.session import RUN_CLIENT

    ctx, reg, _app, _db = env
    called_client = []

    async def _check_client(args):
        called_client.append(RUN_CLIENT.get())
        return {"ok": True}

    a = type("A", (App,), {"id": "ui", "name": "ui", "tools": lambda self: [Tool("test_nav", "test", {}, _check_client, READ)]})(ctx)
    reg.register(a)
    hub, runs, _ = hub_for(env)
    tok = hub.open("conv-test")
    # Simulate an active run with client ID
    run_obj = runs.runs.get("conv-test")
    assert run_obj is not None
    run_obj.client = "window-client-123"

    res = asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "ui_test_nav", "arguments": {}})))
    assert res["result"]["isError"] is False
    assert called_client == ["window-client-123"]


def test_tool_listing_cleans_array_schemas_missing_items(env):
    ctx, reg, _app, _db = env
    a = type("A", (App,), {
        "id": "testapp", "name": "Test App",
        "tools": lambda self: [Tool("bad_array", "test", {"properties": {"items_list": {"type": "array"}}}, _noop, READ)]
    })(ctx)
    reg.register(a)
    ctx.config.data["mcp"] = {"apps": ["testapp"]}
    hub = McpHub(ctx, reg, RunManager())
    tok = hub.open("c1")
    tools = asyncio.run(hub.handle(tok, rpc("tools/list")))["result"]["tools"]
    bad_tool = next(t for t in tools if t["name"] == "testapp_bad_array")
    assert bad_tool["inputSchema"]["properties"]["items_list"].get("items") == {"type": "string"}


async def _noop(a):
    return {}


def test_a_read_call_runs_and_returns_its_result(env):
    hub, _, _ = hub_for(env)
    tok = hub.open("c1")
    r = asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "fake_read", "arguments": {"text": "hi"}})))["result"]
    assert r["isError"] is False and "hi" in r["content"][0]["text"]


def test_a_destructive_call_waits_for_the_user_and_runs_only_after_yes(env):
    ctx, reg, app, _ = env
    hub, _, events = hub_for(env)
    tok = hub.open("c1")

    async def go():
        t = asyncio.create_task(hub.handle(tok, rpc("tools/call", {"name": "fake_nuke", "arguments": {"id": 3}})))
        for _ in range(50):
            await asyncio.sleep(0.01)
            card = next((e for e in events if e.get("type") == "tool_call" and e.get("needs_approval")), None)
            if card:
                break
        assert card and not app.calls, "the card is shown and nothing has run yet"
        assert approvals.resolve(card["id"], True)
        return await t

    r = asyncio.run(go())["result"]
    assert r["isError"] is False and app.calls == [("nuke", {"id": 3})]


def test_denying_the_card_stops_the_call_and_tells_the_agent(env):
    ctx, reg, app, _ = env
    hub, _, events = hub_for(env)
    tok = hub.open("c1")

    async def go():
        t = asyncio.create_task(hub.handle(tok, rpc("tools/call", {"name": "fake_nuke", "arguments": {"id": 3}})))
        for _ in range(50):
            await asyncio.sleep(0.01)
            card = next((e for e in events if e.get("type") == "tool_call" and e.get("needs_approval")), None)
            if card:
                break
        approvals.resolve(card["id"], False)
        return await t

    r = asyncio.run(go())["result"]
    assert r["isError"] is True and "denied" in r["content"][0]["text"].lower() and app.calls == []


def test_a_private_app_asks_before_its_data_goes_to_the_cloud_agent(env):
    ctx, reg, app, _ = env
    app.private = True
    hub, _, events = hub_for(env)
    tok = hub.open("c1")

    async def go():
        t = asyncio.create_task(hub.handle(tok, rpc("tools/call", {"name": "fake_read", "arguments": {"text": "x"}})))
        for _ in range(50):
            await asyncio.sleep(0.01)
            card = next((e for e in events if e.get("type") == "tool_call" and e.get("share")), None)
            if card:
                break
        assert card and not app.calls and "Antigravity" in card["describe"]
        approvals.resolve(card["id"], True)
        return await t

    assert asyncio.run(go())["result"]["isError"] is False


def test_auto_permission_mode_allows_safe_private_app_reads_without_asking(env):
    ctx, reg, app, _ = env
    app.private = True
    ctx.config.data["antigravity"] = {"permissions": "auto"}
    hub, _, events = hub_for(env)
    tok = hub.open("c1")

    # Safe read runs directly without asking
    r = asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "fake_read", "arguments": {"text": "hello"}})))["result"]
    assert r["isError"] is False
    assert "hello" in r["content"][0]["text"]
    assert app.calls == [("read", {"text": "hello"})]

    # Destructive action STILL requires approval
    app.calls.clear()

    async def go_nuke():
        t = asyncio.create_task(hub.handle(tok, rpc("tools/call", {"name": "fake_nuke", "arguments": {"id": 42}})))
        for _ in range(50):
            await asyncio.sleep(0.01)
            card = next((e for e in events if e.get("type") == "tool_call" and e.get("needs_approval")), None)
            if card:
                break
        assert card and not app.calls
        approvals.resolve(card["id"], True)
        return await t

    r_nuke = asyncio.run(go_nuke())["result"]
    assert r_nuke["isError"] is False
    assert app.calls == [("nuke", {"id": 42})]



def test_a_session_that_read_untrusted_content_stays_marked_across_calls(env):
    ctx, reg, app, _ = env
    hub, _, _ = hub_for(env)
    tok = hub.open("c1")
    app.untrusted_tools = ["read"]
    asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "fake_read", "arguments": {"text": "x"}})))
    assert hub.sessions[tok].untrusted == {"fake": True}
    other = hub.open("c2")
    assert hub.sessions[other].untrusted == {}, "another conversation is not marked"


def test_unknown_tools_and_bad_requests_get_a_clear_error_not_a_crash(env):
    hub, _, _ = hub_for(env)
    tok = hub.open("c1")
    assert asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "nope", "arguments": {}})))["result"]["isError"] is True
    assert asyncio.run(hub.handle(tok, rpc("resources/list")))["error"]["code"] == -32601
    assert asyncio.run(hub.handle(tok, [1, 2]))["error"]["code"] == -32600
    assert asyncio.run(hub.handle(tok, {"jsonrpc": "2.0", "method": "notifications/initialized"})) is None


def test_the_http_route_needs_the_conversations_token(env):
    hub, _, _ = hub_for(env)
    tok = hub.open("c1")
    with TestClient(Starlette(routes=[Route("/mcp", endpoint(hub), methods=["POST", "GET"])])) as c:
        assert c.post("/mcp", json=rpc("tools/list")).status_code == 401
        assert c.post("/mcp", json=rpc("tools/list"), headers={"Authorization": "Bearer wrong"}).status_code == 401
        ok = c.post("/mcp", json=rpc("tools/list"), headers={"Authorization": f"Bearer {tok}"})
        assert ok.status_code == 200 and ok.json()["result"]["tools"]
        assert c.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers={"Authorization": f"Bearer {tok}"}).status_code == 202
        assert c.get("/mcp", headers={"Authorization": f"Bearer {tok}"}).status_code == 405
        hub.close(tok)
        assert c.post("/mcp", json=rpc("tools/list"), headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_the_events_of_a_call_reach_the_conversations_run(env):
    hub, runs, events = hub_for(env)
    tok = hub.open("c1", "Antigravity")
    asyncio.run(hub.handle(tok, rpc("tools/call", {"name": "fake_read", "arguments": {"text": "x"}})))
    kinds = [e["type"] for e in events if e.get("conversation_id") == "c1"]
    assert kinds[0] == "run_start" and "tool_call" in kinds and "tool_result" in kinds
    hub.close(tok)
    assert "c1" not in runs.runs
