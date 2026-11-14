"""The app contract.

An app is a sub-package of `diatonic.apps` exposing `create(ctx) -> App`.
It contributes tools for the agent, optional HTTP routes for its UI panel,
and a settings schema. The harness discovers it automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"


class ToolError(Exception):
    """Raised by a tool handler; the message is shown to the model and the user."""


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema (type: object)
    handler: Callable[[dict], Awaitable[Any]]
    risk: str = READ  # read | write | destructive
    title: str = ""
    describe: Callable[[dict], str] | None = None  # human-readable summary for approval cards

    def __post_init__(self):
        self.title = self.title or self.name.replace("_", " ").capitalize()
        self.parameters.setdefault("type", "object")
        self.parameters.setdefault("properties", {})

    @property
    def needs_approval(self) -> bool:
        return self.risk != READ


class App:
    id: str = ""
    name: str = ""
    icon: str = "box"  # a key in web/js/icons.js
    description: str = ""
    # [{"key","label","secret":bool,"help":str,"placeholder":str}]
    settings: list[dict] = []
    panel: bool = False  # True if web/apps/<id>.js exists

    def __init__(self, ctx):
        self.ctx = ctx

    def tools(self) -> list[Tool]:
        return []

    def routes(self) -> list:
        return []

    async def status(self) -> dict:
        """{"connected": bool, "detail": str, "action": optional {"label","href"}}"""
        return {"connected": True, "detail": ""}

    # secrets are namespaced per app
    def secret(self, key: str) -> str | None:
        return self.ctx.secrets.get(f"{self.id}.{key}")

    def set_secret(self, key: str, value: str) -> None:
        self.ctx.secrets.set(f"{self.id}.{key}", value)

    def setting(self, key: str, default: str = "") -> str:
        return self.ctx.app_setting(self.id, key, default)
