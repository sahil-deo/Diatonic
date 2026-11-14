import asyncio

import pytest

from diatonic.agent import approvals, loop
from diatonic.agent.providers import ProviderError
from conftest import ScriptedProvider


def call(name, **args):
    return {"tool_calls": [{"name": name, "arguments": args}]}


async def collect(ctx, reg, db, provider, monkeypatch, text="hi", speak=False, on_event=None):
    monkeypatch.setattr(loop, "make_provider", lambda c: provider)
    conv = db.create_conversation()
    events = []
    async for ev in loop.run_agent(ctx, reg, conv["id"], text, speak=speak):
        events.append(ev)
        if on_event:
            await on_event(ev)
    return conv, events


def types(events):
    return [e["type"] for e in events]


def test_plain_reply(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"content": "Hello "}, {"content": "there"}]])
    conv, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch))
    assert "".join(e["text"] for e in ev if e["type"] == "token") == "Hello there"
    assert types(ev)[-1] == "done"
    assert [m["role"] for m in db.messages(conv["id"])] == ["user", "assistant"]


def test_read_tool_runs_without_approval(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_read", text="x")], [{"content": "done"}]])
    conv, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch))
    assert app.calls == [("read", {"text": "x"})]
    assert "tool_result" in types(ev) and "approval" not in types(ev)
    # the tool result is fed back to the model
    assert any(m.get("role") == "tool" and "echo" in m["content"] for m in p.seen[1])
    meta = db.messages(conv["id"])[-1]["meta"]
    assert meta["tools"][0]["name"] == "fake_read" and meta["tools"][0]["ok"] is True


def test_invalid_args_and_unknown_tool_are_reported_to_model(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_read")], [call("nope_tool")], [{"content": "ok"}]])
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch))
    results = [e for e in ev if e["type"] == "tool_result"]
    assert [r["ok"] for r in results] == [False, False]
    assert "invalid arguments" in results[0]["preview"] and "no tool named" in results[1]["preview"]
    assert app.calls == []


def test_tool_error_is_not_fatal(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_boom")], [{"content": "it failed"}]])
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch))
    assert [e for e in ev if e["type"] == "tool_result"][0]["preview"] == "Error: nope"


@pytest.mark.parametrize("approve", [True, False])
def test_destructive_needs_approval(env, monkeypatch, approve):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_nuke", id=7)], [{"content": "ok"}]])

    async def on_event(ev):
        if ev["type"] == "tool_call":
            assert ev["needs_approval"] and ev["describe"] == "Nuke 7"
            asyncio.get_running_loop().call_later(0.01, approvals.resolve, ev["id"], approve)

    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch, on_event=on_event))
    assert bool(app.calls) is approve
    assert any(e["type"] == "notify" for e in ev)
    assert [e for e in ev if e["type"] == "tool_status"][0]["status"] == ("approved" if approve else "denied")


def test_always_allow_skips_approval(env, monkeypatch):
    ctx, reg, app, db = env
    ctx.app_cfg("fake")["always_allow"] = ["nuke"]
    p = ScriptedProvider([[call("fake_nuke", id=1)], [{"content": "ok"}]])
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch))
    assert app.calls == [("nuke", {"id": 1})]
    assert not [e for e in ev if e["type"] == "notify"]


def test_disabled_app_has_no_tools(env):
    ctx, reg, app, db = env
    ctx.app_cfg("fake")["enabled"] = False
    assert reg.tool_map() == {}


def test_provider_error_is_surfaced(env, monkeypatch):
    ctx, reg, app, db = env

    class Bad(ScriptedProvider):
        async def stream(self, *a, **k):
            raise ProviderError("Can't reach Ollama")
            yield

    _, ev = asyncio.run(collect(ctx, reg, db, Bad([]), monkeypatch, speak=True))
    assert any(e["type"] == "error" for e in ev)
    assert [e for e in ev if e["type"] == "summary"][0]["text"].startswith("Something went wrong")


def test_summary_only_when_relevant(env, monkeypatch):
    ctx, reg, app, db = env
    # trivial chat -> silent even with speak on
    p = ScriptedProvider([[{"content": "hi"}]], summary="Hello!")
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch, speak=True))
    assert "summary" not in types(ev)
    # tool run -> spoken, cleaned
    p = ScriptedProvider([[call("fake_read", text="x")], [{"content": "done"}]],
                         summary="I checked it. See https://x.io **now**")
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch, speak=True))
    assert [e for e in ev if e["type"] == "summary"][0]["text"] == "I checked it. See now"
    # model says NONE -> silent
    p = ScriptedProvider([[call("fake_read", text="x")], [{"content": "done"}]], summary="NONE")
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch, speak=True))
    assert "summary" not in types(ev)
    # toggle off -> never
    p = ScriptedProvider([[call("fake_read", text="x")], [{"content": "done"}]], summary="Done.")
    _, ev = asyncio.run(collect(ctx, reg, db, p, monkeypatch, speak=False))
    assert "summary" not in types(ev)


def test_history_is_replayed_without_tool_traces(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"content": "one"}], [{"content": "two"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = db.create_conversation()

    async def go(t):
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], t)]

    asyncio.run(go("first"))
    asyncio.run(go("second"))
    roles = [(m["role"], m["content"]) for m in p.seen[1] if m["role"] != "system"]
    assert roles == [("user", "first"), ("assistant", "one"), ("user", "second")]
