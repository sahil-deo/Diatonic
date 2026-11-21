"""What the model is told: system prompt sections, skills and recipes as files, and how the agent reads them."""
import asyncio
import json

import pytest
from conftest import ScriptedProvider
from starlette.testclient import TestClient

from diatonic import instructions
from diatonic.agent import loop
from diatonic.apps.ui import create as create_ui
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DIATONIC_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture
def web(home, tmp_path):
    with TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})) as c:
        yield c


def test_the_system_prompt_is_the_sections_in_a_fixed_order(home):
    secs = instructions.sections()
    assert [s["name"] for s in secs] == ["identity", "scope", "tools", "discovery", "building"]
    assert all(s["text"] and s["source"] == "built in" and s["chars"] == len(s["text"]) for s in secs)
    assert instructions.system_text() == "\n".join(s["text"] for s in secs)
    assert instructions.system_text().startswith("You are Diatonic")


def test_a_file_in_the_users_folder_replaces_a_section_and_an_empty_one_switches_it_off(home):
    (home / "prompts").mkdir(parents=True)
    (home / "prompts" / "scope.md").write_text("Only talk about mail.\n")
    (home / "prompts" / "building.md").write_text("")
    (home / "prompts" / "unknown.md").write_text("ignored")
    secs = {s["name"]: s for s in instructions.sections()}
    assert secs["scope"]["text"] == "Only talk about mail." and secs["scope"]["source"] == "yours"
    assert "Only talk about mail." in instructions.system_text() and "app-creator" not in instructions.system_text()
    assert "ignored" not in instructions.system_text()


def test_the_prompt_still_holds_no_clock_or_per_turn_state(env):
    ctx, reg, *_ = env
    text = loop.build_system_prompt(ctx, reg)
    assert text == loop.build_system_prompt(ctx, reg) and "Now:" not in text and len(text) < 1600


def test_the_prompt_route_lists_sections_with_sizes(web):
    d = web.get("/api/prompt").json()
    assert [s["name"] for s in d["sections"]][:2] == ["identity", "scope"]
    assert d["chars"] == sum(s["chars"] for s in d["sections"]) > 0


def test_the_first_skills_exist_with_a_name_and_a_description(home):
    by = {s.name: s for s in instructions.skills()}
    assert {"app-creator", "recipes"} <= set(by)
    assert all(s.description and s.body for s in by.values())


def test_the_recipes_skill_lists_every_recipe_and_each_is_readable_as_a_skill(home):
    recipes = sorted(p.name for p in (instructions.HERE / "recipes").iterdir() if p.is_dir())
    assert recipes
    index = instructions.get_skill("recipes").body
    assert "{{recipes}}" not in index
    for r in recipes:
        assert f"recipe-{r}" in index
        body = instructions.get_skill(f"recipe-{r}").body
        assert "manifest.json:" in body and "export async function render(el, ctx)" in body


def test_a_user_skill_is_added_and_can_replace_a_built_in_one(home):
    (home / "skills").mkdir(parents=True)
    (home / "skills" / "poems.md").write_text("---\nname: poems\ndescription: How to write a poem.\n---\nRhyme.\n")
    (home / "skills" / "app-creator.md").write_text("---\nname: app-creator\ndescription: Mine.\n---\nMy rules.\n")
    (home / "skills" / "Bad Name.md").write_text("---\nname: Bad Name\ndescription: x\n---\nno\n")
    assert instructions.get_skill("poems").body == "Rhyme."
    assert instructions.get_skill("app-creator").body == "My rules." and instructions.get_skill("app-creator").source == "yours"
    assert instructions.get_skill("Bad Name") is None


def test_skills_are_found_through_the_catalog(env):
    ctx, reg, *_ = env
    ctx.registry = reg
    found = ctx.catalog.search("build an app", kind="skill")
    assert found and found[0].name == "app-creator" and found[0].example == 'ui_read_skill("app-creator")'
    assert "recipe-timer" in [e.name for e in ctx.catalog.search("timer", kind="skill")]


def test_the_agent_reads_a_skill_and_an_unknown_name_lists_the_real_ones(env):
    ctx, reg, *_ = env
    ctx.registry = reg
    reg.register(ui := create_ui(ctx))
    assert "ctx.store" in asyncio.run(ui.read_skill({"name": "app-creator"}))
    with pytest.raises(Exception) as e:
        asyncio.run(ui.read_skill({"name": "nope"}))
    assert "app-creator" in str(e.value)
    assert "ui_read_skill" in [f"ui_{t.name}" for t in ui.tools() if t.name == "read_skill"]


def test_a_slash_skill_adds_its_text_to_that_turn_only_and_the_chat_keeps_what_was_typed(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"content": "ok"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], "/recipes show me the list")]
    asyncio.run(go())
    system, user = p.seen[0][0]["content"], p.seen[0][-1]["content"]
    assert "Skill recipes:" in system and "recipe-timer" in system
    assert user.startswith("show me the list") and "/recipes" not in user
    assert db.history(conv["id"], limit=5)[0]["content"] == "/recipes show me the list"
    p2 = ScriptedProvider([[{"content": "ok"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p2)

    async def go2():
        return [e async for e in loop.run_agent(ctx, reg, db.create_conversation()["id"], "/nothing here")]
    asyncio.run(go2())
    assert "Skill " not in p2.seen[0][0]["content"] and p2.seen[0][-1]["content"].startswith("/nothing here")


def test_a_build_request_carries_a_small_prompt_and_reaches_the_api_only_through_lookups(env):
    ctx, reg, *_ = env
    ctx.registry = reg
    reg.register(ui := create_ui(ctx))
    specs = [loop.tool_spec(ui, t) for t in ui.builder_tools()]
    names = {s["function"]["name"] for s in specs}
    assert {"ui_platform_search", "ui_platform_describe", "ui_read_skill", "ui_write_app_file", "ui_patch_app_file"} <= names
    assert not any("ctx.store" in json.dumps(s) or "ctx.timer" in json.dumps(s) for s in specs)   # the API is looked up, not listed
    assert len(loop.build_system_prompt(ctx, reg)) + len(json.dumps(specs)) < 8800   # characters; the Phase 4 request measured 8,900 on the real registry
