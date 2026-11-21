"""Antigravity as a chat backend. `agy acp` runs the conversation with its own agent and tools; this module starts it, shows what it does
as ordinary chat events, and turns every permission it asks for into an approval card.

What keeps it inside Diatonic's rules:
- Its settings live in a Diatonic-owned folder (`--app-data-dir`), never the user's own: everything but our MCP server asks first.
- `--sandbox` is always on and it works in a folder of its own, one per conversation. `--dangerously-skip-permissions` is never passed.
- Diatonic's apps reach it only through `mcp.py`, which applies the same approvals and guards as the built-in agent.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

from ..config import home
from . import approvals
from .acp import AcpError, AcpProcess

MODEL_LABEL = "Antigravity"
SERVER_NAME = "diatonic"   # the name `agy` knows our MCP server by; the settings below allow exactly this one
# Everything asks except Diatonic's own tools, which ask in Diatonic (the permission request would only be a second card for the same call).
SETTINGS = {"agentMode": "", "toolPermission": "request-review", "allowNonWorkspaceAccess": False,
            "permissions": {"allow": [f"mcp({SERVER_NAME}/*)"], "ask": ["command(*)", "write_file(*)", "read_url(*)", "execute_url(*)", "download_url(*)"]}}
MAX_EAGER_TOOLS = 30   # past this many, `agy` stopped using the tools directly in testing
AUTH_HELP = "Antigravity is not signed in. Run `agy acp --login` in a terminal, then send your message again."
RISK = {"read": "read", "search": "read", "fetch": "read", "think": "read", "edit": "write", "move": "write", "execute": "write", "delete": "destructive"}


@dataclass
class Session:
    conv_id: str
    proc: AcpProcess
    session_id: str
    token: str
    workdir: Path
    fresh: bool = True   # the next prompt is the first, so it carries the context note
    tools: frozenset[str] = field(default_factory=frozenset)
    used: float = field(default_factory=time.time)


class Connector:
    def __init__(self, ctx):
        self.ctx = ctx
        self.sessions: dict[str, Session] = {}

    # ---- process and session -------------------------------------------------
    def _command(self) -> list[str]:
        c = self.ctx.config.data.get("antigravity", {}).get("command") or "agy"
        return list(c) if isinstance(c, list) else [str(c)]

    def _appdata(self) -> Path:
        d = home() / "antigravity" / "appdata"
        d.mkdir(parents=True, exist_ok=True)
        auto_perm = self.ctx.config.data.get("antigravity", {}).get("permissions") == "auto"
        if auto_perm:
            settings = {
                "agentMode": "", "toolPermission": "request-review", "allowNonWorkspaceAccess": False,
                "permissions": {
                    "allow": [f"mcp({SERVER_NAME}/*)", "command(*)", "write_file(*)", "read_url(*)", "execute_url(*)", "download_url(*)"],
                    "ask": [],
                },
            }
        else:
            settings = SETTINGS
        (d / "settings.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
        return d

    def _workdir(self, conv_id: str, token: str) -> Path:
        d = home() / "antigravity" / "work" / re.sub(r"[^A-Za-z0-9_-]", "_", conv_id)
        (d / ".agents").mkdir(parents=True, exist_ok=True)
        cfg = d / ".agents" / "mcp_config.json"
        tools = self.ctx.mcp.tools() if getattr(self.ctx, "mcp", None) else {}
        if not tools:   # no app is switched on for outside agents: it gets no way back into Diatonic at all
            cfg.unlink(missing_ok=True)
            return d
        entry: dict = {"serverUrl": f"http://127.0.0.1:{self.ctx.config.data['server']['port']}/mcp", "headers": {"Authorization": f"Bearer {token}"}}
        if len(tools) <= MAX_EAGER_TOOLS:
            entry["tools"] = {name: {"eager": True} for name in tools}
        cfg.write_text(json.dumps({"mcpServers": {SERVER_NAME: entry}}, indent=2), encoding="utf-8")
        cfg.chmod(0o600)
        return d

    async def session(self, conv_id: str, model_id: str | None = None) -> Session:
        await self.reap()
        tools = self.ctx.mcp.tools() if getattr(self.ctx, "mcp", None) else {}
        tool_keys = frozenset(tools.keys())
        s = self.sessions.get(conv_id)
        if s and s.proc.alive and s.tools == tool_keys:
            s.used = time.time()
            return s
        if s:
            self.drop(conv_id)
        token = self.ctx.mcp.open(conv_id, MODEL_LABEL) if getattr(self.ctx, "mcp", None) else ""
        workdir = self._workdir(conv_id, token)
        rel = os.path.relpath(self._appdata(), Path.home() / ".gemini")   # `agy` wants it relative to its own folder
        argv = [*self._command(), "acp", "--sandbox", "--disable-telemetry", "--app-data-dir", rel]
        proc = AcpProcess(argv, str(workdir), dict(os.environ))
        mcp_servers = []
        if token and tools:
            port = self.ctx.config.data.get("server", {}).get("port", 5005)
            mcp_servers = [{"name": SERVER_NAME, "url": f"http://127.0.0.1:{port}/mcp", "headers": {"Authorization": f"Bearer {token}"}}]
        try:
            await proc.start()
            await proc.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}, "clientInfo": {"name": "diatonic", "version": "1"}}, 30)
            r = await proc.request("session/new", {"cwd": str(workdir), "mcpServers": mcp_servers}, 60)
        except Exception:
            await proc.close()
            if token:
                self.ctx.mcp.close(token)
            raise
        s = self.sessions[conv_id] = Session(conv_id, proc, r["sessionId"], token, workdir, tools=tool_keys)
        await self._pick_model(s, r, want_model=model_id)
        return s

    async def _pick_model(self, s: Session, new: dict, want_model: str | None = None) -> None:
        want = want_model or self.ctx.config.data["agent"].get("model") or ""
        ids = [m.get("modelId") for m in (new.get("models") or {}).get("availableModels", [])]
        if want in ids and want != (new.get("models") or {}).get("currentModelId"):
            try:
                await s.proc.request("session/set_model", {"sessionId": s.session_id, "modelId": want}, 15)
            except AcpError:
                pass   # the account's default model is used
        mode = self.ctx.config.data.get("antigravity", {}).get("mode") or "default"
        if mode:
            try:
                await s.proc.request("session/set_config_option", {"sessionId": s.session_id, "configId": "mode", "value": mode}, 10)
            except AcpError:
                pass
        thought = self.ctx.config.data.get("antigravity", {}).get("thought_level") or "high"
        if thought:
            try:
                await s.proc.request("session/set_config_option", {"sessionId": s.session_id, "configId": "thought_level", "value": thought}, 10)
            except AcpError:
                pass

    def drop(self, conv_id: str) -> asyncio.Task | None:
        s = self.sessions.pop(conv_id, None)
        if not s:
            return None
        if s.token:
            self.ctx.mcp.close(s.token)
        return asyncio.ensure_future(s.proc.close())

    async def reap(self) -> None:
        idle = float(self.ctx.config.data.get("antigravity", {}).get("idle_minutes", 15)) * 60
        for cid, s in list(self.sessions.items()):
            if time.time() - s.used > idle or not s.proc.alive:
                self.drop(cid)

    async def close_all(self) -> None:
        await asyncio.gather(*(t for t in (self.drop(cid) for cid in list(self.sessions)) if t))


def _history_block(ctx, conv_id: str) -> str:
    msgs = ctx.db.messages(conv_id) if hasattr(ctx, "db") else []
    if not msgs:
        return ""
    lines = ["[Prior conversation in this chat:"]
    for m in msgs[-12:]:
        role = "User" if m.get("role") == "user" else "Assistant"
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{role}: {content[:2000]}")
    lines.append("]\n\n")
    return "\n".join(lines)


CMD_APP_LAUNCH = re.compile(r"\b(open\s+-(?:a|b)\b|open\s+/(?:System/)?Applications|osascript\b|ps\s+|kill\s+|pkill\s+)", re.I)
CMD_DIATONIC_HOME = re.compile(r"(?:~|\$HOME|/Users/[^/\s]+)/\.diatonic\b|\b\.diatonic\b", re.I)


def _check_command_guardrail(text: str) -> str | None:
    if CMD_APP_LAUNCH.search(text):
        return ("Blocked by Diatonic guardrail: Do not use shell commands to open apps or inspect processes. "
                "Use Diatonic's MCP tools (ui_open_app, ui_open_item) to display apps on screen.")
    if CMD_DIATONIC_HOME.search(text):
        return ("Blocked by Diatonic guardrail: Direct shell access to ~/.diatonic is forbidden. "
                "Use Diatonic's MCP tools to interact with apps and data.")
    return None


def _context_note(ctx, s: Session) -> str:
    tools = ctx.mcp.tools() if getattr(ctx, "mcp", None) else {}
    apps = sorted({a.name for a, _t in tools.values()})
    mcp = (f"Diatonic's {', '.join(apps)} are available through the MCP server '{SERVER_NAME}'. Each call may ask the user first." if apps
           else "No Diatonic apps are currently shared with you over MCP (apps can be enabled by the user in Settings → Apps).")
    hist = _history_block(ctx, s.conv_id)
    return (
        f"[Context] You are answering inside Diatonic, a local app, as the user's assistant. It is {ctx.now().strftime('%A %Y-%m-%d %H:%M')}. "
        f"Your working folder is {s.workdir}. {mcp}\n\n"
        "[Diatonic UI & Environment Rules]\n"
        "- To navigate, view, or open apps/items on the user's screen, ALWAYS use Diatonic's MCP tools:\n"
        "  * ui_open_app(app='gmail', view='inbox'): show an app panel (e.g. gmail, gcal, notes, files, todos).\n"
        "  * ui_open_item(app='gmail', what='latest' or query): open a specific email, note, file, or calendar event.\n"
        "  * ui_set_layout(mode='zoom', app='gmail'): full-screen/zoom an app without closing other panels. To exit zoom: ui_set_layout(mode='unzoom').\n"
        "  * ui_set_layout(mode='split'): place chat and panel side-by-side.\n"
        "- NEVER create a macro to zoom or change layout. Use ui_set_layout.\n"
        "- To create or edit custom UI mini-apps and panels:\n"
        "  1) Call ui_read_skill('app-creator') to see recipes and rules.\n"
        "  2) Call ui_create_file_app(id='...', name='...').\n"
        "  3) Call ui_write_app_file(app_id='...', path='index.js', content='...').\n"
        "- NEVER use shell commands ('open -a', 'osascript', 'ps', 'kill') to launch desktop apps or inspect processes.\n"
        "- NEVER inspect ~/.diatonic directly via shell. All user data is accessed through Diatonic's MCP tools.\n\n"
        f"{hist}"
    )


def _clip(v, n: int = 1500):
    return v if not isinstance(v, str) or len(v) <= n else v[:n] + "…"


class Turn:
    """What one chat turn has produced so far: the text, the tool cards shown, and calls seen but not shown yet."""

    def __init__(self, token: str):
        self.token = token
        self.segs: list[str] = []      # the agent's text, one entry between tool calls
        self.thoughts: list[str] = []  # the agent's reasoning/thoughts
        self.new_seg = True
        self.new_thought = True
        self.cards: dict[str, dict] = {}
        self.pending: dict[str, dict] = {}

    def thought(self, t: str) -> None:
        if self.new_thought or not self.thoughts:
            self.thoughts.append("")
            self.new_thought = False
        self.thoughts[-1] += t

    def clean(self, v):
        """The MCP token is the way back into Diatonic: it never goes on a card, where it would be read, copied or saved in the chat."""
        if self.token and isinstance(v, str):
            return v.replace(self.token, "…")
        return v

    def card(self, call: dict) -> dict:
        kind = call.get("kind") or "other"
        args = call.get("rawInput") or call.get("args") or {}
        args = {k: _clip(self.clean(v)) for k, v in args.items()} if isinstance(args, dict) else {"input": _clip(self.clean(str(args)))}
        title = str(self.clean(call.get("title") or call.get("name") or "Tool"))
        return {"id": f"agy-{call['toolCallId']}", "name": f"antigravity_{call.get('name') or kind}", "app": "antigravity", "title": title[:120],
                "args": args, "risk": RISK.get(kind, "write"), "describe": title[:300]}

    def show(self, card: dict, needs_approval: bool) -> dict:
        self.cards[card["id"]] = card
        self.new_seg = True   # what it says after a tool call is a new segment
        return {"type": "tool_call", **card, "needs_approval": needs_approval, "preview": None, "share": False, "job": None}

    def text(self, t: str) -> None:
        self.new_thought = True
        if self.new_seg or not self.segs:
            self.segs.append("")
            self.new_seg = False
        self.segs[-1] += t


async def run(ctx, registry, conv_id: str, text: str, *, attachments: str = "", model: dict | None = None, **_) -> AsyncIterator[dict]:
    """One chat turn through Antigravity, yielding the same events the built-in agent does."""
    t0 = time.monotonic()
    lbl = (model.get("label") or MODEL_LABEL) if model else MODEL_LABEL
    trace = ctx.telemetry.begin(conv_id, text, "chat", lbl)
    ctx.db.add_message(conv_id, "user", text)
    t = Turn("")
    guards: list[str] = []
    error: str | None = None
    s: Session | None = None
    inp = out = 0
    try:
        model_name = model.get("model") if model and model.get("provider") == "antigravity" and model.get("model") != "default" else None
        s = await ctx.antigravity.session(conv_id, model_id=model_name)
        t.token = s.token
        prompt = (_context_note(ctx, s) if s.fresh else "") + text + attachments
        s.fresh = False
        turn = asyncio.create_task(s.proc.request("session/prompt", {"sessionId": s.session_id, "prompt": [{"type": "text", "text": prompt}]}))
        try:
            while True:
                getter = asyncio.create_task(s.proc.incoming.get())
                await asyncio.wait({getter, turn}, return_when=asyncio.FIRST_COMPLETED)
                if not getter.done():
                    getter.cancel()
                    if s.proc.incoming.empty():
                        break
                    continue
                msg = getter.result()
                if msg is None:
                    break
                async for ev in _handle(s, t, msg, ctx):
                    yield ev
            while not s.proc.incoming.empty():   # what it said just before it finished
                msg = s.proc.incoming.get_nowait()
                if msg:
                    async for ev in _handle(s, t, msg, ctx):
                        yield ev
            turn_res = await turn
            stop = turn_res.get("stopReason")
            usage = turn_res.get("usage") or {}
            inp = usage.get("inputTokens", 0)
            out = usage.get("outputTokens", 0)
            if inp or out:
                trace.model(time.monotonic() - t0, {"prompt": inp, "completion": out, "prefill_ms": 0, "gen_ms": 0, "load_ms": 0}, int((time.monotonic() - t0) * 1000))
        except asyncio.CancelledError:
            s.proc.notify("session/cancel", {"sessionId": s.session_id})
            turn.cancel()
            raise
        for cid in [c for c, v in t.cards.items() if "ok" not in v]:   # a call it never finished
            t.cards[cid].update(ok=False, preview="Stopped", ms=0)
            yield {"type": "tool_result", "id": cid, "ok": False, "preview": "Stopped", "ms": 0}
        if stop == "cancelled":
            yield {"type": "cancelled"}
        elif stop in ("refusal", "max_tokens", "max_turn_requests"):
            guards.append(f"Antigravity stopped early ({stop.replace('_', ' ')}).")
            yield {"type": "guard", "kind": "stopped", "message": guards[-1]}
    except AcpError as e:
        error = AUTH_HELP if e.code == -32000 else str(e)
        if s:
            ctx.antigravity.drop(conv_id)   # the next message starts a fresh process
        yield {"type": "error", "message": error}
    except asyncio.CancelledError:
        trace.finish("cancelled")
        ctx.db.add_message(conv_id, "assistant", "\n\n".join(t.segs), {"tools": list(t.cards.values()), "stopped": True, "guards": guards})
        raise

    final = (t.segs[-1] if t.cards and t.segs else "\n\n".join(t.segs)).strip()
    perf = trace.finish("error" if error else "ok", error, len(final))
    meta = {"tools": list(t.cards.values()), "error": error, "guards": guards, "perf": perf}
    if t.thoughts:
        meta["thoughts"] = t.thoughts
    ctx.db.add_message(conv_id, "assistant", final, meta)
    yield {"type": "message", "text": final}
    yield {"type": "done", "seconds": round(time.monotonic() - t0, 1), "tokens": inp + out, "perf": perf}


async def _handle(s: Session, t: Turn, msg: dict, ctx=None) -> AsyncIterator[dict]:
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "session/update":
        u = params.get("update") or {}
        kind = u.get("sessionUpdate")
        if kind == "agent_message_chunk":
            text = (u.get("content") or {}).get("text") or ""
            if text:
                t.text(text)
                yield {"type": "token", "text": text}
        elif kind == "agent_thought_chunk":
            thought = (u.get("content") or {}).get("text") or ""
            if thought:
                t.thought(thought)
                yield {"type": "thought", "text": thought}
        elif kind == "session_info_update":
            title = u.get("title")
            if title and ctx and hasattr(ctx, "db"):
                ctx.db.rename(s.conv_id, str(title).strip())
                yield {"type": "title", "title": str(title).strip()}
        elif kind in ("tool_call", "tool_call_update"):
            async for ev in _tool_update(t, u):
                yield ev
    elif method == "session/request_permission":
        async for ev in _permission(s, t, msg, ctx):
            yield ev
    elif "id" in msg:   # a request we did not offer to serve (files, terminals, forms)
        s.proc.respond(msg["id"], error=(-32601, f"Diatonic does not handle {method}."))


async def _tool_update(t: Turn, u: dict) -> AsyncIterator[dict]:
    tid = f"agy-{u['toolCallId']}"
    t.pending[tid] = {**t.pending.get(tid, {}), **{k: v for k, v in u.items() if v is not None}}
    status = u.get("status")
    # `agy` announces a call as in_progress and only then asks permission for it, so a card shown now could not carry the approval:
    # it is shown when the permission request arrives, or when the call has finished without asking.
    if tid not in t.cards and status in ("completed", "failed"):
        yield t.show(t.card(t.pending[tid]), False)
    if tid in t.cards and status in ("completed", "failed"):
        raw = t.pending[tid].get("rawOutput") or t.pending[tid].get("content") or ""
        preview = _clip(t.clean(raw if isinstance(raw, str) else json.dumps(raw)[:1500]), 1500)
        t.cards[tid].update(ok=status == "completed", preview=preview, ms=0)
        yield {"type": "tool_result", "id": tid, "ok": status == "completed", "preview": preview, "ms": 0}


async def _permission(s: Session, t: Turn, msg: dict, ctx=None) -> AsyncIterator[dict]:
    params = msg.get("params") or {}
    call = params.get("toolCall") or {}
    tid = f"agy-{call.get('toolCallId')}"
    t.pending[tid] = {**t.pending.get(tid, {}), **call}
    card = t.cards.get(tid) or t.card(t.pending[tid])

    # Guardrail check: block disallowed system commands or direct ~/.diatonic access
    cmd_text = f"{card.get('describe', '')} {json.dumps(card.get('args', {}))}"
    blocked_reason = _check_command_guardrail(cmd_text)
    if blocked_reason:
        opt = next((o["optionId"] for o in params.get("options") or [] if o.get("kind") == "reject_once"), None)
        s.proc.respond(msg["id"], {"outcome": {"outcome": "selected", "optionId": opt} if opt else {"outcome": "cancelled"}})
        if tid not in t.cards:
            yield t.show(card, False)
        card.update(ok=False, preview=blocked_reason, ms=0)
        yield {"type": "tool_status", "id": tid, "status": "denied"}
        yield {"type": "tool_result", "id": tid, "ok": False, "preview": blocked_reason, "ms": 0}
        return

    # Auto permission mode: safe actions are automatically approved without stopping
    auto_perm = False
    if ctx and hasattr(ctx, "config"):
        auto_perm = ctx.config.data.get("antigravity", {}).get("permissions") == "auto"

    if auto_perm:
        opt = next((o["optionId"] for o in params.get("options") or [] if o.get("kind") in ("allow_once", "allow_always")), None)
        if opt:
            s.proc.respond(msg["id"], {"outcome": {"outcome": "selected", "optionId": opt}})
            if tid not in t.cards:
                yield t.show(card, False)
            yield {"type": "tool_status", "id": tid, "status": "approved"}
            return

    fut = approvals.request(tid)   # before the card is shown, so an answer that comes straight back finds it
    try:
        if tid not in t.cards:
            yield t.show(card, True)
        yield {"type": "notify", "text": f"I need your approval: {card['describe']}"}
        approved = await fut
    except asyncio.CancelledError:
        s.proc.respond(msg["id"], {"outcome": {"outcome": "cancelled"}})
        raise
    finally:
        approvals.discard(tid)
    want = "allow_once" if approved else "reject_once"
    opt = next((o["optionId"] for o in params.get("options") or [] if o.get("kind") == want), None)
    s.proc.respond(msg["id"], {"outcome": {"outcome": "selected", "optionId": opt} if opt else {"outcome": "cancelled"}})
    yield {"type": "tool_status", "id": tid, "status": "approved" if approved else "denied"}
    if not approved:
        card.update(ok=False, preview="Denied by you", ms=0)
        yield {"type": "tool_result", "id": tid, "ok": False, "preview": "Denied by you", "ms": 0}
