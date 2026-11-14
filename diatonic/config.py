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
        "temperature": 0.6,        # very low temperatures make small models loop
        "top_p": 0.8,
        "top_k": 20,
        "repeat_penalty": 1.05,
        "num_ctx": 8192,
        "max_tokens": 1024,         # hard cap on one model reply
        "max_steps": 8,             # model calls per run
        "max_tool_calls": 10,       # tool executions per run
        "run_timeout": 300,         # seconds per run
        "first_token_timeout": 240, # cold model loads can be slow
        "idle_timeout": 60,         # silence mid-reply = the model is stuck
        "history_messages": 12,
        "history_chars": 6000,      # prompt budget for earlier turns
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
    "appearance": {
        "theme": "system",          # system | light | dark
        "accent": "",               # #rrggbb, empty = theme default
        "density": "comfortable",   # comfortable | compact
        "font": "system",           # system | sans | serif | mono | rounded
        "font_size": 14,            # px, 11-22
        "radius": "default",        # sharp | default | round
        "width": "default",         # narrow | default | wide | full (chat column)
        "sidebar": "show",          # show | hide
        "show_status_bar": True,
        "show_quick_actions": True,
        "show_suggestions": True,
        "show_speak_button": True,
        "rail_order": [],           # app ids, in the order you want them
        "rail_hidden": [],
        "custom_css": "",
    },
    "memory": {
        "enabled": True,
        "auto_learn": True,     # learn tool quirks from error -> fixed-retry pairs (no model involved)
        "budget_chars": 600,    # most characters of facts added to the prompt (~150 tokens)
        "max_items": 8,         # most facts added to the prompt
    },
    "actions": [],  # saved prompts and script buttons; see diatonic/actions.py
    "notifications": {
        "native": True,        # macOS Notification Center, works with the browser closed
        "browser": False,      # web notifications while the page is open in the background
        "speak": True,         # say reminders aloud when the spoken-summary toggle is on
        "quiet_enabled": False,
        "quiet_start": "22:00",
        "quiet_end": "07:00",
    },
    "scheduler": {
        "allow_scripts": False,   # script jobs run shell commands: off until you turn it on
        "script_timeout": 60,     # seconds (hard max 900)
        "keep_prompt_chats": 5,   # chats kept per scheduled prompt
        "approval_timeout": 900,  # unattended runs deny writes nobody approves within this
    },
    "timezone": "",  # empty = system timezone
    "_v": 2,
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
                stored = json.loads(self.path.read_text())
            except (OSError, json.JSONDecodeError):
                return  # fall back to defaults rather than refusing to start
            version = stored.get("_v", 1)
            deep_merge(self.data, stored)
            if version < 2:  # v1 shipped temperature 0.3, which makes small models repeat themselves
                if stored.get("agent", {}).get("temperature") == 0.3:
                    self.data["agent"]["temperature"] = DEFAULTS["agent"]["temperature"]
                self.data["_v"] = 2
                self.save()

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
        return a
