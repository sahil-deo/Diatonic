"""Real terminal sessions for the Terminal panel: your login shell on a pseudo-terminal, so vim, htop, ssh and REPLs work.

Sessions live in the server, not the page: close the panel or reload and they keep running; reopening replays the
recent output. Output is raw bytes (the browser's terminal decodes them), kept in a bounded buffer per session.
"""
from __future__ import annotations

import asyncio
import fcntl
import os
import pty
import signal
import struct
import termios
import time
import uuid

BUFFER = 512 * 1024


class Session:
    def __init__(self, sid: str, pid: int, fd: int, cwd: str):
        self.id, self.pid, self.fd, self.cwd = sid, pid, fd, cwd
        self.buf = bytearray()
        self.subs: set[asyncio.Queue] = set()
        self.started = time.time()
        self.exited: int | None = None

    def info(self) -> dict:
        return {"id": self.id, "cwd": self.cwd, "started": self.started, "exited": self.exited}


class Terminals:
    def __init__(self):
        self.sessions: dict[str, Session] = {}

    def create(self, cwd: str | None = None, cols: int = 100, rows: int = 30) -> Session:
        cwd = cwd if cwd and os.path.isdir(cwd) else os.path.expanduser("~")
        shell = os.environ.get("SHELL") or "/bin/zsh"
        env = {**os.environ, "TERM": "xterm-256color", "COLORTERM": "truecolor", "TERM_PROGRAM": "Diatonic"}
        env.setdefault("LANG", "en_US.UTF-8")
        pid, fd = pty.fork()
        if pid == 0:   # child: become the shell straight away (no Python code runs here)
            try:
                os.chdir(cwd)
                os.execve(shell, [shell, "-l"], env)
            finally:
                os._exit(127)
        os.set_blocking(fd, False)
        s = Session(uuid.uuid4().hex[:10], pid, fd, cwd)
        self.sessions[s.id] = s
        self.resize(s.id, cols, rows)
        asyncio.get_running_loop().add_reader(fd, self._read, s)
        return s

    def _read(self, s: Session) -> None:
        try:
            data = os.read(s.fd, 65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            return self._exited(s)
        s.buf += data
        if len(s.buf) > BUFFER:
            del s.buf[: len(s.buf) - BUFFER]
        for q in list(s.subs):
            q.put_nowait(data)

    def _exited(self, s: Session) -> None:
        loop = asyncio.get_running_loop()
        loop.remove_reader(s.fd)
        try:
            _, status = os.waitpid(s.pid, os.WNOHANG)
            s.exited = os.waitstatus_to_exitcode(status) if status else 0
        except ChildProcessError:
            s.exited = 0
        try:
            os.close(s.fd)
        except OSError:
            pass
        for q in list(s.subs):
            q.put_nowait(None)

    def write(self, sid: str, data: str) -> bool:
        s = self.sessions.get(sid)
        if not s or s.exited is not None:
            return False
        raw = data.encode()
        while raw:   # a big paste may not fit in one write
            try:
                n = os.write(s.fd, raw)
                raw = raw[n:]
            except BlockingIOError:
                time.sleep(0.005)
        return True

    def resize(self, sid: str, cols: int, rows: int) -> None:
        s = self.sessions.get(sid)
        if s and s.exited is None:
            fcntl.ioctl(s.fd, termios.TIOCSWINSZ, struct.pack("HHHH", max(2, min(rows, 500)), max(2, min(cols, 1000)), 0, 0))

    def close(self, sid: str) -> None:
        s = self.sessions.pop(sid, None)
        if s and s.exited is None:
            try:
                os.killpg(os.getpgid(s.pid), signal.SIGHUP)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                asyncio.get_running_loop().remove_reader(s.fd)
                os.close(s.fd)
            except (OSError, RuntimeError):
                pass
            for q in list(s.subs):
                q.put_nowait(None)

    def shutdown(self) -> None:
        for sid in list(self.sessions):
            self.close(sid)
