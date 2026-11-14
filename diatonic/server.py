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
from .agent.loop import run_agent
from .agent.providers import ProviderError, make_provider
from .apps.registry import Registry
from .config import Config, home
from .context import Context
from .db import Database
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
               allowed_hosts: set[str] | None = None, registry: Registry | None = None) -> Starlette:
    config = config or Config()
    db = db or Database(home() / "diatonic.db")
    ctx = Context(config, db)
    registry = registry or Registry(ctx).discover()
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
                           "always_allow": t.name in cfg["always_allow"]} for t in a.tools()]}

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
        return JSONResponse(config.update(await request.json()))

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
        return JSONResponse({**c, "messages": db.messages(c["id"])})

    async def patch_conv(request):
        body = await request.json()
        db.rename(request.path_params["cid"], str(body.get("title", "Chat")))
        return JSONResponse({"ok": True})

    async def delete_conv(request):
        db.delete_conversation(request.path_params["cid"])
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

        async def gen():
            yield sse({"type": "conversation", "id": conv["id"], "title": conv["title"]})
            try:
                async for ev in run_agent(ctx, registry, conv["id"], text, speak=bool(body.get("speak"))):
                    yield sse(ev)
            except asyncio.CancelledError:
                raise
            except Exception as e:  # never leave the stream hanging
                yield sse({"type": "error", "message": f"{type(e).__name__}: {e}"})
                yield sse({"type": "done"})

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

    routes = [
        Route("/api/state", get_state),
        Route("/api/health", health),
        Route("/api/config", put_config, methods=["PUT"]),
        Route("/api/apps", get_apps),
        Route("/api/apps/{app_id}/config", put_app, methods=["PUT"]),
        Route("/api/apps/{app_id}/secrets/{key}", clear_secret, methods=["DELETE"]),
        Route("/api/conversations", list_convs),
        Route("/api/conversations", create_conv, methods=["POST"]),
        Route("/api/conversations/{cid}", get_conv),
        Route("/api/conversations/{cid}", patch_conv, methods=["PATCH"]),
        Route("/api/conversations/{cid}", delete_conv, methods=["DELETE"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/approvals/{aid}", approve, methods=["POST"]),
        Route("/api/voice/transcribe", transcribe, methods=["POST"]),
        Route("/api/voice/tts", speak, methods=["POST"]),
        Route("/api/voice/setup", voice_setup, methods=["POST"]),
        *[Mount(f"/api/apps/{a.id}", routes=a.routes()) for a in registry.apps.values() if a.routes()],
        Mount("/", NoCacheStatic(directory=WEB, html=True)),
    ]

    @asynccontextmanager
    async def lifespan(app):
        yield
        await ctx.http.aclose()

    app = Starlette(routes=routes, lifespan=lifespan,
                    middleware=[Middleware(LocalOnly, allowed_hosts=hosts)])
    app.state.ctx, app.state.registry = ctx, registry
    return app
