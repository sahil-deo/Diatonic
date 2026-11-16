"""File-based custom apps: folders with manifest.json, index.js, style.css and optional Python tools.

Stored in ~/.diatonic/apps/<app-id>/. Allows surgical file edits (patch/write), hot reload in the workspace,
runtime error logs for agent self-debugging, custom agent tools, and portable export/import bundles.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
import logging
import re
import shutil
import time
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Any, Callable

from .apps.base import App, Tool, ToolError, READ, WRITE
from .mods import check_css, ModError

log = logging.getLogger("diatonic.fileapps")

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
MAX_FILE_SIZE = 100_000  # 100 KB max per file
MAX_LOGS = 50

STARTER_INDEX_JS = """// {name} custom app panel
export async function render(el, ctx) {{
  el.replaceChildren();
  const wrap = ctx.h("div", {{ class: "custom-app-container" }},
    ctx.h("h2", {{ class: "custom-app-title" }}, "{name}"),
    ctx.h("p", {{ class: "p-empty" }}, "{description}")
  );
  el.append(wrap);
}}
"""

STARTER_STYLE_CSS = """/* Styles for {name} */
.custom-app-container {{
  padding: 16px;
}}
.custom-app-title {{
  font-size: 18px;
  font-weight: 600;
  margin-bottom: 8px;
}}
"""


class FileAppError(Exception):
    pass


def validate_app_id(app_id: str) -> str:
    slug = (app_id or "").strip().lower().replace(" ", "-").replace("_", "-")
    if not NAME.fullmatch(slug):
        raise FileAppError("App id must be 1-40 lowercase letters, digits, or dashes (e.g. 'pomodoro-timer').")
    return slug


class FileApp(App):
    """An app dynamically loaded from a directory in ~/.diatonic/apps/<id>/."""

    def __init__(self, ctx, app_dir: Path, manifest: dict, custom_tools: list[Tool] | None = None):
        super().__init__(ctx)
        self.app_dir = Path(app_dir)
        self.manifest = manifest
        self.id = manifest.get("id", app_dir.name)
        self.name = manifest.get("name") or self.id.replace("-", " ").title()
        self.icon = manifest.get("icon") or "box"
        self.description = manifest.get("description") or ""
        self.panel = True  # File-based apps provide a UI panel
        self.keywords = [str(k) for k in manifest.get("keywords", [])]
        self.private = bool(manifest.get("private", False))
        self.file_app = True
        self.module = f"/custom-apps/{self.id}/index.js"
        self.styles = f"/custom-apps/{self.id}/style.css"
        self._tools_list = custom_tools or []

    def tools(self) -> list[Tool]:
        return list(self._tools_list)


class FileAppStore:
    def __init__(self, folder: Path):
        self.root = Path(folder)
        self.root.mkdir(parents=True, exist_ok=True)
        self.on_change: Callable[[str], None] = lambda app_id: None
        self._logs: dict[str, list[dict]] = {}
        self.errors: dict[str, str] = {}

    def _safe_path(self, app_id: str, relative_path: str) -> Path:
        """Resolve a relative path inside an app folder, strictly forbidding traversal outside."""
        clean_id = validate_app_id(app_id)
        app_dir = (self.root / clean_id).resolve()
        app_dir.mkdir(parents=True, exist_ok=True)
        target = (app_dir / relative_path).resolve()
        if not target.is_relative_to(app_dir):
            raise FileAppError("Path escapes app directory.")
        return target

    def app_dir(self, app_id: str) -> Path:
        clean_id = validate_app_id(app_id)
        return self.root / clean_id

    def exists(self, app_id: str) -> bool:
        clean_id = validate_app_id(app_id)
        return (self.root / clean_id / "manifest.json").is_file()

    # ---- listing and metadata -------------------------------------------
    def list_apps(self) -> list[dict]:
        out = []
        if not self.root.is_dir():
            return out
        for child in sorted(self.root.iterdir()):
            if not child.is_dir():
                continue
            manifest_path = child / "manifest.json"
            if not manifest_path.is_file():
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                files = [p.relative_to(child).as_posix() for p in child.rglob("*") if p.is_file()]
                tools_list = [t.get("name") for t in manifest.get("tools", []) if isinstance(t, dict)]
                out.append({
                    "id": child.name,
                    "name": manifest.get("name", child.name),
                    "icon": manifest.get("icon", "box"),
                    "description": manifest.get("description", ""),
                    "version": manifest.get("version", 1),
                    "files": files,
                    "tools": tools_list,
                    "error": self.errors.get(child.name),
                })
            except Exception as e:
                out.append({
                    "id": child.name,
                    "name": child.name,
                    "icon": "box",
                    "description": "",
                    "version": 0,
                    "files": [],
                    "tools": [],
                    "error": str(e),
                })
        return out

    def get_app(self, app_id: str) -> dict | None:
        clean_id = validate_app_id(app_id)
        app_dir = self.root / clean_id
        manifest_path = app_dir / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            files = [p.relative_to(app_dir).as_posix() for p in app_dir.rglob("*") if p.is_file()]
            return {
                "id": clean_id,
                "manifest": manifest,
                "files": files,
                "error": self.errors.get(clean_id),
            }
        except Exception as e:
            return {
                "id": clean_id,
                "manifest": {},
                "files": [],
                "error": str(e),
            }

    # ---- scaffolding and file management -------------------------------
    def create_app(self, app_id: str, name: str, description: str = "", icon: str = "box") -> dict:
        clean_id = validate_app_id(app_id)
        app_dir = self.root / clean_id
        if (app_dir / "manifest.json").is_file():
            raise FileAppError(f"App '{clean_id}' already exists.")

        app_dir.mkdir(parents=True, exist_ok=True)
        clean_name = (name or clean_id).strip()[:60]
        clean_desc = (description or "")[:300]
        clean_icon = (icon or "box").strip().lower()

        manifest = {
            "id": clean_id,
            "name": clean_name,
            "icon": clean_icon,
            "description": clean_desc,
            "version": 1,
            "entry": "index.js",
            "styles": "style.css",
            "tools": [],
            "keywords": [w.lower() for w in clean_name.split() if len(w) > 2],
        }

        (app_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        (app_dir / "index.js").write_text(STARTER_INDEX_JS.format(name=clean_name, description=clean_desc), encoding="utf-8")
        (app_dir / "style.css").write_text(STARTER_STYLE_CSS.format(name=clean_name), encoding="utf-8")

        self.errors.pop(clean_id, None)
        try:
            self.on_change(clean_id)
        except Exception:
            pass
        return manifest

    def read_file(self, app_id: str, relative_path: str) -> str:
        target = self._safe_path(app_id, relative_path)
        if not target.is_file():
            raise FileAppError(f"File '{relative_path}' not found in app '{app_id}'.")
        return target.read_text(encoding="utf-8")

    def _validate_content(self, relative_path: str, content: str) -> None:
        if len(content) > MAX_FILE_SIZE:
            raise FileAppError(f"File size exceeds maximum allowed ({MAX_FILE_SIZE:,} characters).")
        ext = Path(relative_path).suffix.lower()
        if ext == ".css":
            try:
                check_css(content)
            except ModError as e:
                raise FileAppError(str(e))
        elif ext == ".json":
            try:
                json.loads(content)
            except ValueError as e:
                raise FileAppError(f"Invalid JSON: {e}")
        elif ext == ".py":
            try:
                ast.parse(content)
            except SyntaxError as e:
                raise FileAppError(f"Python syntax error on line {e.lineno}: {e.msg}")

    def write_file(self, app_id: str, relative_path: str, content: str) -> dict:
        clean_id = validate_app_id(app_id)
        target = self._safe_path(clean_id, relative_path)
        self._validate_content(relative_path, content)

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        # Bump version in manifest.json if a file changed
        manifest_path = self.root / clean_id / "manifest.json"
        if manifest_path.is_file() and relative_path != "manifest.json":
            try:
                m = json.loads(manifest_path.read_text(encoding="utf-8"))
                m["version"] = m.get("version", 1) + 1
                manifest_path.write_text(json.dumps(m, indent=2), encoding="utf-8")
            except Exception:
                pass

        self.errors.pop(clean_id, None)
        try:
            self.on_change(clean_id)
        except Exception:
            pass
        return {"id": clean_id, "path": relative_path, "bytes": len(content)}

    def patch_file(self, app_id: str, relative_path: str, old: str, new: str) -> dict:
        target = self._safe_path(app_id, relative_path)
        if not target.is_file():
            raise FileAppError(f"File '{relative_path}' not found in app '{app_id}'.")

        text = target.read_text(encoding="utf-8")
        if old not in text:
            raise FileAppError(f"Could not find the target text to replace in '{relative_path}'.")

        count = text.count(old)
        if count > 1:
            raise FileAppError(f"Target text appears {count} times in '{relative_path}'. Provide a more specific snippet.")

        updated = text.replace(old, new, 1)
        return self.write_file(app_id, relative_path, updated)

    def delete_file(self, app_id: str, relative_path: str) -> bool:
        if relative_path in ("manifest.json", "index.js"):
            raise FileAppError(f"Cannot delete core file '{relative_path}'. Delete the whole app instead.")
        target = self._safe_path(app_id, relative_path)
        if not target.is_file():
            raise FileAppError(f"File '{relative_path}' not found.")
        target.unlink()
        try:
            self.on_change(app_id)
        except Exception:
            pass
        return True

    def delete_app(self, app_id: str) -> bool:
        clean_id = validate_app_id(app_id)
        app_dir = self.root / clean_id
        if not app_dir.is_dir():
            raise FileAppError(f"App '{clean_id}' does not exist.")
        shutil.rmtree(app_dir)
        self.errors.pop(clean_id, None)
        self._logs.pop(clean_id, None)
        try:
            self.on_change(clean_id)
        except Exception:
            pass
        return True

    # ---- logging and diagnostics for self-debugging -------------------
    def log_error(self, app_id: str, error: str, source: str = "runtime") -> None:
        clean_id = validate_app_id(app_id)
        entry = {
            "timestamp": time.time(),
            "time_iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "source": source,
            "error": str(error)[:500],
        }
        logs = self._logs.setdefault(clean_id, [])
        logs.append(entry)
        if len(logs) > MAX_LOGS:
            self._logs[clean_id] = logs[-MAX_LOGS:]
        self.errors[clean_id] = str(error)[:200]

    def get_logs(self, app_id: str) -> list[dict]:
        clean_id = validate_app_id(app_id)
        return list(self._logs.get(clean_id, []))

    def clear_logs(self, app_id: str) -> None:
        clean_id = validate_app_id(app_id)
        self._logs.pop(clean_id, None)
        self.errors.pop(clean_id, None)

    # ---- python tools loading ------------------------------------------
    def load_tools(self, app_id: str, ctx) -> list[Tool]:
        """Loads custom python tools from tools.py if present."""
        clean_id = validate_app_id(app_id)
        app_dir = self.root / clean_id
        tools_path = app_dir / "tools.py"
        manifest_path = app_dir / "manifest.json"
        if not tools_path.is_file() or not manifest_path.is_file():
            return []

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            return []

        try:
            source = tools_path.read_text(encoding="utf-8")
            ast.parse(source)

            spec = importlib.util.spec_from_file_location(f"diatonic_custom_{clean_id}", str(tools_path))
            if not spec or not spec.loader:
                return []
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)

            # Option 1: Module defines export_tools(ctx)
            if hasattr(mod, "export_tools") and callable(mod.export_tools):
                tools = mod.export_tools(ctx)
                if isinstance(tools, list):
                    return [t for t in tools if isinstance(t, Tool)]

            # Option 2: Tools declared in manifest.json mapping to functions in tools.py
            tools = []
            for tdef in manifest.get("tools", []):
                if not isinstance(tdef, dict):
                    continue
                name = tdef.get("name")
                if not name or not hasattr(mod, name):
                    continue
                fn = getattr(mod, name)
                if not callable(fn):
                    continue

                is_async = iscoroutinefunction(fn)

                async def make_handler(func, is_coro):
                    async def handler(args: dict):
                        try:
                            if is_coro:
                                return await func(args)
                            return await asyncio.to_thread(func, args)
                        except ToolError:
                            raise
                        except Exception as ex:
                            raise ToolError(f"{name} failed: {ex}")
                    return handler

                handler = asyncio.run(make_handler(fn, is_async)) if asyncio.get_event_loop().is_running() else None
                # synchronous setup for handler
                async def _exec(a, _fn=fn, _is_async=is_async):
                    if _is_async:
                        return await _fn(a)
                    return await asyncio.to_thread(_fn, a)

                risk = WRITE if tdef.get("risk") == "write" else READ
                tools.append(Tool(
                    name=name,
                    description=tdef.get("description") or (fn.__doc__ or "").strip() or f"Tool {name}",
                    parameters=tdef.get("parameters") or {"type": "object", "properties": {}},
                    handler=_exec,
                    risk=risk,
                    title=tdef.get("title") or name.replace("_", " ").title(),
                ))
            return tools
        except Exception as e:
            log.exception("Failed to load tools for %s", clean_id)
            self.log_error(clean_id, f"tools.py load error: {e}", source="python_tools")
            return []

    # ---- export and import ----------------------------------------------
    def export_app(self, app_id: str) -> dict:
        clean_id = validate_app_id(app_id)
        app_dir = self.root / clean_id
        manifest_path = app_dir / "manifest.json"
        if not manifest_path.is_file():
            raise FileAppError(f"App '{clean_id}' does not exist.")

        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        files = {}
        for p in sorted(app_dir.rglob("*")):
            if p.is_file() and p.name != "manifest.json":
                rel = p.relative_to(app_dir).as_posix()
                try:
                    files[rel] = p.read_text(encoding="utf-8")
                except UnicodeDecodeError:
                    pass  # skip binary files for text bundles

        return {
            "diatonic_app": 1,
            "id": clean_id,
            "manifest": manifest,
            "files": files,
        }

    def import_app(self, bundle: dict, overwrite: bool = False) -> dict:
        if not isinstance(bundle, dict) or bundle.get("diatonic_app") != 1:
            raise FileAppError("Invalid app bundle. Expected 'diatonic_app': 1.")

        manifest = bundle.get("manifest")
        if not isinstance(manifest, dict) or not manifest.get("name"):
            raise FileAppError("Bundle manifest is missing or invalid.")

        app_id = validate_app_id(bundle.get("id") or manifest.get("id"))
        app_dir = self.root / app_id
        if app_dir.is_dir() and not overwrite:
            raise FileAppError(f"App '{app_id}' already exists. Pass overwrite=True to replace it.")

        # Validate all files before creating anything
        files = bundle.get("files") or {}
        if not isinstance(files, dict):
            raise FileAppError("Bundle 'files' must be a dictionary of relative paths to contents.")

        for rel, content in files.items():
            # Check path safety
            clean_rel = str(rel).replace("\\", "/")
            if ".." in clean_rel.split("/") or clean_rel.startswith("/"):
                raise FileAppError(f"Illegal path '{rel}' in bundle.")
            self._validate_content(clean_rel, str(content))

        # Safe to unpack
        if app_dir.is_dir() and overwrite:
            shutil.rmtree(app_dir)
        app_dir.mkdir(parents=True, exist_ok=True)

        manifest["id"] = app_id
        (app_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        for rel, content in files.items():
            dest = app_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(str(content), encoding="utf-8")

        self.errors.pop(app_id, None)
        try:
            self.on_change(app_id)
        except Exception:
            pass
        return {"id": app_id, "files": list(files.keys())}
