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

    def sync_file_apps(self) -> None:
        """Turn file-based custom apps in ~/.diatonic/apps/<id> into apps: added, changed or removed."""
        if not getattr(self.ctx, "fileapps", None):
            return
        from ..fileapps import FileApp
        import json

        # Remove previously synced file apps
        for k in [k for k, a in self.apps.items() if getattr(a, "file_app", False)]:
            del self.apps[k]

        # Discover all apps from FileAppStore
        for app_meta in self.ctx.fileapps.list_apps():
            app_id = app_meta["id"]
            if app_meta.get("error") or app_id in self.apps:
                continue
            app_dir = self.ctx.fileapps.app_dir(app_id)
            try:
                manifest_path = app_dir / "manifest.json"
                if not manifest_path.is_file():
                    continue
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                tools = self.ctx.fileapps.load_tools(app_id, self.ctx)
                self.apps[app_id] = FileApp(self.ctx, app_dir, manifest, tools)
            except Exception as e:
                log.exception("failed to load file app %s", app_id)
                self.errors[app_id] = str(e)

    def register(self, app: App) -> None:
        self.apps[app.id] = app

    def enabled(self) -> list[App]:
        return [a for a in self.apps.values() if self.ctx.app_cfg(a.id)["enabled"]]

    def tool_map(self) -> dict[str, tuple[App, Tool]]:
        """Full tool name (`<app>_<tool>`) -> (app, tool), enabled apps only."""
        return {f"{a.id}_{t.name}": (a, t) for a in self.enabled() for t in a.tools()}

    def is_private(self, app: App) -> bool:
        """Private apps (mail, files, notes...) can't be read by a cloud model without asking. The user can change it per app."""
        v = self.ctx.app_cfg(app.id).get("private")
        return bool(app.private if v is None else v)

    def is_always_allowed(self, app: App, tool: Tool) -> bool:
        allowed = self.ctx.app_cfg(app.id).get("always_allow")
        return tool.name in (app.default_allow if allowed is None else allowed)
