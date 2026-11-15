"""The shell: the agent's `run` tool (classified, approved when needed, logged) and the Terminal panel's real sessions.

Read-only commands run without asking; anything else shows an approval card with the exact command, the folder and
warnings; a few things never run (sudo, the keychain, Diatonic's own data). Everything the agent runs is logged.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import time

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from ...agent import session
from ...shell import runner
from ...shell.classify import classify
from ...shell.terminal import Terminals
from ..base import WRITE, App, Tool, ToolError


class ShellApp(App):
    id = "shell"
    name = "Terminal"
    icon = "command"
    description = "A real terminal in the app, and shell commands the agent can run (read-only ones freely, others with your approval)."
    panel = True
    private = True                 # output can contain anything on disk: a cloud model must ask first
    untrusted_tools = ["run"]       # command output can contain text written by others (logs, downloads)
    keywords = ["shell", "terminal", "command", "commands", "run", "git", "npm", "pip", "brew", "install", "installed", "process", "processes",
                "space", "disk", "size", "largest", "how big", "directory", "script", "python", "node", "uv", "docker", "port", "ollama",
                "build", "tests", "version", "versions", "running", "cpu", "memory usage", "uptime"]

    def __init__(self, ctx):
        super().__init__(ctx)
        self.terms = Terminals()

    @property
    def cfg(self) -> dict:
        return self.ctx.config.data["shell"]

    # ---- the agent's tool ------------------------------------------------
    def _cwd(self, a: dict) -> str:
        want = str(a.get("cwd") or "").strip()
        if not want:
            roots = (self.ctx.app_cfg("files")["settings"].get("roots") or []) if self.ctx.registry and "files" in self.ctx.registry.apps else []
            want = roots[0] if roots else "~"
        path = os.path.realpath(os.path.expanduser(want))
        if not os.path.isdir(path):
            raise ToolError(f"There is no folder {want}.", bad_args=True)
        if path.startswith(os.path.realpath(os.path.expanduser("~/.diatonic"))):
            raise ToolError("Diatonic's own data folder is off limits to the agent.")
        return path

    def _precheck(self, a: dict) -> str | None:
        if not self.cfg.get("agent"):
            return ("Shell access for the agent is switched off, so nothing was run. Tell the user they can turn it on in "
                    "Settings → Automation → \"Let the agent run shell commands\" (read-only commands then run at once, others ask first).")
        c = classify(a.get("command", ""))
        if c["kind"] == "refuse":
            self._log(a, "refused", output=c["reason"])
            return c["reason"]
        try:
            self._cwd(a)
        except ToolError as e:
            return str(e)
        return None

    def _ask(self, a: dict) -> bool:
        return classify(a.get("command", ""))["kind"] != "read"

    def _describe(self, a: dict) -> str:
        try:
            where = self._cwd(a).replace(os.path.expanduser("~"), "~", 1)
        except ToolError:
            where = a.get("cwd") or "?"
        return f"Run in {where}: {a.get('command', '')}"

    def _preview(self, a: dict) -> str | None:
        c = classify(a.get("command", ""))
        lines = [f"$ {a.get('command', '')}"]
        if c["warnings"]:
            lines += [f"! {w}" for w in c["warnings"]]
        if session.untrusted_sources():
            lines.append(f"! this run has read content written by others ({', '.join(session.untrusted_sources())}): check the command is what you asked for")
        t = self._timeout(a)
        if t != self.cfg.get("timeout", 60):
            lines.append(f"! may run for up to {int(t)} seconds")
        return "\n".join(lines)

    def _timeout(self, a: dict) -> float:
        try:
            t = float(a.get("timeout") or self.cfg.get("timeout", 60))
        except (TypeError, ValueError):
            t = self.cfg.get("timeout", 60)
        return max(5.0, min(t, float(self.cfg.get("max_timeout", 600))))

    def _log(self, a: dict, decision: str, **kw) -> None:
        call = session.CALL.get()
        try:
            self.ctx.db.log_shell(source="scheduled" if session.JOB.get() is not None else "chat", conv_id=call[0] if call else None,
                                  cwd=a.get("cwd"), command=str(a.get("command", ""))[:2000], decision=decision, **kw)
        except Exception:
            pass

    async def run(self, a: dict):
        why = self._precheck(a)
        if why:
            raise ToolError(why)
        cwd, cmd = self._cwd(a), str(a["command"]).strip()
        kind = classify(cmd)["kind"]
        job = session.JOB.get()
        decision = "read-only" if kind == "read" else "pre-approved (job)" if job and cmd in job.get("commands", []) else "approved"
        call = session.CALL.get()

        def stream(text: str) -> None:   # live output into the tool card of the chat that asked
            if call:
                self.ctx.broadcast({"type": "tool_output", "conversation_id": call[0], "id": call[1], "text": text})
        r = await runner.run(cmd, cwd, self._timeout(a), stream)
        self._log({**a, "cwd": cwd}, decision, exit_code=r["exit_code"], seconds=r["seconds"], output=r["full"][-20000:])
        self.ctx.broadcast({"type": "app_changed", "app": "shell"})
        res = {"exit_code": r["exit_code"], "output": r["output"] or "(no output)", "truncated": r["truncated"], "timed_out": r["timed_out"],
               "folder": cwd.replace(os.path.expanduser("~"), "~", 1)}
        if r["timed_out"]:
            res["note"] = f"Stopped after {int(self._timeout(a))} s. For long commands pass a larger timeout."
        return {k: v for k, v in res.items() if v is not None}

    def tools(self) -> list[Tool]:
        # Offered even when switched off: then every call is stopped by the precheck with how to turn it on,
        # instead of the model claiming it has no way to run commands.
        return [Tool("run", "Run a shell command (zsh) on the user's Mac and get its output. Read-only commands (ls, cat, grep, git status/log/diff, "
                     "du, df, ps, `cd X && ls`) run at once; anything else is shown to the user to approve first. No interactive programs, no sudo. "
                     "Work inside cwd with relative paths, e.g. sizes: `du -sh * | sort -hr | head`. To edit a file prefer the Files tools. "
                     "cwd defaults to the user's main project folder.",
                     {"properties": {"command": {"type": "string"}, "cwd": {"type": "string", "description": "Folder to run in, e.g. ~/Developer/Diatonic"},
                                     "timeout": {"type": "integer", "description": "Seconds, default 60, at most 600 (for installs and builds)"}},
                      "required": ["command"]},
                     self.run, WRITE, title="Run command", describe=self._describe, precheck=self._precheck, preview=self._preview,
                     ask=self._ask, timeout=float(self.cfg.get("max_timeout", 600)) + 15)]

    async def snapshot(self, n: int) -> dict:
        rows = self.ctx.db.shell_log(n)
        return {"items": [{"text": r["command"][:120], "sub": f"{r['decision']} · exit {r['exit_code']}" if r["exit_code"] is not None else r["decision"],
                           "tone": "warn" if (r["exit_code"] or 0) != 0 or r["decision"] == "refused" else None} for r in rows],
                "empty": "The agent hasn't run any commands yet."}

    # ---- the Terminal panel ----------------------------------------------
    @staticmethod
    def _guard(request: Request) -> None:
        # A real terminal: writes must come from Diatonic's own page. A cross-site page can't send this header without a
        # CORS preflight the server never answers, on top of the Host/Origin checks every request already gets.
        if request.headers.get("x-diatonic") != "1":
            raise PermissionError

    async def r_list(self, request: Request):
        return JSONResponse([s.info() for s in self.terms.sessions.values()])

    async def r_create(self, request: Request):
        try:
            self._guard(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, 403)
        b = await request.json()
        cwd = os.path.expanduser(str(b.get("cwd") or "~"))
        s = self.terms.create(cwd, int(b.get("cols") or 100), int(b.get("rows") or 30))
        return JSONResponse(s.info())

    async def r_input(self, request: Request):
        try:
            self._guard(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, 403)
        b = await request.json()
        return JSONResponse({"ok": self.terms.write(request.path_params["sid"], str(b.get("data", "")))})

    async def r_resize(self, request: Request):
        try:
            self._guard(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, 403)
        b = await request.json()
        self.terms.resize(request.path_params["sid"], int(b.get("cols") or 80), int(b.get("rows") or 24))
        return JSONResponse({"ok": True})

    async def r_close(self, request: Request):
        try:
            self._guard(request)
        except PermissionError:
            return JSONResponse({"error": "forbidden"}, 403)
        self.terms.close(request.path_params["sid"])
        return JSONResponse({"ok": True})

    async def r_stream(self, request: Request):
        s = self.terms.sessions.get(request.path_params["sid"])
        if not s:
            return JSONResponse({"error": "No such terminal."}, 404)
        q: asyncio.Queue = asyncio.Queue()
        s.subs.add(q)

        async def gen():
            try:
                if s.buf:   # what happened while the panel was closed
                    yield f"data: {json.dumps({'d': base64.b64encode(bytes(s.buf)).decode()})}\n\n"
                if s.exited is not None:
                    yield f"data: {json.dumps({'exit': s.exited})}\n\n"
                    return
                while True:
                    try:
                        chunk = await asyncio.wait_for(q.get(), 20)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if chunk is None:
                        yield f"data: {json.dumps({'exit': s.exited if s.exited is not None else 0})}\n\n"
                        return
                    while not q.empty():   # coalesce bursts (a fast `cat` of a big file)
                        nxt = q.get_nowait()
                        if nxt is None:
                            q.put_nowait(None); break
                        chunk += nxt
                    yield f"data: {json.dumps({'d': base64.b64encode(chunk).decode()})}\n\n"
            finally:
                s.subs.discard(q)
        return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def r_log(self, request: Request):
        return JSONResponse(self.ctx.db.shell_log(int(request.query_params.get("limit") or 100)))

    def routes(self):
        return [Route("/term", self.r_list), Route("/term", self.r_create, methods=["POST"]),
                Route("/term/{sid}/stream", self.r_stream), Route("/term/{sid}/input", self.r_input, methods=["POST"]),
                Route("/term/{sid}/resize", self.r_resize, methods=["POST"]), Route("/term/{sid}", self.r_close, methods=["DELETE"]),
                Route("/log", self.r_log)]


def create(ctx) -> App:
    return ShellApp(ctx)
