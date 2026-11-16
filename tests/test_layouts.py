"""User and agent named layout storage: stored in layouts.json in the Diatonic home folder."""
import json
import pytest
from starlette.testclient import TestClient

from diatonic import layouts
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
    assert layouts.load() == []
    (home / "layouts.json").write_text("{ not json")
    assert layouts.load() == []


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
    saved = layouts.save(raw)
    assert len(saved) == 1
    assert saved[0]["id"] == "inbox_zero"
    assert saved[0]["name"] == "Inbox Zero"
    assert saved[0]["description"] == "Mail on left, todos on right"
    assert saved[0]["tree"]["root"]["type"] == "split"
    assert len(saved[0]["tree"]["root"]["children"]) == 2

    # Read back
    assert layouts.load() == saved


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
def test_bad_layouts_are_refused(home, bad):
    with pytest.raises(layouts.LayoutError):
        layouts.clean_layouts(bad)


def test_api_reads_and_writes_layouts(home, tmp_path):
    with client(tmp_path) as c:
        d = c.get("/api/layouts").json()
        assert d["layouts"] == [] and d["path"].endswith("layouts.json")

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
        r = c.put("/api/layouts", json=payload, headers={"origin": "http://testserver"})
        assert r.status_code == 200
        assert r.json()["layouts"][0]["id"] == "dev"

        # Read back
        assert c.get("/api/layouts").json()["layouts"][0]["name"] == "Dev"

        # Invalid payload
        r_bad = c.put("/api/layouts", json={"not": "a list"}, headers={"origin": "http://testserver"})
        assert r_bad.status_code == 400


def test_layouts_saved_under_the_old_workspaces_name_are_still_read(home):
    tree = {"v": 1, "root": {"type": "stack", "id": "s", "active": 0, "tabs": [{"id": "t", "app": "chat", "params": {}}]}}
    (home / "workspaces.json").write_text(json.dumps([{"id": "dev", "name": "Dev", "tree": tree}]))
    assert [x["name"] for x in layouts.load()] == ["Dev"]
    layouts.save(layouts.load())                                         # the next save writes the new file
    assert (home / "layouts.json").exists() and [x["id"] for x in layouts.load()] == ["dev"]


def test_the_layouts_route_saves_and_lists(home):
    c = client(home)
    tree = {"v": 1, "root": {"type": "stack", "id": "s", "active": 0, "tabs": [{"id": "t", "app": "files", "params": {}}]}}
    r = c.put("/api/layouts", json=[{"id": "files", "name": "Files", "tree": tree}], headers={"X-Diatonic": "1"})
    assert r.status_code == 200 and r.json()["layouts"][0]["name"] == "Files"
    assert c.get("/api/layouts").json()["layouts"][0]["id"] == "files"
    assert c.get("/api/workspaces").status_code == 404
