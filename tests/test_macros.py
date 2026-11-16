"""User and agent macro storage: stored in macros.json in the Diatonic home folder."""
import json
import pytest
from starlette.testclient import TestClient

from diatonic import macros
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("DIATONIC_HOME", str(tmp_path))
    return tmp_path


def client(tmp_path):
    return TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"}))


def test_missing_or_broken_file_means_empty(home):
    assert macros.load() == []
    (home / "macros.json").write_text("{ not json")
    assert macros.load() == []


def test_save_cleans_and_round_trips(home):
    raw = [
        {
            "id": "dev_setup",
            "name": " Dev Setup ",
            "description": " Open chat and files ",
            "steps": [
                {"command": "layout.preset.side-by-side"},
                {"command": "app.open", "args": {"id": "chat"}},
                {"wait": 150},
                {"if": "!input", "command": "app.open", "args": {"id": "files"}},
            ],
            "extra_ignored": 123,
        }
    ]
    saved = macros.save(raw)
    expected = [
        {
            "id": "dev_setup",
            "name": "Dev Setup",
            "description": "Open chat and files",
            "steps": [
                {"command": "layout.preset.side-by-side"},
                {"command": "app.open", "args": {"id": "chat"}},
                {"wait": 150},
                {"command": "app.open", "args": {"id": "files"}, "if": "!input"},
            ],
        }
    ]
    assert saved == expected
    assert macros.load() == expected
    assert json.loads((home / "macros.json").read_text()) == expected


@pytest.mark.parametrize("bad", [
    {}, "not a list",
    [{"name": "No id", "steps": []}],
    [{"id": "invalid id with spaces", "name": "Name", "steps": []}],
    [{"id": "a", "name": "", "steps": []}],
    [{"id": "a", "name": "A", "description": "d" * 301, "steps": []}],
    [{"id": "a", "name": "A", "steps": "not list"}],
    [{"id": "a", "name": "A", "steps": [{}]}],
    [{"id": "a", "name": "A", "steps": [{"command": 123}]}],
    [{"id": "a", "name": "A", "steps": [{"command": "x", "wait": -1}]}],
    [{"id": "a", "name": "A", "steps": [{"command": "x", "args": "not a dict"}]}],
    [{"id": "a", "name": "A", "steps": [{"command": "x", "args": {"k": "v" * 2100}}]}],
    [{"id": "a", "name": "A", "steps": [{"command": "x"}] * 51}],
    [{"id": "a", "name": "A", "steps": [{"command": "x"}]}, {"id": "a", "name": "Dup", "steps": [{"command": "y"}]}],
    [{"id": f"m_{i}", "name": f"M{i}", "steps": []} for i in range(51)],
])
def test_bad_macros_are_refused(home, bad):
    with pytest.raises(macros.MacroError):
        macros.clean_macros(bad)


def test_api_reads_and_writes_macros(home, tmp_path):
    with client(tmp_path) as c:
        d = c.get("/api/macros").json()
        assert d["macros"] == [] and d["path"].endswith("macros.json")

        payload = [
            {
                "id": "my_macro",
                "name": "My Macro",
                "description": "Test",
                "steps": [{"command": "app.open", "args": {"id": "chat"}}],
            }
        ]
        r = c.put("/api/macros", json=payload, headers={"origin": "http://testserver"})
        assert r.status_code == 200
        assert r.json()["macros"][0]["id"] == "my_macro"

        # Read back
        assert c.get("/api/macros").json()["macros"][0]["name"] == "My Macro"

        # Invalid payload
        r_bad = c.put("/api/macros", json={"not": "a list"}, headers={"origin": "http://testserver"})
        assert r_bad.status_code == 400
