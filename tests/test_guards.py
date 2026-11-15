import asyncio

import pytest

from diatonic.agent import loop
from diatonic.agent.guards import RunGuard, collapse_repeats, detect_loop, looks_unfinished
from conftest import ScriptedProvider

S1 = 'I see that the repository "sahil-deo/Mid-Map" is still listed in your repositories.'
S2 = "Let me check its current description to verify if the change has been implemented."
LOOPED = "I'll list the repositories.\n\n" + ("\n\n".join([S1 + " " + S2] * 6))


def call(name, **args):
    return {"tool_calls": [{"name": name, "arguments": args}]}


async def collect(env, provider, monkeypatch, text="do it", speak=False):
    ctx, reg, app, db = env
    monkeypatch.setattr(loop, "make_provider", lambda c: provider)
    conv = db.create_conversation()
    return conv, [e async for e in loop.run_agent(ctx, reg, conv["id"], text, speak=speak)]


def run(env, provider, monkeypatch, **kw):
    return asyncio.run(collect(env, provider, monkeypatch, **kw))


# ---------- detection ----------
def test_detects_the_screenshot_loop_at_the_second_copy():
    cut = detect_loop(LOOPED)
    assert cut is not None
    kept = LOOPED[:cut]
    assert kept.count("is still listed in your repositories") == 1


def test_streaming_prefix_is_detected_early_not_late():
    # as tokens arrive, we must trip long before the 6th paragraph
    acc, tripped_at = "", None
    for i in range(0, len(LOOPED), 7):
        acc = LOOPED[: i + 7]
        if detect_loop(acc) is not None:
            tripped_at = i
            break
    assert tripped_at is not None and tripped_at < len(LOOPED) / 2


@pytest.mark.parametrize("text", [
    "Here are your repos:\n- alpha: first project\n- beta: second project\n- gamma: third project\n\nYou have three. The newest is alpha. Let me know if you want details.",
    "| name | stars |\n|---|---|\n| a | 1 |\n| b | 2 |\n| c | 3 |\n| d | 4 |\n| e | 5 |",
    "Yes. Yes. It is done. It is done. All set, nothing else needed for this request right now.",
    "def f(x):\n    return x + 1\n\nprint(f(1))\nprint(f(2))\nprint(f(3))\nprint(f(4))\nprint(f(5))",
    "Your meeting with Priya is at 3 PM. Your meeting with Dev is at 4 PM. Your meeting with Sam is at 5 PM.",
])
def test_no_false_positives_on_normal_text(text):
    assert detect_loop(text) is None


def test_detects_character_level_loops():
    assert detect_loop("The answer is " + "the " * 40) is not None
    assert detect_loop("Result: " + "- " * 60) is not None
    assert detect_loop("Loading" + "." * 200 + " done and more text here to pad it out") is None  # ends differently


def test_in_progress_sentence_is_not_flagged():
    s = "I will now go ahead and carefully check that repository for you right away."
    assert detect_loop(s + " " + s) is not None          # a complete repeat is flagged
    assert detect_loop(s + " " + s[:30]) is None         # but a half-written one is not (yet)
    assert detect_loop("Totally different intro text goes in here. " + s[:40]) is None


def test_collapse_repeats_keeps_first_copy_only():
    out = collapse_repeats(LOOPED)
    assert out.count("is still listed in your repositories") == 1
    assert out.startswith("I'll list the repositories.")
    assert collapse_repeats("Short text.") == "Short text."


def test_looks_unfinished():
    assert looks_unfinished("I'll list the repositories to check.")
    assert looks_unfinished("Here is what I found:")
    assert not looks_unfinished("You have 3 repos. The newest is alpha.")
    assert not looks_unfinished("You have 3 repos. Let me know if you want me to check one.")
    assert not looks_unfinished("")


# ---------- the loop recovers ----------
def test_looping_reply_is_cut_and_retried(env, monkeypatch):
    p = ScriptedProvider([[{"content": LOOPED}], [{"content": "You have 3 repos."}]])
    conv, ev = run(env, p, monkeypatch)
    kinds = [e["type"] for e in ev]
    assert "rewrite" in kinds and "guard" in kinds
    final = [e for e in ev if e["type"] == "message"][0]["text"]
    assert final == "You have 3 repos."
    # the retry used harsher sampling and the broken text was not fed back to the model
    assert len(p.seen) == 2
    assert not any("is still listed" in (m.get("content") or "") for m in p.seen[1])
    assert env[3].messages(conv["id"])[-1]["meta"]["guards"]


def test_second_loop_gives_up_with_clean_partial(env, monkeypatch):
    p = ScriptedProvider([[{"content": LOOPED}], [{"content": LOOPED}]])
    _, ev = run(env, p, monkeypatch)
    final = [e for e in ev if e["type"] == "message"][0]["text"]
    assert final.count("is still listed in your repositories") == 1  # not six
    assert [e["type"] for e in ev][-1] == "done"
    assert sum(1 for e in ev if e["type"] == "guard") == 2


def test_stream_is_closed_as_soon_as_loop_is_detected(env, monkeypatch):
    produced = []

    class Endless(ScriptedProvider):
        async def stream(self, messages, tools=None, model=None, options=None):
            for i in range(2000):
                produced.append(i)
                yield {"content": (S1 + " " + S2 + "\n\n")[:20] if i else S1 + " "}
                await asyncio.sleep(0)

    p = Endless([])
    # 1st call loops; 2nd call also loops -> run ends. Neither should consume the whole stream.
    run(env, p, monkeypatch)
    assert len(produced) < 400


# ---------- tool-call guards ----------
def test_identical_read_call_is_not_executed_twice(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_read", text="x")], [call("fake_read", text="x")], [{"content": "done"}]])
    _, ev = run(env, p, monkeypatch)
    assert app.calls == [("read", {"text": "x"})]  # executed once
    assert [e["kind"] for e in ev if e["type"] == "guard"] == ["duplicate"]
    assert any("Already fetched" in (m.get("content") or "") for m in p.seen[2] if m.get("role") == "tool")


def test_repeated_write_is_never_repeated_or_reprompted(env, monkeypatch):
    ctx, reg, app, db = env
    ctx.app_cfg("fake")["always_allow"] = ["nuke"]
    p = ScriptedProvider([[call("fake_nuke", id=1)], [call("fake_nuke", id=1)], [{"content": "ok"}]])
    _, ev = run(env, p, monkeypatch)
    assert app.calls == [("nuke", {"id": 1})]


def test_endless_same_call_is_forced_to_an_answer(env, monkeypatch):
    ctx, reg, app, db = env
    same = [call("fake_read", text="x")] * 3
    p = ScriptedProvider([[c] for c in same] + [[{"content": "Here is the answer."}]])
    _, ev = run(env, p, monkeypatch)
    assert len(app.calls) == 1
    assert "budget" in [e.get("kind") for e in ev if e["type"] == "guard"]
    assert [e for e in ev if e["type"] == "message"][0]["text"] == "Here is the answer."
    assert len(p.seen) == 4  # 3 looping calls + 1 forced answer


def test_repeated_failures_force_an_answer(env, monkeypatch):
    p = ScriptedProvider([[call("fake_boom")], [call("fake_read")], [call("fake_read")], [{"content": "It failed."}]])
    _, ev = run(env, p, monkeypatch)
    assert [e for e in ev if e["type"] == "message"][0]["text"] == "It failed."


def test_tool_budget_caps_executions(env, monkeypatch):
    ctx, reg, app, db = env
    ctx.config.data["agent"]["max_tool_calls"] = 3
    ctx.config.data["agent"]["max_steps"] = 12
    script = [[call("fake_read", text=str(i))] for i in range(10)] + [[{"content": "enough"}]]
    _, ev = run(env, ScriptedProvider(script), monkeypatch)
    assert len(app.calls) == 3


def test_announcing_without_acting_gets_one_nudge(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"content": "Let me check that for you."}], [call("fake_read", text="x")], [{"content": "Found it."}]])
    _, ev = run(env, p, monkeypatch)
    assert app.calls == [("read", {"text": "x"})]
    assert [e for e in ev if e["type"] == "message"][0]["text"].endswith("Found it.")
    # nudged only once even if it keeps announcing
    p2 = ScriptedProvider([[{"content": "Let me check."}], [{"content": "Let me look."}]])
    _, ev2 = run(env, p2, monkeypatch)
    assert len(p2.seen) == 2


def test_run_deadline_stops_the_run(env, monkeypatch):
    ctx, reg, app, db = env
    ctx.config.data["agent"]["run_timeout"] = -1
    _, ev = run(env, ScriptedProvider([[{"content": "never"}]]), monkeypatch)
    assert any(e.get("kind") == "timeout" for e in ev if e["type"] == "guard")
    assert [e for e in ev if e["type"] == "message"][0]["text"]  # still ends with a clean message


def test_empty_model_reply_gets_a_fallback_message(env, monkeypatch):
    _, ev = run(env, ScriptedProvider([[{"content": ""}]]), monkeypatch)
    assert "rephras" in [e for e in ev if e["type"] == "message"][0]["text"]


# ---------- optimisations ----------
def test_only_relevant_apps_tools_are_offered(env):
    ctx, reg, app, db = env
    from diatonic.apps.registry import Registry
    from diatonic.apps.github import create as gh
    from diatonic.apps.gcal import create as gc
    r = Registry(ctx); r.register(gh(ctx)); r.register(gc(ctx))
    names = lambda t, prior=frozenset(): {n.split("_")[0] for n in loop.select_tools(r, t, set(prior))[0]}
    assert names("what's on my calendar tomorrow?") == {"gcal"}
    assert names("list my gh repos") == {"github"}
    assert names("hello there") == {"github", "gcal"}            # unknown: offer everything
    assert names("and the second one?", {"github"}) == {"github"}  # sticky within a chat


def test_history_is_deduped_clipped_and_budgeted():
    rows = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": LOOPED},
            {"role": "user", "content": "q2"}, {"role": "assistant", "content": "ok " * 2000}]
    h = loop.build_history(rows, 3000)
    assert h[0]["role"] == "user"
    assert all(len(m["content"]) <= 1600 for m in h)
    assert sum(len(m["content"]) for m in h) <= 3100
    assert not any(m["content"].count("is still listed") > 1 for m in h)


def test_system_prompt_is_stable_so_the_kv_cache_can_be_reused(env):
    ctx, reg, *_ = env
    assert loop.build_system_prompt(ctx, reg) == loop.build_system_prompt(ctx, reg)
    assert "Now:" not in loop.build_system_prompt(ctx, reg)


def test_tool_results_are_compacted():
    out = loop._dump({"a": 1, "b": None, "c": "", "d": [], "e": {"f": None, "g": 2}})
    assert out == '{"a":1,"e":{"g":2}}'


def test_config_migration_resets_old_low_temperature(tmp_path):
    import json
    from diatonic.config import Config
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"agent": {"temperature": 0.3, "model": "x"}}))
    c = Config(p)
    assert c.data["agent"]["temperature"] == 0.6 and c.data["agent"]["model"] == "x"
    p.write_text(json.dumps({"_v": 2, "agent": {"temperature": 0.3}}))
    assert Config(p).data["agent"]["temperature"] == 0.3  # a deliberate choice after migration is kept


def test_runguard_budgets():
    t = [0.0]
    g = RunGuard({"run_timeout": 10, "max_tool_calls": 2}, clock=lambda: t[0])
    assert not g.expired()
    t[0] = 11
    assert g.expired()
    g.dups = 2
    assert g.must_finalize


def test_narration_before_tool_calls_is_dropped_everywhere(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[{"content": "I'll look that up for you."}, call("fake_read", text="x")],
                          [{"content": "Result: x."}]])
    conv, ev = run(env, p, monkeypatch)
    assert {"type": "rewrite", "text": ""}.items() <= [e for e in ev if e["type"] == "rewrite"][0].items()
    # not stored, and not shown to the model on the next step
    assert db.messages(conv["id"])[-1]["content"] == "Result: x."
    assistant_turns = [m for m in p.seen[1] if m.get("role") == "assistant"]
    assert assistant_turns and all(not m["content"] for m in assistant_turns)


def test_nudge_also_applies_after_a_tool_when_the_answer_is_a_promise(env, monkeypatch):
    ctx, reg, app, db = env
    p = ScriptedProvider([[call("fake_read", text="a")], [{"content": "I'll retrieve the details now."}],
                          [call("fake_read", text="b")], [{"content": "Done: b."}]])
    _, ev = run(env, p, monkeypatch)
    assert app.calls == [("read", {"text": "a"}), ("read", {"text": "b"})]
    assert [e for e in ev if e["type"] == "message"][0]["text"] == "Done: b."


def test_reply_without_a_tool_to_a_tool_request_is_nudged_once(env, monkeypatch):
    ctx, reg, app, db = env
    app.keywords = ["thing"]
    p = ScriptedProvider([[{"content": "I've retrieved your things, they are fine."}], [call("fake_read", text="x")],
                          [{"content": "Real answer."}]])
    _, ev = run(env, p, monkeypatch, text="show my thing")
    assert app.calls == [("read", {"text": "x"})]
    assert [e for e in ev if e["type"] == "message"][0]["text"] == "Real answer."
    # a question back to the user is a legitimate non-tool reply
    p2 = ScriptedProvider([[{"content": "Which one do you mean?"}]])
    _, ev2 = run(env, p2, monkeypatch, text="show my thing")
    assert len(p2.seen) == 1


def test_recent_tool_trace_is_replayed_as_real_tool_calls():
    rows = [{"role": "user", "content": "list repos", "meta": {}},
            {"role": "assistant", "content": "You have a and b.", "meta": {"tools": [
                {"name": "github_list_repos", "args": {"limit": 2}, "ok": True, "preview": '[{"name":"a"},{"name":"b"}]'}]}},
            {"role": "user", "content": "and the second?", "meta": {}}]
    h = loop.build_history(rows, 6000)
    assert [m["role"] for m in h] == ["user", "assistant", "tool", "assistant", "user"]
    assert h[1]["tool_calls"][0]["function"]["name"] == "github_list_repos"
    assert h[2]["content"].startswith("[{")
    # only the most recent tool-using answer keeps its trace
    older = rows[:2] + [{"role": "user", "content": "x", "meta": {}}] + rows[1:]
    assert sum(1 for m in loop.build_history(older, 6000) if m["role"] == "tool") == 1


def test_a_nudged_reply_is_not_shown_twice(env, monkeypatch):
    """The model answers, gets nudged, answers the same again: the user must see it once (this showed it twice live)."""
    ctx, reg, app, db = env
    app.keywords = ["thing"]
    same = "I cannot check that. Please let me know if you'd like help with something else."
    p = ScriptedProvider([[{"content": same}], [{"content": same}]])
    conv, ev = run(env, p, monkeypatch, text="show my things")
    assert [e for e in ev if e["type"] == "message"][0]["text"] == same
    assert {"type": "rewrite", "text": ""}.items() <= next(e for e in ev if e["type"] == "rewrite").items()    # the UI clears the first draft
    assert env[3].messages(conv["id"])[-1]["content"] == same
