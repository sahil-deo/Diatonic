"""Shared helpers for the tests of what a file app may do (permissions, fetch, notify, timers, tools)."""
import json

import httpx
import pytest
from starlette.testclient import TestClient

from diatonic import appfetch
from diatonic.agent import approvals
from diatonic.apps.base import ToolError
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app

H = {"X-Diatonic": "1"}


def declare(c, perms, app="tube"):
    """Write the app's manifest with these permissions, the way the agent would."""
    m = {"id": app, "name": app.title(), "version": 1}
    if perms is not None:
        m["permissions"] = perms
    return c.put(f"/api/custom-apps/{app}/file", json={"path": "manifest.json", "content": json.dumps(m)}, headers=H)


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("DIATONIC_HOME", str(tmp_path / "home"))
    cfg = Config(tmp_path / "c.json")
    with TestClient(create_app(cfg, Database(tmp_path / "d.db"), allowed_hosts={"testserver"})) as c:
        c.post("/api/custom-apps", json={"id": "tube", "name": "Tube"}, headers=H)
        c.cfg, c.ctx, c.events = cfg, c.app.state.ctx, []
        c.ctx.broadcast = c.ctx.notifier.broadcast = c.events.append
        yield c


@pytest.fixture
def user(monkeypatch):
    """The person at the screen. `user.says = True/False` answers every card; `user.cards` lists the cards that were shown."""
    class User:
        says, cards = True, []

    u = User()
    u.cards = []
    real = approvals.request

    def request(aid):
        fut = real(aid)
        u.cards.append(aid)
        if u.says is not None:
            fut.set_result(u.says)
        return fut

    monkeypatch.setattr(approvals, "request", request)
    return u


def web_pages(monkeypatch, pages):
    """Every host looks public except localhost and intranet.test, and the web answers from `pages` (url -> httpx.Response arguments)."""
    async def public(host):
        if host in ("localhost", "intranet.test"):
            raise ToolError(f"{host} is a local or private address, which apps may not reach.")

    async def send(self, request, **kw):
        spec = pages.get(str(request.url))
        resp = httpx.Response(**spec) if spec else httpx.Response(404, text="none")
        resp.request = request
        return resp

    monkeypatch.setattr(appfetch, "_public", public)
    monkeypatch.setattr(httpx.AsyncClient, "send", send)
