"""API apps: data-defined apps run by one runtime. Built step by step by the agent, tested as they're built."""
import asyncio
import json

import httpx
import pytest

from diatonic import apiapps
from diatonic.agent import loop
from diatonic.agent.runs import RunManager
from diatonic.agent.session import RUN_CLIENT
from diatonic.apiapps import AppSpecError, check_manifest, key_paths, parse_params, pick
from diatonic.apps.base import ToolError
from diatonic.apps.ui import create as create_ui
from diatonic.mods import ModStore

WEATHER = {"location": {"name": "Pune", "country": "India"}, "current": {"temp_c": 29.5, "condition": {"text": "Sunny"}}}


def test_params_are_read_the_way_a_small_model_writes_them():
    assert parse_params("q*: City = London, days: Days ahead = 1, lang") == [
        {"name": "q", "label": "City", "required": True, "default": "London"},
        {"name": "days", "label": "Days ahead", "required": False, "default": "1"},
        {"name": "lang", "label": "lang", "required": False, "default": ""}]
    with pytest.raises(AppSpecError):
        parse_params("bad name!")


def test_manifest_rules():
    m = check_manifest({"title": "W", "base_url": "https://api.x.test/v1/", "auth": {"type": "query", "name": "key"},
                        "actions": {"current": {"path": "current.json", "params": "q*"}}})
    assert m["base_url"] == "https://api.x.test/v1" and m["actions"]["current"]["path"] == "/current.json"
    for bad in [{"base_url": "http://api.x.test"}, {"base_url": "https://x.test", "auth": {"type": "query"}},
                {"base_url": "https://x.test", "actions": {"Bad Name": {}}}, {"base_url": "https://x.test", "actions": {"a": {"path": "../admin"}}}]:
        with pytest.raises(AppSpecError):
            check_manifest(bad)


def test_picking_values_out_of_a_response():
    assert pick(WEATHER, "current.condition.text") == "Sunny" and pick({"a": [{"b": 1}]}, "a.0.b") == 1 and pick(WEATHER, "nope.x") is None
    assert "current.condition.text" in key_paths(WEATHER) and "location.name" in key_paths(WEATHER)


def client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_calls_carry_the_key_only_to_the_app_host_and_never_echo_it(monkeypatch):
    async def ok(host):
        return None
    monkeypatch.setattr(apiapps, "_public_host", ok)
    seen = []

    def handler(req):
        seen.append(req)
        if req.url.params.get("q") == "Nowhere":
            return httpx.Response(400, json={"error": {"message": "No matching location; key=SECRET123"}})
        return httpx.Response(200, json=WEATHER)
    m = check_manifest({"title": "W", "base_url": "https://api.x.test/v1", "auth": {"type": "query", "name": "key"},
                        "actions": {"current": {"path": "/current.json", "params": "q*: City"}, "item": {"path": "/items/{id}"}}})

    async def go():
        async with client(handler) as http:
            r = await apiapps.call(http, m, "current", {"q": "Pune"}, "SECRET123")
            assert r["data"] == WEATHER and seen[-1].url.params["key"] == "SECRET123" and seen[-1].url.host == "api.x.test"
            with pytest.raises(ToolError) as e:
                await apiapps.call(http, m, "current", {"q": "Nowhere"}, "SECRET123")
            assert "SECRET123" not in str(e.value) and "400" in str(e.value)
            with pytest.raises(ToolError, match="Missing: City"):
                await apiapps.call(http, m, "current", {}, "SECRET123")
            await apiapps.call(http, m, "item", {"id": "a/../b"}, "SECRET123")
            assert seen[-1].url.raw_path.startswith(b"/v1/items/a%2F..%2Fb")                # path values can't climb out
            with pytest.raises(ToolError, match="API key"):
                await apiapps.call(http, m, "current", {"q": "x"}, None)
    asyncio.run(go())


def test_local_and_private_addresses_are_refused():
    async def go(host):
        await apiapps._public_host(host)
    for host in ["127.0.0.1", "localhost", "10.0.0.5", "192.168.1.1", "169.254.169.254"]:
        with pytest.raises(ToolError, match="local or private"):
            asyncio.run(go(host))


# ---------- building one with the tools ----------
@pytest.fixture
def build(env, tmp_path, monkeypatch):
    ctx, reg, app, db = env
    ctx.mods = ModStore(tmp_path)
    runs = RunManager()
    ctx.send_ui, ctx.registry, ctx.broadcast = runs.send_ui, reg, (lambda ev: None)
    ctx.mods.on_change = reg.sync_api_apps
    ui = create_ui(ctx); reg.register(ui)

    async def ok(host):
        return None
    monkeypatch.setattr(apiapps, "_public_host", ok)
    calls = []

    def handler(req):
        calls.append(req)
        if req.url.path.endswith("/nope.json"):
            return httpx.Response(404, json={"error": {"message": "API method not found"}})
        return httpx.Response(200, json=WEATHER)
    ctx.http = client(handler)
    return ctx, reg, ui, calls


def test_build_an_app_step_by_step(build):
    ctx, reg, ui, calls = build

    async def go():
        RUN_CLIENT.set("tab")
        r = await ui.connect_api({"name": "weather", "title": "Weather", "base_url": "https://api.weatherapi.com/v1", "key_in": "query", "key_name": "key"})
        assert "API key" in r and "weather" in reg.apps and reg.apps["weather"].panel             # a real app at once
        r = await ui.add_action({"app": "weather", "name": "current", "path": "/current.json", "params": "q*: City = London"})
        assert "key isn't set" in r and not calls                                                   # nothing sent without the key
        reg.apps["weather"].set_secret("api_key", "K-1")
        r = await ui.add_action({"app": "Weather", "name": "current", "path": "/current.json", "params": "q*: City = London"})
        assert "test call OK" in r and "current.temp_c" in r and calls[-1].url.params["key"] == "K-1"
        r = await ui.add_action({"app": "weather", "name": "broken", "path": "/nope.json"})
        assert "TEST FAILED" in r and "404" in r                                                    # the model is told, not left guessing
        with pytest.raises(ToolError, match="did you mean current.temp_c"):
            await ui.set_fields({"app": "weather", "action": "current", "fields": "location.name, current.temp"})
        await ui.set_fields({"app": "weather", "action": "current", "fields": "location.name, current.temp_c, current.condition.text"})
        await ui.remove_action({"app": "weather", "action": "broken"})
        # the agent can now use it from chat, and sees only the chosen values
        tool = next(t for t in reg.apps["weather"].tools() if t.name == "current")
        out = await tool.handler({"q": "Pune"})
        assert out == {"location.name": "Pune", "current.temp_c": 29.5, "current.condition.text": "Sunny"}
        assert "K-1" not in json.dumps(ctx.mods.list())                                             # the key is never in the definition
        # editing: "ask for a city as well" is one small change, and the model can see what exists
        assert "weather ('Weather', api.weatherapi.com, key: query): current: GET /current.json ?q*" in next(
            t for t in ui.tools() if t.name == "add_action").description
    asyncio.run(go())
    assert reg.apps["weather"].untrusted_tools == ["current"]                                      # API text can't restyle the UI


def test_creating_needs_approval_editing_does_not(build):
    ctx, reg, ui, calls = build
    tools = {t.name: t for t in ui.tools()}
    assert tools["connect_api"].needs_approval and not reg.is_always_allowed(ui, tools["connect_api"])
    assert reg.is_always_allowed(ui, tools["add_action"]) and reg.is_always_allowed(ui, tools["set_fields"])


def test_requests_about_building_get_the_build_tools(build):
    ctx, reg, ui, calls = build

    async def go():
        RUN_CLIENT.set("tab")
        await ui.connect_api({"name": "weather", "title": "Weather", "base_url": "https://api.weatherapi.com/v1", "key_in": "none"})
    asyncio.run(go())
    for text in ["implement an app that uses this weather api", "can you update the whther app to ask for a city as well",
                 "add field for city", "update the weather app", "add an endpoint for the forecast"]:
        tools, _ = loop.select_tools(reg, text, set())
        assert "ui_add_action" in tools, text
    tools, _ = loop.select_tools(reg, "make the accent green", set())
    assert "ui_set_theme" in tools and "ui_connect_api" not in tools


def test_required_params_are_tested_with_example_values_and_real_use_teaches_the_shape(build):
    ctx, reg, ui, calls = build

    async def go():
        RUN_CLIENT.set("tab")
        await ui.connect_api({"name": "meteo", "base_url": "https://api.open-meteo.com/v1", "key_in": "none"})
        r = await ui.add_action({"app": "meteo", "name": "forecast", "path": "/forecast", "params": "latitude*, longitude*, current_weather = true"})
        assert "NOT TESTED" in r and "test_with" in r and "latitude, longitude" in r
        r = await ui.add_action({"app": "meteo", "name": "forecast", "params": "latitude*, longitude*, current_weather = true",
                                 "test_with": "latitude=18.52, longitude=73.86"})
        assert "test call OK" in r and calls[-1].url.params["latitude"] == "18.52" and calls[-1].url.params["current_weather"] == "true"
        await ui.add_action({"app": "meteo", "name": "daily", "path": "/daily", "params": "latitude*"})         # built without a test...
        tool = next(t for t in reg.apps["meteo"].tools() if t.name == "daily")
        await tool.handler({"latitude": "1"})                                                                     # ...first real use records the shape
        await ui.set_fields({"app": "meteo", "action": "daily", "fields": "current.temp_c"})
    asyncio.run(go())


def test_a_chat_that_is_building_an_app_keeps_the_build_tools(build):
    ctx, reg, ui, calls = build
    prior = {"ui_create_app", "ui_add_action"}
    tools, _ = loop.select_tools(reg, "show the temperature and wind speed only", {"ui"}, prior_tools=prior)
    assert "ui_set_fields" in tools                                        # not mistaken for "show me an app"
    tools, _ = loop.select_tools(reg, "show the temperature and wind speed only", {"ui"})
    assert "ui_set_fields" not in tools
