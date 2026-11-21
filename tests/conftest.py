import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DIATONIC_HOME"] = _tmp
os.environ["DIATONIC_SECRETS_BACKEND"] = "file"
os.environ["DIATONIC_NO_NATIVE"] = "1"  # tests must never pop real macOS notifications

from diatonic.apps.base import DESTRUCTIVE, READ, App, Tool, ToolError  # noqa: E402
from diatonic.apps.registry import Registry  # noqa: E402
from diatonic.config import Config  # noqa: E402
from diatonic.context import Context  # noqa: E402
from diatonic.db import Database  # noqa: E402


class FakeApp(App):
    id, name = "fake", "Fake"
    calls: list

    def __init__(self, ctx):
        super().__init__(ctx)
        self.calls = []

    def tools(self):
        async def read(a):
            self.calls.append(("read", a))
            return {"echo": a["text"]}

        async def boom(a):
            raise ToolError("nope")

        async def nuke(a):
            self.calls.append(("nuke", a))
            return {"done": True}

        async def lim(a):
            self.calls.append(("lim", a))
            return {"limit": a.get("limit")}

        async def pick(a):
            self.calls.append(("pick", a))
            if a.get("mode") == "bad":
                raise ToolError("422: mode 'bad' is not supported", bad_args=True)
            if a.get("mode") == "down":
                raise ToolError("503: service unavailable")
            return {"mode": a.get("mode")}

        return [
            Tool("lim", "limited", {"properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 30}}}, lim),
            Tool("pick", "picks", {"properties": {"mode": {"type": "string", "enum": ["bad", "good", "down", "Fast"]},
                                                  "count": {"type": "integer"}, "q": {"type": "string"}}}, pick),
            Tool("read", "read", {"properties": {"text": {"type": "string"}}, "required": ["text"]}, read),
            Tool("boom", "fails", {}, boom),
            Tool("nuke", "dangerous", {"properties": {"id": {"type": "integer"}}, "required": ["id"]},
                 nuke, DESTRUCTIVE, describe=lambda a: f"Nuke {a['id']}"),
        ]


class ScriptedProvider:
    """Yields pre-scripted chunk lists, one per model call; complete() returns `summary`."""

    def __init__(self, script, summary="NONE"):
        self.script, self.summary, self.seen, self.tools_seen = list(script), summary, [], []

    async def stream(self, messages, tools=None, model=None, options=None):
        self.seen.append(messages)
        self.tools_seen.append(tools)
        for chunk in self.script.pop(0):
            yield chunk

    async def complete(self, messages, model=None, options=None):
        return self.summary

    async def health(self):
        return {"ok": True, "model_present": True, "models": [], "error": None}


@pytest.fixture
def env(tmp_path):
    cfg = Config(tmp_path / "c.json")
    db = Database(tmp_path / "d.db")
    ctx = Context(cfg, db)
    reg = Registry(ctx)
    app = FakeApp(ctx)
    reg.register(app)
    return ctx, reg, app, db


@pytest.fixture
def sched(env):
    """env + a real Scheduler/Notifier/Store wired to the context, capturing broadcast events."""
    from diatonic.agent.runs import RunManager
    from diatonic.scheduler.engine import Scheduler
    from diatonic.scheduler.notify import Notifier
    from diatonic.scheduler.store import Store

    ctx, reg, app, db = env
    events = []
    ctx.broadcast = events.append
    store = Store(db)
    ctx.notifier = Notifier(ctx, store, events.append)
    runs = RunManager()
    ctx.scheduler = Scheduler(ctx, reg, runs, ctx.notifier, store)
    return ctx, reg, ctx.scheduler, store, runs, events


@pytest.fixture(autouse=True)
def _no_verify_wait(monkeypatch):
    """New panel code is test-run in the user's window first; tests have no window to answer, so don't wait for one."""
    monkeypatch.setattr("diatonic.apps.ui.builder.VERIFY_WAIT", 0.05)
