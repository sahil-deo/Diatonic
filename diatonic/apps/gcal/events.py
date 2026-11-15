"""Pure helpers for Google Calendar events: refs, normalisation, request bodies, recurrence, free-time maths."""
from __future__ import annotations

import uuid
from datetime import date, datetime, time as dtime, timedelta, tzinfo

RRULES = {"daily": "RRULE:FREQ=DAILY", "weekly": "RRULE:FREQ=WEEKLY", "monthly": "RRULE:FREQ=MONTHLY", "yearly": "RRULE:FREQ=YEARLY",
          "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"}
WRITE_ROLES = {"owner", "writer"}


def make_ref(account: str, cal_id: str, event_id: str) -> str:
    return f"{account}|{cal_id}|{event_id}"


def parse_ref(ref: str) -> tuple[str, str, str]:
    parts = (ref or "").strip().split("|", 2)
    if len(parts) != 3 or not all(parts):
        raise ValueError("That isn't an event reference. Use the `ref` shown by list_events (account|calendar|id).")
    return parts[0], parts[1], parts[2]


def is_all_day(e: dict) -> bool:
    return "date" in e.get("start", {})


def norm_event(e: dict, account: dict, cal: dict, label: str) -> dict:
    s, en = e.get("start", {}), e.get("end", {})
    attendees = [{"email": a.get("email", ""), "name": a.get("displayName", ""), "response": a.get("responseStatus", "needsAction"),
                  "self": bool(a.get("self")), "organizer": bool(a.get("organizer"))} for a in e.get("attendees", [])]
    me = next((a for a in attendees if a["self"]), None)
    meet = e.get("hangoutLink") or next((p.get("uri") for p in e.get("conferenceData", {}).get("entryPoints", []) if p.get("entryPointType") == "video"), None)
    return {"ref": make_ref(account["email"], cal["id"], e["id"]), "id": e["id"], "account": account["email"], "account_label": label,
            "calendar_id": cal["id"], "calendar_name": cal.get("name", ""), "color": cal.get("color") or account.get("color"),
            "summary": e.get("summary") or "(no title)", "start": s.get("dateTime") or s.get("date"), "end": en.get("dateTime") or en.get("date"),
            "all_day": "date" in s, "location": e.get("location") or "", "description": (e.get("description") or "")[:2000],
            "attendees": attendees, "meet_url": meet, "link": e.get("htmlLink"), "recurring": bool(e.get("recurringEventId") or e.get("recurrence")),
            "recurring_id": e.get("recurringEventId"), "status": e.get("status"), "my_response": me["response"] if me else None,
            "organizer_self": bool((e.get("organizer") or {}).get("self")) or not attendees,
            "can_edit": cal.get("role", "owner") in WRITE_ROLES and e.get("eventType", "default") in ("default", "outOfOffice")}


def when_obj(value: str, all_day: bool, tzname: str) -> dict:
    """UI/tool value -> Google start/end object. All-day values are dates; others are RFC 3339 date-times."""
    if all_day:
        return {"date": value[:10]}
    return {"dateTime": value, "timeZone": tzname}


def build_body(f: dict, tzname: str) -> dict:
    """Request body from user-facing fields (only fields present are included, so it works for patches too)."""
    body: dict = {}
    for k in ("summary", "description", "location"):
        if f.get(k) is not None:
            body[k] = f[k]
    all_day = bool(f.get("all_day"))
    if f.get("start"):
        body["start"] = when_obj(f["start"], all_day, tzname)
    if f.get("end"):
        body["end"] = when_obj(f["end"], all_day, tzname)
    if f.get("attendees") is not None:
        body["attendees"] = [{"email": a} for a in f["attendees"] if a]
    rep = f.get("repeat")
    if rep and rep != "none":
        if rep not in RRULES:
            raise ValueError(f"Unknown repeat '{rep}'. Use {', '.join(RRULES)} or none.")
        body["recurrence"] = [RRULES[rep]]
    if f.get("meet"):
        body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex, "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    return body


def parse_when(v: str, tz: tzinfo) -> date | datetime:
    v = v.strip()
    try:
        if len(v) == 10:
            return date.fromisoformat(v)
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(f"Couldn't read '{v}' as a date/time. Use ISO 8601 like 2026-11-20T15:00:00 or 2026-11-20.")
    return d if d.tzinfo else d.replace(tzinfo=tz)


def free_slots(busy: list[tuple[datetime, datetime]], start_day: date, end_day: date, duration: timedelta, day_start: dtime, day_end: dtime,
               now: datetime, tz: tzinfo, limit: int = 10) -> list[tuple[datetime, datetime]]:
    """Free gaps of at least `duration` inside working hours, given busy intervals (any number of calendars/accounts)."""
    busy = sorted(busy)
    merged: list[list[datetime]] = []
    for s, e in busy:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    out, day = [], start_day
    while day <= end_day and len(out) < limit:
        cur = max(datetime.combine(day, day_start, tzinfo=tz), now)
        end = datetime.combine(day, day_end, tzinfo=tz)
        for bs, be in merged:
            if be <= cur or bs >= end:
                continue
            if bs - cur >= duration:
                out.append((cur, bs))
            cur = max(cur, be)
        if end - cur >= duration:
            out.append((cur, end))
        day += timedelta(days=1)
    return out[:limit]
