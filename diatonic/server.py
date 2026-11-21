"""HTTP server: JSON API, SSE chat stream, static UI."""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import jsonschema
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .agent import approvals
from .actions import ActionRunner, clean_actions, clean_appearance, export_bundle, import_bundle
from .agent.guards import collapse_repeats
from .agent.runs import RunBusy, RunManager
from . import keymap
from . import macros
from . import layouts
from .agent.antigravity import Connector as AntigravityConnector
from .mcp import McpHub, endpoint as mcp_endpoint
from .mods import ModError
from . import models as M
from . import secrets
from .apps.base import ToolError
from . import appfetch, appperms, instructions
from .fileapps import FileAppError, validate_app_id
from .catalog import KINDS
from .agent.providers import ProviderError, make_provider
from .apps.registry import Registry
from .config import Config, home
from .context import Context
from .db import Database
from .google import auth as google_auth
from .scheduler.engine import Scheduler
from .scheduler.scripts import ScriptsDisabled
from .scheduler.notify import Notifier
from .scheduler.store import Store
from .voice.models import VoiceSetup
from .voice.stt import STT
from .voice.tts import TTS

WEB = Path(__file__).parent / "web"
MAX_AUDIO = 8 * 1024 * 1024  # ~4 minutes of 16 kHz mono int16


class LocalOnly:
    """Blocks DNS-rebinding (Host check) and cross-site writes (Origin check)."""

    def __init__(self, app, allowed_hosts: set[str]):
        self.app, self.hosts = app, allowed_hosts

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            h = {k.decode().lower(): v.decode() for k, v in scope["headers"]}
            host = h.get("host", "")
            anyhost = "*" in self.hosts   # opt-in via server.allowed_hosts; the Origin must then still match the Host
            bad = not host if anyhost else host not in self.hosts
            origin = h.get("origin")
            if not bad and scope["method"] not in ("GET", "HEAD", "OPTIONS") and origin:
                o = origin.split("://", 1)[-1]
                bad = o != host if anyhost else o not in self.hosts
            if bad:
                await Response("Forbidden", status_code=403)(scope, receive, send)
                return
        await self.app(scope, receive, send)


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        r = await super().get_response(path, scope)
        r.headers["Cache-Control"] = "no-cache"
        return r


def sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def create_app(config: Config | None = None, db: Database | None = None, *,
               allowed_hosts: set[str] | None = None, registry: Registry | None = None, http=None) -> Starlette:
    config = config or Config()
    db = db or Database(home() / "diatonic.db")
    ctx = Context(config, db, http)
    runs = RunManager()
    store = Store(db)
    ctx.broadcast = runs.broadcast
    ctx.send_ui = runs.send_ui
    ctx.notifier = Notifier(ctx, store, runs.broadcast)
    registry = registry or Registry(ctx).discover()
    ctx.registry = registry
    ctx.mods.on_layout_revert = lambda layout: ctx.broadcast({"type": "undo_layout", "tree": layout})
    def forget_file_app(app_id: str) -> None:
        """A deleted app leaves nothing behind in settings: its switch, rail slot, status bar pin and home tile."""
        d = ctx.config.data
        d["apps"].pop(app_id, None)
        ap = d["appearance"]
        for key in ("rail_hidden", "rail_order"):
            ap[key] = [x for x in ap.get(key, []) if x != app_id]
        ap["statusbar_pins"] = [p for p in ap.get("statusbar_pins", []) if not (isinstance(p, dict) and p.get("appId") == app_id)]
        home = ap.get("home") or {}
        home["slots"] = [x for x in home.get("slots", []) if x != app_id and not (isinstance(x, dict) and app_id in (x.get("app"), x.get("id")))]
        ctx.config.save()
        ctx.appstore.drop_app(app_id)
        ctx.apptimers.drop_app(app_id)

    ctx.fileapps.on_delete = forget_file_app

    def drop_orphan_settings() -> None:
        """Settings for an app that no longer exists (a tracker or API app removed, a folder deleted by hand): nothing else would ever clear them."""
        known = set(registry.apps) | set(ctx.mods.data.get("mods", {})) | {a["id"] for a in ctx.fileapps.list_apps()}
        gone = [k for k in ctx.config.data["apps"] if k not in known]
        for k in gone:
            forget_file_app(k)
    ctx.fileapps.prune_empty()
    ctx.fileapps.on_change = lambda app_id: (registry.sync_file_apps(), ctx.broadcast({"type": "custom_app_changed", "id": app_id}))
    registry.sync_file_apps()
    drop_orphan_settings()
    ctx.scheduler = Scheduler(ctx, registry, runs, ctx.notifier, store)
    action_runner = ActionRunner(ctx)
    stt, tts = STT(), TTS()
    setup = VoiceSetup(stt, tts)
    port = config.data["server"]["port"]
    extra = {str(x).strip().lower() for x in config.data["server"].get("allowed_hosts", []) if str(x).strip()}
    hosts = allowed_hosts or {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"} | extra | {f"{x}:{port}" for x in extra}

    def app_or_404(app_id: str):
        a = registry.apps.get(app_id)
        if not a:
            raise HTTPException(404, "Unknown app")
        return a

    def public_config() -> dict:
        return config.data

    # ---- state -----------------------------------------------------------
    async def app_info(a) -> dict:
        cfg = ctx.app_cfg(a.id)
        fields = [{**f, "set": bool(a.secret(f["key"])) if f.get("secret") else None,
                   "value": None if f.get("secret") else cfg["settings"].get(f["key"], "")} for f in a.settings]
        return {"id": a.id, "name": a.name, "icon": a.icon, "description": a.description, "panel": a.panel, "module": getattr(a, "module", None),
                "file_app": bool(getattr(a, "file_app", False)), "styles": getattr(a, "styles", None),
                "private": registry.is_private(a),
                "enabled": cfg["enabled"], "fields": fields, "status": await a.status() if cfg["enabled"] else None,
                "tools": [{"name": t.name, "title": t.title, "risk": t.risk, "description": t.description,
                           "always_allow": t.name in (a.default_allow if cfg.get("always_allow") is None else cfg["always_allow"])}
                          for t in a.tools()]}

    async def get_state(request):
        apps = list(await asyncio.gather(*(app_info(a) for a in registry.apps.values())))
        return JSONResponse({"config": public_config(), "apps": apps, "app_errors": registry.errors})

    async def get_apps(request):
        return JSONResponse(list(await asyncio.gather(*(app_info(a) for a in registry.apps.values()))))

    async def health(request):
        agent = await make_provider(ctx).health()
        voice = config.data["voice"]
        act = M.active(config)
        return JSONResponse({
            "agent": {**agent, "model": config.data["agent"]["model"], "provider": config.data["agent"]["provider"],
                      "label": act["label"], "cloud": M.is_cloud(act), "model_id": act["id"]},
            "voice": {"stt": {"available": stt.available(), "ready": stt.ready(voice["stt_model"]),
                              "model": voice["stt_model"]},
                      "tts": {"available": tts.available(), "ready": tts.ready(voice["tts_voice"]),
                              "voice": voice["tts_voice"]},
                      "setup": setup.state}})

    async def put_config(request):
        patch = await request.json()
        if "appearance" in patch:
            try:
                patch["appearance"] = clean_appearance(patch["appearance"])
            except (ValueError, TypeError) as e:
                return JSONResponse({"error": str(e)}, 400)
        patch.pop("actions", None)  # actions go through the validating endpoint
        if "antigravity" in patch:
            if isinstance(patch["antigravity"], dict):
                patch["antigravity"].pop("command", None)   # names a program Diatonic starts: only the config file may set it, never a request
            else:
                patch.pop("antigravity", None)
        return JSONResponse(config.update(patch))

    # ---- interface mods (the agent's own changes to the UI) ----------------------
    def mods_changed(summary: str) -> None:
        ctx.broadcast({"type": "mods_changed", "by": None, "summary": summary})

    async def mods_list(request):
        return JSONResponse(ctx.mods.list())

    async def mods_set(request):
        try:
            ctx.mods.set_enabled(request.path_params["name"], bool((await request.json()).get("enabled")))
        except ModError as e:
            return JSONResponse({"error": str(e)}, 404)
        mods_changed("changed a mod")
        return JSONResponse({"ok": True})

    async def mods_delete(request):
        try:
            ctx.mods.remove(request.path_params["name"])
        except ModError as e:
            return JSONResponse({"error": str(e)}, 404)
        mods_changed("removed a mod")
        return JSONResponse({"ok": True})

    async def mods_revert(request):
        try:
            what = ctx.mods.revert(config)
        except ModError as e:
            return JSONResponse({"error": str(e)}, 400)
        mods_changed(what)
        return JSONResponse({"ok": True, "what": what})

    NOTIFY_PER_MINUTE = 20
    notify_times: dict[str, list[float]] = {}

    def file_app_id(request) -> str | None:
        """The file app a panel call comes from (the page says so in X-Diatonic-App), or None when it is not an existing file app."""
        try:
            app_id = validate_app_id(request.headers.get("x-diatonic-app", ""))
        except FileAppError:
            return None
        return app_id if ctx.fileapps.exists(app_id) else None

    async def panel_tool(request):
        """A panel the user approved may read through an app's read-only tools (calendar events, notes, to-dos...). A file app that declared
        a tool in its manifest permissions may also call one that changes things, but every such call shows an approval card naming the app.
        The terminal and the interface tools are never available to panels."""
        app = registry.apps.get(request.path_params["app_id"])
        if app is None or app not in registry.enabled() or app.id in ("shell", "ui"):
            return JSONResponse({"error": "No such app."}, 404)
        tool = next((t for t in app.tools() if t.name == request.path_params["tool"]), None)
        if tool is None:
            return JSONResponse({"error": f"{app.name} has no tool '{request.path_params['tool']}'."}, 404)
        caller = file_app_id(request)
        if tool.changes and caller is None:
            return JSONResponse({"error": f"{tool.title} changes things. A panel can only read; ask the agent to do it."}, 403)
        args = await request.json()
        try:
            jsonschema.validate(args, tool.parameters)
            if tool.changes:
                await appperms.require(ctx, caller, "tools", f"{app.id}_{tool.name}")
                if not await ask_tool(caller, app, tool, args):
                    return JSONResponse({"error": f"You did not allow that: {tool.title}."}, 403)
            result = await asyncio.wait_for(tool.handler(args), tool.timeout or 60)
        except jsonschema.ValidationError as e:
            return JSONResponse({"error": f"Invalid arguments: {e.message}"}, 400)
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)
        return Response(json.dumps({"result": result}, default=str), media_type="application/json")

    async def ask_tool(caller: str, app, tool, args) -> bool:
        """The approval card for one change a panel asks for: the same words the chat card would use, plus which app is asking."""
        name = next((a["name"] for a in ctx.fileapps.list_apps() if a["id"] == caller), caller)
        aid = "ptool_" + uuid.uuid4().hex[:12]
        fut = approvals.request(aid)
        describe = tool.describe(args) if tool.describe else tool.title
        ctx.broadcast({"type": "tool_ask", "id": aid, "app": caller, "name": name, "describe": describe, "risk": tool.risk})
        try:
            return bool(await asyncio.wait_for(fut, appperms.ASK_TIMEOUT))
        except asyncio.TimeoutError:
            return False
        finally:
            approvals.discard(aid)
            ctx.broadcast({"type": "perm_ask_done", "id": aid})

    async def app_notify(request):
        """ctx.notify: an app tells the user something. It only notifies, so it needs no approval, but it must be declared and is rate limited."""
        try:
            app_id = validate_app_id(request.path_params["app_id"])
            if not ctx.fileapps.exists(app_id):
                return JSONResponse({"error": f"There is no app '{app_id}'."}, 404)
            a = await request.json()
            title, body = str((a or {}).get("title") or "").strip(), str((a or {}).get("body") or "").strip()
            if not title:
                return JSONResponse({"error": "ctx.notify needs a title: ctx.notify(\"Done\", \"The timer ended\")."}, 400)
            await appperms.require(ctx, app_id, "notify")
            now = time.time()
            recent = notify_times[app_id] = [t for t in notify_times.get(app_id, []) if now - t < 60]
            if len(recent) >= NOTIFY_PER_MINUTE:
                return JSONResponse({"error": f"An app can send {NOTIFY_PER_MINUTE} notifications a minute. Wait a moment."}, 429)
            recent.append(now)
            row = await ctx.notifier.send(title, body, kind="info", data={"app": app_id})
            return JSONResponse({"result": {"id": row["id"]}})
        except (ToolError, FileAppError) as e:
            return JSONResponse({"error": str(e)}, 400)
        except (ValueError, AttributeError):
            return JSONResponse({"error": "Send a JSON object with a title and a body."}, 400)

    async def app_timer(request):
        """ctx.timer: start, pause, resume, cancel and list an app's countdowns (they run on the server; see apptimers.py)."""
        op = request.path_params["op"]
        try:
            app_id = validate_app_id(request.path_params["app_id"])
            if not ctx.fileapps.exists(app_id):
                return JSONResponse({"error": f"There is no app '{app_id}'."}, 404)
            a, t = await request.json(), ctx.apptimers
            if not isinstance(a, dict):
                return JSONResponse({"error": "Send a JSON object."}, 400)
            ops = {"start": lambda: t.start(app_id, a.get("seconds"), a.get("label", "")), "pause": lambda: t.pause(app_id, a.get("id")),
                   "resume": lambda: t.resume(app_id, a.get("id")), "cancel": lambda: t.cancel(app_id, a.get("id")), "list": lambda: t.list(app_id)}
            if op not in ops:
                return JSONResponse({"error": f"Unknown timer call '{op}'. Use: {', '.join(ops)}."}, 404)
            await appperms.require(ctx, app_id, "timer")
            return JSONResponse({"result": ops[op]()})
        except (ToolError, FileAppError) as e:
            return JSONResponse({"error": str(e)}, 400)
        except ValueError:
            return JSONResponse({"error": "Send a JSON object."}, 400)

    async def app_store(request):
        """ctx.store: a file app's own data (key/value and tables). Plain data, so no approval; only apps that exist have a store."""
        op = request.path_params["op"]
        try:
            app_id = validate_app_id(request.path_params["app_id"])   # the same spelling the folder uses, so "My_App" and "my-app" are one store
            if not ctx.fileapps.exists(app_id):
                return JSONResponse({"error": f"There is no app '{app_id}' to store data for."}, 404)
            a, s = await request.json(), ctx.appstore
            if not isinstance(a, dict):
                return JSONResponse({"error": "Send a JSON object."}, 400)
            ops = {
                "get": lambda: s.get(app_id, a.get("key"), a.get("default")),
                "set": lambda: s.set(app_id, a.get("key"), a.get("value")),
                "keys": lambda: s.keys(app_id),
                "remove": lambda: s.remove(app_id, a.get("key")),
                "add": lambda: s.add(app_id, a.get("table"), a.get("row")),
                "addMany": lambda: s.add_many(app_id, a.get("table"), a.get("rows")),
                "update": lambda: s.update(app_id, a.get("table"), a.get("id"), a.get("patch")),
                "updateMany": lambda: s.update_many(app_id, a.get("table"), a.get("rows")),
                "delete": lambda: s.delete(app_id, a.get("table"), a.get("ids", a.get("id"))),
                "query": lambda: s.query(app_id, a.get("table"), a.get("where"), a.get("order", ""), a.get("limit", 200), a.get("offset", 0)),
                "tables": lambda: s.tables(app_id),
            }
            if op not in ops:
                return JSONResponse({"error": f"Unknown store call '{op}'. Use: {', '.join(ops)}."}, 404)
            return JSONResponse({"result": ops[op]()})
        except (ToolError, FileAppError) as e:
            return JSONResponse({"error": str(e)}, 400)
        except (ValueError, TypeError):
            return JSONResponse({"error": "That call has a wrong argument type. Check the names and types in the docs."}, 400)

    async def app_fetch(request):
        """ctx.fetch: read a web address for a file app (GET only). The domain must be declared in the manifest and approved by the user; see appfetch.py and appperms.py."""
        try:
            app_id = validate_app_id(request.path_params["app_id"])
            if not ctx.fileapps.exists(app_id):
                return JSONResponse({"error": f"There is no app '{app_id}'."}, 404)
            a = await request.json()
            if not isinstance(a, dict):
                return JSONResponse({"error": "Send a JSON object with a url."}, 400)
            return JSONResponse({"result": await appfetch.fetch(ctx, app_id, a.get("url"), a.get("method", "GET"))})
        except (ToolError, FileAppError) as e:
            return JSONResponse({"error": str(e)}, 400)
        except ValueError:
            return JSONResponse({"error": "Send a JSON object with a url."}, 400)

    async def mods_verified(request):
        """The window's report on code it test-ran for the agent (see UiApp._verify)."""
        ok = registry.apps["ui"].resolve_verify(request.path_params["rid"], await request.json())
        return JSONResponse({"ok": ok})

    # ---- custom file apps: apps as folders in ~/.diatonic/apps/<id>/ ---------------------
    async def custom_app_file(request):
        app_id = request.path_params["app_id"]
        rel_path = request.path_params.get("path", "index.js")
        try:
            target = ctx.fileapps._safe_path(app_id, rel_path)
            if not target.is_file():
                return Response("File not found", status_code=404)
            ext = target.suffix.lower()
            media = "text/javascript" if ext == ".js" else "text/css" if ext == ".css" else "application/json" if ext == ".json" else "text/plain"
            return Response(target.read_bytes(), media_type=media, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        except Exception as e:
            return Response(str(e), status_code=400)

    async def custom_apps_list(request):
        return JSONResponse(ctx.fileapps.list_apps())

    async def custom_apps_create(request):
        body = await request.json()
        try:
            manifest = ctx.fileapps.create_app(body.get("id"), body.get("name"), body.get("description", ""), body.get("icon", "box"))
            return JSONResponse(manifest, status_code=201)
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    async def custom_apps_get(request):
        app_id = request.path_params["app_id"]
        app_data = ctx.fileapps.get_app(app_id)
        if not app_data:
            return JSONResponse({"error": "App not found"}, 404)
        return JSONResponse(app_data)

    async def custom_apps_delete(request):
        app_id = request.path_params["app_id"]
        try:
            ctx.fileapps.delete_app(app_id)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    async def custom_apps_get_file(request):
        app_id = request.path_params["app_id"]
        path = request.query_params.get("path", "")
        if not path:
            return JSONResponse({"error": "Missing path parameter"}, 400)
        try:
            content = ctx.fileapps.read_file(app_id, path)
            return JSONResponse({"content": content, "path": path})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 404 if "not found" in str(e).lower() else 400)

    async def custom_apps_put_file(request):
        app_id = request.path_params["app_id"]
        body = await request.json()
        path = body.get("path")
        content = body.get("content")
        if not path or content is None:
            return JSONResponse({"error": "Path and content are required"}, 400)
        try:
            res = ctx.fileapps.write_file(app_id, path, content)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    async def custom_apps_patch_file(request):
        app_id = request.path_params["app_id"]
        body = await request.json()
        path = body.get("path")
        old = body.get("old")
        new = body.get("new")
        if not path or old is None or new is None:
            return JSONResponse({"error": "path, old, and new are required"}, 400)
        try:
            res = ctx.fileapps.patch_file(app_id, path, old, new)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    async def custom_apps_delete_file(request):
        app_id = request.path_params["app_id"]
        path = request.query_params.get("path", "")
        if not path:
            return JSONResponse({"error": "Missing path parameter"}, 400)
        try:
            ctx.fileapps.delete_file(app_id, path)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    async def custom_apps_get_logs(request):
        app_id = request.path_params["app_id"]
        return JSONResponse(ctx.fileapps.get_logs(app_id))

    async def custom_apps_post_logs(request):
        app_id = request.path_params["app_id"]
        body = await request.json()
        error = body.get("error", "")
        source = body.get("source", "client_runtime")
        ctx.fileapps.log_error(app_id, error, source)
        return JSONResponse({"ok": True})

    async def custom_apps_export(request):
        app_id = request.path_params["app_id"]
        try:
            bundle = ctx.fileapps.export_app(app_id)
            return JSONResponse(bundle, headers={"Content-Disposition": f"attachment; filename={app_id}.diatonic-app.json"})
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    async def custom_apps_import(request):
        body = await request.json()
        bundle = body.get("bundle", body)
        overwrite = bool(body.get("overwrite", False))
        try:
            res = ctx.fileapps.import_app(bundle, overwrite=overwrite)
            return JSONResponse(res)
        except Exception as e:
            return JSONResponse({"error": str(e)}, 400)

    # ---- keymap: the user's key bindings, kept in keymap.json in the Diatonic home folder ----------------------
    async def keymap_get(request):
        return JSONResponse({"path": str(keymap.path()), "keymap": keymap.load()})

    async def keymap_put(request):
        try:
            saved = keymap.save(await request.json())
        except keymap.KeymapError as e:
            return JSONResponse({"error": str(e)}, 400)
        ctx.broadcast({"type": "keymap_changed"})
        return JSONResponse({"path": str(keymap.path()), "keymap": saved})

    # ---- catalog: everything the platform can do, searchable (see catalog.py) ----
    async def catalog_search(request):
        q = request.query_params
        if q.get("name"):
            e = ctx.catalog.describe(q["name"])
            return JSONResponse(e.full()) if e else JSONResponse({"error": f"Nothing is called '{q['name']}'."}, 404)
        if q.get("kind") and q["kind"] not in KINDS:
            return JSONResponse({"error": f"kind must be one of: {', '.join(KINDS)}."}, 400)
        try:
            found = ctx.catalog.search(q.get("q", ""), q.get("kind"), int(q.get("limit", 8)))
        except ValueError:
            return JSONResponse({"error": "limit must be a number."}, 400)
        return JSONResponse({"results": [e.short() for e in found]})

    async def prompt_sections(request):
        """The system prompt, section by section, with sizes (the "Show the system prompt" command)."""
        secs = instructions.sections()
        return JSONResponse({"sections": secs, "chars": sum(x["chars"] for x in secs)})

    async def catalog_commands(request):
        """The page lists its commands once at load, so the agent can look them up."""
        return JSONResponse({"ok": True, "count": ctx.catalog.set_commands(await request.json())})

    # ---- macros: declarative sequences of commands, kept in macros.json in the Diatonic home folder ----
    async def macros_get(request):
        return JSONResponse({"path": str(macros.path()), "macros": macros.load()})

    async def macros_put(request):
        try:
            saved = macros.save(await request.json())
        except macros.MacroError as e:
            return JSONResponse({"error": str(e)}, 400)
        ctx.broadcast({"type": "macros_changed"})
        return JSONResponse({"path": str(macros.path()), "macros": saved})

    # ---- layouts: named layout trees, kept in layouts.json in the Diatonic home folder ----
    async def layouts_get(request):
        return JSONResponse({"path": str(layouts.path()), "layouts": layouts.load()})

    async def layouts_put(request):
        try:
            saved = layouts.save(await request.json())
        except layouts.LayoutError as e:
            return JSONResponse({"error": str(e)}, 400)
        ctx.broadcast({"type": "layouts_changed"})
        return JSONResponse({"path": str(layouts.path()), "layouts": saved})

    # ---- saved models: switch from the status bar, manage in Settings → Models ------------------------------
    async def models_list(request):
        return JSONResponse(M.public(config))

    async def models_save(request):
        b = await request.json()
        saved = M.ensure(config)
        try:
            m = M.clean(b, [x for x in saved if x["id"] != b.get("id")])
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        if b.get("key"):   # the key is per API host, kept in the keychain
            secrets.set(M.key_name(m["endpoint"]), str(b["key"]).strip())
        config.data["models"] = [x for x in saved if x["id"] != m["id"]] + [m]
        config.save()
        if config.data["agent"].get("active_model") == m["id"]:
            M.activate(config, m["id"]); ctx.broadcast({"type": "model_changed", "model": m["id"]})
        return JSONResponse(M.public(config))

    async def models_delete(request):
        mid = request.path_params["mid"]
        if config.data["agent"].get("active_model") == mid:
            return JSONResponse({"error": "That model is in use. Switch to another one first."}, 400)
        config.data["models"] = [x for x in M.ensure(config) if x["id"] != mid]
        config.save()
        return JSONResponse(M.public(config))

    async def models_activate(request):
        try:
            m = M.activate(config, str((await request.json()).get("id") or ""))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 404)
        ctx.broadcast({"type": "model_changed", "model": m["id"]})   # every open window follows
        return JSONResponse(M.public(config))

    async def models_test(request):
        """One small request with a tool, so you know the model answers and can call tools before relying on it."""
        b = await request.json()
        m = next((x for x in M.ensure(config) if x["id"] == b.get("id")), None)
        if m is None:
            return JSONResponse({"error": "Save the model first."}, 404)
        tool = {"type": "function", "function": {"name": "get_time", "description": "Current time in a city.",
                                                  "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}
        t0 = time.monotonic()
        try:
            calls, text = [], ""
            async for ch in make_provider(ctx, m).stream([{"role": "user", "content": "What time is it in Tokyo? Use the tool."}], [tool]):
                text += ch.get("content", "")
                calls += ch.get("tool_calls", [])
        except Exception as e:
            return JSONResponse({"ok": False, "error": str(e)})
        return JSONResponse({"ok": True, "tools": bool(calls) and calls[0]["name"] == "get_time", "seconds": round(time.monotonic() - t0, 1),
                             "reply": text[:200]})

    async def models_available(request):
        """Model names the provider reports (Ollama: installed; cloud: what the key can use), for the Add form."""
        q = request.query_params
        endpoint = (q.get("endpoint") or "").rstrip("/")
        try:
            prov = make_provider(ctx, {"provider": q.get("provider") or "openai", "model": "-", "endpoint": endpoint})
            if q.get("key"):
                prov.cfg["api_key"] = q["key"]
            names = (await prov.health())["models"] if q.get("provider") == "ollama" else await prov.list_models()
        except Exception as e:
            return JSONResponse({"names": [], "error": str(e)})
        return JSONResponse({"names": names[:300]})

    async def models_routing_get(request):
        from .agent import router
        return JSONResponse(config.data.get("model_routing") or {"enabled": False, "rules": router.DEFAULT_RULES, "custom_rules": []})

    async def models_routing_put(request):
        from .agent import router
        try:
            cleaned = router.clean_routing_config(await request.json())
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        config.data["model_routing"] = cleaned
        config.save()
        ctx.broadcast({"type": "routing_changed", "routing": cleaned})
        return JSONResponse(cleaned)

    async def models_routing_test(request):
        from .agent import router
        b = await request.json()
        text = str(b.get("text") or "").strip()
        screen = b.get("screen") if isinstance(b.get("screen"), dict) else None
        cat_id, rule_name = router.classify_task(ctx, text, screen=screen)
        model_m, route_info = router.resolve_model(ctx, text, screen=screen)
        return JSONResponse({
            "category": cat_id,
            "rule_name": rule_name,
            "resolved_model": model_m,
            "route_info": route_info,
        })

    # ---- home screen tiles: a small live snapshot from any app that offers one ------------------------------
    snap_cache: dict[tuple, tuple[float, dict]] = {}

    async def home_catalog(request):
        return JSONResponse([{"id": a.id, "name": a.name, "icon": a.icon} for a in registry.enabled() if callable(getattr(a, "snapshot", None))])

    async def home_tile(request):
        a = registry.apps.get(request.path_params["app_id"])
        if not a or not callable(getattr(a, "snapshot", None)) or not ctx.app_cfg(a.id)["enabled"]:
            return JSONResponse({"error": "This app has no home tile."}, 404)
        n = max(1, min(12, int(request.query_params.get("n") or 4)))
        key, now = (a.id, n), time.time()
        if request.query_params.get("fresh") != "1" and key in snap_cache and now - snap_cache[key][0] < 60:   # mail and GitHub are network calls
            return JSONResponse(snap_cache[key][1])
        try:
            data = await asyncio.wait_for(a.snapshot(n), 12)
        except ToolError as e:
            return JSONResponse({"error": str(e)})
        except asyncio.TimeoutError:
            return JSONResponse({"error": f"{a.name} took too long."})
        except Exception as e:   # a broken tile must not break the home screen
            return JSONResponse({"error": f"{a.name}: {type(e).__name__}"})
        snap_cache[key] = (now, data)
        return JSONResponse(data)

    # ---- quick actions -------------------------------------------------------
    async def put_actions(request):
        try:
            config.data["actions"] = clean_actions(await request.json())
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        config.save()
        return JSONResponse(config.data["actions"])

    async def run_action(request):
        a = action_runner.find(request.path_params["aid"])
        if not a:
            raise HTTPException(404, "No such action")
        body = await request.json() if await request.body() else {}
        try:
            rid = action_runner.start(a, str(body.get("input", "")))
        except ScriptsDisabled:
            return JSONResponse({"error": "Scripts are turned off. Enable them in Settings → Automation.",
                                 "code": "scripts_disabled"}, 403)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({"run_id": rid})

    async def cancel_action(request):
        return JSONResponse({"ok": action_runner.cancel(request.path_params["rid"])})

    async def export_custom(request):
        return JSONResponse(export_bundle(config, ctx.mods, ctx.fileapps), headers={"Content-Disposition": "attachment; filename=diatonic-customization.json"})

    async def import_custom(request):
        try:
            return JSONResponse(import_bundle(config, await request.json(), ctx.mods, ctx.fileapps))
        except (ValueError, TypeError) as e:
            return JSONResponse({"error": str(e)}, 400)

    # ---- apps ------------------------------------------------------------
    async def put_app(request):
        a = app_or_404(request.path_params["app_id"])
        body = await request.json()
        cfg = ctx.app_cfg(a.id)
        if "enabled" in body:
            cfg["enabled"] = bool(body["enabled"])
        if "settings" in body:
            cfg["settings"].update({k: v for k, v in body["settings"].items()
                                    if k in {f["key"] for f in a.settings if not f.get("secret")}})
        if "private" in body:
            cfg["private"] = bool(body["private"])
        if "always_allow" in body:
            known = {t.name for t in a.tools()}
            cfg["always_allow"] = [n for n in body["always_allow"] if n in known]
        if "secrets" in body:
            for k, v in body["secrets"].items():
                if k in {f["key"] for f in a.settings if f.get("secret")} and isinstance(v, str):
                    if v.strip():
                        a.set_secret(k, v.strip())
                    elif v == "":
                        pass  # empty = unchanged; use DELETE to clear
        config.save()
        return JSONResponse(await app_info(a))

    async def clear_secret(request):
        a = app_or_404(request.path_params["app_id"])
        key = request.path_params["key"]
        if key in {f["key"] for f in a.settings if f.get("secret")}:
            a.set_secret(key, "")
        return JSONResponse(await app_info(a))

    # ---- conversations ---------------------------------------------------
    async def list_convs(request):
        return JSONResponse(db.list_conversations())

    async def create_conv(request):
        return JSONResponse(db.create_conversation())

    async def get_conv(request):
        c = db.get_conversation(request.path_params["cid"])
        if not c:
            raise HTTPException(404)
        run = runs.runs.get(c["id"])
        msgs = [{**m, "content": collapse_repeats(m["content"]) if m["role"] == "assistant" else m["content"]}
                for m in db.messages(c["id"])]
        return JSONResponse({**c, "messages": msgs, "run": run.replay() if run else None})

    async def patch_conv(request):
        body = await request.json()
        db.rename(request.path_params["cid"], str(body.get("title", "Chat")))
        return JSONResponse({"ok": True})

    async def delete_conv(request):
        cid = request.path_params["cid"]
        if runs.cancel(cid):
            await asyncio.sleep(0.05)
        db.delete_conversation(cid)
        return JSONResponse({"ok": True})

    # ---- chat ------------------------------------------------------------
    async def chat(request):
        body = await request.json()
        text = str(body.get("message", "")).strip()
        if not text:
            raise HTTPException(400, "Empty message")
        conv = db.get_conversation(body.get("conversation_id") or "") or db.create_conversation()
        if conv["title"] == "New chat":
            conv["title"] = (text[:48] + "…") if len(text) > 48 else text
            db.rename(conv["id"], conv["title"])

        attach, refs = "", []
        files_app = registry.apps.get("files")
        if files_app and ctx.app_cfg("files")["enabled"] and "@" in text:
            granted = ctx.cloud_grants.get((conv["id"], config.data["agent"].get("active_model") or config.data["agent"]["model"]), set())
            if ctx.cloud() and registry.is_private(files_app) and "files" not in granted:
                attach = "\n\n[Files mentioned with @ aren't attached for a cloud model; read them with files_read_file (the user is asked first).]"
            else:
                attach, refs = files_app.expand_mentions(text)   # @file / @file:10-25 become compact numbered attachments
        try:
            run = runs.start(ctx, registry, conv, text, bool(body.get("speak")), attachments=attach,
                             force_apps=frozenset({"files"}) if attach else frozenset(), client=str(body.get("client") or "")[:64] or None,
                             screen=body.get("screen") if isinstance(body.get("screen"), dict) else None)
        except RunBusy:
            return JSONResponse({"error": "This chat is still working. Stop it first, or start a new chat."}, 409)
        return JSONResponse({"conversation_id": conv["id"], "title": conv["title"], "run": run.replay(), "attached": refs})

    async def client_focus(request):
        runs.set_focus(request.path_params["cid"])
        return JSONResponse({"ok": True})

    async def cancel_run(request):
        return JSONResponse({"ok": runs.cancel(request.path_params["cid"])})

    async def events(request):
        """One long-lived stream carrying every run's events, tagged with conversation_id."""
        q = runs.subscribe(request.query_params.get("client", "")[:64] or None)

        async def gen():
            try:
                yield sse({"type": "hello", "runs": runs.snapshot()})
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 15)
                    except asyncio.TimeoutError:
                        yield ": ping\n\n"
                        continue
                    yield sse(ev)
            finally:
                runs.unsubscribe(q)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def approve(request):
        body = await request.json()
        scope = body.get("scope") if body.get("approve") and body.get("scope") in ("chat", "job") else None
        ok = approvals.resolve(request.path_params["aid"], scope or bool(body.get("approve")))
        return JSONResponse({"ok": ok})

    # ---- voice -----------------------------------------------------------
    async def transcribe(request):
        if not stt.available():
            return JSONResponse({"error": "Voice support isn't installed. Run: pip install 'diatonic[voice]'"}, 501)
        name = config.data["voice"]["stt_model"]
        if not stt.ready(name):
            return JSONResponse({"error": "Speech model not downloaded yet. Open Settings → Voice."}, 409)
        pcm = await request.body()
        if len(pcm) > MAX_AUDIO:
            return JSONResponse({"error": "Recording too long."}, 413)
        if len(pcm) < 3200:  # < 0.1 s
            return JSONResponse({"text": ""})
        return JSONResponse({"text": await stt.transcribe(pcm[: len(pcm) - len(pcm) % 2], name)})

    async def speak(request):
        v = config.data["voice"]
        text = str((await request.json()).get("text", ""))[:400].strip()
        if not text:
            raise HTTPException(400, "No text")
        if not tts.ready(v["tts_voice"]):
            return JSONResponse({"error": "Piper voice not installed", "fallback": "system"}, 501)
        wav = await tts.synth(text, v["tts_voice"], float(v["tts_speed"]))
        return Response(wav, media_type="audio/wav")

    async def voice_setup(request):
        v = config.data["voice"]
        asyncio.create_task(setup.run(v["stt_model"], v["tts_voice"]))
        await asyncio.sleep(0)
        return JSONResponse(setup.state)

    # ---- notification inbox ----------------------------------------------
    async def list_notifications(request):
        return JSONResponse({"items": store.notifications(50), "unread": store.unread_count()})

    async def read_notification(request):
        store.mark_read(int(request.path_params["nid"]))
        return JSONResponse({"unread": store.unread_count()})

    async def read_all(request):
        store.mark_read(None)
        return JSONResponse({"unread": 0})

    async def delete_notification(request):
        store.delete_notification(int(request.path_params["nid"]))
        return JSONResponse({"unread": store.unread_count()})

    async def clear_notifications(request):
        store.delete_notification(None)
        return JSONResponse({"unread": 0})

    async def snooze_notification(request):
        """Re-fire a reminder later: creates a fresh one-off reminder with the same text."""
        n = store.notification(int(request.path_params["nid"]))
        if not n:
            raise HTTPException(404)
        minutes = int((await request.json()).get("minutes", 10))
        text = n["data"].get("text") or n["body"]
        ctx.scheduler.create(name=text, kind="notify", when=f"in {max(1, minutes)} minutes",
                             payload={"text": text}, created_by="user")
        store.mark_read(n["id"])
        return JSONResponse({"ok": True, "unread": store.unread_count()})

    # ---- memory ----------------------------------------------------------------
    def mem_json():
        rows = ctx.memory.all()
        facts = [r for r in rows if r["kind"] == "fact" and r["enabled"]]
        return {"items": rows, "cfg": config.data["memory"],
                "facts_chars": sum(len(r["text"]) for r in facts), "apps": [{"id": a.id, "name": a.name} for a in registry.apps.values()]}

    async def list_memories(request):
        return JSONResponse(mem_json())

    async def add_memory(request):
        b = await request.json()
        try:
            row, status = ctx.memory.add(str(b.get("text", "")), scope=str(b.get("scope", "global")), source="user",
                                         pinned=bool(b.get("pinned")))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        return JSONResponse({**mem_json(), "status": status, "item": row})

    async def update_memory(request):
        try:
            row = ctx.memory.update(int(request.path_params["mid"]), **(await request.json()))
        except ValueError as e:
            return JSONResponse({"error": str(e)}, 400)
        if not row:
            raise HTTPException(404)
        return JSONResponse(mem_json())

    async def delete_memory(request):
        ctx.memory.delete(int(request.path_params["mid"]))
        return JSONResponse(mem_json())

    async def test_notification(request):
        await ctx.notifier.send("Test notification", "If you can see this, notifications work.", kind="info",
                                speak_text="This is a test notification.")
        return JSONResponse({"ok": True})

    google_mounted = google_auth.build(ctx, ctx.google)

    ctx.mcp = McpHub(ctx, registry, runs)
    ctx.antigravity = AntigravityConnector(ctx)

    routes = [
        Route("/mcp", mcp_endpoint(ctx.mcp), methods=["POST", "GET", "DELETE"]),
        Route("/api/memories", list_memories),
        Route("/api/memories", add_memory, methods=["POST"]),
        Route("/api/memories/{mid}", update_memory, methods=["PUT"]),
        Route("/api/memories/{mid}", delete_memory, methods=["DELETE"]),
        Route("/api/notifications", list_notifications),
        Route("/api/notifications/test", test_notification, methods=["POST"]),
        Route("/api/notifications/read-all", read_all, methods=["POST"]),
        Route("/api/notifications/clear", clear_notifications, methods=["POST"]),
        Route("/api/notifications/{nid}/read", read_notification, methods=["POST"]),
        Route("/api/notifications/{nid}/snooze", snooze_notification, methods=["POST"]),
        Route("/api/notifications/{nid}", delete_notification, methods=["DELETE"]),
        Route("/api/state", get_state),
        Route("/api/health", health),
        Route("/api/config", put_config, methods=["PUT"]),
        Route("/api/actions", put_actions, methods=["PUT"]),
        Route("/api/actions/runs/{rid}/cancel", cancel_action, methods=["POST"]),
        Route("/api/actions/{aid}/run", run_action, methods=["POST"]),
        Route("/api/customization/export", export_custom),
        Route("/api/customization/import", import_custom, methods=["POST"]),
        Route("/api/home", home_catalog),
        Route("/api/home/{app_id}", home_tile),
        Route("/api/models", models_list),
        Route("/api/models", models_save, methods=["POST"]),
        Route("/api/models/active", models_activate, methods=["PUT"]),
        Route("/api/models/test", models_test, methods=["POST"]),
        Route("/api/models/available", models_available),
        Route("/api/models/routing", models_routing_get),
        Route("/api/models/routing", models_routing_put, methods=["PUT"]),
        Route("/api/models/routing/test", models_routing_test, methods=["POST"]),
        Route("/api/models/{mid}", models_delete, methods=["DELETE"]),
        Route("/api/catalog", catalog_search),
        Route("/api/prompt", prompt_sections),
        Route("/api/catalog/commands", catalog_commands, methods=["POST"]),
        Route("/api/keymap", keymap_get),
        Route("/api/keymap", keymap_put, methods=["PUT"]),
        Route("/api/macros", macros_get),
        Route("/api/macros", macros_put, methods=["PUT"]),
        Route("/api/layouts", layouts_get),
        Route("/api/layouts", layouts_put, methods=["PUT"]),
        Route("/api/mods", mods_list),
        Route("/api/mods/revert", mods_revert, methods=["POST"]),
        Route("/api/mods/verify/{rid}", mods_verified, methods=["POST"]),
        Route("/api/panel/tools/{app_id}/{tool}", panel_tool, methods=["POST"]),
        Route("/api/mods/{name}", mods_set, methods=["PUT"]),
        Route("/api/mods/{name}", mods_delete, methods=["DELETE"]),
        Route("/custom-apps/{app_id}/{path:path}", custom_app_file),
        Route("/api/store/{app_id}/{op}", app_store, methods=["POST"]),
        Route("/api/fetch/{app_id}", app_fetch, methods=["POST"]),
        Route("/api/notify/{app_id}", app_notify, methods=["POST"]),
        Route("/api/timers/{app_id}/{op}", app_timer, methods=["POST"]),
        Route("/api/custom-apps", custom_apps_list),
        Route("/api/custom-apps", custom_apps_create, methods=["POST"]),
        Route("/api/custom-apps/import", custom_apps_import, methods=["POST"]),
        Route("/api/custom-apps/{app_id}", custom_apps_get),
        Route("/api/custom-apps/{app_id}", custom_apps_delete, methods=["DELETE"]),
        Route("/api/custom-apps/{app_id}/file", custom_apps_get_file),
        Route("/api/custom-apps/{app_id}/file", custom_apps_put_file, methods=["PUT"]),
        Route("/api/custom-apps/{app_id}/file", custom_apps_delete_file, methods=["DELETE"]),
        Route("/api/custom-apps/{app_id}/patch", custom_apps_patch_file, methods=["POST"]),
        Route("/api/custom-apps/{app_id}/logs", custom_apps_get_logs),
        Route("/api/custom-apps/{app_id}/logs", custom_apps_post_logs, methods=["POST"]),
        Route("/api/custom-apps/{app_id}/export", custom_apps_export),
        Route("/api/apps", get_apps),
        Route("/api/apps/{app_id}/config", put_app, methods=["PUT"]),
        Route("/api/apps/{app_id}/secrets/{key}", clear_secret, methods=["DELETE"]),
        Route("/api/conversations", list_convs),
        Route("/api/conversations", create_conv, methods=["POST"]),
        Route("/api/conversations/{cid}", get_conv),
        Route("/api/conversations/{cid}", patch_conv, methods=["PATCH"]),
        Route("/api/conversations/{cid}", delete_conv, methods=["DELETE"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/conversations/{cid}/cancel", cancel_run, methods=["POST"]),
        Route("/api/events", events),
        Route("/api/clients/{cid}/focus", client_focus, methods=["POST"]),
        Route("/api/approvals/{aid}", approve, methods=["POST"]),
        Route("/api/voice/transcribe", transcribe, methods=["POST"]),
        Route("/api/voice/tts", speak, methods=["POST"]),
        Route("/api/voice/setup", voice_setup, methods=["POST"]),
        Mount("/api/google", routes=google_mounted),
        *[Mount(f"/api/apps/{a.id}", routes=a.routes()) for a in registry.apps.values() if a.routes()],
        Mount("/", NoCacheStatic(directory=WEB, html=True)),
    ]

    @asynccontextmanager
    async def lifespan(app):
        ctx.memory.prune()
        await ctx.google.adopt_legacy()
        ctx.scheduler.start()
        ctx.apptimers.start_all()
        yield
        await ctx.scheduler.stop()
        await ctx.apptimers.stop()
        await action_runner.shutdown()
        await runs.shutdown()
        await ctx.antigravity.close_all()
        if hasattr(registry.apps.get("shell"), "terms"):
            registry.apps["shell"].terms.shutdown()   # don't leave login shells behind
        await ctx.http.aclose()

    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(LocalOnly, allowed_hosts=hosts)])
    app.state.ctx, app.state.registry, app.state.runs = ctx, registry, runs
    return app
