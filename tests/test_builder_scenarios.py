"""v3 acceptance scenarios 1, 4 and 5, as one scripted conversation: build, follow up, restyle, remove (the model is scripted, the harness is real)."""
import asyncio
import json
from pathlib import Path

from conftest import ScriptedProvider
from test_mods import call, setup

from diatonic.agent import approvals, loop
from diatonic.apps.ui.builder import BUILDER_TOOLS

RECIPE = Path(__file__).parent.parent / "diatonic" / "recipes" / "timer"
OK = {"errors": [], "rendered": True, "usedApi": False}


def converse(env, tmp_path, monkeypatch, turns):
    """Each turn is (what the user types, the model's scripted replies). Returns, per turn, the tool names offered to the model."""
    ctx, reg, db, runs, events = setup(env, tmp_path)
    ui = reg.apps["ui"]
    conv, offered = db.create_conversation("S"), []

    async def one(text, script):
        q = runs.subscribe("tab-A")
        provider = ScriptedProvider(script)
        monkeypatch.setattr(loop, "make_provider", lambda c: provider)
        run = runs.start(ctx, reg, conv, text, False, client="tab-A")
        while not run.task.done():
            await asyncio.sleep(0.01)
            while not q.empty():
                ev = q.get_nowait()
                if ev.get("action") == "verify":
                    ui.resolve_verify(ev["id"], OK)
            for ev in list(run.events):
                if ev["type"] == "tool_call" and ev.get("needs_approval") and not ev.get("_done"):
                    ev["_done"] = True
                    approvals.resolve(ev["id"], True)
        offered.append(sorted(t["function"]["name"] for t in provider.tools_seen[0]))

    async def go():
        for text, script in turns:
            await one(text, script)
    asyncio.run(go())
    return ctx, offered


def test_a_conversation_builds_one_app_then_edits_it_restyles_it_and_removes_it(env, tmp_path, monkeypatch):
    index = (RECIPE / "index.js").read_text()
    manifest = (RECIPE / "manifest.json").read_text()
    done = [{"content": "Done."}]
    turns = [
        ("Create a timer app with start, pause, resume, reset and Sec/Min/Hour options; notify when done.",
         [[call("ui_create_file_app", id="timer", name="Timer")], [call("ui_write_app_file", app_id="timer", path="manifest.json", content=manifest)],
          [call("ui_write_app_file", app_id="timer", path="index.js", content=index)], done]),
        ("there is no pause restart options at all",
         [[call("ui_patch_app_file", app_id="timer", path="index.js", old='label: "Pause"', new='label: "Pause timer"')], done]),
        ("integrate that then", [[call("ui_read_app_file", app_id="timer", path="index.js")], done]),
        ("The UI is bad, match the harness, dark mode only",
         [[call("ui_patch_app_file", app_id="timer", path="index.js", old='label: "Resume"', new='label: "Resume now"')], done]),
        ("Remove the timer app", [[call("ui_delete_file_app", app_id="timer")], done]),
    ]
    ctx, offered = converse(env, tmp_path, monkeypatch, turns)
    assert all(o == sorted(f"ui_{n}" for n in BUILDER_TOOLS) for o in offered), offered     # every turn, the same fixed toolset, follow-ups included
    assert not ctx.fileapps.exists("timer")                                                    # scenario 5: removed
    assert ctx.mods.list() == []                                                               # scenario 4: no style, script or top-bar button was added
    assert [a["id"] for a in ctx.fileapps.list_apps()] == []                                   # one app the whole time, never a second one


def test_the_follow_ups_edit_the_same_app_in_place(env, tmp_path, monkeypatch):
    index = (RECIPE / "index.js").read_text()
    done = [{"content": "Done."}]
    turns = [
        ("Make a timer app with pause.", [[call("ui_create_file_app", id="timer", name="Timer")], [call("ui_write_app_file", app_id="timer", path="index.js", content=index)], done]),
        ("there is no pause restart options at all",
         [[call("ui_patch_app_file", app_id="timer", path="index.js", old='label: "Pause"', new='label: "Pause timer"')], done]),
    ]
    ctx, _ = converse(env, tmp_path, monkeypatch, turns)
    text = ctx.fileapps.read_file("timer", "index.js")
    assert 'label: "Pause timer"' in text and 'label: "Pause"' not in text
    assert [a["id"] for a in ctx.fileapps.list_apps()] == ["timer"]
