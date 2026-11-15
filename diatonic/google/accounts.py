"""Accounts, tokens and authenticated requests for Google APIs."""
from __future__ import annotations

import asyncio
import time

from ..apps.base import ToolError
from .endpoints import urls

SCOPES = {
    "calendar": "https://www.googleapis.com/auth/calendar",
    "gmail_read": "https://www.googleapis.com/auth/gmail.readonly",
    "gmail_modify": "https://www.googleapis.com/auth/gmail.modify",
    "gmail_compose": "https://www.googleapis.com/auth/gmail.compose",
}
BASE_SCOPES = ["openid", "email", "profile"]
COLORS = ["#4285f4", "#0b8043", "#d50000", "#f4511e", "#8e24aa", "#039be5", "#e67c73", "#f6bf26"]
LEVELS = ("read", "manage", "send")


class GoogleAuthError(ToolError):
    """The saved login for an account stopped working (revoked, expired in testing mode, wrong client)."""


def scopes_for(services: list[str]) -> list[str]:
    """['calendar', 'gmail:manage'] -> scope URLs. gmail:read | gmail:manage | gmail:send (send includes manage)."""
    out = list(BASE_SCOPES)
    for s in services:
        if s == "calendar":
            out.append(SCOPES["calendar"])
        elif s == "gmail:read":
            out.append(SCOPES["gmail_read"])
        elif s == "gmail:manage":
            out.append(SCOPES["gmail_modify"])
        elif s == "gmail:send":
            out += [SCOPES["gmail_modify"], SCOPES["gmail_compose"]]
    return list(dict.fromkeys(out))


def has_calendar(a: dict) -> bool:
    return SCOPES["calendar"] in a.get("scopes", [])


def gmail_level(a: dict) -> str | None:
    sc = a.get("scopes", [])
    if SCOPES["gmail_compose"] in sc:
        return "send"
    if SCOPES["gmail_modify"] in sc:
        return "manage"
    return "read" if SCOPES["gmail_read"] in sc else None


def services_of(a: dict) -> dict:
    return {"calendar": has_calendar(a), "gmail": gmail_level(a)}


class GoogleAccounts:
    def __init__(self, ctx):
        self.ctx = ctx
        self._tok: dict[str, tuple[str, float]] = {}
        self._cache: dict[str, tuple[float, object]] = {}

    # ---- config --------------------------------------------------------
    @property
    def cfg(self) -> dict:
        return self.ctx.config.data["google"]

    def client(self) -> tuple[str, str] | None:
        cid, sec = self.ctx.secrets.get("google.client_id"), self.ctx.secrets.get("google.client_secret")
        return (cid, sec or "") if cid else None

    def set_client(self, client_id: str, client_secret: str) -> None:
        if client_id:
            self.ctx.secrets.set("google.client_id", client_id.strip())
        if client_secret:
            self.ctx.secrets.set("google.client_secret", client_secret.strip())

    def all(self) -> list[dict]:
        return self.cfg["accounts"]

    def get(self, email: str) -> dict | None:
        return next((a for a in self.all() if a["email"].lower() == email.lower()), None)

    def label(self, a: dict) -> str:
        return a.get("label") or a["email"]

    def upsert(self, email: str, name: str, scopes: list[str], refresh_token: str | None) -> dict:
        a = self.get(email)
        if a is None:
            a = {"email": email, "name": name or "", "label": "", "color": COLORS[len(self.all()) % len(COLORS)],
                 "scopes": [], "enabled": True, "needs_reauth": False}
            self.all().append(a)
        a["name"] = name or a.get("name", "")
        a["scopes"] = sorted(set(a.get("scopes", [])) | set(scopes))
        a["needs_reauth"] = False
        if refresh_token:
            self.ctx.secrets.set(f"google.rt.{email.lower()}", refresh_token)
        self._tok.pop(email.lower(), None)
        self.ctx.config.save()
        return a

    def update(self, email: str, **f) -> dict | None:
        a = self.get(email)
        if not a:
            return None
        if "label" in f:
            a["label"] = str(f["label"]).strip()[:30]
        if "color" in f and (not f["color"] or len(str(f["color"])) == 7 and str(f["color"]).startswith("#")):
            a["color"] = f["color"] or a["color"]
        if "enabled" in f:
            a["enabled"] = bool(f["enabled"])
        self.ctx.config.save()
        return a

    def remove(self, email: str) -> bool:
        a = self.get(email)
        if not a:
            return False
        self.all().remove(a)
        self.ctx.secrets.delete(f"google.rt.{email.lower()}")
        self._tok.pop(email.lower(), None)
        if self.cfg["default_account"].lower() == email.lower():
            self.cfg["default_account"] = ""
        self.cfg["calendar_prefs"] = {k: v for k, v in self.cfg["calendar_prefs"].items() if not k.lower().startswith(email.lower() + "|")}
        self.ctx.config.save()
        return True

    # ---- picking accounts for a request ----------------------------------------
    def with_service(self, service: str, level: str | None = None) -> list[dict]:
        out = []
        for a in self.all():
            if not a.get("enabled", True):
                continue
            if service == "calendar" and has_calendar(a):
                out.append(a)
            elif service == "gmail":
                lv = gmail_level(a)
                if lv and (level is None or LEVELS.index(lv) >= LEVELS.index(level)):
                    out.append(a)
        return out

    def describe(self, accounts: list[dict]) -> str:
        return ", ".join(f"{self.label(a)} ({a['email']})" if a.get("label") else a["email"] for a in accounts) or "none"

    def pick(self, spec: str | None, service: str, *, many: bool, level: str | None = None) -> list[dict]:
        """Resolve a user/model-supplied account reference ('work', 'sam@x.com', 'all', None)."""
        pool = self.with_service(service, level)
        if not pool:
            need = {"calendar": "Google Calendar", "gmail": "Gmail"}[service] + (" with permission to " + {"manage": "manage mail", "send": "send mail"}[level] if level and level != "read" else "")
            connected = self.with_service(service)
            hint = ("Reconnect an account with that permission in Settings → Accounts." if connected
                    else "Connect a Google account in Settings → Accounts.")
            raise ToolError(f"No Google account is connected for {need}. {hint}")
        s = (spec or "").strip().lower()
        if s in ("default", "primary", "main", "mine", "my", "me", "both", "everyone", "every", "all accounts", "none", "null", "n/a"):
            s = ""   # models say things like account="default" when they mean "no particular account"
        if s in ("", "all", "*", "any"):
            if many:
                return pool
            if len(pool) == 1:
                return pool
            d = self.cfg["default_account"].lower()
            default = next((a for a in pool if a["email"].lower() == d), None)
            if default:
                return [default]
            raise ToolError(f"Which account? Options: {self.describe(pool)}. Ask the user, or set a default in Settings → Accounts.")
        exact = [a for a in pool if s in (a["email"].lower(), (a.get("label") or "").lower())]
        hits = exact or [a for a in pool if s in a["email"].lower() or s in (a.get("label") or "").lower() or s in (a.get("name") or "").lower()]
        if not hits:
            if level and level != "read":   # the account exists but its connection is too weak for this
                weak = [a for a in self.with_service(service) if s in a["email"].lower() or s in (a.get("label") or "").lower()]
                if weak:
                    what = {"manage": "manage mail", "send": "send mail"}[level]
                    raise ToolError(f"{self.label(weak[0])} was connected without permission to {what}. Reconnect it in Settings → Accounts and allow that.")
            raise ToolError(f"No connected account matches '{spec}'. Options: {self.describe(pool)}.")
        if len(hits) > 1 and not many:
            raise ToolError(f"'{spec}' matches several accounts: {self.describe(hits)}. Be more specific.")
        return hits

    # ---- tokens and requests ---------------------------------------------------
    async def access_token(self, email: str) -> str:
        key = email.lower()
        hit = self._tok.get(key)
        if hit and hit[1] > time.time() + 60:
            return hit[0]
        rt, cl = self.ctx.secrets.get(f"google.rt.{key}"), self.client()
        if not rt or not cl:
            raise GoogleAuthError(f"{email} isn't connected. Reconnect it in Settings → Accounts.")
        r = await self.ctx.http.post(urls()["token"], data={"client_id": cl[0], "client_secret": cl[1], "refresh_token": rt,
                                                              "grant_type": "refresh_token"})
        if r.status_code != 200:
            if r.status_code in (400, 401):
                a = self.get(email)
                if a:
                    a["needs_reauth"] = True
                    self.ctx.config.save()
            raise GoogleAuthError(f"Google rejected the saved login for {email}. Reconnect it in Settings → Accounts."
                                  " (If this keeps happening within a week, your OAuth app may still be in “Testing”; see the README.)")
        d = r.json()
        self._tok[key] = (d["access_token"], time.time() + int(d.get("expires_in", 3600)))
        return d["access_token"]

    async def request(self, email: str, method: str, url: str, *, retry: bool = True, **kw):
        headers = {"Authorization": f"Bearer {await self.access_token(email)}", **kw.pop("headers", {})}
        r = await self.ctx.http.request(method, url, headers=headers, **kw)
        if r.status_code == 401 and retry:
            self._tok.pop(email.lower(), None)
            return await self.request(email, method, url, retry=False, headers=kw.get("headers", {}), **{k: v for k, v in kw.items() if k != "headers"})
        if r.status_code in (429, 500, 502, 503) and retry:
            await asyncio.sleep(1.0)
            return await self.request(email, method, url, retry=False, **kw)
        return r

    async def json(self, email: str, method: str, url: str, **kw):
        r = await self.request(email, method, url, **kw)
        if r.status_code >= 400:
            try:
                err = r.json()["error"]
                msg, status = err.get("message", r.text), err.get("status", "")
            except Exception:
                msg, status = r.text, ""
            if r.status_code == 403 and ("scope" in msg.lower() or "SCOPE" in status or "insufficient" in msg.lower()):
                raise ToolError(f"{email} wasn't connected with permission for that. Reconnect it in Settings → Accounts and allow it.", bad_args=False)
            raise ToolError(f"Google {r.status_code}: {str(msg)[:300]}", bad_args=r.status_code in (400, 422))
        return r.json() if r.content else {}

    # ---- short-lived cache (calendar lists, labels) ----------------------------------
    def cached(self, key: str, ttl: float):
        hit = self._cache.get(key)
        return hit[1] if hit and hit[0] > time.time() else None

    def store(self, key: str, value, ttl: float):
        self._cache[key] = (time.time() + ttl, value)
        return value

    def forget(self, prefix: str = "") -> None:
        for k in [k for k in self._cache if k.startswith(prefix)]:
            del self._cache[k]

    async def adopt_legacy(self) -> None:
        """One-time upgrade from the single-account Calendar setup (gcal.* secrets)."""
        s = self.ctx.secrets
        if not self.client() and s.get("gcal.client_id"):
            self.set_client(s.get("gcal.client_id") or "", s.get("gcal.client_secret") or "")
        rt = s.get("gcal.refresh_token")
        if not rt or self.all() or not self.client():
            return
        cl = self.client()
        try:
            r = await self.ctx.http.post(urls()["token"], data={"client_id": cl[0], "client_secret": cl[1], "refresh_token": rt, "grant_type": "refresh_token"})
            if r.status_code != 200:
                return
            tok = r.json()
            u = await self.ctx.http.get(urls()["userinfo"], headers={"Authorization": f"Bearer {tok['access_token']}"})
            email = u.json().get("email")
            if email:
                self.upsert(email, u.json().get("name", ""), [SCOPES["calendar"]], rt)
                s.delete("gcal.refresh_token")
        except Exception:
            pass
