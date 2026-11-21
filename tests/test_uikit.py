import re
from pathlib import Path

from diatonic import uikit
from diatonic.ctxapi import entries as ctx_entries


def test_every_component_is_in_the_catalog_with_a_doc_args_and_an_example(env):
    ctx, *_ = env
    found = {e.name: e for e in ctx.catalog.entries() if e.kind == "component"}
    assert set(found) == {f"ctx.ui.{n}" for n in uikit.declarations()}
    for e in found.values():
        assert e.doc and e.args and e.example.startswith("el.append(ctx.ui.") and all(a["doc"] and a["type"] for a in e.args)


def test_the_agent_finds_the_kit_by_asking(env):
    ctx, *_ = env
    assert ctx.catalog.search("card with a title", "component")[0].name == "ctx.ui.card"
    assert ctx.catalog.search("bar chart", "component")[0].name == "ctx.ui.chart"
    assert ctx.catalog.search("list with search, filter and detail pane")[0].name == "ctx.ui.listApp"


def test_listapp_and_run_are_documented_and_match_what_the_page_offers():
    names = {e.name for e in ctx_entries()}
    ui = Path("diatonic/web/js/ui/index.js").read_text()
    offered = set(re.search(r"names = \(\) => \[(.*?)\]", ui).group(1).replace("...Object.keys(components), ", "").replace('"', "").split(", "))
    assert offered == {"listApp", "run"} and {f"ctx.ui.{n}" for n in offered} <= names


def test_the_sandbox_knows_the_component_names_so_a_wrong_one_fails_the_test_run():
    assert "__UI__" in Path("diatonic/web/js/verify.js").read_text() and "uiNames()" in Path("diatonic/web/js/verify.js").read_text()


RECIPES = sorted(p for p in Path("diatonic/recipes").iterdir() if p.is_dir())


def test_the_recipes_exist_and_pass_the_checks_an_agent_written_app_must_pass(tmp_path):
    from diatonic.fileapps import FileAppStore
    assert {p.name for p in RECIPES} >= {"timer", "tracker", "search", "tools"}
    store = FileAppStore(tmp_path)
    for r in RECIPES:
        store.check_content("manifest.json", (r / "manifest.json").read_text())
        store.check_content("index.js", (r / "index.js").read_text())


def test_the_recipes_keep_no_module_level_state_and_use_the_kit():
    import subprocess
    for r in RECIPES:
        out = subprocess.run(["node", "scripts/check_module_state.mjs", str(r / "index.js")], capture_output=True, text=True).stdout
        assert "no module-level state found" in out, f"{r.name}: {out}"
        code = (r / "index.js").read_text()
        assert "const { ui" in code, f"{r.name} does not build from ctx.ui"
        assert not re.search(r"#[0-9a-fA-F]{3,6}\b|style=\"[^\"]*color", code), f"{r.name} sets its own colours"


def test_a_recipe_declares_what_it_uses():
    import json
    for r in RECIPES:
        code, perms = (r / "index.js").read_text(), json.loads((r / "manifest.json").read_text()).get("permissions", {})
        assert ("ctx.timer" in code) == bool(perms.get("timer")), r.name
        assert ("ctx.fetch" in code) == bool(perms.get("fetch")), r.name
        assert ("ctx.notify" in code) == bool(perms.get("notify")), r.name


def test_the_tools_recipe_lists_what_it_changes_in_its_manifest():
    import json
    r = Path("diatonic/recipes/tools")
    code, perms = (r / "index.js").read_text(), json.loads((r / "manifest.json").read_text())["permissions"]
    assert all(t in perms["tools"] for t in re.findall(r'ctx\.tool\("(todos_(?:complete|add|update|delete)_todo)"', code))
