"""Run Diatonic in the background and at login via a macOS launchd agent."""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

from .config import home

LABEL = "com.diatonic.server"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def build_plist(python: str | None = None, port: int = 5005) -> dict:
    logs = home() / "logs"
    path = os.environ.get("PATH", "/usr/bin:/bin")
    for extra in ("/opt/homebrew/bin", "/usr/local/bin"):
        if extra not in path.split(":"):
            path += ":" + extra
    env = {"PATH": path, "HOME": str(Path.home())}
    if os.environ.get("DIATONIC_HOME"):
        env["DIATONIC_HOME"] = os.environ["DIATONIC_HOME"]
    return {
        "Label": LABEL,
        "ProgramArguments": [python or sys.executable, "-m", "diatonic", "run", "--port", str(port)],
        "RunAtLoad": True,
        "KeepAlive": True,          # restart if it ever crashes
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "EnvironmentVariables": env,
        "StandardOutPath": str(logs / "diatonic.log"),
        "StandardErrorPath": str(logs / "diatonic.err.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def install(port: int = 5005) -> str:
    if sys.platform != "darwin":
        raise SystemExit("Background service install is macOS-only (launchd).")
    (home() / "logs").mkdir(parents=True, exist_ok=True)
    p = plist_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(plistlib.dumps(build_plist(port=port)))
    domain = f"gui/{os.getuid()}"
    _launchctl("bootout", domain, str(p))  # replace any running copy
    r = _launchctl("bootstrap", domain, str(p))
    if r.returncode != 0:
        raise SystemExit(f"launchctl failed: {r.stderr.strip() or r.stdout.strip()}")
    return f"Installed and started. Diatonic now runs at login: http://localhost:{port}\nLogs: {home() / 'logs'}"


def uninstall() -> str:
    p = plist_path()
    _launchctl("bootout", f"gui/{os.getuid()}", str(p))
    if p.exists():
        p.unlink()
        return "Stopped and removed the background service."
    return "The background service wasn't installed."


def status() -> str:
    if not plist_path().exists():
        return "Not installed. Run: diatonic install-service"
    r = _launchctl("print", f"gui/{os.getuid()}/{LABEL}")
    state = "running" if r.returncode == 0 and "state = running" in r.stdout else "installed but not running"
    return f"Background service: {state} ({plist_path()})"
