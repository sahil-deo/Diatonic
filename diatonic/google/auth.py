"""HTTP routes for connecting and managing Google accounts (mounted at /api/google)."""
from __future__ import annotations

import base64
import hashlib
import secrets as pysecrets
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from .accounts import SCOPES, GoogleAccounts, scopes_for, services_of
from .endpoints import urls


def public(ga: GoogleAccounts, a: dict) -> dict:
    return {"email": a["email"], "name": a.get("name", ""), "label": a.get("label", ""), "color": a["color"], "enabled": a.get("enabled", True),
            "needs_reauth": a.get("needs_reauth", False), "services": services_of(a), "default": ga.cfg["default_account"].lower() == a["email"].lower()}


def routes(ctx, ga: GoogleAccounts) -> list[Route]:
    pending: dict[str, dict] = {}

    def redirect_uri(request: Request) -> str:
        return f"{request.url.scheme}://{request.url.netloc}/api/google/auth/callback"

    async def state(request: Request):
        cl = ga.client()
        return JSONResponse({"client_set": bool(cl), "client_id_hint": (cl[0][:12] + "…") if cl else "", "redirect_uri": redirect_uri(request),
                             "accounts": [public(ga, a) for a in ga.all()], "default_account": ga.cfg["default_account"],
                             "fake": "fake" if urls()["auth"].startswith("http://localhost") or "127.0.0.1" in urls()["auth"] else ""})

    async def put_client(request: Request):
        b = await request.json()
        ga.set_client(str(b.get("client_id", "")), str(b.get("client_secret", "")))
        return await state(request)

    async def start(request: Request):
        cl = ga.client()
        if not cl:
            return HTMLResponse("Save your OAuth client ID and secret in Settings → Accounts first.", status_code=400)
        services = [s for s in request.query_params.get("services", "calendar").split(",") if s]
        if not services:
            return HTMLResponse("Choose at least one service (Calendar or Gmail).", status_code=400)
        verifier, st = pysecrets.token_urlsafe(64), pysecrets.token_urlsafe(16)
        pending[st] = {"verifier": verifier, "services": services}
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = {"client_id": cl[0], "redirect_uri": redirect_uri(request), "response_type": "code", "scope": " ".join(scopes_for(services)),
             "access_type": "offline", "prompt": "consent select_account", "include_granted_scopes": "true", "state": st,
             "code_challenge": challenge, "code_challenge_method": "S256"}
        hint = request.query_params.get("login_hint")
        if hint:
            q["login_hint"] = hint
        return RedirectResponse(f"{urls()['auth']}?{urlencode(q)}")

    async def callback(request: Request):
        st, code = request.query_params.get("state", ""), request.query_params.get("code")
        info = pending.pop(st, None)
        if request.query_params.get("error"):
            return RedirectResponse("/?settings=accounts&error=" + request.query_params["error"])
        if not code or not info:
            return HTMLResponse("Sign-in failed or expired. Close this tab and try again.", status_code=400)
        cl = ga.client()
        r = await ctx.http.post(urls()["token"], data={"client_id": cl[0], "client_secret": cl[1], "code": code, "code_verifier": info["verifier"],
                                                         "grant_type": "authorization_code", "redirect_uri": redirect_uri(request)})
        if r.status_code != 200:
            return HTMLResponse(f"Google returned an error: {r.text[:300]}", status_code=400)
        tok = r.json()
        u = await ctx.http.get(urls()["userinfo"], headers={"Authorization": f"Bearer {tok['access_token']}"})
        if u.status_code != 200 or not u.json().get("email"):
            return HTMLResponse("Connected, but Google didn't tell us which account this was.", status_code=400)
        ui = u.json()
        granted = tok.get("scope", "").split() or scopes_for(info["services"])
        ga.upsert(ui["email"], ui.get("name", ""), [s for s in granted if s in SCOPES.values()], tok.get("refresh_token"))
        ga.forget()
        return RedirectResponse(f"/?settings=accounts&connected={ui['email']}")

    async def update_account(request: Request):
        a = ga.update(request.path_params["email"], **(await request.json()))
        if not a:
            return JSONResponse({"error": "No such account"}, 404)
        ga.forget()
        return await state(request)

    async def delete_account(request: Request):
        email = request.path_params["email"]
        rt = ctx.secrets.get(f"google.rt.{email.lower()}")
        if rt:   # best effort: tell Google to forget this grant
            try:
                await ctx.http.post(urls()["revoke"], data={"token": rt})
            except Exception:
                pass
        ga.remove(email)
        ga.forget()
        return await state(request)

    async def put_default(request: Request):
        email = str((await request.json()).get("email", ""))
        ga.cfg["default_account"] = email if (not email or ga.get(email)) else ga.cfg["default_account"]
        ctx.config.save()
        return await state(request)

    return [Route("/state", state), Route("/client", put_client, methods=["PUT"]), Route("/auth/start", start),
            Route("/auth/callback", callback), Route("/accounts/{email}", update_account, methods=["POST"]),
            Route("/accounts/{email}", delete_account, methods=["DELETE"]), Route("/default", put_default, methods=["PUT"])]
