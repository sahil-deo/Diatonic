from __future__ import annotations

from typing import AsyncIterator


class ProviderError(Exception):
    pass


class Provider:
    """chunk = {"content": str} and/or {"tool_calls": [{"name": str, "arguments": dict}]}"""

    def __init__(self, http, cfg: dict):
        self.http = http
        self.cfg = cfg

    def stream(self, messages: list[dict], tools: list[dict] | None = None,
               model: str | None = None) -> AsyncIterator[dict]:
        raise NotImplementedError

    async def complete(self, messages: list[dict], model: str | None = None) -> str:
        out = ""
        async for chunk in self.stream(messages, None, model):
            out += chunk.get("content", "")
        return out

    async def health(self) -> dict:
        raise NotImplementedError
