"""Google Calendar over the REST API with an OAuth 2.0 (PKCE, loopback) flow."""
from __future__ import annotations

import base64
import hashlib
import secrets as pysecrets
import time
from datetime import date, datetime, time as dtime, timedelta
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.routing import Route

from ..base import DESTRUCTIVE, WRITE, App, Tool, ToolError

API = "https://www.googleapis.com/calendar/v3"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/calendar"


def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


TIME = "ISO 8601 date-time like 2026-11-20T15:00:00 (local time assumed if no offset), or a date like 2026-11-20"
CAL = _s("Calendar id; defaults to 'primary'")


class GCalApp(App):
    id = "gcal"
    name = "Google Calendar"
    icon = "calendar"
    description = "See your agenda, create, move and delete events, and find free time."
    panel = True
    settings = [
        {"key": "client_id", "label": "OAuth client ID", "secret": True, "placeholder": "…apps.googleusercontent.com",
         "help": "Google Cloud Console → Credentials → OAuth client ID (Desktop app). See the README."},
        {"key": "client_secret", "label": "OAuth client secret", "secret": True, "placeholder": "GOCSPX-…"},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._access: tuple[str, float] | None = None
        self._pending: dict[str, str] = {}  # state -> pkce verifier

    # ---- time helpers --------------------------------------------------
    def parse(self, v: str) -> date | datetime:
        v = v.strip()
        try:
            if len(v) == 10:
                return date.fromisoformat(v)
            d = datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ToolError(f"Couldn't read '{v}' as a date/time. Use ISO 8601, e.g. 2026-11-20T15:00:00.")
        return d if d.tzinfo else d.replace(tzinfo=self.ctx.tz)

    def _bound(self, v: str | None, default: datetime) -> str:
        d = self.parse(v) if v else default
        if not isinstance(d, datetime):
            d = datetime.combine(d, dtime.min, tzinfo=self.ctx.tz)
        return d.isoformat()

    def _when(self, d: date | datetime) -> dict:
        if isinstance(d, datetime):
            return {"dateTime": d.isoformat(), "timeZone": self.ctx.tz_name}
        return {"date": d.isoformat()}

    # ---- auth ----------------------------------------------------------
    def _redirect_uri(self, request: Request) -> str:
        return f"{request.url.scheme}://{request.url.netloc}/api/apps/gcal/auth/callback"

    async def _access_token(self) -> str:
        if self._access and self._access[1] > time.time() + 60:
            return self._access[0]
        refresh, cid, csec = self.secret("refresh_token"), self.secret("client_id"), self.secret("client_secret")
        if not refresh or not cid:
            raise ToolError("Google Calendar isn't connected. Open Settings → Apps → Google Calendar and click Connect.")
        r = await self.ctx.http.post(TOKEN_URL, data={
            "client_id": cid, "client_secret": csec or "", "refresh_token": refresh, "grant_type": "refresh_token"})
        if r.status_code != 200:
            raise ToolError("Google rejected the saved login. Reconnect Google Calendar in Settings.")
        d = r.json()
        self._access = (d["access_token"], time.time() + d.get("expires_in", 3600))
        return d["access_token"]

    async def auth_start(self, request: Request):
        cid = self.secret("client_id")
        if not cid:
            return HTMLResponse("Save your OAuth client ID and secret in Settings first.", status_code=400)
        verifier = pysecrets.token_urlsafe(64)
        state = pysecrets.token_urlsafe(16)
        self._pending[state] = verifier
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        q = urlencode({"client_id": cid, "redirect_uri": self._redirect_uri(request), "response_type": "code",
                       "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state,
                       "code_challenge": challenge, "code_challenge_method": "S256"})
        return RedirectResponse(f"{AUTH_URL}?{q}")

    async def auth_callback(self, request: Request):
        state, code = request.query_params.get("state", ""), request.query_params.get("code")
        verifier = self._pending.pop(state, None)
        if not code or not verifier:
            return HTMLResponse("Sign-in failed or expired. Close this tab and try again.", status_code=400)
        r = await self.ctx.http.post(TOKEN_URL, data={
            "client_id": self.secret("client_id"), "client_secret": self.secret("client_secret") or "",
            "code": code, "code_verifier": verifier, "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri(request)})
        if r.status_code != 200:
            return HTMLResponse(f"Google returned an error: {r.text[:300]}", status_code=400)
        d = r.json()
        if d.get("refresh_token"):
            self.set_secret("refresh_token", d["refresh_token"])
        self._access = (d["access_token"], time.time() + d.get("expires_in", 3600))
        return RedirectResponse("/?settings=apps&connected=gcal")

    async def disconnect(self, request: Request):
        self.set_secret("refresh_token", "")
        self._access = None
        return JSONResponse({"ok": True})

    async def status(self) -> dict:
        if not self.secret("client_id"):
            return {"connected": False, "detail": "Add your OAuth client ID and secret, then connect."}
        if not self.secret("refresh_token"):
            return {"connected": False, "detail": "Not connected yet.",
                    "action": {"label": "Connect Google", "href": "/api/apps/gcal/auth/start"}}
        try:
            cals = await self._req("GET", "/users/me/calendarList", params={"maxResults": 1})
            return {"connected": True, "detail": f"Connected ({len(cals.get('items', []))}+ calendars)",
                    "action": {"label": "Reconnect", "href": "/api/apps/gcal/auth/start"}}
        except ToolError as e:
            return {"connected": False, "detail": str(e),
                    "action": {"label": "Reconnect", "href": "/api/apps/gcal/auth/start"}}

    async def _req(self, method: str, path: str, **kw):
        tok = await self._access_token()
        r = await self.ctx.http.request(method, API + path, headers={"Authorization": f"Bearer {tok}"}, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json()["error"]["message"]
            except Exception:
                msg = r.text
            raise ToolError(f"Google Calendar {r.status_code}: {msg[:300]}")
        return r.json() if r.content else {}

    # ---- shaping -------------------------------------------------------
    @staticmethod
    def _event(e: dict) -> dict:
        s, en = e.get("start", {}), e.get("end", {})
        return {"id": e["id"], "summary": e.get("summary", "(no title)"),
                "start": s.get("dateTime") or s.get("date"), "end": en.get("dateTime") or en.get("date"),
                "all_day": "date" in s, "location": e.get("location"),
                "attendees": [a["email"] for a in e.get("attendees", [])][:10] or None,
                "link": e.get("htmlLink"), "status": e.get("status")}

    # ---- tools ---------------------------------------------------------
    def tools(self) -> list[Tool]:
        ev = {"summary": _s("Event title"), "start": _s(TIME), "end": _s("End, same format as start"),
              "description": _s("Event description"), "location": _s("Location"),
              "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee emails"},
              "calendar_id": CAL}
        upd = {k: v for k, v in ev.items()} | {"event_id": _s("Event id from list_events/search_events")}
        return [
            Tool("list_calendars", "List the user's calendars.", {}, self.list_calendars),
            Tool("list_events", "List events in a time range, soonest first. Defaults to the next 7 days.",
                 {"properties": {"start": _s(TIME), "end": _s(TIME), "calendar_id": CAL,
                                 "max_results": {"type": "integer", "minimum": 1, "maximum": 50}}},
                 self.list_events),
            Tool("search_events", "Search events by text (title, description, attendees).",
                 {"properties": {"query": _s("Search text"), "start": _s(TIME), "end": _s(TIME), "calendar_id": CAL},
                  "required": ["query"]}, self.search_events),
            Tool("find_free_time", "Find free slots of a given length within working hours.",
                 {"properties": {"start": _s("First day to check (date or date-time)"),
                                 "end": _s("Last day to check"),
                                 "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 480},
                                 "day_start": _s("Working day start HH:MM, default 09:00"),
                                 "day_end": _s("Working day end HH:MM, default 18:00")},
                  "required": ["start", "end"]}, self.find_free),
            Tool("create_event", "Create a calendar event. Pass a date (no time) as start for an all-day event. "
                 "If end is omitted the event lasts one hour.",
                 {"properties": ev, "required": ["summary", "start"]}, self.create_event, WRITE,
                 describe=lambda a: f"Create “{a.get('summary')}” at {a.get('start')}"),
            Tool("update_event", "Change an existing event. Only pass the fields to change. "
                 "Changing start without end keeps the original duration.",
                 {"properties": upd, "required": ["event_id"]}, self.update_event, WRITE,
                 describe=lambda a: "Update event " + ", ".join(f"{k}={v}" for k, v in a.items()
                                                                if k not in ("event_id", "calendar_id"))),
            Tool("delete_event", "Delete an event.",
                 {"properties": {"event_id": _s("Event id"), "calendar_id": CAL}, "required": ["event_id"]},
                 self.delete_event, DESTRUCTIVE, describe=lambda a: f"Delete event {a.get('event_id')}"),
        ]

    async def list_calendars(self, a):
        d = await self._req("GET", "/users/me/calendarList")
        return [{"id": c["id"], "name": c.get("summaryOverride") or c["summary"], "primary": c.get("primary", False),
                 "timezone": c.get("timeZone")} for c in d.get("items", [])]

    async def _events(self, a, extra=None):
        now = self.ctx.now()
        p = {"timeMin": self._bound(a.get("start"), now), "timeMax": self._bound(a.get("end"), now + timedelta(days=7)),
             "singleEvents": "true", "orderBy": "startTime", "maxResults": a.get("max_results", 25)} | (extra or {})
        d = await self._req("GET", f"/calendars/{a.get('calendar_id', 'primary')}/events", params=p)
        return [self._event(e) for e in d.get("items", [])]

    async def list_events(self, a):
        return await self._events(a)

    async def search_events(self, a):
        now = self.ctx.now()
        a = {"start": (now - timedelta(days=30)).isoformat(), "end": (now + timedelta(days=180)).isoformat()} | a
        return await self._events(a, {"q": a["query"]})

    async def create_event(self, a):
        s = self.parse(a["start"])
        if a.get("end"):
            e = self.parse(a["end"])
        else:
            e = s + (timedelta(days=1) if not isinstance(s, datetime) else timedelta(hours=1))
        body = {"summary": a["summary"], "start": self._when(s), "end": self._when(e)}
        for k in ("description", "location"):
            if a.get(k):
                body[k] = a[k]
        if a.get("attendees"):
            body["attendees"] = [{"email": x} for x in a["attendees"]]
        r = await self._req("POST", f"/calendars/{a.get('calendar_id', 'primary')}/events", json=body,
                            params={"sendUpdates": "all"} if a.get("attendees") else None)
        return {"created": self._event(r)}

    async def update_event(self, a):
        cal, eid = a.get("calendar_id", "primary"), a["event_id"]
        cur = await self._req("GET", f"/calendars/{cal}/events/{eid}")
        body: dict = {}
        for k in ("summary", "description", "location"):
            if a.get(k) is not None:
                body[k] = a[k]
        if a.get("attendees") is not None:
            body["attendees"] = [{"email": x} for x in a["attendees"]]
        if a.get("start"):
            s = self.parse(a["start"])
            if a.get("end"):
                e = self.parse(a["end"])
            else:  # keep duration
                old_s = cur["start"].get("dateTime") or cur["start"]["date"]
                old_e = cur["end"].get("dateTime") or cur["end"]["date"]
                dur = self.parse(old_e) - self.parse(old_s)
                e = s + dur
            body["start"], body["end"] = self._when(s), self._when(e)
        elif a.get("end"):
            body["end"] = self._when(self.parse(a["end"]))
        r = await self._req("PATCH", f"/calendars/{cal}/events/{eid}", json=body)
        return {"updated": self._event(r)}

    async def delete_event(self, a):
        await self._req("DELETE", f"/calendars/{a.get('calendar_id', 'primary')}/events/{a['event_id']}")
        return {"deleted": a["event_id"]}

    async def find_free(self, a):
        s = self.parse(a["start"])
        e = self.parse(a["end"])
        s_day = s.date() if isinstance(s, datetime) else s
        e_day = e.date() if isinstance(e, datetime) else e
        dur = timedelta(minutes=a.get("duration_minutes", 30))
        h0, m0 = map(int, a.get("day_start", "09:00").split(":"))
        h1, m1 = map(int, a.get("day_end", "18:00").split(":"))
        tmin = datetime.combine(s_day, dtime.min, tzinfo=self.ctx.tz)
        tmax = datetime.combine(e_day + timedelta(days=1), dtime.min, tzinfo=self.ctx.tz)
        fb = await self._req("POST", "/freeBusy", json={"timeMin": tmin.isoformat(), "timeMax": tmax.isoformat(),
                                                         "items": [{"id": "primary"}]})
        busy = sorted((datetime.fromisoformat(b["start"].replace("Z", "+00:00")),
                       datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
                      for b in fb["calendars"]["primary"]["busy"])
        now, slots, day = self.ctx.now(), [], s_day
        while day <= e_day and len(slots) < 10:
            cur = max(datetime.combine(day, dtime(h0, m0), tzinfo=self.ctx.tz), now)
            end = datetime.combine(day, dtime(h1, m1), tzinfo=self.ctx.tz)
            for bs, be in busy:
                if be <= cur or bs >= end:
                    continue
                if bs - cur >= dur:
                    slots.append((cur, bs))
                cur = max(cur, be)
            if end - cur >= dur:
                slots.append((cur, end))
            day += timedelta(days=1)
        return [{"from": f.isoformat(), "to": t.isoformat()} for f, t in slots[:10]]

    # ---- panel ---------------------------------------------------------
    async def agenda(self, request: Request):
        days = int(request.query_params.get("days", 7))
        try:
            now = self.ctx.now()
            start = datetime.combine(now.date(), dtime.min, tzinfo=self.ctx.tz)
            ev = await self._events({"start": start.isoformat(), "end": (start + timedelta(days=days)).isoformat(),
                                     "max_results": 50})
            return JSONResponse({"events": ev, "tz": self.ctx.tz_name})
        except ToolError as e:
            return JSONResponse({"error": str(e)})

    def routes(self):
        return [Route("/agenda", self.agenda), Route("/auth/start", self.auth_start),
                Route("/auth/callback", self.auth_callback), Route("/auth/disconnect", self.disconnect, methods=["POST"])]
