"""Turn human phrases into schedules, deterministically.

The 4B model is poor at date arithmetic, so tools take the user's own wording ("in 20 minutes",
"tomorrow 9am", "every weekday at 9") and this module does the maths.

A schedule spec is a dict:
  {"type": "at",    "at": "<ISO datetime>",             "text": "..."}   one-off
  {"type": "every", "seconds": N, "anchor": <epoch>,    "text": "..."}   fixed interval
  {"type": "cron",  "expr": "m h dom mon dow",          "text": "..."}   calendar rule
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, tzinfo

from cronsim import CronSim, CronSimError

DAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2, "thursday": 3,
        "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
DAY_NAMES = "|".join(sorted(DAYS, key=len, reverse=True))
CRON_DOW = {0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 0}  # python weekday -> cron weekday
UNITS = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600,
         "d": 86400, "day": 86400, "w": 604800, "wk": 604800, "week": 604800}
PARTS = {"morning": (9, 0), "noon": (12, 0), "afternoon": (14, 0), "evening": (18, 0), "tonight": (20, 0),
         "midnight": (0, 0), "night": (21, 0)}
TIME = r"(?:(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?|noon|midnight)"
MIN_INTERVAL = 60


class ParseError(ValueError):
    pass


def _unit(u: str) -> int:
    return UNITS[u.rstrip("s") if u.rstrip("s") in UNITS else u]


def _clock(m_h, m_m, m_ap, word: str | None, *, assume_pm_before: int | None = 8) -> tuple[int, int]:
    if word in ("noon", "midnight"):
        return (12, 0) if word == "noon" else (0, 0)
    h, mi = int(m_h), int(m_m or 0)
    ap = (m_ap or "").replace(".", "")
    if ap == "pm" and h < 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    elif not ap and assume_pm_before and 1 <= h < assume_pm_before:
        h += 12  # "at 5" means 5 pm, not 5 am
    if not (0 <= h < 24 and 0 <= mi < 60):
        raise ParseError(f"'{h}:{mi:02d}' isn't a valid time.")
    return h, mi


_T = re.compile(TIME)


def _find_time(text: str) -> tuple[int, int] | None:
    for part, hm in PARTS.items():
        if re.search(rf"\b{part}\b", text) and part not in ("noon", "midnight"):
            return hm
    m = re.search(rf"\b(?:at\s+)?{TIME}\b", text)
    if not m:
        return None
    word = m.group(0).split()[-1] if m.group(0).split()[-1] in ("noon", "midnight") else None
    if not word and not (m.group(1) and (m.group(2) or m.group(3) or re.search(r"\bat\s+\d", m.group(0)))):
        return None  # a bare number isn't a time
    return _clock(m.group(1), m.group(2), m.group(3), word)


def _at(dt: datetime, text: str) -> dict:
    return {"type": "at", "at": dt.isoformat(), "text": text}


def _cron(expr: str, text: str) -> dict:
    try:
        CronSim(expr, datetime(2000, 1, 1))
    except (CronSimError, ValueError) as e:
        raise ParseError(f"Invalid cron expression '{expr}': {e}")
    return {"type": "cron", "expr": expr, "text": text}


def parse_schedule(text: str, now: datetime) -> dict:
    """Parse `text` relative to `now` (timezone-aware). Raises ParseError with a model-friendly message."""
    raw = text.strip()
    t = re.sub(r"\s+", " ", raw.lower().strip(" .,"))
    t = re.sub(r"^(?:remind me |please )", "", t)
    if not t:
        raise ParseError("Say when, e.g. 'in 20 minutes', 'tomorrow 9am' or 'every weekday at 9'.")
    tz = now.tzinfo

    # --- cron expression -------------------------------------------------
    if re.fullmatch(r"[\d*/,\-]+(?: [\d*/,\-a-z?]+){4}", t):
        return _cron(t, f"cron {t}")

    # --- recurring -------------------------------------------------------
    m = re.fullmatch(r"(?:every|each) (\d+) ?(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|[smhdw])", t)
    if m:
        secs = int(m.group(1)) * _unit(m.group(2))
        if secs < MIN_INTERVAL:
            raise ParseError("The shortest interval is 1 minute.")
        return {"type": "every", "seconds": secs, "anchor": now.timestamp(), "text": f"every {m.group(1)} {m.group(2)}"}
    if t in ("hourly", "every hour"):
        return {"type": "every", "seconds": 3600, "anchor": now.timestamp(), "text": "every hour"}
    if t in ("every minute",):
        return {"type": "every", "seconds": 60, "anchor": now.timestamp(), "text": "every minute"}

    m = re.fullmatch(rf"(?:every|each) ?(day|weekday|weekend|{DAY_NAMES})s?(?: (.*))?|daily(?: (.*))?|weekly(?: on ({DAY_NAMES}))?(?: (.*))?", t)
    if m:
        if m.group(1) is not None:
            which, rest = m.group(1), m.group(2) or ""
        elif t.startswith("daily"):
            which, rest = "day", m.group(3) or ""
        else:
            which, rest = (m.group(4) or now.strftime("%A").lower()), m.group(5) or ""
        hm = _find_time(rest) if rest else (9, 0)
        if hm is None:
            raise ParseError(f"I couldn't read a time in '{rest}'. Try 'every {which} at 9am'.")
        dow = {"day": "*", "weekday": "1-5", "weekend": "0,6"}.get(which) or str(CRON_DOW[DAYS[which]])
        label = f"every {which}" + (f" at {hm[0]:02d}:{hm[1]:02d}")
        return _cron(f"{hm[1]} {hm[0]} * * {dow}", label)

    m = re.fullmatch(r"(?:every month|monthly)(?: on the (\d{1,2})(?:st|nd|rd|th)?)?(?: (.*))?", t)
    if m:
        hm = _find_time(m.group(2)) if m.group(2) else (9, 0)
        if hm is None:
            raise ParseError("I couldn't read the time for the monthly schedule.")
        day = int(m.group(1) or 1)
        return _cron(f"{hm[1]} {hm[0]} {day} * *", f"monthly on day {day} at {hm[0]:02d}:{hm[1]:02d}")

    # --- one-off ---------------------------------------------------------
    try:
        iso = datetime.fromisoformat(raw.replace("Z", "+00:00").strip())
    except ValueError:
        iso = None
    if iso is not None:
        iso = iso if iso.tzinfo else iso.replace(tzinfo=tz)
        return _check_future(_at(iso, raw), now)

    m = re.fullmatch(r"in (\d+|an?|half an?) ?(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|[smhdw])", t)
    if m:
        n = {"a": 1, "an": 1}.get(m.group(1), 0.5 if m.group(1).startswith("half") else None)
        n = n if n is not None else int(m.group(1))
        return _check_future(_at(now + timedelta(seconds=n * _unit(m.group(2))), f"in {m.group(1)} {m.group(2)}"), now)
    m = re.fullmatch(r"in (\d+) ?(?:hours?|hrs?|h) (?:and )?(\d+) ?(?:minutes?|mins?|m)", t)
    if m:
        return _check_future(_at(now + timedelta(hours=int(m.group(1)), minutes=int(m.group(2))), t), now)

    hm = _find_time(t)
    day = None
    base = now.replace(second=0, microsecond=0)
    if re.search(r"\btomorrow\b", t):
        day = (base + timedelta(days=1)).date()
    elif re.search(r"\b(today|tonight)\b", t):
        day = base.date()
    elif (dm := re.search(rf"\b(?:next |on |this )?({DAY_NAMES})\b", t)):
        target = DAYS[dm.group(1)]
        ahead = (target - base.weekday()) % 7
        if ahead == 0 and (re.search(r"\bnext\b", t) or hm is None or _past(base, hm)):
            ahead = 7
        day = (base + timedelta(days=ahead)).date()
    elif (nm := re.search(r"\bnext week\b", t)):
        day = (base + timedelta(days=7)).date()
    if day is None and hm is None:
        raise ParseError(f"I couldn't understand '{raw}'. Try 'in 20 minutes', 'tomorrow 9am', 'friday 5pm', "
                         "'every weekday at 9' or an ISO date-time.")
    if hm is None:
        hm = (9, 0)
    if day is None:  # a bare time: today, or tomorrow if it has passed
        day = base.date()
        if _past(base, hm):
            day = (base + timedelta(days=1)).date()
    dt = datetime(day.year, day.month, day.day, hm[0], hm[1], tzinfo=tz)
    return _check_future(_at(dt, raw), now)


def _past(now: datetime, hm: tuple[int, int]) -> bool:
    return (now.hour, now.minute) >= hm


def _check_future(spec: dict, now: datetime) -> dict:
    when = datetime.fromisoformat(spec["at"])
    if when <= now:
        raise ParseError(f"{when.strftime('%a %d %b %H:%M')} has already passed.")
    return spec


def next_run(spec: dict, after: datetime) -> datetime | None:
    """The next fire time strictly after `after`, or None if the schedule is finished."""
    kind = spec["type"]
    if kind == "at":
        when = datetime.fromisoformat(spec["at"])
        return when if when > after else None
    if kind == "every":
        s, anchor = spec["seconds"], spec["anchor"]
        k = math.floor((after.timestamp() - anchor) / s) + 1
        return datetime.fromtimestamp(anchor + max(k, 1) * s, after.tzinfo)
    if kind == "cron":
        return next(CronSim(spec["expr"], after))
    raise ValueError(f"unknown schedule type {kind}")


def human(when: datetime, now: datetime) -> str:
    """'today 17:00', 'tomorrow 09:00', 'Fri 20 Nov 15:00'."""
    d = (when.date() - now.date()).days
    clock = when.strftime("%H:%M")
    if d == 0:
        return f"today {clock}"
    if d == 1:
        return f"tomorrow {clock}"
    if 1 < d < 7:
        return when.strftime("%A ") + clock
    return when.strftime("%a %d %b ") + clock
