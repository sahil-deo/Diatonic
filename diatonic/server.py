"""HTTP server: JSON API, SSE chat stream, static UI."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path

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
            bad = h.get("host", "") not in self.hosts
            origin = h.get("origin")
            if not bad and scope["method"] not in ("GET", "HEAD", "OPTIONS") and origin:
                bad = origin.split("://", 1)[-1] not in self.hosts
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
    ctx.notifier = Notifier(ctx, store, runs.broadcast)
    registry = registry or Registry(ctx).discover()
    ctx.scheduler = Scheduler(ctx, registry, runs, ctx.notifier, store)
    action_runner = ActionRunner(ctx)
    stt, tts = STT(), TTS()
    setup = VoiceSetup(stt, tts)
    port = config.data["server"]["port"]
    hosts = allowed_hosts or {f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}"}

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
        return {"id": a.id, "name": a.name, "icon": a.icon, "description": a.description, "panel": a.panel,
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
        return JSONResponse({
            "agent": {**agent, "model": config.data["agent"]["model"], "provider": config.data["agent"]["provider"]},
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
        return JSONResponse(config.update(patch))

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
        return JSONResponse(export_bundle(config), headers={"Content-Disposition": "attachment; filename=diatonic-customization.json"})

    async def import_custom(request):
        try:
            return JSONResponse(import_bundle(config, await request.json()))
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
            attach, refs = files_app.expand_mentions(text)   # @file / @file:10-25 become compact numbered attachments
        try:
            run = runs.start(ctx, registry, conv, text, bool(body.get("speak")), attachments=attach,
                             force_apps=frozenset({"files"}) if attach else frozenset())
        except RunBusy:
            return JSONResponse({"error": "This chat is still working. Stop it first, or start a new chat."}, 409)
        return JSONResponse({"conversation_id": conv["id"], "title": conv["title"], "run": run.replay(), "attached": refs})

    async def cancel_run(request):
        return JSONResponse({"ok": runs.cancel(request.path_params["cid"])})

    async def events(request):
        """One long-lived stream carrying every run's events, tagged with conversation_id."""
        q = runs.subscribe()

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
        ok = approvals.resolve(request.path_params["aid"], bool(body.get("approve")))
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

    routes = [
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
        Route("/api/approvals/{aid}", approve, methods=["POST"]),
        Route("/api/voice/transcribe", transcribe, methods=["POST"]),
        Route("/api/voice/tts", speak, methods=["POST"]),
        Route("/api/voice/setup", voice_setup, methods=["POST"]),
        Mount("/api/google", routes=google_auth.routes(ctx, ctx.google)),
        *[Mount(f"/api/apps/{a.id}", routes=a.routes()) for a in registry.apps.values() if a.routes()],
        Mount("/", NoCacheStatic(directory=WEB, html=True)),
    ]

    @asynccontextmanager
    async def lifespan(app):
        ctx.memory.prune()
        await ctx.google.adopt_legacy()
        ctx.scheduler.start()
        yield
        await ctx.scheduler.stop()
        await action_runner.shutdown()
        await runs.shutdown()
        await ctx.http.aclose()

    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(LocalOnly, allowed_hosts=hosts)])
    app.state.ctx, app.state.registry, app.state.runs = ctx, registry, runs
    return app
