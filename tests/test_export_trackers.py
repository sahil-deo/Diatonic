"""The one-off tracker export (v3 Phase 0): it saves everything first and only then deletes."""
import importlib.util
import json
import sqlite3
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("export_trackers", Path(__file__).parent.parent / "scripts" / "export_trackers.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


@pytest.fixture
def home(tmp_path):
    manifest = {"title": "Study", "lists": {"subjects": {"fields": []}, "topics": {"parent": "subjects", "fields": []}}}
    (tmp_path / "mods.json").write_text(json.dumps({"mods": {
        "study": {"kind": "data", "title": "Study", "code": json.dumps(manifest)},
        "wide": {"kind": "style", "title": "Wide", "code": "body{}"}}, "changes": []}))
    c = sqlite3.connect(str(tmp_path / "diatonic.db"))
    c.execute("CREATE TABLE app_items (id INTEGER PRIMARY KEY AUTOINCREMENT, app TEXT, list TEXT, parent INTEGER, data TEXT, pos REAL, created_at REAL, updated_at REAL)")
    c.executemany("INSERT INTO app_items (app, list, parent, data, pos, created_at, updated_at) VALUES (?,?,?,?,?,0,0)",
                  [("study", "subjects", None, '{"name": "Maths"}', 1), ("study", "topics", 1, '{"name": "Algebra"}', 2), ("gone", "tasks", None, '{"name": "x"}', 3)])
    c.commit()
    c.close()
    return tmp_path


def rows(home):
    c = sqlite3.connect(str(home / "diatonic.db"))
    try:
        return c.execute("SELECT COUNT(*) FROM app_items").fetchone()[0]
    finally:
        c.close()


def test_export_alone_saves_everything_and_deletes_nothing(home):
    r = mod.export_trackers(home)
    saved = json.loads((home / "exports" / "trackers.json").read_text())
    assert saved["apps"]["study"]["manifest"]["title"] == "Study" and len(saved["apps"]["study"]["items"]) == 2
    assert [o["data"]["name"] for o in saved["orphans"]] == ["x"] and r["orphans"] == saved["orphans"]
    assert rows(home) == 3 and "study" in json.loads((home / "mods.json").read_text())["mods"]


def test_delete_removes_the_trackers_and_every_row_but_keeps_other_mods(home):
    mod.export_trackers(home, delete=True)
    assert rows(home) == 0
    assert list(json.loads((home / "mods.json").read_text())["mods"]) == ["wide"]
    assert len(json.loads((home / "exports" / "trackers.json").read_text())["apps"]["study"]["items"]) == 2


def test_a_second_export_stops_instead_of_overwriting(home):
    mod.export_trackers(home)
    with pytest.raises(SystemExit, match="already exists"):
        mod.export_trackers(home, delete=True)
    assert rows(home) == 3


def test_the_agent_no_longer_has_tracker_tools(env):
    from diatonic.apps.ui import create as create_ui
    ctx, reg, _, _ = env
    ctx.registry = reg
    names = {t.name for t in create_ui(ctx).tools()}
    assert not {"create_app", "add_list", "add_field", "remove_field", "remove_list"} & names


def test_old_tracker_mods_are_dropped_when_the_store_loads(tmp_path):
    from diatonic.mods import ModStore
    (tmp_path / "mods.json").write_text(json.dumps({"mods": {"study": {"kind": "data", "code": "{}"}, "wide": {"kind": "style", "code": "body{}"}}, "changes": []}))
    assert [m["name"] for m in ModStore(tmp_path).list()] == ["wide"]


def _db_with_rows(folder):
    from diatonic.db import Database
    Database(folder / "diatonic.db").conn.close()
    c = sqlite3.connect(str(folder / "diatonic.db"))
    c.execute("CREATE TABLE app_items (id INTEGER PRIMARY KEY, app TEXT)")
    c.execute("INSERT INTO app_items (app) VALUES ('study')")
    c.commit()
    c.close()


def _has_table(folder):
    c = sqlite3.connect(str(folder / "diatonic.db"))
    try:
        return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='app_items'").fetchone())
    finally:
        c.close()


def test_the_tracker_table_is_kept_until_the_export_exists(tmp_path):
    from diatonic.db import Database
    _db_with_rows(tmp_path)
    Database(tmp_path / "diatonic.db").conn.close()
    assert _has_table(tmp_path)                                  # rows and no export: nothing is dropped
    (tmp_path / "exports").mkdir()
    (tmp_path / "exports" / "trackers.json").write_text("{}")
    Database(tmp_path / "diatonic.db").conn.close()
    assert not _has_table(tmp_path)


def test_an_empty_tracker_table_is_dropped_at_once(tmp_path):
    from diatonic.db import Database
    _db_with_rows(tmp_path)
    c = sqlite3.connect(str(tmp_path / "diatonic.db"))
    c.execute("DELETE FROM app_items")
    c.commit()
    c.close()
    Database(tmp_path / "diatonic.db").conn.close()
    assert not _has_table(tmp_path)
