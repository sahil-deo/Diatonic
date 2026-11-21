"""The app contract.

An app is a sub-package of `diatonic.apps` exposing `create(ctx) -> App`.
It contributes tools for the agent, optional HTTP routes for its UI panel,
and a settings schema. The harness discovers it automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

READ, WRITE, DESTRUCTIVE = "read", "write", "destructive"

# What a tool can do to the world. `risk` is only the short label shown on cards; every rule reads `effects`.
#   read: looks at data    write: changes data    destroy: deletes it    external: talks to the network or another service
#   execute: runs commands or code    spend: costs money or quota    display: changes the interface itself (theme, panels, buttons)
EFFECTS = frozenset({"read", "write", "destroy", "external", "execute", "spend", "display"})
CHANGING = frozenset({"write", "destroy", "execute", "spend"})   # these ask first, and a panel may not use them


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
    risk: str = READ  # read | write | destructive: a shorthand that sets `effects` when none are given
    effects: frozenset[str] | set[str] | None = None
    domain: str = ""   # what the tool is about ("mail", "files"); empty means the app's own id
    title: str = ""
    describe: Callable[[dict], str] | None = None  # human-readable summary for approval cards
    # Return an error message if the call can't possibly work; the user is then not asked to approve it.
    precheck: Callable[[dict], str | None] | None = None
    # Return a unified-diff-style text shown on the approval card (computed before anything changes).
    preview: Callable[[dict], str | None] | None = None
    # Decide per call whether to ask (e.g. a shell command that only reads doesn't). Overrides risk and "always allow".
    ask: Callable[[dict], bool] | None = None
    timeout: float | None = None   # seconds the harness waits for the handler (default 60)
    # How this tool works inside an app the agent builds on it: {"inputs": [params the form asks for],
    # "when": {regex on the request: {preset args}}, "swap": {param: better param for a form}, "title": button label}.
    build: dict | None = None

    def __post_init__(self):
        self.title = self.title or self.name.replace("_", " ").capitalize()
        self.parameters.setdefault("type", "object")
        self.parameters.setdefault("properties", {})
        if self.effects is None:
            self.effects = frozenset({READ} if self.risk == READ else {"write", "destroy"} if self.risk == DESTRUCTIVE else {WRITE})
        else:
            self.effects = frozenset(self.effects)
            if bad := self.effects - EFFECTS:
                raise ValueError(f"Tool {self.name}: unknown effects {sorted(bad)}")
            self._sync_risk()

    def _sync_risk(self) -> None:
        self.risk = DESTRUCTIVE if "destroy" in self.effects else WRITE if self.effects & CHANGING else READ

    def add_effects(self, *more: str) -> None:
        self.effects = frozenset(self.effects | set(more))
        self._sync_risk()

    @property
    def changes(self) -> bool:
        """True if the tool changes anything beyond reading."""
        return bool(self.effects & CHANGING)

    @property
    def needs_approval(self) -> bool:
        return self.changes


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
