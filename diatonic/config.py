"""Settings persisted to ~/.diatonic/config.json (override dir with DIATONIC_HOME)."""
from __future__ import annotations

import copy
import json
import os
import threading
from pathlib import Path
from typing import Any


def home() -> Path:
    p = Path(os.environ.get("DIATONIC_HOME") or Path.home() / ".diatonic")
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS: dict[str, Any] = {
    "server": {"host": "127.0.0.1", "port": 5005},
    "agent": {
        "provider": "ollama",
        "model": "qwen3:4b-instruct",
        "endpoint": "http://localhost:11434",
        "temperature": 0.3,
        "num_ctx": 8192,
        "max_steps": 8,
        "system_prompt": "",
    },
    "voice": {
        "stt_model": "small.en",
        "auto_send": False,  # False = review the transcript before sending
        "speak_summary": False,  # the spoken-summary toggle
        "speak_when": {
            "tools": True,  # a run that used tools
            "approvals": True,  # a run that needed approval
            "errors": True,
            "min_seconds": 10,  # or any run slower than this
            "only_unfocused": False,  # only when this tab isn't focused
        },
        "hotkey": "Alt+Shift+Space",
        "tts_voice": "en_US-lessac-medium",
        "tts_speed": 1.0,
        "summary_model": "",  # empty = same model as the agent
    },
    "apps": {},  # {app_id: {"enabled": bool, "settings": {}, "always_allow": []}}
    "appearance": {"theme": "system"},
    "timezone": "",  # empty = system timezone
}


def deep_merge(base: dict, patch: dict) -> dict:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


class Config:
    def __init__(self, path: Path | None = None):
        self.path = path or home() / "config.json"
        self._lock = threading.Lock()
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        if self.path.exists():
            try:
                deep_merge(self.data, json.loads(self.path.read_text()))
            except (OSError, json.JSONDecodeError):
                pass  # fall back to defaults rather than refusing to start

    def save(self) -> None:
        with self._lock:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)

    def update(self, patch: dict) -> dict:
        """Merge a partial update. Unknown top-level keys are ignored."""
        clean = {k: v for k, v in patch.items() if k in DEFAULTS and k != "server"}
        deep_merge(self.data, clean)
        self.save()
        return self.data

    def app(self, app_id: str) -> dict:
        a = self.data["apps"].setdefault(app_id, {})
        a.setdefault("enabled", True)
        a.setdefault("settings", {})
        a.setdefault("always_allow", [])
        return a
