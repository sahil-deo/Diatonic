"""The agent loop: model -> tool calls -> approvals -> results -> model, streamed as events.

Built for small local models, so it assumes the model will misbehave and contains the damage:
repetition is detected mid-stream and cut off, identical tool calls are never re-run, every run has a
time/step/tool budget, and when the model can't finish on its own the harness forces a short answer.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from contextlib import aclosing
from typing import AsyncIterator

import jsonschema

from ..apps.base import ToolError
from ..voice import summarizer
from . import approvals
from ..memory import LessonTracker, facts_block
from .coerce import coerce_args
from .guards import RunGuard, clip, collapse_repeats, detect_loop, looks_unfinished
from .providers import ProviderError, make_provider

MAX_RESULT_CHARS = 3500   # per tool result sent to the model
MAX_CALLS_PER_STEP = 4
RETRY_OPTIONS = {"temperature": 0.9, "repeat_penalty": 1.2, "top_k": 50}

SYSTEM_PROMPT = """You are Diatonic, a concise assistant that operates the user's apps with tools.
- To look something up or change it, call a tool. Never guess data.
- Call a tool once per question. When you have its result, answer; don't call it again with the same arguments.
- If no available tool can do what was asked, say so plainly. Don't improvise with unrelated tools.
- Don't announce what you're about to do; do it. Never repeat yourself.
- Say an action succeeded only if its tool succeeded. If a tool failed, say why in one line.
- If a required detail is missing, ask one short question.
- Changes need the user's approval; the system asks, you just call the tool.
- Resolve relative dates from the current time and pass ISO 8601.
- Keep answers short. Lead with the result."""


def build_system_prompt(ctx, registry) -> str:
    extra = ctx.config.data["agent"].get("system_prompt", "").strip()
    # The clock goes on the user turn instead, so this prefix stays identical and Ollama can reuse its KV cache.
    return SYSTEM_PROMPT + f"\nTimezone: {ctx.tz_name}." + (f"\n\nUser preferences:\n{extra}" if extra else "")


def select_tools(registry, text: str, prior_apps: set[str]) -> tuple[dict, bool]:
    """Show the model only the tools that plausibly matter (apps named, or already used in this chat).

    Returns (tool_map, matched) where `matched` means the request itself names an app, so a reply
    that calls no tool is suspicious.
    """
    apps = registry.enabled()
    low = text.lower()
    named = [a for a in apps if any(re.search(rf"\b{re.escape(k)}\b", low) for k in a.keywords)]
    chosen = [a for a in apps if a in named or a.id in prior_apps]
    return {f"{a.id}_{t.name}": (a, t) for a in (chosen or apps) for t in a.tools()}, bool(named)


TRACE_TURNS = 1       # how many of the most recent tool-using answers keep their tool trace in context
TRACE_CHARS = 500     # per replayed tool result


def build_history(rows: list[dict], max_chars: int) -> list[dict]:
    """Recent turns, de-looped and clipped, newest-first within a character budget.

    The latest tool-using answer is replayed as a real tool call + result, so the model keeps seeing that
    answers come from tools (and can answer follow-ups from the data) instead of imitating plain-text replies.
    """
    out, used, traces = [], 0, 0
    for m in reversed(rows):
        content = collapse_repeats(m["content"]) if m["role"] == "assistant" else m["content"]
        content = clip(content, 1500 if m["role"] == "assistant" else 1200)
        turn = [{"role": m["role"], "content": content}]
        tools = [t for t in (m.get("meta") or {}).get("tools", []) if t.get("ok") and t.get("name")]
        if m["role"] == "assistant" and tools and traces < TRACE_TURNS:
            traces += 1
            calls = [{"function": {"name": t["name"], "arguments": t.get("args") or {}}} for t in tools[:3]]
            turn = [{"role": "assistant", "content": "", "tool_calls": calls},
                    *({"role": "tool", "tool_name": t["name"], "content": clip(t.get("preview") or "", TRACE_CHARS)}
                      for t in tools[:3]), *turn]
        cost = sum(len(x.get("content") or "") for x in turn)
        if used + cost > max_chars and out:
            break
        used += cost
        out = turn + out
    while out and out[0]["role"] != "user":  # always start on a user turn
        out.pop(0)
    return out


# Parameter names that explain themselves; their schema descriptions only cost prompt tokens.
_OBVIOUS = {"summary", "description", "location", "title", "body", "query", "labels", "state", "limit",
            "max_results", "calendar_id", "attendees", "homepage", "number", "event_id", "reason", "method"}


def tool_spec(app, tool, notes: list[str] | tuple = ()) -> dict:
    props = {k: ({kk: vv for kk, vv in v.items() if kk != "description"} if k in _OBVIOUS else v)
             for k, v in tool.parameters.get("properties", {}).items()}
    params = {**tool.parameters, "properties": props}
    desc = tool.description + (" Note: " + " ".join(notes) if notes else "")   # learned quirks ride along for free
    return {"type": "function", "function": {"name": f"{app.id}_{tool.name}", "description": desc, "parameters": params}}


def _args(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _compact(obj):
    """Drop nulls/empties so tool results cost fewer tokens."""
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [_compact(v) for v in obj]
    return obj


def _dump(result) -> str:
    if isinstance(result, str):
        return result
    return json.dumps(_compact(result), default=str, ensure_ascii=False, separators=(",", ":"))


async def _generate(provider, messages, specs, holder: dict, options=None) -> AsyncIterator[dict]:
    """One model call. Streams tokens, cuts the stream the moment it starts repeating itself."""
    content, calls = "", []
    holder.update(looped=False, truncated=False, tokens=0)
    async with aclosing(provider.stream(messages, specs, options=options)) as stream:
        async for chunk in stream:
            if chunk.get("content"):
                content += chunk["content"]
                cut = detect_loop(content)
                if cut is not None:
                    content = content[:cut].rstrip()
                    holder["looped"] = True
                    yield {"type": "rewrite", "text": content}
                    break  # leaving the block closes the HTTP stream, so Ollama stops generating
                yield {"type": "token", "text": chunk["content"]}
            if chunk.get("tool_calls"):
                calls.extend(chunk["tool_calls"])
            if chunk.get("usage"):
                holder["tokens"] = chunk["usage"].get("completion", 0)
            if chunk.get("truncated"):
                holder["truncated"] = True
    holder.update(content=content, calls=calls)


async def _execute(call: dict, tool_map: dict, registry, guard: RunGuard, out: dict, ctx=None,
                   learner: LessonTracker | None = None) -> AsyncIterator[dict]:
    """Run one tool call, yielding UI events. What the model should see goes in out['content']."""
    cid = uuid.uuid4().hex[:8]
    name, args = call["name"], _args(call["arguments"])
    entry = tool_map.get(name)
    if entry is None:
        avail = ", ".join(tool_map) or "none"
        out.update(ok=False, content=f"Error: no tool named '{name}'. Available: {avail}.")
        guard.errors += 1
        yield {"type": "guard", "kind": "unknown_tool", "message": f"The model asked for a tool that doesn't exist ({name})."}
        return
    app, tool = entry
    args, clamps = coerce_args(args, tool.parameters)   # forgive harmless mistakes (limit 50 > max 30) instead of retrying
    key = guard.key(name, args)

    prev = guard.seen.get(key)
    if prev:  # identical call already made in this run: never run it twice
        guard.dups += 1
        if not prev["ok"]:
            msg = f"This exact call already failed: {prev['content']} Change the arguments, or stop and explain."
        elif tool.risk == "read":
            msg = "[Already fetched earlier in this run. Use this result and answer; don't call again.]\n" + prev["content"]
        else:
            msg = "Already done earlier in this run. Don't repeat it; tell the user it's done."
        out.update(ok=True, content=msg, dup=True)
        yield {"type": "guard", "kind": "duplicate", "message": f"Skipped a repeated “{tool.title}” call."}
        return

    try:
        jsonschema.validate(args, tool.parameters)
    except jsonschema.ValidationError as e:
        yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title,
               "args": args, "risk": tool.risk, "describe": "", "needs_approval": False}
        out.update(ok=False, content=f"Error: invalid arguments for {name}: {e.message}. Fix them and retry.")
        guard.errors += 1
        if learner:
            learner.on_failure(name, args, e.message)
        yield {"type": "tool_result", "id": cid, "ok": False, "preview": out["content"], "ms": 0}
        return

    blocked = tool.precheck(args) if tool.precheck else None
    if blocked:  # don't ask the user to approve something that cannot run
        yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title, "args": args,
               "risk": tool.risk, "describe": "", "needs_approval": False}
        out.update(ok=False, content=f"Error: {blocked}")
        guard.errors += 1
        yield {"type": "tool_result", "id": cid, "ok": False, "preview": out["content"], "ms": 0}
        return

    describe = tool.describe(args) if tool.describe else tool.title
    needs = tool.needs_approval and not registry.is_always_allowed(app, tool)
    yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title, "args": args,
           "risk": tool.risk, "describe": describe, "needs_approval": needs}
    out["approval"] = False
    if needs:
        out["approval"] = True
        fut = approvals.request(cid)
        yield {"type": "notify", "text": f"I need your approval: {describe}"}
        try:
            approved = await asyncio.wait_for(fut, guard.approval_timeout)
        except asyncio.TimeoutError:
            approved = False  # unattended and nobody answered
        finally:
            approvals.discard(cid)
        yield {"type": "tool_status", "id": cid, "status": "approved" if approved else "denied"}
        if not approved:
            out.update(ok=False, denied=True, content="The user denied this action. Don't retry it; acknowledge and ask what they'd like instead.")
            guard.remember(key, ok=False, content="The user denied it.", risk=tool.risk)
            yield {"type": "tool_result", "id": cid, "ok": False, "preview": "Denied by you", "ms": 0}
            return

    guard.calls += 1
    t0 = time.monotonic()
    try:
        result = await asyncio.wait_for(tool.handler(args), 60)
        text, ok = _dump(result), True
    except ToolError as e:
        text, ok = f"Error: {e}", False
        if learner and e.bad_args:
            learner.on_failure(name, args, str(e))
    except asyncio.TimeoutError:
        text, ok = "Error: the app took too long to respond.", False
    except Exception as e:  # tool bugs shouldn't crash the run
        text, ok = f"Error: {type(e).__name__}: {e}", False
    ms = int((time.monotonic() - t0) * 1000)
    guard.remember(key, ok=ok, content=clip(text, MAX_RESULT_CHARS), risk=tool.risk)
    if not ok:
        guard.errors += 1
    content = clip(text, MAX_RESULT_CHARS)
    if clamps and ok:
        content = f"[adjusted: {'; '.join(clamps)}]\n" + content
    out.update(ok=ok, content=content)
    yield {"type": "tool_result", "id": cid, "ok": ok, "preview": clip(text, 1500), "ms": ms}
    if ok and learner and ctx is not None:
        lesson = learner.on_success(name, args, tool.parameters, tool.title)
        if lesson:
            text_, lkey = lesson
            try:
                _, status = ctx.memory.add(text_, scope=f"tool:{name}", kind="lesson", source="auto", key=lkey)
                yield {"type": "guard", "kind": "learned",
                       "message": f"Learned for next time: {text_}" if status == "created" else f"Confirmed a known quirk: {text_}"}
            except ValueError:
                pass


def _fallback(cards: list[dict]) -> str:
    done = [c for c in cards if c.get("ok")]
    if done:
        return f"I ran {len(done)} action{'s' if len(done) != 1 else ''} but couldn't put together a summary. The results are in the cards above."
    if cards:
        return "I couldn't complete that. The tool results above show what went wrong."
    return "I didn't manage to answer that. Try rephrasing, or break it into smaller steps."


async def run_agent(ctx, registry, conv_id: str, text: str, *, speak: bool = False, readonly: bool = False,
                    approval_timeout: float | None = None, extra_system: str = "") -> AsyncIterator[dict]:
    cfg = ctx.config.data
    acfg = cfg["agent"]
    t0 = time.monotonic()
    provider = make_provider(ctx)
    guard = RunGuard(acfg)
    guard.approval_timeout = approval_timeout

    history = build_history(ctx.db.history(conv_id, limit=int(acfg["history_messages"])), int(acfg["history_chars"]))
    prior_apps = ctx.db.apps_used(conv_id)
    ctx.db.add_message(conv_id, "user", text)
    tool_map, expects_tool = select_tools(registry, text, prior_apps)
    if readonly:  # unattended runs may only read; nothing can be changed without a person present
        tool_map = {n: (a, t) for n, (a, t) in tool_map.items() if not t.needs_approval}
    mem = ctx.memory
    # With no app named in the request every app's tools are offered as a fallback, but app-specific facts aren't.
    facts = mem.relevant({a.id for a, _ in tool_map.values()} if (expects_tool or prior_apps) else set())
    specs = [tool_spec(a, t, mem.tool_notes(n)) for n, (a, t) in tool_map.items()]
    learner = LessonTracker() if acfg and ctx.config.data["memory"]["auto_learn"] and ctx.config.data["memory"]["enabled"] else None
    now = ctx.now().strftime("%A %Y-%m-%d %H:%M")
    messages = [{"role": "system", "content": build_system_prompt(ctx, registry) + facts_block(facts) + extra_system}, *history,
                {"role": "user", "content": f"{text}\n\n(Now: {now})"}]

    cards: dict[str, dict] = {}
    texts: list[str] = []
    actions: list[str] = []
    guards: list[str] = []
    used_tools = had_approval = had_error = False
    error: str | None = None
    tokens = 0
    options: dict | None = None
    forced = finished = False

    def note(kind: str, message: str) -> dict:
        guards.append(message)
        return {"type": "guard", "kind": kind, "message": message}

    try:
        for _step in range(int(acfg["max_steps"])):
            if guard.expired():
                yield note("timeout", f"Stopped: this run took longer than {int(acfg['run_timeout'])}s.")
                break
            holder: dict = {}
            async for ev in _generate(provider, messages, None if forced else specs, holder, options):
                yield ev
            content, calls = holder["content"], [] if forced else holder["calls"]
            tokens += holder["tokens"]

            if holder["looped"]:
                guard.loops += 1
                if guard.loops == 1:  # retry once: hotter sampling, stronger repetition penalty, explicit nudge
                    yield {"type": "rewrite", "text": ""}
                    yield note("loop", "The model started repeating itself, so I cut it off and retried.")
                    options = RETRY_OPTIONS
                    messages.append({"role": "user", "content": "(Your last reply repeated itself. Reply once, briefly, without repeating. Call a tool if one is needed.)"})
                    continue
                if content:
                    texts.append(content)
                yield note("loop", "The model kept repeating itself, so I stopped. Try rephrasing or splitting the request.")
                finished = True
                break
            if holder["truncated"]:
                yield note("length", "The reply hit the length limit and was cut short.")

            if calls:
                # "I'll check that for you" before a tool call is noise, and small models imitate it
                # (then loop on it). Drop it from the UI, the stored chat and the context.
                if content.strip():
                    yield {"type": "rewrite", "text": ""}
                content = ""
            elif content.strip():
                texts.append(content.strip())
            if not calls:
                if not forced and not guard.nudged and tool_map:
                    why = None
                    if looks_unfinished(content):  # "Let me check..." and then nothing
                        why = "Don't describe what you'll do. Call the tool now."
                    elif expects_tool and not used_tools and not content.rstrip().endswith("?"):
                        why = ("You didn't call a tool, so that reply may be made up. If the request needs data or an "
                               "action, call the right tool now. If no tool can do it, say so plainly.")
                    if why:
                        guard.nudged = True  # once per run
                        messages += [{"role": "assistant", "content": content}, {"role": "user", "content": why}]
                        continue
                finished = True
                break

            messages.append({"role": "assistant", "content": content,
                             "tool_calls": [{"function": {"name": c["name"], "arguments": _args(c["arguments"])}}
                                            for c in calls[:MAX_CALLS_PER_STEP]]})
            used_tools = True
            for call in calls[:MAX_CALLS_PER_STEP]:
                out: dict = {}
                if guard.calls >= guard.max_calls:
                    out.update(ok=False, content="Tool budget for this run is used up. Answer with what you have.")
                else:
                    async for ev in _execute(call, tool_map, registry, guard, out, ctx, learner):
                        if ev["type"] == "guard":
                            guards.append(ev["message"])
                        elif ev["type"] == "tool_call":
                            cards[ev["id"]] = {k: ev[k] for k in ("id", "name", "app", "title", "args", "risk", "describe")}
                        elif ev["type"] == "tool_status":
                            cards[ev["id"]]["status"] = ev["status"]
                        elif ev["type"] == "tool_result":
                            cards[ev["id"]].update(ok=ev["ok"], preview=ev["preview"], ms=ev["ms"])
                            actions.append(f"{cards[ev['id']]['title']} ({cards[ev['id']]['app']}): "
                                           f"{'ok' if ev['ok'] else 'failed'}")
                        yield ev
                had_approval = had_approval or bool(out.get("approval"))
                had_error = had_error or (not out.get("ok", False) and not out.get("denied", False))
                messages.append({"role": "tool", "tool_name": call["name"], "content": out.get("content", "")})

            if guard.must_finalize and not forced:
                forced = True  # the model is going in circles: next turn has no tools and must answer
                yield note("budget", "Wrapping up: repeated or failing tool calls.")
                messages.append({"role": "user", "content": "Using only the information above, answer the user now in 1-4 sentences. Do not call tools."})
        else:
            if not finished and not (forced and texts):
                yield note("steps", "Stopped at the step limit before finishing.")
    except ProviderError as e:
        error, had_error = str(e), True
        yield {"type": "error", "message": error}
    except asyncio.CancelledError:
        # Stopped by the user: keep what was produced so the chat history stays coherent.
        partial = collapse_repeats("\n\n".join(texts))
        ctx.db.add_message(conv_id, "assistant", partial, {"tools": list(cards.values()), "stopped": True, "guards": guards})
        raise

    final = collapse_repeats(texts[-1] if used_tools and texts else "\n\n".join(texts))
    if not final and not error:
        final = _fallback(list(cards.values()))
        yield {"type": "rewrite", "text": final}
    ctx.db.add_message(conv_id, "assistant", final, {"tools": list(cards.values()), "error": error, "guards": guards})
    yield {"type": "message", "text": final}

    seconds = time.monotonic() - t0
    rules = cfg["voice"]["speak_when"]
    if speak and (final or error):
        if error:
            yield {"type": "summary", "text": "Something went wrong. " + (summarizer.clean_for_speech(error, 14) or "")}
        elif guards and not used_tools:
            yield {"type": "summary", "text": "I got stuck on that one. Please try again."}
        elif summarizer.should_speak(rules, used_tools=used_tools, had_approval=had_approval,
                                     had_error=had_error or bool(guards), seconds=seconds):
            try:
                s = await summarizer.summarize(provider, cfg["voice"]["summary_model"], text, actions, final)
            except ProviderError:
                s = None
            if s:
                yield {"type": "summary", "text": s}
    yield {"type": "done", "seconds": round(seconds, 1), "tokens": tokens}
