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
    """Raised by a tool handler; the message is shown to the model and the user.

    `bad_args=True` means the service rejected the arguments (HTTP 400/422 and similar), as opposed to being
    down, unauthorised or rate-limited. Only those failures can teach the harness a lesson.
    """

    def __init__(self, message: str = "", *, bad_args: bool = False):
        super().__init__(message)
        self.bad_args = bad_args


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON Schema (type: object)
    handler: Callable[[dict], Awaitable[Any]]
    risk: str = READ  # read | write | destructive
    title: str = ""
    describe: Callable[[dict], str] | None = None  # human-readable summary for approval cards
    # Return an error message if the call can't possibly work; the user is then not asked to approve it.
    precheck: Callable[[dict], str | None] | None = None
    # Return a unified-diff-style text shown on the approval card (computed before anything changes).
    preview: Callable[[dict], str | None] | None = None
    # Decide per call whether to ask (e.g. a shell command that only reads doesn't). Overrides risk and "always allow".
    ask: Callable[[dict], bool] | None = None
    timeout: float | None = None   # seconds the harness waits for the handler (default 60)

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
    # Words that suggest a request is about this app. Only matching apps' tools are shown to the model
    # (a small model chooses far better from a handful of tools). No match = all apps.
    keywords: list[str] = []
    keyword_patterns: list[str] = []   # regexes that also route a request here (e.g. file names)
    # Tools that run without an approval card until the user chooses otherwise in Settings.
    default_allow: list[str] = []
    # Holds the user's private data (mail, files, notes...). A cloud model must ask before any of its tools run.
    private: bool = False
    # Tools whose results are written by someone else (emails). A run that used one is 'untrusted' and can't change the interface.
    untrusted_tools: list[str] = []

    def __init__(self, ctx):
        self.ctx = ctx

    def tools(self) -> list[Tool]:
        return []

    def tools_for(self, text: str, prior_tools: set[str] | frozenset = frozenset()) -> list[Tool]:
        """The tools worth offering for this request. Apps with many tools can narrow this to save prompt space."""
        return self.tools()

    # A home-screen tile. Return {"items": [{"text", "sub"?, "badge"?, "tone"?: "warn"|"ok", "done"?: bool,
    # "open"?: navigate params, "url"?: link}], "stats"?: [{"label", "value"}], "empty"?: str, "more"?: str},
    # at most `n` items. Apps without one simply don't offer a tile. Raise ToolError("...") for "not connected" etc.
    snapshot = None

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
