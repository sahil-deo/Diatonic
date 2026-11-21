"""App code (index.js) and scripts are test-run in the user's window before the approval card: the model hears about errors first."""
import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import ScriptedProvider
from starlette.testclient import TestClient
from test_mods import call, setup

from diatonic.agent import approvals, loop
from diatonic.apps.ui import sdk
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app

GOOD = "export async function render(el, ctx) { el.append(ctx.h('div', {}, 'Hello')); }"
BROKEN = "export async function render(el, ctx) { el.append(nope); }"
BLANK = "export async function render(el, ctx) { const a = 1; }"


def write(app, code):
    return call("ui_write_app_file", app_id=app, path="index.js", content=code)


def with_app(env, name):
    ctx = env[0]
    if not ctx.fileapps.exists(name):
        ctx.fileapps.create_app(name, name.title(), "x")
    return ctx


def chat(env, tmp_path, monkeypatch, script, answers):
    """Run a chat whose 'window' answers each verification with the next item of `answers` (None = never answers)."""
    ctx, reg, db, runs, events = setup(env, tmp_path)
    ui = reg.apps["ui"]
    asked, answers = [], list(answers)

    async def go():
        q = runs.subscribe("tab-A")
        monkeypatch.setattr(loop, "make_provider", lambda c: ScriptedProvider(script))
        run = runs.start(ctx, reg, db.create_conversation("S"), "build a panel", False, client="tab-A")
        out = []
        while not run.task.done():
            await asyncio.sleep(0.01)
            while not q.empty():
                ev = q.get_nowait()
                out.append(ev)
                if ev.get("action") == "verify":
                    asked.append(ev)
                    reply = answers.pop(0) if answers else None
                    if reply is not None:
                        ui.resolve_verify(ev["id"], reply)
            for ev in list(run.events):
                if ev["type"] == "tool_call" and ev.get("needs_approval") and not ev.get("_done"):
                    ev["_done"] = True
                    approvals.resolve(ev["id"], True)
        while not q.empty():
            out.append(q.get_nowait())
        return out

    return ctx, asked, asyncio.run(go())


OK = {"errors": [], "rendered": True, "usedApi": False}


def test_working_code_goes_on_to_the_approval_card_and_is_stored(env, tmp_path, monkeypatch):
    with_app(env, "hello")
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[write("hello", GOOD)], [{"content": "ok"}]], [OK])
    assert len(asked) == 1 and asked[0]["kind"] == "file_app" and asked[0]["code"] == GOOD
    assert ctx.fileapps.read_file("hello", "index.js") == GOOD


def test_broken_code_is_sent_back_to_the_model_and_never_shown_to_the_user(env, tmp_path, monkeypatch):
    with_app(env, "p")
    bad = {"errors": ["nope is not defined"], "rendered": False, "usedApi": False}
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[write("p", BROKEN)], [write("p", GOOD)], [{"content": "ok"}]], [bad, OK])
    results = [e for e in ev if e.get("type") == "tool_result"]
    assert not results[0]["ok"] and "nope is not defined" in results[0]["preview"]
    cards = [e for e in ev if e.get("type") == "tool_call"]
    assert not cards[0]["needs_approval"]                    # the user was not asked to approve the broken attempt
    assert cards[1]["needs_approval"] and ctx.fileapps.read_file("p", "index.js") == GOOD   # the retry was shown, approved and kept


def test_an_app_that_shows_nothing_is_refused_unless_it_waits_on_the_server(env, tmp_path, monkeypatch):
    with_app(env, "p")
    blank = {"errors": [], "rendered": False, "usedApi": False}
    ctx, _, ev = chat(env, tmp_path, monkeypatch, [[write("p", BLANK)], [{"content": "x"}]], [blank])
    assert ctx.fileapps.read_file("p", "index.js") != BLANK and "showed nothing" in [e for e in ev if e.get("type") == "tool_result"][0]["preview"]
    waiting = {"errors": [], "rendered": False, "usedApi": True}
    ctx, _, ev = chat(env, tmp_path, monkeypatch, [[write("p", BLANK)], [{"content": "x"}]], [waiting])
    assert ctx.fileapps.read_file("p", "index.js") == BLANK


def test_scripts_are_checked_too(env, tmp_path, monkeypatch):
    bad = {"errors": ["diatonic.addButon is not a function"], "rendered": False, "usedApi": False}
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[call("ui_add_script", name="s", code="diatonic.addButon({})")], [{"content": "x"}]], [bad])
    assert asked[0]["kind"] == "script" and ctx.mods.get("s") is None


def test_no_answer_from_the_window_falls_back_to_the_approval_card(env, tmp_path, monkeypatch):
    with_app(env, "p")
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[write("p", GOOD)], [{"content": "ok"}]], [None])
    assert len(asked) == 1 and ctx.fileapps.read_file("p", "index.js") == GOOD


def test_the_same_code_is_not_tested_twice(env, tmp_path, monkeypatch):
    with_app(env, "p")
    ctx, asked, ev = chat(env, tmp_path, monkeypatch, [[write("p", GOOD)], [{"content": "ok"}]], [OK])
    assert len(asked) == 1       # the approval card's preview tested it; running the approved tool did not test it again


def test_the_report_endpoint_ignores_unknown_ids(tmp_path):
    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        assert c.post("/api/mods/verify/nothing", json={"errors": []}, headers={"origin": "http://testserver"}).json() == {"ok": False}


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_every_recipe_is_valid_javascript_and_has_the_render_shape():
    recipes = [d for d in (Path(sdk.__file__).parents[2] / "recipes").iterdir() if d.is_dir()]
    assert recipes
    for d in recipes:
        r = subprocess.run(["node", "--check", str(d / "index.js")], capture_output=True, text=True)
        assert r.returncode == 0, f"{d.name}: {r.stderr}"
        code = (d / "index.js").read_text()
        assert "export async function render(el, ctx)" in code and "localStorage" not in code and " fetch(" not in code


def test_the_sdk_text_stays_short_enough_for_a_small_model():
    assert len(sdk.FILE_HINT) < 1200      # characters, in every build request
