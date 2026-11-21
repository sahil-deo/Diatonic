"""Antigravity is not a model endpoint: `agy acp` runs the whole conversation itself (see agent/antigravity.py).

This class exists so the rest of the app (the status bar, the model list, the settings form) can treat it like any other model.
"""
from __future__ import annotations

import asyncio
import re
import shutil

from .base import Provider, ProviderError

DEFAULT_MODELS = [
    "gemini-3.8-flash-high",
    "gemini-3.8-flash-medium",
    "gemini-3.8-flash-low",
    "claude-sonnet-4-6",
    "claude-opus-4-6-thinking",
    "gpt-oss-120b-medium",
]


_models_cache: list[str] | None = None


class AntigravityProvider(Provider):
    def _command(self) -> str:
        c = self.cfg.get("command") or "agy"
        return str(c[0] if isinstance(c, list) else c)

    async def health(self) -> dict:
        found = bool(shutil.which(self._command()))
        return {"ok": found, "model_present": found, "models": _models_cache or DEFAULT_MODELS,
                "error": None if found else "Antigravity's command line tool (agy) is not installed or not on the path."}

    async def list_models(self) -> list[str]:
        global _models_cache
        if _models_cache:
            return _models_cache
        cmd = self._command()
        if not shutil.which(cmd):
            return DEFAULT_MODELS
        try:
            proc = await asyncio.create_subprocess_exec(
                cmd, "models", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
            )
            out, _ = await asyncio.wait_for(proc.communicate(), 5.0)
            names = []
            for line in out.decode(errors="replace").splitlines():
                line = re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]", "", line).strip()
                if line and not line.startswith(("Fetching", "Usage", "Available")):
                    parts = line.split()
                    if parts and re.match(r"^[a-zA-Z0-9][-a-zA-Z0-9_.]+$", parts[0]):
                        names.append(parts[0])
            _models_cache = names or DEFAULT_MODELS
            return _models_cache
        except Exception:
            return DEFAULT_MODELS

    async def stream(self, messages, tools=None, model=None, options=None):
        raise ProviderError("Antigravity answers chats with its own agent, so it can't be used for this step.")
        yield {}   # a generator, like the other providers
