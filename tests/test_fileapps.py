from __future__ import annotations

import json
from pathlib import Path
import pytest

from diatonic.apps.base import Tool
from diatonic.fileapps import FileAppStore, FileApp, FileAppError


def test_create_and_list_file_app(tmp_path: Path):
    store = FileAppStore(tmp_path)
    manifest = store.create_app("timer-app", "Pomodoro Timer", "A simple countdown timer", "clock")
    assert manifest["id"] == "timer-app"
    assert manifest["name"] == "Pomodoro Timer"

    assert (tmp_path / "timer-app" / "manifest.json").is_file()
    assert (tmp_path / "timer-app" / "index.js").is_file()
    assert (tmp_path / "timer-app" / "style.css").is_file()

    apps = store.list_apps()
    assert len(apps) == 1
    assert apps[0]["id"] == "timer-app"
    assert apps[0]["name"] == "Pomodoro Timer"
    assert "index.js" in apps[0]["files"]


def test_path_traversal_blocked(tmp_path: Path):
    store = FileAppStore(tmp_path)
    store.create_app("safe-app", "Safe App")

    with pytest.raises(FileAppError, match="Path escapes"):
        store.read_file("safe-app", "../other.txt")

    with pytest.raises(FileAppError, match="Path escapes"):
        store.write_file("safe-app", "../../etc/passwd", "root:x:0:0")


def test_file_read_write_patch(tmp_path: Path):
    store = FileAppStore(tmp_path)
    store.create_app("notes-widget", "Notes Widget")

    content = store.read_file("notes-widget", "index.js")
    assert "Notes Widget" in content

    # Patch file with a specific snippet
    target_snippet = 'ctx.h("h2", { class: "custom-app-title" }, "Notes Widget")'
    replacement_snippet = 'ctx.h("h2", { class: "custom-app-title" }, "My Enhanced Notes")'
    store.patch_file("notes-widget", "index.js", target_snippet, replacement_snippet)
    updated = store.read_file("notes-widget", "index.js")
    assert "My Enhanced Notes" in updated

    # Patch with non-existent text fails
    with pytest.raises(FileAppError, match="Could not find the target text"):
        store.patch_file("notes-widget", "index.js", "Non Existent Snippet", "Replacement")


def test_content_validation(tmp_path: Path):
    store = FileAppStore(tmp_path)
    store.create_app("val-app", "Validation App")

    # Bad CSS (network URL or @import)
    with pytest.raises(FileAppError, match="styles can't load anything from the network"):
        store.write_file("val-app", "style.css", "p { background: url('https://evil.com/bg.png'); }")

    # Bad CSS (styling body directly)
    with pytest.raises(FileAppError, match="must not style body"):
        store.write_file("val-app", "style.css", "body { padding: 16px; }")

    # Bad CSS (styling .btn directly)
    with pytest.raises(FileAppError, match="must not override global '.btn'"):
        store.write_file("val-app", "style.css", ".btn { background: red; }")

    # Scoped CSS succeeds
    res_css = store.write_file("val-app", "style.css", ".val-app .btn { background: red; }")
    assert res_css["bytes"] > 0

    # Bad JSON
    with pytest.raises(FileAppError, match="Invalid JSON"):
        store.write_file("val-app", "data.json", "{ unclosed json")

    # Bad Python syntax
    with pytest.raises(FileAppError, match="Python syntax error"):
        store.write_file("val-app", "tools.py", "def broken(:")

    # index.js attempting to replace document.body
    with pytest.raises(FileAppError, match="must not overwrite document.body"):
        store.write_file("val-app", "index.js", "document.body.replaceChildren(container); export function render() {}")

    # index.js attempting to overwrite document.body.innerHTML
    with pytest.raises(FileAppError, match="must not overwrite document.body"):
        store.write_file("val-app", "index.js", "document.body.innerHTML = '<div>hi</div>'; export function render() {}")

    # index.js missing exported render function
    with pytest.raises(FileAppError, match="must export a render"):
        store.write_file("val-app", "index.js", "const container = document.createElement('div');")

    # Valid index.js
    res = store.write_file("val-app", "index.js", "export async function render(el, ctx) { el.textContent = 'ok'; }")
    assert res["bytes"] > 0


def test_tools_py_loading(tmp_path: Path):
    store = FileAppStore(tmp_path)
    store.create_app("calc-app", "Calculator")

    tools_code = """
from diatonic.apps.base import Tool

def export_tools(ctx):
    async def add(args):
        return args.get("a", 0) + args.get("b", 0)

    return [
        Tool("add_numbers", "Add two numbers", {"properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}}, add)
    ]
"""
    store.write_file("calc-app", "tools.py", tools_code)
    tools = store.load_tools("calc-app", None)
    assert len(tools) == 1
    assert tools[0].name == "add_numbers"

    # Also test manifest-defined tools using dict format
    store.create_app("dict-tools-app", "Dict Tools App")
    tools_code2 = """
def search_items(args):
    return ["item1", "item2"]
"""
    manifest_dict_tools = {
        "id": "dict-tools-app",
        "name": "Dict Tools App",
        "tools": {
            "search_items": {
                "description": "Search items",
                "risk": "read"
            }
        }
    }
    store.write_file("dict-tools-app", "manifest.json", json.dumps(manifest_dict_tools))
    store.write_file("dict-tools-app", "tools.py", tools_code2)
    tools2 = store.load_tools("dict-tools-app", None)
    assert len(tools2) == 1
    assert tools2[0].name == "search_items"


def test_error_logging(tmp_path: Path):
    store = FileAppStore(tmp_path)
    store.create_app("buggy-app", "Buggy App")

    store.log_error("buggy-app", "TypeError: undefined is not a function", source="client_runtime")
    logs = store.get_logs("buggy-app")
    assert len(logs) == 1
    assert logs[0]["source"] == "client_runtime"
    assert "TypeError" in logs[0]["error"]

    store.clear_logs("buggy-app")
    assert store.get_logs("buggy-app") == []


def test_export_and_import_bundle(tmp_path: Path):
    store1 = FileAppStore(tmp_path / "store1")
    store1.create_app("exportable", "Exportable App", "Export test description")
    store1.write_file("exportable", "extra.js", "console.log('extra');")

    bundle = store1.export_app("exportable")
    assert bundle["diatonic_app"] == 1
    assert bundle["id"] == "exportable"
    assert "index.js" in bundle["files"]
    assert "extra.js" in bundle["files"]

    store2 = FileAppStore(tmp_path / "store2")
    imported = store2.import_app(bundle)
    assert imported["id"] == "exportable"
    assert store2.exists("exportable")
    assert store2.read_file("exportable", "extra.js") == "console.log('extra');"


def test_custom_app_http_endpoints(tmp_path: Path):
    from starlette.testclient import TestClient
    from diatonic.config import Config
    from diatonic.db import Database
    from diatonic.server import create_app

    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        # Create app
        res = c.post("/api/custom-apps", json={"id": "http-app", "name": "HTTP App", "description": "Testing endpoints"})
        assert res.status_code == 201
        manifest = res.json()
        assert manifest["id"] == "http-app"

        # List apps
        res = c.get("/api/custom-apps")
        assert res.status_code == 200
        apps = res.json()
        assert any(a["id"] == "http-app" for a in apps)

        # Serve static file
        res = c.get("/custom-apps/http-app/index.js")
        assert res.status_code == 200
        assert "export async function render" in res.text
        assert res.headers["content-type"].startswith("text/javascript")

        # Read file via API
        res = c.get("/api/custom-apps/http-app/file?path=index.js")
        assert res.status_code == 200
        assert "HTTP App" in res.json()["content"]

        # Patch file
        res = c.post("/api/custom-apps/http-app/patch", json={
            "path": "index.js",
            "old": 'ctx.h("h2", { class: "custom-app-title" }, "HTTP App")',
            "new": 'ctx.h("h2", { class: "custom-app-title" }, "Patched HTTP App")',
        })
        assert res.status_code == 200

        # Check patched file served
        res = c.get("/custom-apps/http-app/index.js")
        assert "Patched HTTP App" in res.text

        # Error logging
        res = c.post("/api/custom-apps/http-app/logs", json={"error": "Something went wrong", "source": "test"})
        assert res.status_code == 200
        res = c.get("/api/custom-apps/http-app/logs")
        logs = res.json()
        assert len(logs) == 1
        assert "Something went wrong" in logs[0]["error"]

        # Export app
        res = c.get("/api/custom-apps/http-app/export")
        assert res.status_code == 200
        bundle = res.json()
        assert bundle["diatonic_app"] == 1
        assert bundle["id"] == "http-app"

        # Delete app
        res = c.delete("/api/custom-apps/http-app")
        assert res.status_code == 200
        res = c.get("/api/custom-apps/http-app")
        assert res.status_code == 404


def test_custom_app_panel_tool_invocation(tmp_path: Path):
    from starlette.testclient import TestClient
    from diatonic.config import Config
    from diatonic.db import Database
    from diatonic.server import create_app

    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        # Create app
        c.post("/api/custom-apps", json={"id": "math-app", "name": "Math App"})

        # Write tools.py
        tools_code = """
from diatonic.apps.base import Tool, READ

def export_tools(ctx):
    async def multiply(args):
        return {"product": args.get("x", 1) * args.get("y", 1)}

    return [
        Tool("multiply", "Multiply numbers", {"properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}}, multiply, risk=READ)
    ]
"""
        c.put("/api/custom-apps/math-app/file", json={"path": "tools.py", "content": tools_code})

        # Test panel tool route
        res = c.post("/api/panel/tools/math-app/multiply", json={"x": 6, "y": 7})
        assert res.status_code != 200 and "product" not in res.text    # tools.py is agent-written code: it asks first, so a panel cannot run it


def test_ui_app_file_tools(env, monkeypatch):
    from diatonic.apps.ui import create as create_ui
    from diatonic.agent.session import RUN_CLIENT

    ctx, reg, _, db = env
    ctx.registry = reg
    ui = create_ui(ctx)
    reg.register(ui)

    token = RUN_CLIENT.set("client-1")
    try:
        import asyncio
        async def run_tools():
            # 1. create_file_app
            res = await ui.create_file_app({"id": "tracker", "name": "Habit Tracker", "description": "Track daily habits"})
            assert "Habit Tracker" in res

            # 2. list_app_files
            info = await ui.list_app_files({"app_id": "tracker"})
            assert "index.js" in info["files"]

            # 3. read_app_file
            content = await ui.read_app_file({"app_id": "tracker", "path": "index.js"})
            assert "Habit Tracker" in content

            # 4. patch_app_file
            res = await ui.patch_app_file({
                "app_id": "tracker",
                "path": "index.js",
                "old": 'ctx.h("h2", { class: "custom-app-title" }, "Habit Tracker")',
                "new": 'ctx.h("h2", { class: "custom-app-title" }, "Daily Habit Tracker")',
            })
            assert "Patched" in res

            # 5. write_app_file
            res = await ui.write_app_file({
                "app_id": "tracker",
                "path": "extra.txt",
                "content": "Habit list data",
            })
            assert "Saved" in res

            # 6. read_app_logs (initially empty)
            logs_msg = await ui.read_app_logs({"app_id": "tracker"})
            assert "No errors" in logs_msg

            # Log an error and verify
            ctx.fileapps.log_error("tracker", "Test error 123", source="runtime")
            logs = await ui.read_app_logs({"app_id": "tracker"})
            assert len(logs) == 1
            assert "Test error 123" in logs[0]["error"]

            # 7. export and import
            bundle = await ui.export_file_app({"app_id": "tracker"})
            assert bundle["diatonic_app"] == 1

            bundle["id"] = "tracker-copy"
            bundle["manifest"]["id"] = "tracker-copy"
            bundle["manifest"]["name"] = "Habit Tracker Copy"
            res = await ui.import_file_app({"bundle": bundle})
            assert "Imported" in res
            assert ctx.fileapps.exists("tracker-copy")

            # 8. delete_file_app
            del_res = await ui.delete_file_app({"app_id": "tracker-copy"})
            assert "deleted custom app" in del_res.lower()
            assert not ctx.fileapps.exists("tracker-copy")

        asyncio.run(run_tools())
    finally:
        RUN_CLIENT.reset(token)




def _ui(env):
    from diatonic.apps.ui import create as create_ui
    ctx, reg, _, _ = env
    ctx.registry = reg
    ui = create_ui(ctx)
    reg.register(ui)
    return ctx, ui


def test_a_write_that_will_fail_is_refused_before_the_approval_card(env):
    import asyncio
    import pytest
    from diatonic.apps.base import ToolError
    ctx, ui = _ui(env)
    ctx.fileapps.create_app("early-check", "Early Check", "x")
    try:
        bad = {"app_id": "early-check", "path": "index.js", "content": "console.log('no render here')"}
        with pytest.raises(ToolError, match="render"):
            asyncio.run(ui._write_file_preview(bad))          # the preview is what the card shows, so the user is never asked about this
    finally:
        ctx.fileapps.delete_app("early-check")


def test_a_patch_that_matches_nothing_is_an_error_not_an_empty_diff(env):
    import asyncio
    import pytest
    from diatonic.apps.base import ToolError
    ctx, ui = _ui(env)
    ctx.fileapps.create_app("patch-check", "Patch Check", "x")
    try:
        with pytest.raises(ToolError, match="not in index.js"):
            asyncio.run(ui._patch_file_preview({"app_id": "patch-check", "path": "index.js", "old": "does not exist anywhere", "new": "x"}))
        spaced = {"app_id": "patch-check", "path": "index.js", "old": 'ctx.h("h2",\n   { class: "custom-app-title" },   "Patch Check")', "new": 'ctx.h("h2", {}, "Renamed")'}
        assert "Renamed" in asyncio.run(ui._patch_file_preview(spaced))      # spacing differences are forgiven
    finally:
        ctx.fileapps.delete_app("patch-check")


def test_creating_an_app_does_not_read_as_a_finished_app(env):
    import asyncio
    from diatonic.agent.session import RUN_CLIENT
    ctx, ui = _ui(env)
    RUN_CLIENT.set("client-1")
    try:
        msg = asyncio.run(ui.create_file_app({"id": "placeholder-check", "name": "Placeholder Check"}))
        assert "NOT built" in msg and "write_app_file" in msg and "Done" not in msg
    finally:
        ctx.fileapps.delete_app("placeholder-check")


def test_a_placeholder_is_not_finished_by_a_second_create_or_by_saving_the_manifest(env):
    import asyncio
    from diatonic.agent.session import RUN_CLIENT
    from diatonic.apps.base import ToolError
    ctx, ui = _ui(env)
    RUN_CLIENT.set("client-1")
    try:
        asyncio.run(ui.create_file_app({"id": "twice", "name": "Twice"}))
        with pytest.raises(ToolError, match="empty placeholder.*write_app_file"):
            asyncio.run(ui.create_file_app({"id": "twice", "name": "Twice"}))
        msg = asyncio.run(ui.write_app_file({"app_id": "twice", "path": "manifest.json", "content": json.dumps(ctx.fileapps.get_app("twice"))}))
        assert "still the empty placeholder" in msg and "index.js" in msg and "can see it" not in msg
    finally:
        ctx.fileapps.delete_app("twice")


def test_checking_an_app_never_runs_its_tools_py(env, tmp_path):
    ctx, ui = _ui(env)
    ctx.fileapps.create_app("no-exec", "No Exec", "x")
    marker = tmp_path / "ran"
    try:
        ctx.fileapps.write_file("no-exec", "tools.py", f"open({str(marker)!r}, 'w').write('x')\ndef hello(a):\n    return 1\n")
        ctx.fileapps.write_file("no-exec", "manifest.json", '{"id": "no-exec", "name": "No Exec", "tools": [{"name": "hello"}]}')
        assert ctx.fileapps.declared_tool_names("no-exec") == ["hello"]
        assert not marker.exists()          # reading the names must not import the file
    finally:
        ctx.fileapps.delete_app("no-exec")


def test_reading_a_deleted_app_does_not_bring_its_folder_back(env):
    ctx, _ = _ui(env)
    ctx.fileapps.create_app("zombie-check", "Zombie Check", "x")
    ctx.fileapps.delete_app("zombie-check")
    folder = ctx.fileapps.root / "zombie-check"
    for read in (lambda: ctx.fileapps._safe_path("zombie-check", "style.css"), lambda: ctx.fileapps.get_logs("zombie-check"),
                 lambda: ctx.fileapps.read_file("zombie-check", "index.js")):
        try:
            read()
        except Exception:
            pass
    assert not folder.exists()


def test_a_write_to_a_deleted_app_is_refused_not_recreated(env):
    import pytest
    from diatonic.fileapps import FileAppError
    ctx, _ = _ui(env)
    with pytest.raises(FileAppError, match="does not exist"):
        ctx.fileapps.write_file("never-made", "index.js", "export async function render(el, ctx) {}")
    assert not (ctx.fileapps.root / "never-made").exists()


def test_deleting_an_app_drops_its_settings_and_empty_leftovers_are_pruned(env):
    ctx, _ = _ui(env)
    (ctx.fileapps.root / "left-over").mkdir()
    assert ctx.fileapps.prune_empty() == ["left-over"]
    seen = []
    ctx.fileapps.on_delete = seen.append
    ctx.fileapps.create_app("forget-me", "Forget Me", "x")
    ctx.fileapps.delete_app("forget-me")
    assert seen == ["forget-me"]


def test_the_agent_can_delete_an_app_by_the_name_the_user_used(env):
    import asyncio
    from diatonic.agent.session import RUN_CLIENT
    ctx, ui = _ui(env)
    RUN_CLIENT.set("client-1")
    ctx.fileapps.create_app("youtube-search", "YouTube Search", "x")
    assert "delete_file_app" in {t.name for t in ui.builder_tools()}
    assert ui.builder_session("c", "remove the YouTube search app", set(), None).app_id == "youtube-search"
    assert ui.builder_session("c", "delete youtube-search", set(), None).app_id == "youtube-search"
    from diatonic.apps.ui.builder import DELETE_APP
    assert not DELETE_APP.search("remove the field notes from my tracker app")      # a change inside an app, not removing the app
    asyncio.run(ui.delete_file_app({"app_id": "YouTube Search"}))
    assert not ctx.fileapps.exists("youtube-search")


def test_a_patch_says_where_the_old_value_is_still_shown(env):
    import asyncio
    from diatonic.agent.session import RUN_CLIENT
    ctx, ui = _ui(env)
    RUN_CLIENT.set("client-1")
    ctx.fileapps.create_app("left-behind", "Left Behind", "x")
    try:
        code = "export async function render(el, ctx) {\n  let left = 25 * 60;\n  const d = ctx.h('div', {}, '25:00');\n  el.append(d);\n}\n"
        ctx.fileapps.write_file("left-behind", "index.js", code)
        res = asyncio.run(ui.patch_app_file({"app_id": "left-behind", "path": "index.js", "old": "let left = 25 * 60;", "new": "let left = 10 * 60;"}))
        assert "Still in the file" in res and "25:00" in res
        clean = asyncio.run(ui.patch_app_file({"app_id": "left-behind", "path": "index.js", "old": "'25:00'", "new": "'10:00'"}))
        assert "Still in the file" not in clean
    finally:
        ctx.fileapps.delete_app("left-behind")


def test_an_edited_app_is_loaded_from_a_new_url_so_the_browser_does_not_serve_the_old_code(env):
    from diatonic.fileapps import FileApp
    ctx, _ = _ui(env)
    ctx.fileapps.create_app("fresh-url", "Fresh URL", "x")
    try:
        urls = []
        for _ in range(2):
            manifest = __import__("json").loads((ctx.fileapps.root / "fresh-url" / "manifest.json").read_text())
            urls.append(FileApp(ctx, ctx.fileapps.root / "fresh-url", manifest).module)
            ctx.fileapps.write_file("fresh-url", "index.js", "export async function render(el, ctx) { el.append('v' + %d); }" % len(urls))
        assert urls[0] != urls[1] and all(u.startswith("/custom-apps/fresh-url/index.js?v=") for u in urls)
    finally:
        ctx.fileapps.delete_app("fresh-url")


def test_tools_from_an_apps_own_python_always_need_approval(env):
    ctx, _ = _ui(env)
    ctx.fileapps.create_app("claims-read", "Claims Read", "x")
    try:
        ctx.fileapps.write_file("claims-read", "tools.py", "def wipe(args):\n    return 1\n")
        ctx.fileapps.write_file("claims-read", "manifest.json", '{"id": "claims-read", "name": "Claims Read", "tools": [{"name": "wipe", "risk": "read"}]}')
        tools = ctx.fileapps.load_tools("claims-read", ctx)
        assert [t.name for t in tools] == ["wipe"] and tools[0].needs_approval
    finally:
        ctx.fileapps.delete_app("claims-read")


def test_settings_for_an_app_that_no_longer_exists_are_dropped_at_start(tmp_path):
    from starlette.testclient import TestClient
    from diatonic.config import Config
    from diatonic.db import Database
    from diatonic.server import create_app
    cfg = Config(tmp_path / "c.json")
    cfg.data["apps"]["ghost-tracker"] = {"enabled": True, "settings": {}}
    cfg.data["apps"]["todos"] = {"enabled": False, "settings": {}}
    with TestClient(create_app(cfg, Database(tmp_path / "d.db"), allowed_hosts={"testserver"})):
        assert "ghost-tracker" not in cfg.data["apps"]
        assert cfg.data["apps"]["todos"]["enabled"] is False          # a real app keeps its settings
