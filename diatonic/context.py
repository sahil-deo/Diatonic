"""Shared runtime context handed to every app and the agent."""
from __future__ import annotations

import os
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo

import httpx

from . import secrets
from .config import Config
from .db import Database


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
        self.scheduler = None   # set by the server once the engine exists
        self.notifier = None
        self.broadcast = lambda ev: None   # pushes an event to every connected UI

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
