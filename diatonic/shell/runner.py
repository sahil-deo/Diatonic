"""Runs one agent command: no terminal, no input, a clean environment, a timeout, and live output.

The whole process group is killed on timeout or Stop, so `npm run build` can't leave children behind.
"""
from __future__ import annotations

import asyncio
import codecs
import os
import signal
import time
from typing import Callable

KEEP_ENV = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "SHELL")
MODEL_CHARS = 4000          # what the model sees (the end of the output, plus the start if it was cut)
KEEP_BYTES = 400_000        # what the log and the card keep


def clean_env() -> dict:
    """Only harmless variables: the agent never inherits API keys or tokens from the server's environment."""
    env = {k: os.environ[k] for k in KEEP_ENV if k in os.environ}
    path = env.get("PATH", "/usr/bin:/bin:/usr/sbin:/sbin")
    for extra in ("/opt/homebrew/bin", "/usr/local/bin"):
        if extra not in path.split(":"):
            path += ":" + extra
    env.update(PATH=path, TERM="dumb", NO_COLOR="1", PAGER="cat", GIT_PAGER="cat", GIT_TERMINAL_PROMPT="0",
               PIP_NO_INPUT="1", npm_config_yes="true", HOMEBREW_NO_AUTO_UPDATE="1", CI="1")
    return env


def _kill(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return


async def run(command: str, cwd: str, timeout: float, on_output: Callable[[str], None] | None = None) -> dict:
    t0 = time.monotonic()
    proc = await asyncio.create_subprocess_exec("/bin/zsh", "-c", command, cwd=cwd, env=clean_env(),
                                                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    dec, out, total, timed_out = codecs.getincrementaldecoder("utf-8")("replace"), [], 0, False
    pending, last_flush = "", time.monotonic()

    def flush(force=False):
        nonlocal pending, last_flush
        if pending and on_output and (force or time.monotonic() - last_flush > 0.1):
            on_output(pending)
            pending, last_flush = "", time.monotonic()

    async def pump():
        nonlocal total, pending
        while True:
            chunk = await proc.stdout.read(8192)
            if not chunk:
                break
            text = dec.decode(chunk)
            total += len(chunk)
            out.append(text)
            pending += text
            flush()
            while sum(len(x) for x in out) > KEEP_BYTES and len(out) > 1:
                out.pop(1)          # keep the very start and the most recent output
    try:
        await asyncio.wait_for(asyncio.gather(pump(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        timed_out = True
        _kill(proc)
        try:
            await asyncio.wait_for(proc.wait(), 3)
        except asyncio.TimeoutError:
            os.killpg(proc.pid, signal.SIGKILL)
    except asyncio.CancelledError:      # Stop pressed: take the whole process tree down with us
        _kill(proc)
        raise
    flush(True)
    text = "".join(out) + dec.decode(b"", final=True)
    cut = len(text) > MODEL_CHARS
    for_model = (text[:600] + "\n…[output cut]…\n" + text[-(MODEL_CHARS - 600):]) if cut else text
    return {"exit_code": proc.returncode, "output": for_model, "full": text[-KEEP_BYTES:], "truncated": cut or None,
            "timed_out": timed_out or None, "seconds": round(time.monotonic() - t0, 1)}
