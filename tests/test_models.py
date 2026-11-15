"""Saved models (local and cloud), the OpenAI-compatible adapter, and the privacy gate for cloud models."""
import asyncio
import json

import httpx
import pytest
from starlette.testclient import TestClient

from conftest import ScriptedProvider
from diatonic import models as M
from diatonic import secrets
from diatonic.agent import approvals, loop
from diatonic.agent.providers import ProviderError, make_provider
from diatonic.agent.providers.openai import OpenAICompatProvider, to_openai

GROQ = "https://api.groq.com/openai/v1"


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"


def provider(handler, **cfg):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatProvider(http, {"model": "qwen/qwen3-32b", "endpoint": GROQ, "api_key": "gsk_test", "temperature": 0.6,
                                       "max_tokens": 512, "first_token_timeout": 5, "idle_timeout": 5, **cfg})


async def collect(p, msgs, tools=None):
    return [c async for c in p.stream(msgs, tools)]


# ---------- the adapter ----------
def test_streams_text_and_tool_calls_in_the_same_shape_as_ollama():
    seen = {}

    def handler(req):
        seen["body"], seen["auth"] = json.loads(req.content), req.headers["authorization"]
        return httpx.Response(200, text=sse(
            {"choices": [{"delta": {"content": "<think>hmm"}}]}, {"choices": [{"delta": {"content": " plan</think>Sure, "}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "a", "function": {"name": "fake_read", "arguments": '{"te'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'xt": "x"}'}}]}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 120, "completion_tokens": 9}}))
    out = asyncio.run(collect(provider(handler), [{"role": "user", "content": "hi"}], [{"type": "function", "function": {"name": "fake_read"}}]))
    assert "".join(c.get("content", "") for c in out) == "Sure, "                              # reasoning is not shown
    assert [c for c in out if "tool_calls" in c][0]["tool_calls"] == [{"name": "fake_read", "arguments": {"text": "x"}}]
    assert out[-1]["usage"]["prompt"] == 120 and out[-1]["truncated"] is False
    assert seen["auth"] == "Bearer gsk_test" and seen["body"]["model"] == "qwen/qwen3-32b" and seen["body"]["tools"]


def test_history_is_converted_to_openai_tool_calls():
    msgs = to_openai([{"role": "user", "content": "q"},
                      {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "a_b", "arguments": {"x": 1}}}]},
                      {"role": "tool", "tool_name": "a_b", "content": "res"}])
    assert msgs[1]["tool_calls"][0] == {"id": "call_1", "type": "function", "function": {"name": "a_b", "arguments": '{"x": 1}'}}
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_1", "content": "res"}


def test_errors_are_explained_and_rate_limits_retried(monkeypatch):
    async def fast(_):
        return None
    monkeypatch.setattr(asyncio, "sleep", fast)
    n = {"c": 0}

    def flaky(req):
        n["c"] += 1
        return httpx.Response(429, text="slow down") if n["c"] < 3 else httpx.Response(200, text=sse({"choices": [{"delta": {"content": "ok"}}]}))
    assert "".join(c.get("content", "") for c in asyncio.run(collect(provider(flaky), [{"role": "user", "content": "x"}]))) == "ok"
    with pytest.raises(ProviderError, match="rejected the API key"):
        asyncio.run(collect(provider(lambda r: httpx.Response(401, text="bad")), [{"role": "user", "content": "x"}]))
    with pytest.raises(ProviderError, match="No API key"):
        asyncio.run(collect(provider(lambda r: httpx.Response(200), api_key=None), [{"role": "user", "content": "x"}]))


# ---------- saved models ----------
def test_saved_models_seed_validate_and_switch(env):
    ctx, *_ = env
    cfg = ctx.config
    first = M.ensure(cfg)
    assert first[0]["model"] == cfg.data["agent"]["model"] and cfg.data["agent"]["active_model"] == first[0]["id"]
    for bad in [{"preset": "groq", "model": ""}, {"preset": "openai", "model": "x", "endpoint": "http://api.example.com/v1"},
                {"preset": "groq", "model": "has space"}]:
        with pytest.raises(ValueError):
            M.clean(bad, first)
    g = M.clean({"preset": "groq", "model": "qwen/qwen3-32b", "label": "Qwen on Groq"}, first)
    assert g["provider"] == "openai" and g["endpoint"] == GROQ and M.is_cloud(g) and not M.is_cloud(first[0])
    cfg.data["models"].append(g)
    M.activate(cfg, g["id"])
    a = cfg.data["agent"]
    assert (a["provider"], a["model"], a["endpoint"], a["num_ctx"]) == ("openai", "qwen/qwen3-32b", GROQ, 32768)
    assert ctx.cloud()
    secrets.set(M.key_name(GROQ), "gsk_live")
    p = make_provider(ctx)
    assert isinstance(p, OpenAICompatProvider) and p.cfg["api_key"] == "gsk_live"
    assert "gsk_live" not in json.dumps(M.public(cfg)) and M.public(cfg)["models"][-1]["key_set"] is True
    secrets.delete(M.key_name(GROQ))


def test_model_routes_switch_every_window(env):
    ctx, *_ = env
    from diatonic.server import create_app
    app = create_app(ctx.config, ctx.db, allowed_hosts={"testserver"})
    sent = []
    app.state.ctx.broadcast = sent.append
    with TestClient(app) as c:
        d = c.post("/api/models", json={"preset": "gemini", "model": "gemini-flash-x", "label": "Gemini", "key": "AIza-test"}).json()
        gem = next(m for m in d["models"] if m["label"] == "Gemini")
        assert gem["cloud"] and gem["key_set"] and "AIza" not in json.dumps(d)
        assert c.post("/api/models", json={"preset": "groq", "model": "bad name"}).status_code == 400
        assert c.put("/api/models/active", json={"id": gem["id"]}).json()["active"] == gem["id"]
        assert sent[-1] == {"type": "model_changed", "model": gem["id"]}
        assert c.delete(f"/api/models/{gem['id']}").status_code == 400                     # in use
    secrets.delete(M.key_name("https://generativelanguage.googleapis.com/v1beta/openai"))


# ---------- the privacy gate ----------
def go_cloud(ctx):
    ctx.config.data["agent"].update(provider="openai", endpoint=GROQ, model="qwen/qwen3-32b", active_model="groq-qwen")
    ctx.app_cfg("fake")["private"] = True


async def run(ctx, reg, db, script, conv=None, text="read it", on_event=None, **kw):
    conv = conv or db.create_conversation()["id"]
    loop.make_provider = lambda c: ScriptedProvider(script)
    evs = []
    async for ev in loop.run_agent(ctx, reg, conv, text, **kw):
        evs.append(ev)
        if on_event:
            await on_event(ev)
    return conv, evs


def call(name, **a):
    return {"tool_calls": [{"name": name, "arguments": a}]}


def test_a_cloud_model_must_ask_before_using_private_apps(env, monkeypatch):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", loop.make_provider)
    go_cloud(ctx)
    answers = iter(["chat"])

    async def approve(ev):
        if ev["type"] == "tool_call" and ev.get("needs_approval"):
            assert ev["share"] and "(cloud)" in ev["describe"]
            asyncio.get_running_loop().call_later(0.01, approvals.resolve, ev["id"], next(answers))

    async def flow():
        conv, evs = await run(ctx, reg, db, [[call("fake_read", text="a")], [{"content": "done"}]], on_event=approve)
        assert app.calls == [("read", {"text": "a"})]
        # "Allow for this chat": no second card in the same chat with the same model
        conv, evs = await run(ctx, reg, db, [[call("fake_read", text="b")], [{"content": "done"}]], conv=conv)
        assert not [e for e in evs if e.get("needs_approval")] and app.calls[-1] == ("read", {"text": "b"})
        # a new chat asks again
        _, evs = await run(ctx, reg, db, [[call("fake_read", text="c")], [{"content": "x"}]], on_event=None, approval_timeout=0.05)
        assert [e for e in evs if e.get("needs_approval")] and app.calls[-1] != ("read", {"text": "c"})
    asyncio.run(flow())


def test_local_models_and_non_private_apps_are_unaffected(env, monkeypatch):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", loop.make_provider)

    async def flow():
        _, evs = await run(ctx, reg, db, [[call("fake_read", text="a")], [{"content": "done"}]])      # local model
        assert not [e for e in evs if e.get("needs_approval")]
        go_cloud(ctx); ctx.app_cfg("fake")["private"] = False
        _, evs = await run(ctx, reg, db, [[call("fake_read", text="a2")], [{"content": "done"}]])     # cloud, not private
        assert not [e for e in evs if e.get("needs_approval")]
    asyncio.run(flow())


def test_private_history_facts_and_scheduled_runs_stay_local(env, monkeypatch):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", loop.make_provider)
    conv = db.create_conversation()["id"]

    async def flow():
        await run(ctx, reg, db, [[call("fake_read", text="secret")], [{"content": "Your secret is 42"}]], conv=conv)   # local, private data
        go_cloud(ctx)
        ctx.app_cfg("memory")["private"] = True
        seen = {}

        class P(ScriptedProvider):
            async def stream(self, messages, tools=None, model=None, options=None):
                seen.setdefault("msgs", messages)
                async for c in super().stream(messages, tools, model, options):
                    yield c
        loop.make_provider = lambda c: P([[{"content": "ok"}]] * 3)
        async for _ in loop.run_agent(ctx, reg, conv, "and what else?"):
            pass
        text = json.dumps(seen["msgs"])
        assert "Your secret is 42" not in text and "isn't shared with a cloud model" in text
        conv2, evs = await run(ctx, reg, db, [[call("fake_read", text="x")], [{"content": "x"}]], readonly=True)
        assert not [e for e in evs if e["type"] == "tool_result" and e["ok"]]               # unattended + cloud: private tools aren't even offered
    asyncio.run(flow())


def test_gemini_thought_signatures_go_back_with_their_tool_calls():
    sig = {"google": {"thought_signature": "Cv8BAb4+9vu..."}}

    def handler(req):
        return httpx.Response(200, text=sse({"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "x", "extra_content": sig,
                                                     "function": {"name": "notes_list_notes", "arguments": "{}"}}]}, "finish_reason": "tool_calls"}]}))
    p = provider(handler, endpoint="https://generativelanguage.googleapis.com/v1beta/openai")
    out = asyncio.run(collect(p, [{"role": "user", "content": "list notes"}], [{"type": "function", "function": {"name": "notes_list_notes"}}]))
    call = [c for c in out if "tool_calls" in c][0]["tool_calls"][0]
    assert call["extra"] == sig
    # the loop records it with the call; the next request sends it back unchanged
    sent = to_openai([{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": call["name"], "arguments": {}}, "extra": call["extra"]}]},
                      {"role": "tool", "content": "[]"}], gemini=True)
    assert sent[0]["tool_calls"][0]["extra_content"] == sig
    # calls with no signature (replayed history, another model) get Google's placeholder, but only for Gemini
    replay = [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "a", "arguments": {}}}]}, {"role": "tool", "content": "x"}]
    assert to_openai(replay, gemini=True)[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "skip_thought_signature_validator"
    assert "extra_content" not in to_openai(replay)[0]["tool_calls"][0]


def test_the_loop_passes_the_signature_into_the_next_model_call(env, monkeypatch):
    ctx, reg, app, db = env
    seen = []

    class P(ScriptedProvider):
        async def stream(self, messages, tools=None, model=None, options=None):
            seen.append(json.loads(json.dumps(messages)))
            async for c in super().stream(messages, tools, model, options):
                yield c
    sig = {"google": {"thought_signature": "abc"}}
    monkeypatch.setattr(loop, "make_provider", lambda c: P([[{"tool_calls": [{"name": "fake_read", "arguments": {"text": "x"}, "extra": sig}]}], [{"content": "ok"}]]))

    async def go():
        async for _ in loop.run_agent(ctx, reg, db.create_conversation()["id"], "read x"):
            pass
    asyncio.run(go())
    step2 = seen[1]
    assert next(m for m in step2 if m.get("tool_calls"))["tool_calls"][0]["extra"] == sig
