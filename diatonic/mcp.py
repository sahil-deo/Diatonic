"""MCP server: lets an outside agent (Antigravity over `agy acp`) use Diatonic's tools through the same checks as the chat.

Every call goes through the agent loop's `_execute`, so approval cards, the cloud-sharing question for private apps and the
untrusted-content rule apply exactly as they do for the built-in agent. A session belongs to one conversation and is found by its
bearer token; the token is the only way in, so it is random, kept in memory and written only into that conversation's own config.
"""
from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .agent import session
from .agent.guards import RunGuard
from .agent.loop import _execute
from .agent.session import RUN_CLIENT

PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
# The terminal is not for an outside agent: it has its own command runner.
EXCLUDED_APPS = ("shell",)
CALL_TIMEOUT = 900.0   # seconds a call may wait, mostly for the user to answer an approval card
MODEL_LABEL = "Antigravity"


@dataclass
class McpSession:
    conv_id: str
    title: str
    untrusted: dict = field(default_factory=dict)   # sources this session has read (mail), kept across calls like a run's
    created: float = field(default_factory=time.time)


class McpHub:
    def __init__(self, ctx, registry, runs):
        self.ctx, self.registry, self.runs = ctx, registry, runs
        self.sessions: dict[str, McpSession] = {}

    def open(self, conv_id: str, title: str = "Antigravity") -> str:
        token = secrets.token_urlsafe(32)
        self.sessions[token] = McpSession(conv_id, title)
        self.runs.attach(conv_id, title)
        return token

    def close(self, token: str) -> None:
        s = self.sessions.pop(token, None)
        if s and not any(o.conv_id == s.conv_id for o in self.sessions.values()):
            self.runs.detach(s.conv_id)

    def known(self, token: str) -> bool:
        return token in self.sessions

    def tools(self) -> dict:
        """Apps switched on for outside agents (config `mcp.apps`), plus the Interface app (`ui`) for navigation and app building, never the terminal."""
        allowed = (set(self.ctx.config.data.get("mcp", {}).get("apps") or []) | {"ui"}) - set(EXCLUDED_APPS)
        return {name: (app, tool) for name, (app, tool) in self.registry.tool_map().items() if app.id in allowed}

    @staticmethod
    def _clean_schema(schema: dict) -> dict:
        """Ensure all array properties have an items definition, required by strict validators (e.g. Gemini API)."""
        if not isinstance(schema, dict):
            return schema
        out = {}
        for k, v in schema.items():
            if isinstance(v, dict):
                cleaned = McpHub._clean_schema(v)
                if cleaned.get("type") == "array" and "items" not in cleaned:
                    cleaned["items"] = {"type": "string"}
                out[k] = cleaned
            elif isinstance(v, list):
                out[k] = [McpHub._clean_schema(x) if isinstance(x, dict) else x for x in v]
            else:
                out[k] = v
        if out.get("type") == "array" and "items" not in out:
            out["items"] = {"type": "string"}
        return out

    def _listing(self) -> list[dict]:
        return [{"name": n, "description": t.description, "inputSchema": self._clean_schema(t.parameters)} for n, (_a, t) in self.tools().items()]

    async def handle(self, token: str, msg) -> dict | None:
        """One JSON-RPC message in, the reply out (None for a notification)."""
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "Invalid request.")
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if mid is None:
            return None   # notifications/initialized, notifications/cancelled: nothing to answer
        if method == "server/discover":
            return _ok(mid, {})
        if method == "initialize":
            asked = params.get("protocolVersion")
            return _ok(mid, {"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0], "capabilities": {"tools": {"listChanged": False}},
                             "serverInfo": {"name": "diatonic", "version": "1"}})
        if method == "ping":
            return _ok(mid, {})
        if method == "tools/list":
            return _ok(mid, {"tools": self._listing()})
        if method == "tools/call":
            return _ok(mid, await self._call(self.sessions[token], str(params.get("name") or ""), params.get("arguments") or {}))
        return _error(mid, -32601, f"Method not found: {method}")

    async def _call(self, s: McpSession, name: str, args: dict) -> dict:
        tool_map = self.tools()
        if name not in tool_map:
            return _text(f"Error: no tool named '{name}'. Call tools/list to see the tools.", True)
        guard = RunGuard(self.ctx.config.data["agent"])   # fresh per call: a long session must not be told a stale read is "already fetched"
        auto_perm = self.ctx.config.data.get("antigravity", {}).get("permissions") == "auto"
        guard.cloud = not auto_perm   # auto: treated with local-model trust; strict: asks before private apps
        guard.model_label = MODEL_LABEL
        guard.conv_id = s.conv_id
        guard.grants = self.ctx.cloud_grants.setdefault((s.conv_id, MODEL_LABEL), set())
        out: dict = {}
        session.UNTRUSTED.set(s.untrusted)
        run_obj = getattr(self.runs, "runs", {}).get(s.conv_id) if hasattr(self.runs, "runs") else None
        client = getattr(run_obj, "client", None) or getattr(self.runs, "focus", None)
        if client:
            RUN_CLIENT.set(client)

        async def run() -> None:
            async for ev in _execute({"name": name, "arguments": args}, tool_map, self.registry, guard, out, self.ctx):
                self.runs.publish(s.conv_id, ev)

        try:
            await asyncio.wait_for(run(), CALL_TIMEOUT)
        except asyncio.TimeoutError:
            return _text("Error: the user did not answer in time.", True)
        return _text(out.get("content", ""), not out.get("ok", False))


def _ok(mid, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _text(text: str, is_error: bool) -> dict:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def endpoint(hub: McpHub):
    """The HTTP route: POST /mcp with `Authorization: Bearer <token>`; replies are plain JSON (no streaming)."""
    async def mcp(request: Request) -> Response:
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if not hub.known(token):
            return JSONResponse({"error": "Not allowed. This address is only for the agent a chat started."}, 401)
        if request.method != "POST":
            return Response(status_code=405, headers={"Allow": "POST"})
        try:
            msg = await request.json()
        except ValueError:
            return JSONResponse(_error(None, -32700, "Parse error."), 400)
        out = await hub.handle(token, msg)
        return Response(status_code=202) if out is None else JSONResponse(out)
    return mcp
