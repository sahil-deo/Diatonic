import re
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from diatonic.apps.base import ToolError
from diatonic.appstore import AppStore, MAX_VALUE
from diatonic.config import Config
from diatonic.ctxapi import entries
from diatonic.db import Database
from diatonic.server import create_app

H = {"X-Diatonic": "1"}


@pytest.fixture
def store(tmp_path):
    return AppStore(Database(tmp_path / "d.db"))


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.setenv("DIATONIC_HOME", str(tmp_path / "home"))
    with TestClient(create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})) as c:
        c.post("/api/custom-apps", json={"id": "study", "name": "Study"}, headers=H)
        c.post("/api/custom-apps", json={"id": "other", "name": "Other"}, headers=H)
        yield c


def call(c, app, op, **body):
    return c.post(f"/api/store/{app}/{op}", json=body, headers=H)


def test_a_value_is_kept_and_a_missing_one_gives_the_default(store):
    assert store.get("a", "prefs", {"x": 1}) == {"x": 1}
    store.set("a", "prefs", {"unit": "min"})
    assert store.get("a", "prefs") == {"unit": "min"}
    assert store.keys("a") == ["prefs"]
    assert store.remove("a", "prefs") and store.get("a", "prefs") is None


def test_rows_get_ids_and_one_call_adds_many(store):
    rows = store.add_many("a", "topics", [{"name": f"t{i}"} for i in range(25)])
    assert [r["id"] for r in rows] == list(range(1, 26))
    assert len(store.query("a", "topics")) == 25
    store.delete("a", "topics", 25)
    assert store.add("a", "topics", {"name": "next"})["id"] == 26   # an id is never given out twice, even after its row was deleted
    assert store.tables("a") == {"topics": 25}


def test_update_query_order_and_delete(store):
    store.add_many("a", "t", [{"n": "b", "done": False}, {"n": "a", "done": True}, {"n": "c", "done": False}])
    store.update("a", "t", 1, {"done": True})
    assert [r["n"] for r in store.query("a", "t", {"done": True})] == ["b", "a"]
    assert [r["n"] for r in store.query("a", "t", order="n")] == ["a", "b", "c"]
    assert [r["n"] for r in store.query("a", "t", order="-n", limit=2)] == ["c", "b"]
    store.update_many("a", "t", [{"id": 2, "done": False}, {"id": 3, "done": True}])
    assert [r["done"] for r in store.query("a", "t")] == [True, False, True]
    assert store.delete("a", "t", [1, 2]) == 2 and store.delete("a", "t", "all") == 1


def test_a_bad_batch_changes_nothing(store):
    store.add("a", "t", {"n": 1})
    with pytest.raises(ToolError):
        store.update_many("a", "t", [{"id": 1, "n": 2}, {"id": 99, "n": 3}])
    assert store.query("a", "t")[0]["n"] == 1
    with pytest.raises(ToolError):
        store.add_many("a", "t", [{"n": 2}, {"bad": object()}])
    assert len(store.query("a", "t")) == 1


def test_apps_cannot_see_each_others_data_and_size_is_limited(store):
    store.set("a", "k", 1)
    store.add("a", "t", {"n": 1})
    assert store.get("b", "k") is None and store.query("b", "t") == []
    with pytest.raises(ToolError):
        store.set("a", "big", "x" * (MAX_VALUE + 1))


def test_the_routes_work_and_unknown_apps_are_refused(web):
    assert call(web, "study", "addMany", table="topics", rows=[{"name": "a"}, {"name": "b"}]).json()["result"][1]["id"] == 2
    assert call(web, "study", "query", table="topics", where={"name": "b"}).json()["result"] == [{"name": "b", "id": 2}]
    assert call(web, "other", "query", table="topics").json()["result"] == []
    assert call(web, "ghost", "set", key="k", value=1).status_code == 404
    r = call(web, "study", "add", table="topics", row="not an object")
    assert r.status_code == 400 and "error" in r.json()
    assert call(web, "study", "nope").status_code == 404
    assert call(web, "Bad Id!", "keys").status_code == 400


def test_deleting_the_app_deletes_its_data(web):
    call(web, "study", "set", key="k", value=1)
    call(web, "study", "add", table="t", row={"n": 1})
    assert web.delete("/api/custom-apps/study", headers=H).status_code == 200
    web.post("/api/custom-apps", json={"id": "study", "name": "Study"}, headers=H)
    assert call(web, "study", "keys").json()["result"] == [] and call(web, "study", "tables").json()["result"] == {}


def test_every_store_call_has_a_catalog_entry_and_a_panel_method():
    served = set(re.findall(r'"(\w+)": lambda: s\.', Path("diatonic/server.py").read_text()))
    panel = Path("diatonic/web/js/panel.js").read_text()
    assert served == {e.name.split(".")[-1] for e in entries() if e.name.startswith("ctx.store.")}
    for op in served:
        assert re.search(rf"\b{op}:", panel), op
    assert all(e.doc and e.example and e.kind == "ctx-api" for e in entries())


def test_platform_search_finds_the_store(web):
    found = web.get("/api/catalog", params={"q": "save data table", "kind": "ctx-api"}).json()
    names = [r["name"] for r in found["results"]]
    assert "ctx.store.addMany" in names or "ctx.store.add" in names
