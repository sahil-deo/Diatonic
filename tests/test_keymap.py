"""The user's keymap file: stored in the Diatonic home folder, validated, and read back by the page."""
import json

import pytest
from starlette.testclient import TestClient

from diatonic import keymap
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DIATONIC_HOME", str(tmp_path))
    return tmp_path


def client(tmp_path):
    return TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"}))


def test_missing_or_broken_file_means_no_changes(home):
    assert keymap.load() == {"leader": "", "vim": False, "bindings": []}
    (home / "keymap.json").write_text("{ not json")
    assert keymap.load()["bindings"] == []       # a typo never locks the user out


def test_save_cleans_and_round_trips(home):
    saved = keymap.save({"leader": " Ctrl+Space ", "vim": 1, "extra": "dropped", "bindings": [
        {"key": " g g ", "command": "nav.top", "when": "idle", "junk": 1},
        {"key": "Mod+b", "command": None},
        {"key": "<leader> a f", "command": "app.open", "args": {"id": "files"}}]})
    assert saved == {"leader": "Ctrl+Space", "vim": True, "bindings": [
        {"key": "g g", "command": "nav.top", "when": "idle"},
        {"key": "Mod+b", "command": None},
        {"key": "<leader> a f", "command": "app.open", "args": {"id": "files"}}]}
    assert keymap.load() == saved
    assert json.loads((home / "keymap.json").read_text()) == saved


@pytest.mark.parametrize("bad", [
    [], "x", {"leader": 5}, {"bindings": "no"}, {"bindings": [1]}, {"bindings": [{"command": "a"}]},
    {"bindings": [{"key": "a", "command": ""}]}, {"bindings": [{"key": "a", "command": 3}]},
    {"bindings": [{"key": "a", "command": "b", "when": 4}]}, {"bindings": [{"key": "a", "command": "b", "args": "x"}]},
    {"bindings": [{"key": "a", "command": "b", "args": {"x": "y" * 600}}]}, {"bindings": [{"key": "x" * 200, "command": "b"}]},
    {"bindings": [{"key": "a", "command": "b"}] * 501},
])
def test_bad_keymaps_are_refused(home, bad):
    with pytest.raises(keymap.KeymapError):
        keymap.clean(bad)


def test_api_reads_and_writes_the_file(home, tmp_path):
    with client(tmp_path) as c:
        d = c.get("/api/keymap").json()
        assert d["keymap"]["bindings"] == [] and d["path"].endswith("keymap.json")
        r = c.put("/api/keymap", json={"vim": True, "bindings": [{"key": "x", "command": "a.b"}]}, headers={"origin": "http://testserver"})
        assert r.status_code == 200 and r.json()["keymap"]["vim"] is True
        assert c.get("/api/keymap").json()["keymap"]["bindings"] == [{"key": "x", "command": "a.b"}]
        assert c.put("/api/keymap", json={"bindings": "nope"}).status_code == 400
        # a hand edit shows up on the next read
        (home / "keymap.json").write_text(json.dumps({"leader": "Ctrl+Space"}))
        assert c.get("/api/keymap").json()["keymap"]["leader"] == "Ctrl+Space"


def test_api_refuses_foreign_origins(home, tmp_path):
    with client(tmp_path) as c:
        assert c.put("/api/keymap", json={}, headers={"origin": "http://evil.com"}).status_code == 403
