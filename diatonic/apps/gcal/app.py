"""Google Calendar for any number of Google accounts: agent tools and the REST API behind the calendar UI."""
from __future__ import annotations

import asyncio
from datetime import datetime, time as dtime, timedelta
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ...google import GoogleAuthError, has_calendar
from ...google.endpoints import urls
from ..base import DESTRUCTIVE, WRITE, App, Tool, ToolError
from . import events as E


def _s(desc: str) -> dict:
    return {"type": "string", "description": desc}


TIME = "ISO 8601: 2026-11-20T15:00:00 or 2026-11-20"
ACCT = _s("Account label or email; omit to use all (reads) or the default (writes)")
EVENT = _s("The event's `ref` from list_events")


class GCalApp(App):
    private = True   # a cloud model must ask before reading or changing it
    id = "gcal"
    name = "Calendar"
    icon = "calendar"
    description = "Google Calendar across all your connected Google accounts: see, create, move and delete events, find free time."
    panel = True
    keywords = ["calendar", "calendars", "event", "events", "meeting", "meetings", "schedule", "appointment", "agenda", "free", "busy",
                "availability", "invite", "invitation", "reschedule", "today", "tomorrow", "tonight", "week", "weekend", "monday",
                "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "slot", "rsvp"]
    settings: list[dict] = []   # accounts are managed in Settings → Accounts

    @property
    def ga(self):
        return self.ctx.google

    @property
    def tzname(self) -> str:
        return self.ctx.tz_name

    # ---- accounts and calendars ------------------------------------------------------
    async def status(self) -> dict:
        accts = self.ga.with_service("calendar")
        if not accts:
            return {"connected": False, "detail": "No Google account connected for Calendar yet.", "action": {"label": "Connect an account", "href": "/?settings=accounts"}}
        bad = [a for a in accts if a.get("needs_reauth")]
        if bad:
            return {"connected": False, "detail": f"Reconnect {bad[0]['email']} in Settings → Accounts.", "action": {"label": "Fix", "href": "/?settings=accounts"}}
        return {"connected": True, "detail": f"{len(accts)} account{'s' if len(accts) != 1 else ''}: {', '.join(self.ga.label(a) for a in accts)}"}

    async def calendars(self, accounts: list[dict] | None = None) -> list[dict]:
        accounts = accounts if accounts is not None else self.ga.with_service("calendar")
        prefs = self.ga.cfg["calendar_prefs"]

        async def one(a):
            key = f"cal:list:{a['email']}"
            items = self.ga.cached(key, 60)
            if items is None:
                d = await self.ga.json(a["email"], "GET", f"{urls()['cal']}/users/me/calendarList", params={"maxResults": 100})
                items = self.ga.store(key, d.get("items", []), 60)
            out = []
            for c in items:
                if c.get("hidden") or c.get("deleted"):
                    continue
                p = prefs.get(f"{a['email']}|{c['id']}", {})
                out.append({"account": a["email"], "account_label": self.ga.label(a), "account_color": a["color"], "id": c["id"],
                            "name": c.get("summaryOverride") or c.get("summary") or c["id"], "color": p.get("color") or (a["color"] if c.get("primary") else c.get("backgroundColor")) or a["color"],
                            "primary": bool(c.get("primary")), "role": c.get("accessRole", "reader"), "tz": c.get("timeZone"),
                            "visible": p.get("visible", c.get("selected", c.get("primary", False)) is not False)})
            return out

        res = await asyncio.gather(*(one(a) for a in accounts), return_exceptions=True)
        errors = [str(r) for r in res if isinstance(r, Exception)]
        cals = [c for r in res if not isinstance(r, Exception) for c in r]
        self._last_errors = errors
        return cals

    def _cal(self, cals: list[dict], account: str, cal_id: str) -> dict:
        c = next((c for c in cals if c["account"].lower() == account.lower() and c["id"] == cal_id), None)
        if not c:
            raise ToolError(f"Calendar {cal_id} wasn't found on {account}.")
        return c

    async def events_in(self, start: str, end: str, *, accounts: list[dict] | None = None, calendar: str | None = None, q: str | None = None,
                        only_visible: bool = True) -> tuple[list[dict], list[str]]:
        accounts = accounts if accounts is not None else self.ga.with_service("calendar")
        cals = await self.calendars(accounts)
        errors = list(getattr(self, "_last_errors", []))
        todo = [c for c in cals if (c["visible"] or not only_visible) and (not calendar or calendar.lower() in (c["id"].lower(), c["name"].lower()))]
        acct = {a["email"]: a for a in accounts}

        async def one(c):
            params = {"timeMin": start, "timeMax": end, "singleEvents": "true", "orderBy": "startTime", "maxResults": 250}
            if q:
                params["q"] = q
            d = await self.ga.json(c["account"], "GET", f"{urls()['cal']}/calendars/{quote(c['id'], safe='')}/events", params=params)
            return [E.norm_event(e, acct[c["account"]], c, c["account_label"]) for e in d.get("items", []) if e.get("status") != "cancelled"]

        res = await asyncio.gather(*(one(c) for c in todo), return_exceptions=True)
        out = []
        for c, r in zip(todo, res):
            if isinstance(r, Exception):
                errors.append(f"{c['account_label']}: {r}")
            else:
                out += r
        out.sort(key=lambda e: (e["start"] if not e["all_day"] else e["start"] + "T00:00", e["summary"]))
        return out, errors

    def _brief(self, e: dict) -> dict:
        multi = len(self.ga.with_service("calendar")) > 1
        d = {"ref": e["ref"], "title": e["summary"], "start": e["start"], "end": e["end"], "all_day": e["all_day"] or None, "where": e["location"] or None,
             "account": e["account_label"] if multi else None, "calendar": e["calendar_name"] if e["calendar_name"] != e["account"] else None,
             "meet": bool(e["meet_url"]) or None, "attendees": len(e["attendees"]) or None, "repeats": e["recurring"] or None,
             "you": e["my_response"] if e["my_response"] not in (None, "accepted") else None}
        return {k: v for k, v in d.items() if v is not None}

    def _range(self, a: dict) -> tuple[str, str]:
        now = self.ctx.now()
        s = E.parse_when(a["start"], self.ctx.tz) if a.get("start") else now
        e = E.parse_when(a["end"], self.ctx.tz) if a.get("end") else (s if isinstance(s, datetime) else now) + timedelta(days=7)
        s = s if isinstance(s, datetime) else datetime.combine(s, dtime.min, tzinfo=self.ctx.tz)
        e = e if isinstance(e, datetime) else datetime.combine(e, dtime.min, tzinfo=self.ctx.tz)
        if a.get("end") and len(a["end"].strip()) == 10:
            e += timedelta(days=1)   # a bare end date means "through that day"
        return s.isoformat(), e.isoformat()

    def _guard(self, fn):
        try:
            return fn()
        except ValueError as e:
            raise ToolError(str(e))

    # ---- tools ---------------------------------------------------------------------
    def tools(self) -> list[Tool]:
        ev = {"summary": _s("Event title"), "start": _s(TIME), "end": _s("End, same format as start"), "description": _s("Description"),
              "location": _s("Location"), "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee emails"},
              "calendar": _s("Calendar name (default: the account's main calendar)"), "account": ACCT,
              "add_meet": {"type": "boolean", "description": "Add a Google Meet link"}, "repeat": {"type": "string", "enum": list(E.RRULES), "description": "Repeat"}}
        return [
            Tool("list_calendars", "List calendars across the connected Google accounts.", {"properties": {"account": ACCT}}, self.list_calendars),
            Tool("list_events", "List events in a time range, soonest first, across all accounts unless one is named. Defaults to the next 7 days.",
                 {"properties": {"start": _s(TIME), "end": _s(TIME), "account": ACCT, "calendar": _s("Calendar name"), "max_results": {"type": "integer", "minimum": 1, "maximum": 50}}},
                 self.list_events),
            Tool("search_events", "Search events by text (title, description, location).",
                 {"properties": {"query": _s("Search text"), "start": _s(TIME), "end": _s(TIME), "account": ACCT}, "required": ["query"]}, self.search_events),
            Tool("find_free_time", "Find free slots of a given length within working hours, counting busy time on ALL connected accounts.",
                 {"properties": {"start": _s("First day"), "end": _s("Last day"), "duration_minutes": {"type": "integer", "minimum": 5, "maximum": 480},
                                 "day_start": _s("Working day start HH:MM, default 09:00"), "day_end": _s("Working day end HH:MM, default 18:00"), "account": ACCT},
                  "required": ["start", "end"]}, self.find_free),
            Tool("create_event", "Create a calendar event. A date (no time) as start makes it all-day; if end is omitted it lasts one hour. "
                 "With several accounts, pass `account` or the default account is used.",
                 {"properties": ev, "required": ["summary", "start"]}, self.create_event, WRITE,
                 describe=lambda a: f"Create “{a.get('summary')}” at {a.get('start')}" + (f" on {a['account']}" if a.get("account") else "")),
            Tool("update_event", "Change an event. Only pass fields to change. Changing start without end keeps the duration.",
                 {"properties": {"event": EVENT, **{k: ev[k] for k in ("summary", "start", "end", "description", "location", "attendees")}}, "required": ["event"]},
                 self.update_event, WRITE, describe=lambda a: "Update event " + ", ".join(f"{k}={v}" for k, v in a.items() if k != "event")),
            Tool("delete_event", "Delete an event.", {"properties": {"event": EVENT}, "required": ["event"]}, self.delete_event, DESTRUCTIVE,
                 describe=lambda a: f"Delete event {a.get('event')}"),
            Tool("respond_to_event", "Accept, decline or tentatively accept an invitation.",
                 {"properties": {"event": EVENT, "response": {"type": "string", "enum": ["accepted", "declined", "tentative"]}}, "required": ["event", "response"]},
                 self.respond, WRITE, describe=lambda a: f"{a.get('response', 'Respond').capitalize()} invitation {a.get('event')}"),
        ]

    async def snapshot(self, n: int) -> dict:
        now = self.ctx.now()
        evs, errors = await self.events_in(now.isoformat(), (now + timedelta(days=7)).isoformat())
        evs = sorted(evs, key=lambda e: e["start"])
        def when(e):
            d = datetime.fromisoformat(e["start"]) if not e["all_day"] else datetime.fromisoformat(e["start"] + "T00:00:00").replace(tzinfo=now.tzinfo)
            d = d.astimezone(now.tzinfo)
            day = "Today" if d.date() == now.date() else "Tomorrow" if d.date() == (now + timedelta(days=1)).date() else d.strftime("%a %d")
            return f"{day}" + ("" if e["all_day"] else d.strftime(" %H:%M"))
        return {"items": [{"text": e["summary"], "sub": when(e) + (f" · {e['location']}" if e["location"] else ""), "open": {"item": e["ref"]},
                           "tone": "ok" if e["start"][:10] == now.date().isoformat() else None} for e in evs[:n]],
                "empty": errors[0] if errors and not evs else "Nothing in the next 7 days."}

    async def list_calendars(self, a):
        accts = self.ga.pick(a.get("account"), "calendar", many=True)
        cals = await self.calendars(accts)
        if not cals and getattr(self, "_last_errors", None):
            raise ToolError(self._last_errors[0])    # don't pretend there are no calendars when every account failed
        return [{"account": c["account_label"], "name": c["name"], "primary": c["primary"] or None, "access": c["role"], "shown": c["visible"]}
                for c in cals]

    async def list_events(self, a):
        accts = self.ga.pick(a.get("account"), "calendar", many=True)
        s, e = self._guard(lambda: self._range(a))
        evs, errors = await self.events_in(s, e, accounts=accts, calendar=a.get("calendar"))
        evs = evs[: a.get("max_results", 25)]
        return {"events": [self._brief(x) for x in evs] or "No events in that range.", "problems": errors or None}

    async def search_events(self, a):
        accts = self.ga.pick(a.get("account"), "calendar", many=True)
        now = self.ctx.now()
        a = {"start": (now - timedelta(days=30)).isoformat(), "end": (now + timedelta(days=180)).isoformat(), **a}
        s, e = self._guard(lambda: self._range(a))
        evs, errors = await self.events_in(s, e, accounts=accts, q=a["query"], only_visible=False)
        return {"events": [self._brief(x) for x in evs[:25]] or "No events match.", "problems": errors or None}

    async def find_free(self, a):
        accts = self.ga.pick(a.get("account"), "calendar", many=True)
        tz = self.ctx.tz
        s, e = self._guard(lambda: E.parse_when(a["start"], tz)), self._guard(lambda: E.parse_when(a["end"], tz))
        s_day = s.date() if isinstance(s, datetime) else s
        e_day = e.date() if isinstance(e, datetime) else e
        t0 = lambda t: dtime(*map(int, t.split(":")))
        tmin = datetime.combine(s_day, dtime.min, tzinfo=tz)
        tmax = datetime.combine(e_day + timedelta(days=1), dtime.min, tzinfo=tz)
        cals = await self.calendars(accts)

        async def busy(acc):
            ids = [{"id": c["id"]} for c in cals if c["account"] == acc["email"] and c["role"] in ("owner", "writer") and c["visible"]] or [{"id": "primary"}]
            d = await self.ga.json(acc["email"], "POST", f"{urls()['cal']}/freeBusy", json={"timeMin": tmin.isoformat(), "timeMax": tmax.isoformat(), "items": ids})
            return [(datetime.fromisoformat(b["start"].replace("Z", "+00:00")), datetime.fromisoformat(b["end"].replace("Z", "+00:00")))
                    for c in d["calendars"].values() for b in c.get("busy", [])]

        res = await asyncio.gather(*(busy(x) for x in accts), return_exceptions=True)
        failed = [str(r) for r in res if isinstance(r, Exception)]
        if failed and len(failed) == len(res):
            raise ToolError(failed[0])
        all_busy = [b for r in res if not isinstance(r, Exception) for b in r]
        slots = E.free_slots(all_busy, s_day, e_day, timedelta(minutes=a.get("duration_minutes", 30)), t0(a.get("day_start", "09:00")),
                             t0(a.get("day_end", "18:00")), self.ctx.now(), tz)
        return {"free": [{"from": f.isoformat(), "to": t.isoformat()} for f, t in slots] or "No free slot found.",
                "counted_accounts": [self.ga.label(x) for x in accts], "problems": failed or None}

    async def _account_calendar(self, account: str | None, calendar: str | None):
        acc = self.ga.pick(account, "calendar", many=False)[0]
        cals = await self.calendars([acc])
        want = (calendar or "").strip().lower()
        if want in ("primary", "main", "default", "my calendar", "calendar", "personal", acc["email"].lower(), (acc.get("label") or "").lower(), (acc.get("name") or "").lower()):
            want = ""    # "the Home calendar" usually means the Home *account's* main calendar
        cal = next((c for c in cals if want and want in (c["id"].lower(), c["name"].lower()) and c["role"] in E.WRITE_ROLES), None) if want else \
            next((c for c in cals if c["primary"]), None) or next((c for c in cals if c["role"] in E.WRITE_ROLES), None)
        if not cal:
            raise ToolError(f"No writable calendar{' called ' + calendar if calendar else ''} on {acc['email']}.")
        return acc, cal

    async def create_event(self, a):
        acc, cal = await self._account_calendar(a.get("account"), a.get("calendar"))
        tz = self.ctx.tz
        s = self._guard(lambda: E.parse_when(a["start"], tz))
        all_day = not isinstance(s, datetime)
        if a.get("end"):
            en = self._guard(lambda: E.parse_when(a["end"], tz))
            if all_day and en == s:
                en = en + timedelta(days=1)    # Google's all-day end date is exclusive
        else:
            en = s + (timedelta(days=1) if all_day else timedelta(hours=1))
        f = {"summary": a["summary"], "all_day": all_day, "start": s.isoformat(), "end": en.isoformat(), "description": a.get("description"),
             "location": a.get("location"), "attendees": a.get("attendees"), "repeat": a.get("repeat"), "meet": a.get("add_meet")}
        body = self._guard(lambda: E.build_body(f, self.tzname))
        params = {"sendUpdates": "all"} if a.get("attendees") else {}
        if a.get("add_meet"):
            params["conferenceDataVersion"] = 1
        r = await self.ga.json(acc["email"], "POST", f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events", json=body, params=params)
        return {"created": self._brief(E.norm_event(r, acc, cal, self.ga.label(acc)))}

    async def _locate(self, ref: str):
        try:
            email, cal_id, eid = E.parse_ref(ref)
        except ValueError as e:
            raise ToolError(str(e))
        acc = self.ga.get(email)
        if not acc or not has_calendar(acc):
            raise ToolError(f"{email} isn't connected for Calendar.")
        cals = await self.calendars([acc])
        return acc, self._cal(cals, email, cal_id), eid

    async def update_event(self, a):
        acc, cal, eid = await self._locate(a["event"])
        base = f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events/{quote(eid, safe='')}"
        f = {k: a.get(k) for k in ("summary", "description", "location", "attendees")}
        if a.get("start"):
            cur = await self.ga.json(acc["email"], "GET", base)
            tz = self.ctx.tz
            s = self._guard(lambda: E.parse_when(a["start"], tz))
            all_day = not isinstance(s, datetime)
            if a.get("end"):
                en = self._guard(lambda: E.parse_when(a["end"], tz))
            else:
                old_s = E.parse_when(cur["start"].get("dateTime") or cur["start"]["date"], tz)
                old_e = E.parse_when(cur["end"].get("dateTime") or cur["end"]["date"], tz)
                en = s + (old_e - old_s)
            f.update(all_day=all_day, start=s.isoformat(), end=en.isoformat())
        elif a.get("end"):
            en = self._guard(lambda: E.parse_when(a["end"], self.ctx.tz))
            f.update(all_day=not isinstance(en, datetime), end=en.isoformat())
        body = self._guard(lambda: E.build_body({k: v for k, v in f.items() if v is not None}, self.tzname))
        if not body:
            raise ToolError("Nothing to change.")
        r = await self.ga.json(acc["email"], "PATCH", base, json=body, params={"sendUpdates": "all"} if body.get("attendees") else {})
        return {"updated": self._brief(E.norm_event(r, acc, cal, self.ga.label(acc)))}

    async def delete_event(self, a):
        acc, cal, eid = await self._locate(a["event"])
        await self.ga.json(acc["email"], "DELETE", f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events/{quote(eid, safe='')}")
        return {"deleted": a["event"]}

    async def respond(self, a):
        acc, cal, eid = await self._locate(a["event"])
        base = f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events/{quote(eid, safe='')}"
        cur = await self.ga.json(acc["email"], "GET", base)
        atts = cur.get("attendees")
        if not atts or not any(x.get("self") for x in atts):
            raise ToolError("You aren't an invitee on that event.")
        for x in atts:
            if x.get("self"):
                x["responseStatus"] = a["response"]
        await self.ga.json(acc["email"], "PATCH", base, json={"attendees": atts})
        return {"responded": a["response"]}

    # ---- REST for the calendar UI ---------------------------------------------------------
    @staticmethod
    def _err(e: Exception, status: int = 400):
        return JSONResponse({"error": str(e)}, status)

    async def r_calendars(self, request: Request):
        accts = self.ga.with_service("calendar")
        cals = await self.calendars(accts)
        return JSONResponse({"calendars": cals, "errors": getattr(self, "_last_errors", []), "default_account": self.ga.cfg["default_account"],
                             "accounts": [{"email": a["email"], "label": self.ga.label(a), "color": a["color"]} for a in accts]})

    async def r_prefs(self, request: Request):
        b = await request.json()
        key = f"{b['account']}|{b['id']}"
        p = self.ga.cfg["calendar_prefs"].setdefault(key, {})
        for k in ("visible", "color"):
            if k in b:
                p[k] = b[k]
        self.ctx.config.save()
        return JSONResponse({"ok": True})

    async def r_events(self, request: Request):
        q = request.query_params
        try:
            evs, errors = await self.events_in(q["start"], q["end"], q=q.get("q") or None, only_visible=q.get("all") != "1")
        except (KeyError, ToolError) as e:
            return self._err(e)
        return JSONResponse({"events": evs, "errors": errors})

    async def r_get(self, request: Request):
        try:
            acc, cal, eid = await self._locate(request.query_params["ref"])
            r = await self.ga.json(acc["email"], "GET", f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events/{quote(eid, safe='')}")
        except (KeyError, ValueError, ToolError) as e:
            return self._err(e)
        return JSONResponse(E.norm_event(r, acc, cal, self.ga.label(acc)))

    async def r_create(self, request: Request):
        b = await request.json()
        try:
            acc, cal = await self._account_calendar(b.get("account"), b.get("calendar"))
            all_day = bool(b.get("all_day"))
            if not b.get("summary", "").strip():
                b["summary"] = "(no title)"
            body = E.build_body({**b, "all_day": all_day}, b.get("tz") or self.tzname)
            params = {"sendUpdates": "all"} if b.get("attendees") else {}
            if b.get("meet"):
                params["conferenceDataVersion"] = 1
            r = await self.ga.json(acc["email"], "POST", f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events", json=body, params=params)
        except (ValueError, KeyError, ToolError) as e:
            return self._err(e)
        return JSONResponse(E.norm_event(r, acc, cal, self.ga.label(acc)))

    async def r_patch(self, request: Request):
        b = await request.json()
        try:
            acc, cal, eid = await self._locate(b["ref"])
            tz = b.get("tz") or self.tzname
            all_day = bool(b.get("all_day"))
            base_url = f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events"
            body = E.build_body({k: b.get(k) for k in ("summary", "description", "location", "attendees", "start", "end", "meet")} | {"all_day": all_day}, tz)
            params = {"sendUpdates": "all"} if b.get("attendees") else {}
            if b.get("meet"):
                params["conferenceDataVersion"] = 1
            target = eid
            if b.get("scope") == "all":
                cur = await self.ga.json(acc["email"], "GET", f"{base_url}/{quote(eid, safe='')}")
                target = cur.get("recurringEventId") or eid
                if cur.get("recurringEventId") and ("start" in body or "end" in body):   # shift the whole series by the same amount
                    root = await self.ga.json(acc["email"], "GET", f"{base_url}/{quote(target, safe='')}")
                    parse = lambda o: E.parse_when(o.get("dateTime") or o["date"], self.ctx.tz)
                    if "start" in body:
                        body["start"] = E.when_obj((parse(root["start"]) + (parse(body["start"]) - parse(cur["start"]))).isoformat(), all_day, tz)
                    if "end" in body:
                        body["end"] = E.when_obj((parse(root["end"]) + (parse(body["end"]) - parse(cur["end"]))).isoformat(), all_day, tz)
            r = await self.ga.json(acc["email"], "PATCH", f"{base_url}/{quote(target, safe='')}", json=body, params=params)
        except (ValueError, KeyError, ToolError) as e:
            return self._err(e)
        return JSONResponse(E.norm_event(r, acc, cal, self.ga.label(acc)))

    async def r_delete(self, request: Request):
        q = request.query_params
        try:
            acc, cal, eid = await self._locate(q["ref"])
            url = f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events"
            if q.get("scope") == "all":
                cur = await self.ga.json(acc["email"], "GET", f"{url}/{quote(eid, safe='')}")
                eid = cur.get("recurringEventId") or eid
            await self.ga.json(acc["email"], "DELETE", f"{url}/{quote(eid, safe='')}")
        except (KeyError, ToolError) as e:
            return self._err(e)
        return JSONResponse({"ok": True})

    async def r_rsvp(self, request: Request):
        b = await request.json()
        try:
            return JSONResponse(await self.respond({"event": b["ref"], "response": b["response"]}))
        except (KeyError, ToolError) as e:
            return self._err(e)

    async def r_quickadd(self, request: Request):
        b = await request.json()
        try:
            acc, cal = await self._account_calendar(b.get("account"), b.get("calendar"))
            r = await self.ga.json(acc["email"], "POST", f"{urls()['cal']}/calendars/{quote(cal['id'], safe='')}/events/quickAdd", params={"text": b["text"]})
        except (KeyError, ToolError) as e:
            return self._err(e)
        return JSONResponse(E.norm_event(r, acc, cal, self.ga.label(acc)))

    def routes(self):
        return [Route("/calendars", self.r_calendars), Route("/calendar-prefs", self.r_prefs, methods=["PUT"]), Route("/events", self.r_events),
                Route("/events", self.r_create, methods=["POST"]), Route("/event", self.r_get), Route("/event", self.r_patch, methods=["PATCH"]),
                Route("/event", self.r_delete, methods=["DELETE"]), Route("/rsvp", self.r_rsvp, methods=["POST"]),
                Route("/quickadd", self.r_quickadd, methods=["POST"])]
