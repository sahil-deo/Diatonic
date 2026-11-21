"""Antigravity as the chat backend, against a fake `agy acp`: its permission requests become approval cards, its settings and flags keep it inside
Diatonic's rules, and the way back into Diatonic (the MCP token) never reaches the screen or the saved chat."""
import asyncio
import json
import sys
from pathlib import Path

from diatonic import models as M
from diatonic.agent import approvals, loop
from diatonic.agent.antigravity import SETTINGS, Connector
from diatonic.agent.runs import RunManager
from diatonic.mcp import McpHub

FAKE = str(Path(__file__).parent / "fake_acp_server.py")


def wire(env, tmp_path, monkeypatch, *, command=None, auth=False, apps=()):
    ctx, reg, app, db = env
    monkeypatch.setenv("DIATONIC_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FAKE_ACP_LOG", str(tmp_path / "log.jsonl"))
    if auth:
        monkeypatch.setenv("FAKE_ACP_AUTH", "1")
    ctx.config.data["agent"]["provider"] = "antigravity"
    ctx.config.data["agent"]["endpoint"] = "https://antigravity.google"
    ctx.config.data["antigravity"] = {"command": command or [sys.executable, FAKE], "idle_minutes": 15}
    ctx.config.data["mcp"] = {"apps": list(apps)}
    ctx.mcp = McpHub(ctx, reg, RunManager())
    ctx.antigravity = Connector(ctx)
    return ctx, reg, app, db


def log(tmp_path):
    p = tmp_path / "log.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


async def converse(ctx, reg, db, text, on_event=None, conv=None):
    conv = conv or db.create_conversation()
    events = []
    async for ev in loop.run_agent(ctx, reg, conv["id"], text):
        events.append(ev)
        if on_event:
            await on_event(ev)
    return conv, events


def answer(yes):
    async def on(ev):
        if ev["type"] == "tool_call" and ev.get("needs_approval"):
            assert approvals.resolve(ev["id"], yes)
    return on


def run(coro, ctx):
    async def go():
        try:
            return await coro
        finally:
            await ctx.antigravity.close_all()
    return asyncio.run(asyncio.wait_for(go(), 20))   # a card that can never be answered must fail the test, not hang it


def test_the_reply_streams_and_is_saved_as_a_normal_chat(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv, ev = run(converse(ctx, reg, db, "SAY something"), ctx)
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "Hello world"
    assert [e["type"] for e in ev][-2:] == ["message", "done"]
    msgs = db.messages(conv["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == "Hello world"


def test_it_always_runs_sandboxed_with_our_own_settings_and_never_skips_permissions(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    run(converse(ctx, reg, db, "SAY"), ctx)
    argv = log(tmp_path)[0]["argv"]
    assert "--sandbox" in argv and "--disable-telemetry" in argv and "--dangerously-skip-permissions" not in argv
    rel = argv[argv.index("--app-data-dir") + 1]
    assert not Path(rel).is_absolute()
    settings = (Path.home() / ".gemini" / rel / "settings.json").resolve()
    assert settings == (tmp_path / "home" / "antigravity" / "appdata" / "settings.json").resolve(), "settings are Diatonic's own, not the user's"
    saved = json.loads(settings.read_text())
    assert saved == SETTINGS and "command(*)" in saved["permissions"]["ask"] and saved["toolPermission"] == "request-review"


def test_a_permission_request_waits_for_the_user_and_yes_lets_it_run(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv, ev = run(converse(ctx, reg, db, "TOOL please", answer(True)), ctx)
    card = next(e for e in ev if e["type"] == "tool_call")
    assert card["needs_approval"] and card["describe"] == "echo hi" and card["app"] == "antigravity"
    assert next(e for e in ev if e["type"] == "tool_result")["ok"] is True
    assert db.messages(conv["id"])[-1]["content"] == "ran it"
    answered = [m for m in log(tmp_path) if m.get("id") == 100]
    assert answered[0]["result"]["outcome"] == {"outcome": "selected", "optionId": "allow"}


def test_no_stops_it_and_it_is_told_so(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv, ev = run(converse(ctx, reg, db, "TOOL please", answer(False)), ctx)
    assert next(e for e in ev if e["type"] == "tool_status")["status"] == "denied"
    assert [e for e in ev if e["type"] == "tool_result"][0]["preview"] == "Denied by you"
    assert [m for m in log(tmp_path) if m.get("id") == 100][0]["result"]["outcome"]["optionId"] == "reject"
    assert db.messages(conv["id"])[-1]["content"] == "refused"


def test_a_call_it_makes_without_asking_is_still_shown(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    _, ev = run(converse(ctx, reg, db, "QUIET"), ctx)
    card = next(e for e in ev if e["type"] == "tool_call")
    assert card["needs_approval"] is False and card["title"] == "Read notes.txt"
    assert [e["ok"] for e in ev if e["type"] == "tool_result"] == [True]


def test_stop_cancels_the_turn_and_keeps_what_was_said(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv = db.create_conversation()

    async def go():
        seen = asyncio.Event()

        async def on(ev):
            if ev["type"] == "token":
                seen.set()
        task = asyncio.create_task(converse(ctx, reg, db, "SLOW", on, conv))
        await asyncio.wait_for(seen.wait(), 10)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    run(go(), ctx)
    assert any(m.get("method") == "session/cancel" for m in log(tmp_path))
    last = db.messages(conv["id"])[-1]
    assert last["content"] == "working" and last["meta"]["stopped"] is True


def test_not_signed_in_says_how_to_sign_in(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch, auth=True)
    conv, ev = run(converse(ctx, reg, db, "SAY"), ctx)
    err = next(e for e in ev if e["type"] == "error")
    assert "agy acp --login" in err["message"]
    assert db.messages(conv["id"])[-1]["meta"]["error"] == err["message"]
    assert not ctx.antigravity.sessions


def test_a_missing_agy_says_so_instead_of_crashing(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch, command=["definitely-not-agy"])
    _, ev = run(converse(ctx, reg, db, "SAY"), ctx)
    assert "Can't find" in next(e for e in ev if e["type"] == "error")["message"]


def test_it_gets_no_way_back_into_diatonic_until_an_app_is_switched_on(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv, _ = run(converse(ctx, reg, db, "SAY"), ctx)
    assert not list((tmp_path / "home" / "antigravity" / "work").rglob("mcp_config.json"))


def test_switching_an_app_on_writes_its_config_and_the_token_never_reaches_a_card_or_the_chat(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch, apps=["fake"])
    conv = db.create_conversation()
    _, ev = run(converse(ctx, reg, db, "LEAK", None, conv), ctx)
    cfg = next((tmp_path / "home" / "antigravity" / "work").rglob("mcp_config.json"))
    entry = json.loads(cfg.read_text())["mcpServers"]["diatonic"]
    token = entry["headers"]["Authorization"].split()[-1]
    assert entry["serverUrl"].endswith("/mcp") and token and set(entry["tools"]) >= {"fake_read", "fake_nuke"}
    assert (cfg.stat().st_mode & 0o777) == 0o600
    shown = json.dumps(ev, ensure_ascii=False) + json.dumps(db.messages(conv["id"]), ensure_ascii=False)
    assert token not in shown and "Bearer …" in shown
    session_new = next(m for m in log(tmp_path) if m.get("method") == "session/new")
    assert session_new["params"]["mcpServers"][0]["name"] == "diatonic"
    assert session_new["params"]["mcpServers"][0]["url"].endswith("/mcp")
    assert session_new["params"]["mcpServers"][0]["headers"]["Authorization"] == f"Bearer {token}"


def test_mcp_handles_server_discover(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch, apps=["fake"])
    token = ctx.mcp.open("c1", "test")
    res = asyncio.run(ctx.mcp.handle(token, {"jsonrpc": "2.0", "id": 1, "method": "server/discover", "params": {}}))
    assert res == {"jsonrpc": "2.0", "id": 1, "result": {}}


def test_mcp_tools_call_executes_tool_through_approvals(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch, apps=["fake"])
    token = ctx.mcp.open("c1", "test")
    res = asyncio.run(ctx.mcp.handle(token, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "fake_read", "arguments": {"text": "hello mcp"}}
    }))
    assert res["id"] == 2
    assert "hello mcp" in res["result"]["content"][0]["text"]
    assert res["result"]["isError"] is False


def test_requests_for_files_or_a_terminal_are_refused_not_served(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv, ev = run(converse(ctx, reg, db, "FILES"), ctx)
    reply = [m for m in log(tmp_path) if m.get("id") == 200][0]
    assert reply["error"]["code"] == -32601
    assert db.messages(conv["id"])[-1]["content"] == "asked"


def test_the_second_message_reuses_the_process_and_only_the_first_carries_the_context(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)

    async def go():
        conv, _ = await converse(ctx, reg, db, "SAY one")
        await converse(ctx, reg, db, "SAY two", None, conv)
    run(go(), ctx)
    msgs = log(tmp_path)
    assert [m["method"] for m in msgs if m.get("method")].count("initialize") == 1
    prompts = [m["params"]["prompt"][0]["text"] for m in msgs if m.get("method") == "session/prompt"]
    assert prompts[0].startswith("[Context]") and prompts[1] == "SAY two"


def test_it_counts_as_a_cloud_model_and_needs_no_key_or_model_name(tmp_path):
    p = M.PRESETS["antigravity"]
    assert M.is_cloud({"endpoint": p["endpoint"]})
    m = M.clean({"preset": "antigravity", "model": ""}, [])
    assert m["provider"] == "antigravity" and m["model"] == "default"


def test_antigravity_provider_reports_available_models():
    from diatonic.agent.providers.antigravity import AntigravityProvider
    prov = AntigravityProvider(None, {"command": "agy", "provider": "antigravity"})
    models = asyncio.run(prov.list_models())
    assert len(models) >= 4
    assert any("gemini" in m for m in models)


def test_thoughts_stream_and_are_saved_in_message_meta(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv, ev = run(converse(ctx, reg, db, "THINK carefully"), ctx)
    thoughts = [e["text"] for e in ev if e["type"] == "thought"]
    assert "Deep thinking" in thoughts
    msgs = db.messages(conv["id"])
    assert "Deep thinking" in msgs[-1]["meta"]["thoughts"][0]
    done = next(e for e in ev if e["type"] == "done")
    assert done["tokens"] == 150


def test_session_info_updates_conversation_title(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv = db.create_conversation(title="Initial")
    _, ev = run(converse(ctx, reg, db, "TITLE please", conv=conv), ctx)
    title_ev = next(e for e in ev if e["type"] == "title")
    assert title_ev["title"] == "Smart AGY Title"
    assert db.get_conversation(conv["id"])["title"] == "Smart AGY Title"


def test_fresh_session_rehydrates_prior_conversation_history(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    conv = db.create_conversation()
    db.add_message(conv["id"], "user", "Remember my secret code is 42")
    db.add_message(conv["id"], "assistant", "I will remember that your secret code is 42")
    run(converse(ctx, reg, db, "SAY secret", conv=conv), ctx)
    prompt = next(m for m in log(tmp_path) if m.get("method") == "session/prompt")["params"]["prompt"][0]["text"]
    assert "[Prior conversation in this chat:" in prompt
    assert "Remember my secret code is 42" in prompt


def test_session_drops_and_recreates_when_mcp_apps_change(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch, apps=())

    async def go():
        s1 = await ctx.antigravity.session("conv-1")
        assert len(s1.tools) == 0

        # User opts in fake app in settings
        ctx.config.data["mcp"]["apps"] = ["fake"]
        s2 = await ctx.antigravity.session("conv-1")
        assert len(s2.tools) > 0
        assert s2 is not s1

    run(go(), ctx)


def test_command_guardrails_block_app_launching_and_diatonic_home_access(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)

    # 1. Test open -a "Mail" is blocked automatically
    _, ev1 = run(converse(ctx, reg, db, "BLOCKED_APP"), ctx)
    denied1 = next(e for e in ev1 if e["type"] == "tool_status" and e["status"] == "denied")
    result1 = next(e for e in ev1 if e["type"] == "tool_result" and e["ok"] is False)
    assert "Blocked by Diatonic guardrail" in result1["preview"]
    assert "ui_open_app" in result1["preview"]

    # 2. Test ~/.diatonic inspection is blocked automatically
    _, ev2 = run(converse(ctx, reg, db, "BLOCKED_DIR"), ctx)
    denied2 = next(e for e in ev2 if e["type"] == "tool_status" and e["status"] == "denied")
    result2 = next(e for e in ev2 if e["type"] == "tool_result" and e["ok"] is False)
    assert "Blocked by Diatonic guardrail" in result2["preview"]
    assert "~/.diatonic" in result2["preview"]


def test_auto_permission_mode_approves_safe_actions_without_asking(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    ctx.config.data["antigravity"]["permissions"] = "auto"
    conv, ev = run(converse(ctx, reg, db, "TOOL please"), ctx)
    card = next(e for e in ev if e["type"] == "tool_call")
    assert card["needs_approval"] is False and card["describe"] == "echo hi"
    assert next(e for e in ev if e["type"] == "tool_status")["status"] == "approved"
    assert next(e for e in ev if e["type"] == "tool_result")["ok"] is True
    assert db.messages(conv["id"])[-1]["content"] == "ran it"
    answered = [m for m in log(tmp_path) if m.get("id") == 100]
    assert answered[0]["result"]["outcome"] == {"outcome": "selected", "optionId": "allow"}


def test_auto_permission_mode_still_blocks_command_guardrails(env, tmp_path, monkeypatch):
    ctx, reg, app, db = wire(env, tmp_path, monkeypatch)
    ctx.config.data["antigravity"]["permissions"] = "auto"
    _, ev = run(converse(ctx, reg, db, "BLOCKED_APP"), ctx)
    result = next(e for e in ev if e["type"] == "tool_result" and e["ok"] is False)
    assert "Blocked by Diatonic guardrail" in result["preview"]


