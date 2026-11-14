import asyncio
import time

import pytest
from starlette.testclient import TestClient

from diatonic.agent import loop
from diatonic.agent.coerce import coerce_args
from diatonic.apps.memory import create as make_memory
from diatonic.config import Config
from diatonic.db import Database
from diatonic.memory import MAX_FACTS, MemoryStore, facts_block
from diatonic.server import create_app
from conftest import ScriptedProvider


def call(name, **args):
    return {"tool_calls": [{"name": name, "arguments": args}]}


def run(env, provider, monkeypatch, text="do it"):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", lambda c: provider)
    conv = db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], text)]
    return asyncio.run(go())


# ---------------- argument coercion: fix at the source ----------------
SCHEMA = {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30}, "on": {"type": "boolean"},
                         "mode": {"type": "string", "enum": ["Open", "closed"]}, "tags": {"type": "array", "items": {"type": "string"}},
                         "name": {"type": "string"}, "ratio": {"type": "number"}}}


def test_coercion_clamps_and_reports():
    out, notes = coerce_args({"limit": 50}, SCHEMA)
    assert out["limit"] == 30 and len(notes) == 1 and "50" in notes[0] and "30" in notes[0]
    assert coerce_args({"limit": 0}, SCHEMA)[0]["limit"] == 1


@pytest.mark.parametrize("given,key,expected", [
    ("12", "limit", 12), (12.0, "limit", 12), (7.6, "limit", 8), ("true", "on", True), ("No", "on", False),
    ("open", "mode", "Open"), (" CLOSED ", "mode", "closed"), ("a, b ,c", "tags", ["a", "b", "c"]),
    (42, "name", "42"), ("2.5", "ratio", 2.5),
])
def test_coercion_fixes_harmless_formatting_silently(given, key, expected):
    out, notes = coerce_args({key: given}, SCHEMA)
    assert out[key] == expected and notes == []


def test_coercion_leaves_real_errors_for_validation():
    out, _ = coerce_args({"limit": "lots", "mode": "weird", "on": "maybe", "extra": 1}, SCHEMA)
    assert out == {"limit": "lots", "mode": "weird", "on": "maybe", "extra": 1}


def test_over_limit_request_no_longer_fails_or_retries(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_lim", limit=50)], [{"content": "ok"}]])
    ev = run(env, p, monkeypatch)
    assert app.calls == [("lim", {"limit": 30})]                       # ran once, with the allowed value
    assert [e["ok"] for e in ev if e["type"] == "tool_result"] == [True]  # no error, no second model call to fix it
    assert len(p.seen) == 2
    told = next(m["content"] for m in p.seen[1] if m.get("role") == "tool")
    assert "[adjusted:" in told and "30" in told                         # the model is told, so it won't claim 50 results


def test_github_limits_match_what_github_allows():
    import diatonic.apps.github.app as gh
    from diatonic.apps.github import create
    ctx = type("C", (), {})()
    for t in create(ctx).tools():
        if "limit" in t.parameters["properties"]:
            assert t.parameters["properties"]["limit"]["maximum"] == 100


# ---------------- store ----------------
@pytest.fixture
def mem(tmp_path):
    cfg = Config(tmp_path / "c.json")
    return MemoryStore(Database(tmp_path / "d.db"), cfg), cfg


def test_add_dedupes_exact_and_near_duplicates(mem):
    m, _ = mem
    a, s1 = m.add("My default repo is sahil-deo/Mid-Map")
    b, s2 = m.add("my default repo is  sahil-deo/mid-map.")
    c, s3 = m.add("My default repo is sahil-deo/Mid-Map!!")
    assert (s1, s2, s3) == ("created", "duplicate", "duplicate") and a["id"] == b["id"] == c["id"]
    assert len(m.all()) == 1


def test_add_rejects_junk_and_overlong(mem):
    m, _ = mem
    for bad in ("", "   ", "x" * 201):
        with pytest.raises(ValueError):
            m.add(bad)
    with pytest.raises(ValueError):
        m.add("ok", scope="bogus")


def test_fact_cap_is_enforced(mem):
    m, _ = mem
    words = "apple river lantern quartz meadow violin harbor cobalt ember falcon glacier hazel indigo jasmine kestrel lotus marble nectar orchid pepper quill raven saffron thistle umber velvet willow xenon yarrow zephyr tundra".split()
    for i, w in enumerate(words[:MAX_FACTS]):
        m.add(f"Prefers {w} over {words[(i + 7) % 31]} whenever planning {words[(i + 3) % 31]} work, since {words[(i + 11) % 31]}")
    with pytest.raises(ValueError, match="full"):
        m.add("one more completely different fact")


def test_relevance_by_scope_and_budget(mem):
    m, cfg = mem
    m.add("I work in IST", scope="global")
    m.add("Default repo is me/api", scope="app:github")
    m.add("Standups are at 10", scope="app:gcal")
    texts = lambda apps: [f["text"] for f in m.relevant(apps)]
    assert texts({"github"}) == ["I work in IST", "Default repo is me/api"][::-1] or set(texts({"github"})) == {"I work in IST", "Default repo is me/api"}
    assert "Standups are at 10" not in texts({"github"})       # other apps' facts cost nothing
    assert texts(set()) == ["I work in IST"]
    cfg.data["memory"]["max_items"] = 1
    assert len(m.relevant({"github", "gcal"})) == 1
    cfg.data["memory"].update(max_items=8, budget_chars=30)
    assert len(m.relevant({"github", "gcal"})) == 1             # character budget stops the rest


def test_pinned_facts_win_the_budget(mem):
    m, cfg = mem
    m.add("Old but pinned", pinned=True)
    time.sleep(0.01)
    m.add("Newer unpinned fact")
    cfg.data["memory"]["max_items"] = 1
    assert [f["text"] for f in m.relevant(set())] == ["Old but pinned"]


def test_disabled_memory_and_disabled_rows_inject_nothing(mem):
    m, cfg = mem
    row, _ = m.add("Some fact")
    m.update(row["id"], enabled=False)
    assert m.relevant(set()) == []
    m.update(row["id"], enabled=True)
    cfg.data["memory"]["enabled"] = False
    assert m.relevant(set()) == [] and m.tool_notes("x") == []


def test_tool_notes_are_scoped_to_one_tool_and_limited(mem):
    m, _ = mem
    for i in range(4):
        m.add(f"quirk {i} for the picker", scope="tool:fake_pick", kind="lesson", source="auto", key=f"k{i}")  # similar text, distinct keys
    m.add("other tool quirk", scope="tool:fake_lim", kind="lesson", source="auto", key="z")
    assert len(m.tool_notes("fake_pick")) == 2 and m.tool_notes("fake_lim") == ["other tool quirk"] and m.tool_notes("nope") == []
    assert m.relevant({"fake"}) == []     # lessons never go in the system prompt


def test_lesson_key_updates_in_place_and_counts_confirmations(mem):
    m, _ = mem
    m.add("old wording", scope="tool:t", kind="lesson", source="auto", key="t:limit")
    row, status = m.add("new wording", scope="tool:t", kind="lesson", source="auto", key="t:limit")
    assert status == "updated" and row["text"] == "new wording" and row["uses"] == 1 and len(m.all()) == 1


def test_decay_forgets_stale_auto_lessons_only(mem):
    m, _ = mem
    stale, _ = m.add("stale lesson", scope="tool:t", kind="lesson", source="auto", key="a")
    pinned, _ = m.add("kept lesson", scope="tool:t", kind="lesson", source="auto", key="b", pinned=True)
    mine, _ = m.add("my fact")
    old = time.time() - 200 * 86400
    m.c.execute("UPDATE memories SET updated_at=?, created_at=?, last_used=NULL", (old, old))
    m.c.commit()
    assert m.prune() == 1
    assert {r["text"] for r in m.all()} == {"kept lesson", "my fact"}


def test_forget_matching_is_fuzzy_but_careful(mem):
    m, _ = mem
    m.add("My default repo is sahil-deo/Mid-Map")
    assert m.forget_matching("completely unrelated thing") is None and len(m.all()) == 1
    assert m.forget_matching("default repo")["text"].endswith("Mid-Map") and m.all() == []


def test_facts_block_is_compact():
    assert facts_block([]) == ""
    assert facts_block([{"text": "a"}, {"text": "b"}]).count("\n- ") == 2


# ---------------- learning from error -> fix ----------------
def test_learns_a_tool_quirk_from_reject_then_fix(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_pick", mode="bad", count=5)], [call("fake_pick", mode="good", count=5)], [{"content": "done"}]])
    ev = run(env, p, monkeypatch)
    lessons = [r for r in ctx.memory.all() if r["kind"] == "lesson"]
    assert len(lessons) == 1 and lessons[0]["scope"] == "tool:fake_pick" and lessons[0]["source"] == "auto"
    assert "'bad' was rejected" in lessons[0]["text"] and "'good' works" in lessons[0]["text"]
    assert any(e["type"] == "guard" and e["kind"] == "learned" for e in ev)        # visible, never silent
    # next chat: the quirk rides along on that one tool's description
    p2 = ScriptedProvider([[{"content": "hi"}]])
    run(env, p2, monkeypatch)
    desc = next(t["function"]["description"] for t in p2.tools_seen[0] if t["function"]["name"] == "fake_pick")
    assert "Note:" in desc and "'good' works" in desc
    other = next(t["function"]["description"] for t in p2.tools_seen[0] if t["function"]["name"] == "fake_lim")
    assert "Note:" not in other


def test_same_lesson_twice_is_one_row_not_two(env, monkeypatch):
    ctx, *_ = env
    for _ in range(2):
        run(env, ScriptedProvider([[call("fake_pick", mode="bad")], [call("fake_pick", mode="good")], [{"content": "x"}]]), monkeypatch)
    lessons = [r for r in ctx.memory.all() if r["kind"] == "lesson"]
    assert len(lessons) == 1 and lessons[0]["uses"] >= 1


def test_nothing_is_learned_from_outages_free_text_or_unrelated_changes(env, monkeypatch):
    ctx, *_ = env
    run(env, ScriptedProvider([[call("fake_pick", mode="down")], [call("fake_pick", mode="good")], [{"content": "x"}]]), monkeypatch)   # 503: not bad args
    run(env, ScriptedProvider([[call("fake_pick", mode="bad", q="a")], [call("fake_pick", mode="good", q="totally different")], [{"content": "x"}]]), monkeypatch)  # free text changed too
    run(env, ScriptedProvider([[call("fake_read", text="oops")], [call("fake_read", text="ok")], [{"content": "x"}]]), monkeypatch)   # free text only
    assert [r for r in ctx.memory.all() if r["kind"] == "lesson"] == []


def test_auto_learn_can_be_switched_off(env, monkeypatch):
    ctx, *_ = env
    ctx.config.data["memory"]["auto_learn"] = False
    run(env, ScriptedProvider([[call("fake_pick", mode="bad")], [call("fake_pick", mode="good")], [{"content": "x"}]]), monkeypatch)
    assert ctx.memory.all() == []


# ---------------- facts reach the prompt ----------------
def test_facts_are_added_to_the_system_prompt(env, monkeypatch):
    ctx, *_ = env
    ctx.memory.add("My default repo is sahil-deo/Mid-Map")
    p = ScriptedProvider([[{"content": "ok"}]])
    run(env, p, monkeypatch)
    system = p.seen[0][0]["content"]
    assert "Known about the user" in system and "sahil-deo/Mid-Map" in system
    ctx.config.data["memory"]["enabled"] = False
    p2 = ScriptedProvider([[{"content": "ok"}]])
    run(env, p2, monkeypatch)
    assert "Known about the user" not in p2.seen[0][0]["content"]


def test_empty_memory_leaves_the_prompt_byte_identical(env, monkeypatch):
    ctx, reg, *_ = env
    p = ScriptedProvider([[{"content": "ok"}]])
    run(env, p, monkeypatch)
    assert p.seen[0][0]["content"] == loop.build_system_prompt(ctx, reg)   # no memory = no extra tokens, cache intact


# ---------------- remember / forget tools ----------------
def tool(app, name):
    return next(t for t in app.tools() if t.name == name)


def test_remember_tool_needs_approval_by_default_and_works(sched):
    ctx, reg, eng, store, runs, events = sched
    app = make_memory(ctx)
    assert app.default_allow == [] and tool(app, "remember").needs_approval and tool(app, "forget").needs_approval
    r = asyncio.run(tool(app, "remember").handler({"text": "I prefer short answers", "scope": "global"}))
    assert r["remembered"] == "I prefer short answers"
    again = asyncio.run(tool(app, "remember").handler({"text": "I prefer short answers."}))
    assert "already_known" in again and len(ctx.memory.all()) == 1
    scoped = asyncio.run(tool(app, "remember").handler({"text": "default repo is me/api", "scope": "fake"}))
    assert scoped["scope"] == "app:fake"
    assert [m["text"] for m in asyncio.run(tool(app, "list_memories").handler({}))]
    assert asyncio.run(tool(app, "forget").handler({"text": "short answers"}))["forgot"].startswith("I prefer")


def test_remember_tool_rejects_novels_and_unknown_apps(sched):
    ctx, *_ = sched
    app = make_memory(ctx)
    from diatonic.apps.base import ToolError
    with pytest.raises(ToolError, match="200"):
        asyncio.run(tool(app, "remember").handler({"text": "x" * 500}))
    with pytest.raises(ToolError, match="no app called"):
        asyncio.run(tool(app, "remember").handler({"text": "fine", "scope": "nonsense"}))


# ---------------- API ----------------
@pytest.fixture
def client(tmp_path):
    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        yield c


def test_memory_api_crud_and_validation(client):
    r = client.post("/api/memories", json={"text": "I live in IST", "scope": "global", "pinned": True}).json()
    assert r["status"] == "created" and r["item"]["pinned"] == 1
    mid = r["item"]["id"]
    assert client.post("/api/memories", json={"text": "I live in IST"}).json()["status"] == "duplicate"
    assert client.post("/api/memories", json={"text": "x" * 300}).status_code == 400
    assert client.post("/api/memories", json={"text": "ok", "scope": "weird"}).status_code == 400
    upd = client.put(f"/api/memories/{mid}", json={"text": "I live in India (IST)", "enabled": False}).json()
    assert upd["items"][0]["text"] == "I live in India (IST)" and upd["items"][0]["enabled"] == 0 and upd["facts_chars"] == 0
    assert client.put("/api/memories/9999", json={"text": "x"}).status_code == 404
    assert client.delete(f"/api/memories/{mid}").json()["items"] == []
    assert any(a["id"] == "memory" for a in client.get("/api/state").json()["apps"])


def test_app_facts_only_join_requests_that_involve_the_app(env, monkeypatch):
    ctx, reg, app, db = env
    app.keywords = ["widget"]
    ctx.memory.add("Everywhere fact")
    ctx.memory.add("Widget-only fact", scope="app:fake")
    p = ScriptedProvider([[{"content": "ok"}]])
    run(env, p, monkeypatch, text="tell me a joke")            # no app named: only global facts
    sys0 = p.seen[0][0]["content"]
    assert "Everywhere fact" in sys0 and "Widget-only fact" not in sys0
    p2 = ScriptedProvider([[{"content": "ok"}], [{"content": "ok"}]])   # (a no-tool reply to a tool request is nudged once)
    run(env, p2, monkeypatch, text="list my widget things")     # the app is named: its facts join
    assert "Widget-only fact" in p2.seen[0][0]["content"]
