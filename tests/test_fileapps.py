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
        store.write_file("val-app", "style.css", "body { background: url('https://evil.com/bg.png'); }")

    # Bad JSON
    with pytest.raises(FileAppError, match="Invalid JSON"):
        store.write_file("val-app", "data.json", "{ unclosed json")

    # Bad Python syntax
    with pytest.raises(FileAppError, match="Python syntax error"):
        store.write_file("val-app", "tools.py", "def broken(:")


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
        assert res.status_code == 200
        assert res.json()["result"]["product"] == 42


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

        asyncio.run(run_tools())
    finally:
        RUN_CLIENT.reset(token)


