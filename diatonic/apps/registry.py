"""Discovers apps and exposes their tools to the agent."""
from __future__ import annotations

import importlib
import logging
import pkgutil

from .base import App, Tool

log = logging.getLogger("diatonic.apps")


class Registry:
    def __init__(self, ctx):
        self.ctx = ctx
        self.apps: dict[str, App] = {}
        self.errors: dict[str, str] = {}

    def discover(self) -> "Registry":
        from diatonic import apps as pkg

        for m in pkgutil.iter_modules(pkg.__path__):
            if not m.ispkg:
                continue
            try:
                mod = importlib.import_module(f"diatonic.apps.{m.name}")
                app = mod.create(self.ctx)
                self.apps[app.id] = app
            except Exception as e:  # a broken app must not take the harness down
                log.exception("failed to load app %s", m.name)
                self.errors[m.name] = str(e)
        return self

    def register(self, app: App) -> None:
        self.apps[app.id] = app

    def enabled(self) -> list[App]:
        return [a for a in self.apps.values() if self.ctx.app_cfg(a.id)["enabled"]]

    def tool_map(self) -> dict[str, tuple[App, Tool]]:
        """Full tool name (`<app>_<tool>`) -> (app, tool), enabled apps only."""
        return {f"{a.id}_{t.name}": (a, t) for a in self.enabled() for t in a.tools()}

    def is_always_allowed(self, app: App, tool: Tool) -> bool:
        allowed = self.ctx.app_cfg(app.id).get("always_allow")
        return tool.name in (app.default_allow if allowed is None else allowed)
