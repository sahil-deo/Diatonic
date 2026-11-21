"""Building an app is a session: the first build or change request pins the chat to one app and a fixed toolset, with no routing by words afterwards."""
import asyncio
import json
import time
from pathlib import Path

import pytest
from conftest import ScriptedProvider

from diatonic.agent import loop
from diatonic.agent.loop import is_navigation, select_tools
from diatonic.apps.registry import Registry
from diatonic.apps.ui.builder import BUILDER_TOOLS
from diatonic.config import Config
from diatonic.context import Context
from diatonic.db import Database

PROMPTS = json.loads((Path(__file__).parent.parent / "evals" / "build_prompts.json").read_text())["prompts"]
BUILD_KINDS = ("panel", "edit")


@pytest.fixture(scope="module")
def registry(tmp_path_factory):
    d = tmp_path_factory.mktemp("home")
    ctx = Context(Config(d / "c.json"), Database(d / "d.db"))
    reg = Registry(ctx).discover()
    ctx.registry = reg
    return reg


def start(registry, text, prior=(), screen=None, conv="c1"):
    return registry.apps["ui"].builder_session(conv, text, set(prior), screen, is_navigation(text))


@pytest.mark.parametrize("p", [p for p in PROMPTS if p["kind"] in BUILD_KINDS], ids=[p["id"] for p in PROMPTS if p["kind"] in BUILD_KINDS])
def test_a_build_or_change_request_starts_a_session(registry, p):
    if p["kind"] == "edit":
        registry.ctx.fileapps.create_app(p["setup"], p["setup"].title(), "x")       # an edit starts from an app that exists, named for what it is
    try:
        b = start(registry, p["prompt"])
    finally:
        if p["kind"] == "edit" and registry.ctx.fileapps.exists(p["setup"]):
            registry.ctx.fileapps.delete_app(p["setup"])
    assert b is not None, p["prompt"]
    assert (b.app_id == p["setup"]) if p["kind"] == "edit" else b.app_id is None


def test_the_session_toolset_is_fixed_and_small(registry):
    names = [t.name for t in registry.apps["ui"].builder_tools()]
    assert sorted(names) == sorted(BUILDER_TOOLS)
    assert {"write_app_file", "patch_app_file", "read_app_file", "list_app_files", "read_app_logs", "platform_search", "platform_describe", "open_app"} <= set(names)
    assert not {"add_button", "set_theme", "add_script", "add_style", "set_layout", "export_file_app"} & set(names)
    assert len(json.dumps([loop.tool_spec(registry.apps["ui"], t) for t in registry.apps["ui"].builder_tools()])) < 8200


@pytest.mark.parametrize("text", ["show my calendar", "open my notes", "remind me to stretch at 6pm", "show the weather forecast", "Add a button to the top bar that opens my notes."])
def test_ordinary_and_look_requests_do_not_start_a_session(registry, text):
    assert start(registry, text) is None


def test_a_look_request_still_gets_the_look_tools(registry):
    tools, _ = select_tools(registry, "Add a button to the top bar that opens my notes.", set())
    assert "ui_add_button" in tools and "ui_write_app_file" not in tools


def test_a_follow_up_with_no_building_words_stays_in_the_session_on_the_same_app(registry):
    ctx = registry.ctx
    ctx.fileapps.create_app("timer-follow", "Timer Follow", "x")
    try:
        c = ctx.db.create_conversation()["id"]
        ctx.db.add_message(c, "assistant", "Built it.", {"tools": [{"name": "ui_write_app_file", "app": "ui", "args": {"app_id": "timer-follow", "path": "index.js"}}]})
        for text in ["there is no pause restart options at all", "integrate that then", "Continue"]:
            b = start(registry, text, {"ui_write_app_file"}, None, c)
            assert b is not None and b.app_id == "timer-follow", text
        elsewhere = start(registry, "ok", {"ui_write_app_file"}, {"app": "timer-follow"}, "other")
        assert elsewhere.app_id == "timer-follow"      # the app open on screen is the one
    finally:
        ctx.fileapps.delete_app("timer-follow")


def test_the_session_ends_when_the_user_moves_on(registry):
    prior = {"ui_write_app_file"}
    for text in ["show my unread mail", "what is on my calendar tomorrow", "thanks", "that's all", "never mind"]:
        assert start(registry, text, prior) is None, text
    assert start(registry, "make the timer look like my mail app", prior) is not None      # naming another app inside a change request is not leaving


def test_a_request_to_delete_an_app_is_a_session_with_the_delete_tool(registry):
    registry.ctx.fileapps.create_app("youtube-search", "YouTube Search", "x")
    try:
        b = start(registry, "remove the YouTube search app")
        assert b is not None and b.app_id == "youtube-search"
    finally:
        registry.ctx.fileapps.delete_app("youtube-search")
    assert "delete_file_app" in BUILDER_TOOLS


def test_a_pasted_error_is_treated_as_a_turn_on_the_open_app(registry):
    ctx = registry.ctx
    ctx.fileapps.create_app("err-paste", "Err Paste", "x")
    try:
        b = start(registry, "Uncaught TypeError: Cannot read properties of null (reading 'append')", set(), {"app": "err-paste"})
        assert b is not None and b.app_id == "err-paste" and "pasted an error" in b.note
    finally:
        ctx.fileapps.delete_app("err-paste")


def test_errors_the_open_app_logged_reach_the_next_turn_once(registry):
    ctx = registry.ctx
    ctx.fileapps.create_app("err-flow", "Err Flow", "x")
    try:
        ctx.fileapps.log_error("err-flow", "ReferenceError: tick is not defined", "client")
        first = start(registry, "fix it", {"ui_write_app_file"}, {"app": "err-flow"}, "errconv")
        assert "tick is not defined" in first.note
        assert "tick is not defined" not in start(registry, "and again", {"ui_write_app_file"}, {"app": "err-flow"}, "errconv").note
        ctx.fileapps.log_error("err-flow", "TypeError: x is undefined", "client")
        assert "x is undefined" in start(registry, "still broken", {"ui_write_app_file"}, {"app": "err-flow"}, "errconv").note
    finally:
        ctx.fileapps.delete_app("err-flow")


def test_the_loop_gives_a_follow_up_the_builder_tools_and_the_app(env, monkeypatch):
    ctx, reg, app, db = env
    from diatonic.apps.ui import create as create_ui
    ctx.registry = reg
    reg.register(create_ui(ctx))
    ctx.fileapps.create_app("timer-loop", "Timer Loop", "x")
    conv = db.create_conversation()["id"]
    db.add_message(conv, "assistant", "Built it.", {"tools": [{"name": "ui_write_app_file", "app": "ui", "args": {"app_id": "timer-loop", "path": "index.js"}}]})
    p = ScriptedProvider([[{"content": "Which buttons should it have?"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv, "there is no pause option at all")]
    try:
        asyncio.run(go())
    finally:
        ctx.fileapps.delete_app("timer-loop")
    offered = sorted(t["function"]["name"] for t in p.tools_seen[0])
    assert offered == sorted(f"ui_{n}" for n in BUILDER_TOOLS)
    assert "Builder session on the app 'timer-loop'" in p.seen[0][0]["content"]


def test_asking_to_finish_the_placeholder_stays_on_that_app_and_says_to_write_it(registry):
    fa = registry.ctx.fileapps
    fa.create_app("timer-x", "Timer X", "a timer")
    try:
        b = start(registry, "update the placeholder app to actually function properly", prior=("ui_create_file_app",), screen={"app": "timer-x"})
        assert b is not None and b.app_id == "timer-x"
        assert "placeholder" in b.note and "ui_write_app_file" in b.note and "do not create another app" in b.note
    finally:
        fa.delete_app("timer-x")
