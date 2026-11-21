"""A small Agent Client Protocol client: JSON-RPC 2.0, one JSON message per line, over a child process's stdin and stdout.

It only moves messages. What to do with the agent's notifications and requests (tool cards, approvals) is the connector's job
(`antigravity.py`), which reads them from `incoming`.
"""
from __future__ import annotations

import asyncio
import json
from collections import deque


class AcpError(Exception):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class AcpProcess:
    def __init__(self, argv: list[str], cwd: str, env: dict | None = None):
        self.argv, self.cwd, self.env = argv, cwd, env
        self.proc: asyncio.subprocess.Process | None = None
        self.incoming: asyncio.Queue = asyncio.Queue()   # the agent's notifications and requests; None once it has stopped
        self._pending: dict[int, asyncio.Future] = {}
        self._next = 0
        self._tasks: list[asyncio.Task] = []
        self.stderr_tail: deque[str] = deque(maxlen=20)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    async def start(self) -> None:
        try:
            self.proc = await asyncio.create_subprocess_exec(*self.argv, cwd=self.cwd, env=self.env, limit=16 * 1024 * 1024,
                                                             stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        except FileNotFoundError:
            raise AcpError(f"Can't find '{self.argv[0]}'. Install the Antigravity command line tool, or set its path in Settings.")
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._drain_stderr())]

    async def _read(self) -> None:
        try:
            async for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "method" in msg:
                    self.incoming.put_nowait(msg)
                elif (fut := self._pending.pop(msg.get("id"), None)) and not fut.done():
                    fut.set_result(msg)
        finally:
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(AcpError("Antigravity stopped. " + " ".join(self.stderr_tail)[-300:]))
            self._pending.clear()
            self.incoming.put_nowait(None)

    async def _drain_stderr(self) -> None:
        async for line in self.proc.stderr:
            self.stderr_tail.append(line.decode(errors="replace").strip())

    def _send(self, obj: dict) -> None:
        if not self.alive:
            raise AcpError("Antigravity is not running.")
        self.proc.stdin.write((json.dumps(obj) + "\n").encode())

    async def request(self, method: str, params: dict, timeout: float | None = None) -> dict:
        self._next += 1
        mid = self._next
        fut = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params})
        try:
            msg = await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise AcpError(f"Antigravity did not answer {method} in time.")
        if "error" in msg:
            e = msg["error"] or {}
            raise AcpError(str(e.get("message") or "error"), e.get("code"))
        return msg.get("result") or {}

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def respond(self, mid, result: dict | None = None, error: tuple[int, str] | None = None) -> None:
        msg = {"jsonrpc": "2.0", "id": mid}
        if error:
            msg["error"] = {"code": error[0], "message": error[1]}
        else:
            msg["result"] = result or {}
        try:
            self._send(msg)
        except AcpError:
            pass   # it stopped while the user was deciding

    async def close(self) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.stdin.close()
                await asyncio.wait_for(self.proc.wait(), 2)
            except (asyncio.TimeoutError, ProcessLookupError, BrokenPipeError):
                try:
                    self.proc.kill()
                except ProcessLookupError:
                    pass
        for t in self._tasks:
            t.cancel()
