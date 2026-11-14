"""Script jobs: run a shell command with a timeout, captured output and a minimal environment."""
from __future__ import annotations

import asyncio
import os
import signal

from ..config import home

MAX_OUTPUT = 20000
HARD_MAX_TIMEOUT = 900


class ScriptsDisabled(Exception):
    pass


def _env() -> dict:
    path = os.environ.get("PATH", "/usr/bin:/bin")
    for extra in ("/opt/homebrew/bin", "/usr/local/bin"):
        if extra not in path.split(":"):
            path += ":" + extra
    return {"PATH": path, "HOME": os.environ.get("HOME", ""), "LANG": "en_US.UTF-8", "USER": os.environ.get("USER", "")}


async def run_script(ctx, schedule: dict) -> tuple[str, str]:
    """Returns (status, output). status: ok | error | timeout."""
    cfg = ctx.config.data["scheduler"]
    if not cfg.get("allow_scripts"):
        return "error", "Script jobs are turned off. Enable them in Settings → Automation."
    command = schedule["payload"].get("command", "").strip()
    if not command:
        return "error", "This job has no command."
    timeout = min(float(schedule["payload"].get("timeout") or cfg.get("script_timeout", 60)), HARD_MAX_TIMEOUT)
    cwd = home() / "jobs" / str(schedule["id"])
    cwd.mkdir(parents=True, exist_ok=True)
    proc = await asyncio.create_subprocess_exec(
        "/bin/sh", "-c", command, cwd=cwd, env=_env(), stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        text = out.decode(errors="replace")[-MAX_OUTPUT:]
        return ("ok" if proc.returncode == 0 else "error"), (text or "(no output)") + \
            ("" if proc.returncode == 0 else f"\n[exit code {proc.returncode}]")
    except asyncio.TimeoutError:
        _kill(proc)
        return "timeout", f"Killed after {int(timeout)}s."
    except asyncio.CancelledError:
        _kill(proc)
        raise


def _kill(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # the whole process group, not just the shell
    except (ProcessLookupError, PermissionError):
        pass
