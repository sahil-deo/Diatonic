"""User and agent named workspace storage: stored in workspaces.json in the Diatonic home folder."""
import json
import pytest
from starlette.testclient import TestClient

from diatonic import workspaces
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
    assert workspaces.load() == []
    (home / "workspaces.json").write_text("{ not json")
    assert workspaces.load() == []


def test_save_cleans_and_round_trips(home):
    raw = [
        {
            "id": "inbox_zero",
            "name": " Inbox Zero ",
            "description": " Mail on left, todos on right ",
            "tree": {
                "v": 1,
                "root": {
                    "type": "split",
                    "dir": "row",
                    "sizes": [0.7, 0.3],
                    "children": [
                        {"type": "stack", "id": "s1", "active": 0, "tabs": [{"id": "t1", "app": "gmail", "params": {}}]},
                        {"type": "stack", "id": "s2", "active": 0, "tabs": [{"id": "t2", "app": "todos", "params": {}}]},
                    ]
                }
            },
            "created_at": 1700000000.0,
            "extra_field": "ignored"
        }
    ]
    saved = workspaces.save(raw)
    assert len(saved) == 1
    assert saved[0]["id"] == "inbox_zero"
    assert saved[0]["name"] == "Inbox Zero"
    assert saved[0]["description"] == "Mail on left, todos on right"
    assert saved[0]["tree"]["root"]["type"] == "split"
    assert len(saved[0]["tree"]["root"]["children"]) == 2

    # Read back
    assert workspaces.load() == saved


@pytest.mark.parametrize("bad", [
    {}, "not a list",
    [{"name": "No id", "tree": {}}],
    [{"id": "invalid id with space", "name": "Name", "tree": {}}],
    [{"id": "a", "name": "", "tree": {}}],
    [{"id": "a", "name": "A", "description": "d" * 301, "tree": {}}],
    [{"id": "a", "name": "A", "tree": "not dict"}],
    [{"id": "a", "name": "A", "tree": {"root": "not dict"}}],
    [{"id": "a", "name": "A", "tree": {"root": {"type": "unknown"}}}],
    [{"id": "a", "name": "A", "tree": {"root": {"type": "split", "children": []}}}],
    [{"id": "a", "name": "A", "tree": {"root": {"type": "stack", "tabs": "not list"}}}],
    [{"id": f"ws_{i}", "name": f"WS{i}", "tree": {"root": {"type": "stack", "tabs": []}}} for i in range(51)],
    [
        {"id": "dup", "name": "D1", "tree": {"root": {"type": "stack", "tabs": []}}},
        {"id": "dup", "name": "D2", "tree": {"root": {"type": "stack", "tabs": []}}},
    ]
])
def test_bad_workspaces_are_refused(home, bad):
    with pytest.raises(workspaces.WorkspaceError):
        workspaces.clean_workspaces(bad)


def test_api_reads_and_writes_workspaces(home, tmp_path):
    with client(tmp_path) as c:
        d = c.get("/api/workspaces").json()
        assert d["workspaces"] == [] and d["path"].endswith("workspaces.json")

        payload = [
            {
                "id": "dev",
                "name": "Dev",
                "description": "Coding setup",
                "tree": {
                    "v": 1,
                    "root": {"type": "stack", "id": "s1", "active": 0, "tabs": [{"id": "t1", "app": "chat", "params": {}}]}
                }
            }
        ]
        r = c.put("/api/workspaces", json=payload, headers={"origin": "http://testserver"})
        assert r.status_code == 200
        assert r.json()["workspaces"][0]["id"] == "dev"

        # Read back
        assert c.get("/api/workspaces").json()["workspaces"][0]["name"] == "Dev"

        # Invalid payload
        r_bad = c.put("/api/workspaces", json={"not": "a list"}, headers={"origin": "http://testserver"})
        assert r_bad.status_code == 400
