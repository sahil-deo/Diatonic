"""The agent loop: model -> tool calls -> approvals -> results -> model, streamed as events."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import AsyncIterator

import jsonschema

from ..apps.base import ToolError
from ..voice import summarizer
from . import approvals
from .providers import ProviderError, make_provider

MAX_RESULT_CHARS = 6000

BASE_PROMPT = """You are Diatonic, a personal assistant that operates the user's apps through tools.

Rules:
- When the user asks you to look something up or change something, call the right tool. Do not guess data you could fetch.
- Never say an action is done unless its tool call succeeded. If a tool fails, say so plainly and what you tried.
- Ask one short question if a required detail is missing (e.g. which repo, what time). Otherwise act.
- Actions that change things need the user's approval; the system handles that, so just call the tool.
- Be concise. Lead with the result. Use short lists for multiple items. No filler.
- Dates and times: resolve relative expressions (today, tomorrow, next Friday) from the current time below, and pass ISO 8601 to tools."""


def build_system_prompt(ctx, registry) -> str:
    now = ctx.now()
    apps = ", ".join(a.name for a in registry.enabled()) or "none"
    extra = ctx.config.data["agent"].get("system_prompt", "").strip()
    return (
        f"{BASE_PROMPT}\n\nCurrent time: {now.strftime('%A, %Y-%m-%d %H:%M')} ({ctx.tz_name}).\n"
        f"Connected apps: {apps}." + (f"\n\nUser preferences:\n{extra}" if extra else "")
    )


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + f"… [{len(s) - n} more characters truncated]"


def _args(raw) -> dict:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


async def _execute(call: dict, tool_map: dict, registry, out: dict) -> AsyncIterator[dict]:
    """Run one tool call, yielding UI events. The text for the model goes in out['content']."""
    cid = uuid.uuid4().hex[:8]
    name, args = call["name"], _args(call["arguments"])
    entry = tool_map.get(name)
    if entry is None:
        yield {"type": "tool_call", "id": cid, "name": name, "app": "", "title": name, "args": args,
               "risk": "read", "describe": "", "needs_approval": False}
        out.update(ok=False, content=f"Error: no tool named '{name}'. Use only the provided tools.")
        yield {"type": "tool_result", "id": cid, "ok": False, "preview": out["content"], "ms": 0}
        return
    app, tool = entry
    try:
        jsonschema.validate(args, tool.parameters)
    except jsonschema.ValidationError as e:
        yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title,
               "args": args, "risk": tool.risk, "describe": "", "needs_approval": False}
        out.update(ok=False, content=f"Error: invalid arguments for {name}: {e.message}. Fix them and retry.")
        yield {"type": "tool_result", "id": cid, "ok": False, "preview": out["content"], "ms": 0}
        return

    describe = tool.describe(args) if tool.describe else f"{tool.title}"
    needs = tool.needs_approval and not registry.is_always_allowed(app, tool)
    yield {"type": "tool_call", "id": cid, "name": name, "app": app.id, "title": tool.title, "args": args,
           "risk": tool.risk, "describe": describe, "needs_approval": needs}
    out["approval"] = False
    if needs:
        out["approval"] = True
        fut = approvals.request(cid)
        yield {"type": "notify", "text": f"I need your approval: {describe}"}
        try:
            approved = await fut
        finally:
            approvals.discard(cid)
        yield {"type": "tool_status", "id": cid, "status": "approved" if approved else "denied"}
        if not approved:
            out.update(ok=False, denied=True, content="The user denied this action. Do not retry it; acknowledge and ask what they'd like instead.")
            yield {"type": "tool_result", "id": cid, "ok": False, "preview": "Denied by you", "ms": 0}
            return
    t0 = time.monotonic()
    try:
        result = await tool.handler(args)
        text = result if isinstance(result, str) else json.dumps(result, default=str, ensure_ascii=False)
        ok = True
    except ToolError as e:
        text, ok = f"Error: {e}", False
    except Exception as e:  # tool bugs shouldn't crash the run
        text, ok = f"Error: {type(e).__name__}: {e}", False
    ms = int((time.monotonic() - t0) * 1000)
    out.update(ok=ok, content=_clip(text, MAX_RESULT_CHARS))
    yield {"type": "tool_result", "id": cid, "ok": ok, "preview": _clip(text, 1500), "ms": ms}


async def run_agent(ctx, registry, conv_id: str, text: str, *, speak: bool = False) -> AsyncIterator[dict]:
    cfg = ctx.config.data
    t0 = time.monotonic()
    provider = make_provider(ctx)
    history = ctx.db.history(conv_id, limit=20)
    ctx.db.add_message(conv_id, "user", text)
    messages = [{"role": "system", "content": build_system_prompt(ctx, registry)}, *history,
                {"role": "user", "content": text}]
    tool_map = registry.tool_map()
    specs = [{"type": "function", "function": {"name": n, "description": f"[{a.name}] {t.description}",
                                               "parameters": t.parameters}}
             for n, (a, t) in tool_map.items()]

    cards: dict[str, dict] = {}
    texts: list[str] = []
    actions: list[str] = []
    used_tools = had_approval = had_error = False
    error: str | None = None

    try:
        for _step in range(int(cfg["agent"]["max_steps"])):
            content, calls = "", []
            async for chunk in provider.stream(messages, specs):
                if chunk.get("content"):
                    content += chunk["content"]
                    yield {"type": "token", "text": chunk["content"]}
                if chunk.get("tool_calls"):
                    calls.extend(chunk["tool_calls"])
            if content.strip():
                texts.append(content.strip())
            if not calls:
                break
            messages.append({"role": "assistant", "content": content,
                             "tool_calls": [{"function": {"name": c["name"], "arguments": _args(c["arguments"])}}
                                            for c in calls]})
            used_tools = True
            for call in calls:
                out: dict = {}
                async for ev in _execute(call, tool_map, registry, out):
                    if ev["type"] == "tool_call":
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
        else:
            texts.append("I hit my step limit before finishing. Ask me to continue if needed.")
            yield {"type": "token", "text": texts[-1]}
    except ProviderError as e:
        error, had_error = str(e), True
        yield {"type": "error", "message": error}
    except asyncio.CancelledError:
        # Stopped by the user: keep what was produced so the chat history stays coherent.
        partial = "\n\n".join(texts)
        ctx.db.add_message(conv_id, "assistant", partial, {"tools": list(cards.values()), "stopped": True})
        raise

    final = "\n\n".join(texts)
    ctx.db.add_message(conv_id, "assistant", final, {"tools": list(cards.values()), "error": error})
    yield {"type": "message", "text": final}

    seconds = time.monotonic() - t0
    rules = cfg["voice"]["speak_when"]
    if speak and (final or error):
        if error:
            yield {"type": "summary", "text": "Something went wrong. " + (summarizer.clean_for_speech(error, 14) or "")}
        elif summarizer.should_speak(rules, used_tools=used_tools, had_approval=had_approval,
                                     had_error=had_error, seconds=seconds):
            try:
                s = await summarizer.summarize(provider, cfg["voice"]["summary_model"], text, actions, final)
            except ProviderError:
                s = None
            if s:
                yield {"type": "summary", "text": s}
    yield {"type": "done", "seconds": round(seconds, 1)}
