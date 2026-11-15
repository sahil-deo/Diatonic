"""A request to build something must be offered the tools that can build it (the build eval found "make a stopwatch" got none)."""
import json
from pathlib import Path

import pytest

from diatonic.agent.loop import select_tools
from diatonic.apps.registry import Registry
from diatonic.config import Config
from diatonic.context import Context
from diatonic.db import Database

PROMPTS = json.loads((Path(__file__).parent.parent / "evals" / "build_prompts.json").read_text())["prompts"]
NEEDS = {"panel": "ui_add_panel", "data": "ui_create_app", "api": "ui_connect_api", "tools": "ui_create_connected_app", "mod": "ui_add_button", "edit": "ui_edit_panel"}


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    d = tmp_path_factory.mktemp("home")
    ctx = Context(Config(d / "c.json"), Database(d / "d.db"))
    reg = Registry(ctx).discover()
    ctx.registry = reg
    return reg


@pytest.mark.parametrize("p", PROMPTS, ids=[p["id"] for p in PROMPTS])
def test_the_build_tool_for_the_request_is_offered(registry, p):
    mods = registry.ctx.mods
    if p["kind"] == "edit":
        mods.put("panel", p["setup"], "el.append('x')", p["setup"].title())       # an edit starts from a panel that exists, named for what it is
    try:
        tools, _ = select_tools(registry, p["prompt"], set())
    finally:
        if p["kind"] == "edit" and mods.get(p["setup"]):
            mods.remove(p["setup"])
    assert NEEDS[p["kind"]] in tools, f"{p['prompt']!r} was offered {sorted(tools)}"


@pytest.mark.parametrize("text", ["show my calendar", "open my notes", "remind me to stretch at 6pm", "show the weather forecast"])
def test_ordinary_requests_do_not_get_the_build_tools(registry, text):
    tools, _ = select_tools(registry, text, set())
    assert not {"ui_add_panel", "ui_create_app", "ui_connect_api"} & set(tools)


def test_building_a_panel_about_todos_does_not_offer_the_todo_tools(registry):
    tools, _ = select_tools(registry, "Make a panel that lists my open to-dos.", set())
    assert "ui_add_panel" in tools and not [n for n in tools if n.startswith("todos_")]
    tools, _ = select_tools(registry, "show my open to-dos", set())           # a plain request still reaches the app
    assert not [n for n in tools if n.startswith("ui_add_")]
