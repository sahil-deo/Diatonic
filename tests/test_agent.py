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
    assert [r["ok"] for r in results] == [False]
    assert "invalid arguments" in results[0]["preview"]
    assert [e["kind"] for e in ev if e["type"] == "guard"] == ["unknown_tool"]
    assert any("no tool named" in m["content"] for m in p.seen[2] if m.get("role") == "tool")
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
    assert [r[0] for r in roles] == ["user", "assistant", "user"]
    assert roles[0][1].startswith("first") and roles[1][1] == "one" and roles[2][1].startswith("second")


# ---------- concurrent background runs ----------
from diatonic.agent.runs import RunBusy, RunManager  # noqa: E402


class GatedProvider(ScriptedProvider):
    """Blocks inside stream() until its gate opens, so runs overlap."""

    def __init__(self, gate, text):
        super().__init__([])
        self.gate, self.text = gate, text

    async def stream(self, messages, tools=None, model=None, options=None):
        await self.gate.wait()
        yield {"content": self.text}


def test_chats_run_concurrently_and_independently(env, monkeypatch):
    ctx, reg, app, db = env
    async def go():
        runs = RunManager()
        a, b = db.create_conversation("A"), db.create_conversation("B")
        g1, g2 = asyncio.Event(), asyncio.Event()
        queue = [GatedProvider(g1, "answer A"), GatedProvider(g2, "answer B")]
        monkeypatch.setattr(loop, "make_provider", lambda c: queue.pop(0))
        sub = runs.subscribe()
        ra = runs.start(ctx, reg, a, "q1", False)
        rb = runs.start(ctx, reg, b, "q2", False)
        await asyncio.sleep(0.05)
        assert len(runs.runs) == 2  # both in flight at once
        with pytest.raises(RunBusy):  # a chat can't have two overlapping runs
            runs.start(ctx, reg, a, "again", False)
        g2.set()  # B finishes first; A is still blocked
        await asyncio.sleep(0.1)
        assert set(runs.runs) == {a["id"]}
        assert db.messages(b["id"])[-1]["content"] == "answer B"
        g1.set()
        await asyncio.wait([ra.task, rb.task])
        assert runs.runs == {}
        evs = []
        while not sub.empty():
            evs.append(sub.get_nowait())
        # every event is tagged and sequenced per run
        for cid in (a["id"], b["id"]):
            seqs = [e["seq"] for e in evs if e["conversation_id"] == cid]
            assert seqs == list(range(1, len(seqs) + 1))
            assert [e["type"] for e in evs if e["conversation_id"] == cid][0] == "run_start"
            assert [e["type"] for e in evs if e["conversation_id"] == cid][-1] == "done"
        # a late viewer can replay the buffered events
        assert [e["type"] for e in ra.replay()["events"]][-1] == "done"

    asyncio.run(go())


def test_cancel_keeps_partial_and_cleans_up(env, monkeypatch):
    ctx, reg, app, db = env

    async def go():
        runs = RunManager()
        conv = db.create_conversation("C")
        monkeypatch.setattr(loop, "make_provider", lambda c: GatedProvider(asyncio.Event(), "never"))
        run = runs.start(ctx, reg, conv, "hang", False)
        await asyncio.sleep(0.05)
        assert runs.cancel(conv["id"])
        await asyncio.wait([run.task])
        types_ = [e["type"] for e in run.events]
        assert "cancelled" in types_ and types_[-1] == "done"
        assert conv["id"] not in runs.runs
        assert db.messages(conv["id"])[-1]["meta"].get("stopped") is True
        assert not runs.cancel(conv["id"])  # nothing left to cancel

    asyncio.run(go())


def test_approval_waits_in_background_and_status_tracks(env, monkeypatch):
    ctx, reg, app, db = env

    async def go():
        runs = RunManager()
        conv = db.create_conversation("D")
        p = ScriptedProvider([[call("fake_nuke", id=3)], [{"content": "done"}]])
        monkeypatch.setattr(loop, "make_provider", lambda c: p)
        run = runs.start(ctx, reg, conv, "nuke", False)
        await asyncio.sleep(0.1)
        assert run.status == "waiting" and runs.snapshot()[0]["status"] == "waiting"
        pending_id = next(e["id"] for e in run.events if e["type"] == "tool_call")
        approvals.resolve(pending_id, True)  # approved from "elsewhere", no stream attached
        await asyncio.wait([run.task])
        assert app.calls == [("nuke", {"id": 3})]

    asyncio.run(go())
