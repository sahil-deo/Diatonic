"""The one-off API app export (v3 Phase 5), and mods.json ignoring kinds that no longer exist."""
import importlib.util
import json
from pathlib import Path

import pytest

from diatonic.mods import ModStore

SPEC = importlib.util.spec_from_file_location("export_api_apps", Path(__file__).parent.parent / "scripts" / "export_api_apps.py")
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


@pytest.fixture
def home(tmp_path):
    (tmp_path / "mods.json").write_text(json.dumps({"mods": {
        "weather": {"kind": "app", "title": "Weather", "code": json.dumps({"title": "Weather", "base": "https://example.com"})},
        "mail-links": {"kind": "tools", "title": "Mail links", "code": json.dumps({"title": "Mail links", "actions": {}})},
        "wide": {"kind": "style", "title": "Wide", "code": "body{}"}}, "changes": []}))
    return tmp_path


def test_export_alone_saves_both_kinds_and_deletes_nothing(home):
    r = mod.export_api_apps(home)
    saved = json.loads((home / "exports" / "api_apps.json").read_text())
    assert set(saved["apps"]) == {"weather", "mail-links"} and saved["apps"]["weather"]["manifest"]["base"] == "https://example.com"
    assert set(json.loads((home / "mods.json").read_text())["mods"]) == {"weather", "mail-links", "wide"} and len(r["apps"]) == 2


def test_delete_removes_only_those_kinds_after_saving(home):
    mod.export_api_apps(home, delete=True)
    assert set(json.loads((home / "mods.json").read_text())["mods"]) == {"wide"}
    assert (home / "exports" / "api_apps.json").exists()


def test_a_second_export_does_not_overwrite_the_first(home):
    mod.export_api_apps(home)
    with pytest.raises(SystemExit):
        mod.export_api_apps(home)


def test_nothing_to_export_writes_no_file(tmp_path):
    (tmp_path / "mods.json").write_text(json.dumps({"mods": {"wide": {"kind": "style", "title": "W", "code": "body{}"}}, "changes": []}))
    assert mod.export_api_apps(tmp_path)["apps"] == {} and not (tmp_path / "exports").exists()


def test_mods_of_a_retired_kind_are_ignored_on_load(home):
    store = ModStore(home)
    assert [m["name"] for m in store.list()] == ["wide"]
