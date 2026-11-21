"""Shared runtime context handed to every app and the agent."""
from __future__ import annotations

import os
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

import httpx

from . import secrets
from .config import Config, home
from . import ctxapi, instructions, uikit
from .apptimers import AppTimers
from .appstore import AppStore
from .catalog import Catalog
from .mods import ModStore
from .fileapps import FileAppStore
from .db import Database
from .memory import MemoryStore
from .telemetry import Telemetry
from .google import GoogleAccounts


def system_timezone_name() -> str:
    try:
        link = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in link:
            return link.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    return "UTC"


class Context:
    def __init__(self, config: Config, db: Database, http: httpx.AsyncClient | None = None):
        self.config = config
        self.db = db
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0, read=300.0))
        self.secrets = secrets
        self.memory = MemoryStore(db, config)
        self.telemetry = Telemetry(db, config)
        self.google = GoogleAccounts(self)
        self.mods = ModStore(home() / "ui")
        self.fileapps = FileAppStore(home() / "apps")
        self.apptimers = AppTimers(self, db)   # ctx.timer: countdowns that run on the server
        self.appstore = AppStore(db)   # ctx.store: each file app's own key/value area and tables
        self.scheduler = None   # set by the server once the engine exists
        self.notifier = None
        self.broadcast = lambda ev: None   # pushes an event to every connected UI
        self.send_ui = lambda client, ev: False   # pushes a screen-control event to one window
        self.registry = None
        self.catalog = Catalog(self)
        self.catalog.add_source(ctxapi.entries)
        self.catalog.add_source(uikit.entries)
        self.catalog.add_source(instructions.entries)
        self.builder_seen: dict[tuple, float] = {}   # (conversation, app) -> when its logged errors were last put in front of the agent
        self.cloud_grants: dict[tuple, set] = {}   # (conversation, model) -> private apps allowed for that chat

    def cloud(self) -> bool:
        """Is the model in use running somewhere other than this computer?"""
        from .models import is_cloud
        return is_cloud(self.config.data["agent"])

    def app_cfg(self, app_id: str) -> dict:
        return self.config.app(app_id)

    def app_setting(self, app_id: str, key: str, default: str = "") -> str:
        return str(self.app_cfg(app_id)["settings"].get(key, default) or default)

    @property
    def tz_name(self) -> str:
        return self.config.data.get("timezone") or system_timezone_name()

    @property
    def tz(self) -> tzinfo:
        try:
            return ZoneInfo(self.tz_name)
        except Exception:
            return datetime.now().astimezone().tzinfo  # type: ignore[return-value]

    def now(self) -> datetime:
        return datetime.now(self.tz)
