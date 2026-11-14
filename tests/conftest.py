import os
import tempfile

import pytest

_tmp = tempfile.mkdtemp()
os.environ["DIATONIC_HOME"] = _tmp
os.environ["DIATONIC_SECRETS_BACKEND"] = "file"

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

        return [
            Tool("read", "read", {"properties": {"text": {"type": "string"}}, "required": ["text"]}, read),
            Tool("boom", "fails", {}, boom),
            Tool("nuke", "dangerous", {"properties": {"id": {"type": "integer"}}, "required": ["id"]},
                 nuke, DESTRUCTIVE, describe=lambda a: f"Nuke {a['id']}"),
        ]


class ScriptedProvider:
    """Yields pre-scripted chunk lists, one per model call; complete() returns `summary`."""

    def __init__(self, script, summary="NONE"):
        self.script, self.summary, self.seen = list(script), summary, []

    async def stream(self, messages, tools=None, model=None):
        self.seen.append(messages)
        for chunk in self.script.pop(0):
            yield chunk

    async def complete(self, messages, model=None):
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
