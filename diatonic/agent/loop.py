"""The agent loop: model -> tool calls -> approvals -> results -> model, streamed as events.

Built for small local models, so it assumes the model will misbehave and contains the damage:
repetition is detected mid-stream and cut off, identical tool calls are never re-run, every run has a
time/step/tool budget, and when the model can't finish on its own the harness forces a short answer.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
import uuid
from contextlib import aclosing
from typing import AsyncIterator

import jsonschema

from ..apps.base import ToolError
from ..voice import summarizer
from . import approvals, session
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


# "show my calendar", "open my latest email", "hide the sidebar": the user wants the screen moved, not data read out.
# Only the interface tools are offered then, so a small model can't wander off into a lookup (and the prompt is tiny).
_NAV = re.compile(r"^\W*(?:(?:please|can you|could you|just)\s+)*(?:show|open|go to|bring up|pull up|take me to|switch to|display|hide|expand|collapse|maximi[sz]e|minimi[sz]e|exit|leave|restore|shrink)\b", re.I)
_NAV_NOT = re.compile(r"\b(?:and then|then|summari[sz]e|tell me|what|how many|count|explain|read (?:it|me)|list|status|versions?|"
                      r"space|size|usage|running|installed|log|diff)\b|\d", re.I)   # "show me the git status" asks for data


def is_navigation(text: str) -> bool:
    return bool(_NAV.match(text)) and not _NAV_NOT.search(text)


# "summarize this", "reply to it", "delete that one": the user means what is open on their screen.
_DEICTIC = re.compile(r"\b(?:this|that|it|these|those|here|current(?:ly)?|opened|on (?:the )?screen)\b", re.I)


def screen_line(registry, screen) -> tuple[str, str | None]:
    """A short note about the open panel and item, added to the user's turn only when they refer to it."""
    if not isinstance(screen, dict):
        return "", None
    app = registry.apps.get(str(screen.get("app") or ""))
    if not app or not app.panel:
        return "", None
    bits = [app.name]
    if screen.get("title"):
        bits.append(f"showing {str(screen.get('kind') or 'item')} \u201c{str(screen['title'])[:140]}\u201d")
    if screen.get("ref"):
        bits.append(f"ref: {str(screen['ref'])[:300]}")
    return f"\n\n[On the user's screen: {', '.join(bits)}. 'this' or 'it' means that.]", app.id


_STOP = set("""a an the and or but if then so to of in on at for from by with about into over as is are was were be been am do does did
have has had i me my mine you your it its this that these those we us our they them their what which who whom when where why how can could
would should will shall may might must please just also too very some any all each every no not new make made get got give let want need
like use using up down out off than there here now today tomorrow app apps thing things one""".split())


def _words(s: str) -> set[str]:
    return {w[:6] for w in re.findall(r"[a-z]{3,}", s.lower()) if w not in _STOP}


FALLBACK_ALL_CHARS = 9000   # ~2.5k tokens: above this, a request that names no app gets only its best-matching tools


def rank_tools(registry, text: str, limit: int = 8) -> dict:
    """No app was named: offer the few tools whose names and descriptions share words with the request (none for chit-chat).

    Cheap (no model call) and bounded, unlike offering every tool, which no longer fits an 8k context.
    """
    want = _words(text)
    if not want:
        return {}
    scored = []
    for a in registry.enabled():
        for t in a.tools():
            name = _words(t.name.replace("_", " ") + " " + a.name)
            desc = _words(t.description)
            score = 3 * len(want & name) + len(want & desc)
            if score:
                scored.append((score, f"{a.id}_{t.name}", a, t))
    scored.sort(key=lambda x: -x[0])
    return {n: (a, t) for _, n, a, t in scored[:limit]}


def fit_budget(tool_map: dict, specs: list[dict], fixed_chars: int, num_ctx: int, reply_tokens: int) -> tuple[dict, list[dict]]:
    """Drop the last-offered tools until the prompt fits the context window, leaving room for the reply.

    Tokens are estimated at 3.2 characters each, which overestimates for English and JSON, so this errs on the safe side.
    """
    budget = max(0, num_ctx - reply_tokens - 256) * 3.2
    names, out = list(tool_map), list(specs)
    while out and fixed_chars + len(json.dumps(out)) > budget:
        out.pop(); names.pop()
    return {n: tool_map[n] for n in names}, out


def select_tools(registry, text: str, prior_apps: set[str], force: set[str] | frozenset = frozenset(),
                 prior_tools: set[str] | frozenset = frozenset()) -> tuple[dict, bool]:
    """Show the model only the tools that plausibly matter (apps named, or already used in this chat).

    Returns (tool_map, matched) where `matched` means the request itself names an app, so a reply
    that calls no tool is suspicious.
    """
    apps = registry.enabled()
    ui = next((x for x in apps if x.id == "ui"), None)
    building = any(n in ("ui_create_app", "ui_add_list", "ui_connect_api", "ui_add_action", "ui_set_fields") for n in prior_tools)
    if ui and not force and not building and is_navigation(text):
        return {f"ui_{t.name}": (ui, t) for t in ui.tools() if t.name in ("open_app", "open_item", "set_layout")}, True
    low = text.lower()
    # Words inside paths and file names ("notes/todo.md") are not requests about the Notes app, so keyword matching
    # ignores them; only an app's own patterns (like file names for Files) look at the raw text.
    plain = re.sub(r"\S*[/\\]\S*|\S+\.\w{1,5}\b", " ", low)
    named = [a for a in apps if a.id in force or any(re.search(rf"\b{re.escape(k)}(?:s|es)?\b", plain) for k in a.keywords)
             or any(re.search(p, low) for p in a.keyword_patterns)]
    chosen = [a for a in apps if a in named or a.id in prior_apps]
    if ui and ui in chosen and not force and ui.builds_something(text):
        chosen = [ui]          # building a panel about to-dos is not asking about to-dos: the lookup tools would just answer instead
    if not chosen:
        every = {f"{a.id}_{t.name}": (a, t) for a in apps for t in a.tools()}
        if len(json.dumps([tool_spec(a, t) for a, t in every.values()])) <= FALLBACK_ALL_CHARS:
            return every, False          # few tools in total: offering them all is cheap and safest
        return rank_tools(registry, text), False
    return {f"{a.id}_{t.name}": (a, t) for a in chosen for t in a.tools_for(text, prior_tools)}, bool(named)


TRACE_TURNS = 1       # how many of the most recent tool-using answers keep their tool trace in context
TRACE_CHARS = 500     # per replayed tool result


PRIVATE_NOTE = "[This earlier answer used private data, which isn't shared with a cloud model. Ask again if it's needed.]"


def build_history(rows: list[dict], max_chars: int, hide_apps: set | frozenset = frozenset()) -> list[dict]:
    """Recent turns, de-looped and clipped, newest-first within a character budget.

    The latest tool-using answer is replayed as a real tool call + result, so the model keeps seeing that
    answers come from tools (and can answer follow-ups from the data) instead of imitating plain-text replies.
    """
    out, used, traces = [], 0, 0
    for m in reversed(rows):
        content = collapse_repeats(m["content"]) if m["role"] == "assistant" else m["content"]
        content = clip(content, 1500 if m["role"] == "assistant" else 1200)
        tools = [t for t in (m.get("meta") or {}).get("tools", []) if t.get("ok") and t.get("name")]
        if hide_apps and m["role"] == "assistant" and any(t.get("app") in hide_apps for t in tools):
            content, tools = PRIVATE_NOTE, []   # made with a local model from private data: not for the cloud
        turn = [{"role": m["role"], "content": content}]
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
    holder.update(looped=False, truncated=False, tokens=0, usage=None, ttft_ms=None)
    t0 = time.monotonic()
    async with aclosing(provider.stream(messages, specs, options=options)) as stream:
        async for chunk in stream:
            if holder["ttft_ms"] is None and (chunk.get("content") or chunk.get("tool_calls")):
                holder["ttft_ms"] = (time.monotonic() - t0) * 1000   # perceived latency: first output after the request
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
                holder["usage"] = chunk["usage"]
            if chunk.get("truncated"):
                holder["truncated"] = True
    holder.update(content=content, calls=calls, ms=(time.monotonic() - t0) * 1000)


async def _execute(call: dict, tool_map: dict, registry, guard: RunGuard, out: dict, ctx=None,
                   learner: LessonTracker | None = None) -> AsyncIterator[dict]:
    """Run one tool call, yielding UI events. What the model should see goes in out['content']."""
    cid = uuid.uuid4().hex[:8]
    name, args = call["name"], _args(call["arguments"])
    entry = tool_map.get(name)
    if entry is None and not guard.readonly:   # a real tool we didn't offer: the router guessed wrong, so let it run
        entry = registry.tool_map().get(name)
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
    needs = tool.ask(args) if tool.ask else tool.needs_approval and not registry.is_always_allowed(app, tool)
    if needs and tool.ask and guard.approval_timeout is not None:   # unattended (scheduled): nobody to see a card right now
        job = session.JOB.get() or {}
        from ..shell.classify import covered
        if covered(str(args.get("command", "")), job.get("commands", [])):
            needs = False          # the user pre-approved this on the job (each part of a `&&` chain listed, or read-only)
        elif guard.readonly:
            msg = ("This scheduled job may not run that unattended. Commands it may run must be listed (pre-approved) on the job "
                   "in the Scheduler panel, or the job must be allowed to propose changes.")
            yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title, "args": args, "risk": tool.risk,
                   "describe": describe, "needs_approval": False}
            out.update(ok=False, content=f"Error: {msg}")
            guard.errors += 1
            yield {"type": "tool_result", "id": cid, "ok": False, "preview": out["content"], "ms": 0}
            return
    share = bool(getattr(guard, "cloud", False) and registry.is_private(app) and app.id not in guard.grants)
    if share:   # the result would go to a model running elsewhere: the user decides, every time or for this chat
        needs = True
        describe = f"Let {guard.model_label} (cloud) use your {app.name}: {describe}"
    preview = None
    if needs and tool.preview:
        try:
            preview = tool.preview(args)
            if inspect.isawaitable(preview):   # previews that need the network (e.g. fetching the mail being replied to)
                preview = await preview
        except ToolError as e:   # the change can't happen (bad path, text not found...): don't ask the user to approve it
            yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title, "args": args,
                   "risk": tool.risk, "describe": "", "needs_approval": False}
            out.update(ok=False, content=f"Error: {e}")
            guard.errors += 1
            yield {"type": "tool_result", "id": cid, "ok": False, "preview": out["content"], "ms": 0}
            return
        except Exception:
            preview = None
    job_id = (session.JOB.get() or {}).get("id") if needs and tool.ask and args.get("command") else None
    yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title, "args": args,
           "risk": tool.risk, "describe": describe, "needs_approval": needs, "preview": preview, "share": share, "job": job_id}
    out["approval"] = False
    if needs:
        out["approval"] = True
        fut = approvals.request(cid)
        yield {"type": "notify", "text": f"I need your approval: {describe}"}
        if guard.approval_timeout is not None and ctx is not None and getattr(ctx, "notifier", None):   # a scheduled run is waiting
            try:
                await ctx.notifier.send("A scheduled job needs your approval", describe[:300], kind="approval",
                                        data={"conversation_id": guard.conv_id})
            except Exception:
                pass
        try:
            approved = await asyncio.wait_for(fut, guard.approval_timeout)
        except asyncio.TimeoutError:
            approved = False  # unattended and nobody answered
        finally:
            approvals.discard(cid)
        if approved == "chat" and share:
            guard.grants.add(app.id)   # yes for the rest of this chat (with this model)
        if approved == "job" and job_id and ctx is not None and getattr(ctx, "scheduler", None):
            try:                       # yes, and from now on this job may run this exact command unattended
                st = ctx.scheduler.store
                s = st.get(job_id)
                cmds = list(dict.fromkeys([*(s["payload"].get("commands") or []), str(args["command"]).strip()]))[:20]
                st.update(job_id, payload={**s["payload"], "commands": cmds})
                ctx.scheduler.changed()
            except Exception:
                pass
        yield {"type": "tool_status", "id": cid, "status": "approved" if approved else "denied"}
        if not approved:
            out.update(ok=False, denied=True, content="The user denied this action. Don't retry it; acknowledge and ask what they'd like instead.")
            guard.remember(key, ok=False, content="The user denied it.", risk=tool.risk)
            yield {"type": "tool_result", "id": cid, "ok": False, "preview": "Denied by you", "ms": 0}
            return

    guard.calls += 1
    t0 = time.monotonic()
    if tool.name in app.untrusted_tools:
        session.mark_untrusted(app.id)   # from here on this run is not trusted to change the interface
    token = session.CALL.set((guard.conv_id, cid))
    try:
        result = await asyncio.wait_for(tool.handler(args), getattr(tool, "timeout", None) or 60)
        text, ok = _dump(result), True
    except ToolError as e:
        text, ok = f"Error: {e}", False
        if learner and e.bad_args:
            learner.on_failure(name, args, str(e))
    except asyncio.TimeoutError:
        text, ok = "Error: the app took too long to respond.", False
    except Exception as e:  # tool bugs shouldn't crash the run
        text, ok = f"Error: {type(e).__name__}: {e}", False
    finally:
        session.CALL.reset(token)
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
                    approval_timeout: float | None = None, extra_system: str = "", attachments: str = "",
                    force_apps: frozenset = frozenset(), screen: dict | None = None) -> AsyncIterator[dict]:
    cfg = ctx.config.data
    acfg = cfg["agent"]
    session.UNTRUSTED.set({})
    session.REQUEST.set(text or "")
    t0 = time.monotonic()
    provider = make_provider(ctx)
    guard = RunGuard(acfg)
    guard.approval_timeout = approval_timeout
    trace = ctx.telemetry.begin(conv_id, text, "scheduled" if (readonly or approval_timeout is not None) else "chat", acfg["model"])
    # A cloud model sees private apps' data only after the user says yes (once, or for this chat).
    guard.cloud = ctx.cloud() if hasattr(ctx, "cloud") else False
    guard.grants = ctx.cloud_grants.setdefault((conv_id, acfg.get("active_model") or acfg["model"]), set()) if guard.cloud else set()
    guard.model_label = acfg["model"]
    guard.conv_id = conv_id
    hidden = {a.id for a in registry.apps.values() if registry.is_private(a)} - guard.grants if guard.cloud else set()

    history = build_history(ctx.db.history(conv_id, limit=int(acfg["history_messages"])), int(acfg["history_chars"]), hidden)
    prior_apps = ctx.db.apps_used(conv_id)
    prior_tools = ctx.db.tools_used(conv_id)
    ctx.db.add_message(conv_id, "user", text)
    tool_map, expects_tool = select_tools(registry, text, prior_apps, force_apps, prior_tools)
    on_screen, screen_app = screen_line(registry, screen)
    # "summarize this", or a follow-up like "list contents" that names no app: it's about what is on screen.
    if screen_app and (_DEICTIC.search(text) or not expects_tool):
        force_apps = frozenset(force_apps) | {screen_app}
        tool_map, expects_tool = select_tools(registry, text, prior_apps, force_apps, prior_tools)
    else:
        on_screen = ""
    if on_screen and screen_app in hidden:   # don't put an email subject or note title in a cloud prompt
        on_screen = screen_line(registry, {k: v for k, v in screen.items() if k != "title"})[0]
    if attachments:   # the user attached what the answer needs, so a no-tool reply is legitimate (no 'call a tool' nudge)
        expects_tool = False
    if readonly:  # unattended runs may only read; nothing can be changed without a person present
        tool_map = {n: (a, t) for n, (a, t) in tool_map.items() if (not t.needs_approval or t.ask) and a.id not in hidden}
    mem = ctx.memory
    # With no app named in the request every app's tools are offered as a fallback, but app-specific facts aren't.
    facts = mem.relevant({a.id for a, _ in tool_map.values()} if (expects_tool or prior_apps) else set())
    if "memory" in hidden:   # remembered facts about the user stay local
        facts = []
    specs = [tool_spec(a, t, mem.tool_notes(n)) for n, (a, t) in tool_map.items()]
    guard.readonly = readonly
    learner = LessonTracker() if acfg and ctx.config.data["memory"]["auto_learn"] and ctx.config.data["memory"]["enabled"] else None
    now = ctx.now().strftime("%A %Y-%m-%d %H:%M")
    messages = [{"role": "system", "content": build_system_prompt(ctx, registry) + facts_block(facts) + extra_system}, *history,
                {"role": "user", "content": f"{text}{attachments}{on_screen}\n\n(Now: {now})"}]
    # Never send more than the model can take: a context overflow is a hard error from Ollama, not a slow answer.
    fixed = sum(len(m.get("content") or "") for m in messages) + 600 * len(history) // 10
    tool_map, specs = fit_budget(tool_map, specs, fixed, int(acfg.get("num_ctx") or 8192), int(acfg.get("max_tokens") or 1024))
    trace.routing(sorted({a.id for a, _ in tool_map.values()}), len(specs))

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
        trace.guard(kind)
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
            trace.model(holder["ms"], holder["usage"], holder["ttft_ms"], holder["looped"])

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
                        if content.strip():   # the discarded draft must not appear twice (live view, stored chat)
                            texts.pop()
                            yield {"type": "rewrite", "text": ""}
                        messages += [{"role": "assistant", "content": content}, {"role": "user", "content": why}]
                        continue
                finished = True
                break

            messages.append({"role": "assistant", "content": content,
                             "tool_calls": [{"function": {"name": c["name"], "arguments": _args(c["arguments"])},
                                             **({"extra": c["extra"]} if c.get("extra") else {})}   # e.g. Gemini's thought signature, sent back as-is
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
                            trace.guard(ev["kind"])
                        elif ev["type"] == "tool_call":
                            cards[ev["id"]] = {k: ev[k] for k in ("id", "name", "app", "title", "args", "risk", "describe")}
                        elif ev["type"] == "tool_status":
                            cards[ev["id"]]["status"] = ev["status"]
                        elif ev["type"] == "tool_result":
                            cards[ev["id"]].update(ok=ev["ok"], preview=ev["preview"], ms=ev["ms"])
                            trace.tool(cards[ev["id"]]["name"], ev["ms"], ev["ok"])
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
        trace.guard("provider_error")
        yield {"type": "error", "message": error}
    except asyncio.CancelledError:
        # Stopped by the user: keep what was produced so the chat history stays coherent.
        trace.finish("cancelled")
        partial = collapse_repeats("\n\n".join(texts))
        ctx.db.add_message(conv_id, "assistant", partial, {"tools": list(cards.values()), "stopped": True, "guards": guards})
        raise

    final = collapse_repeats(texts[-1] if used_tools and texts else "\n\n".join(texts))
    if not final and not error:
        final = _fallback(list(cards.values()))
        yield {"type": "rewrite", "text": final}
    perf = trace.finish("error" if error else "ok", error, len(final))
    ctx.db.add_message(conv_id, "assistant", final, {"tools": list(cards.values()), "error": error, "guards": guards, "perf": perf})
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
    yield {"type": "done", "seconds": round(seconds, 1), "tokens": tokens, "perf": perf}
