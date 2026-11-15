"""Pure helpers for Gmail messages: headers, bodies, attachments, MIME building, text clean-up."""
from __future__ import annotations

import base64
import html as htmllib
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr, getaddresses, parseaddr


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def header(msg: dict, name: str) -> str:
    return next((h["value"] for h in msg.get("payload", {}).get("headers", []) if h["name"].lower() == name.lower()), "")


def addr(s: str) -> dict:
    name, email = parseaddr(s)
    return {"name": name or "", "email": email or s}


def addrs(s: str) -> list[dict]:
    return [{"name": n, "email": e} for n, e in getaddresses([s]) if e]


def walk(payload: dict):
    yield payload
    for p in payload.get("parts", []) or []:
        yield from walk(p)


def bodies(payload: dict) -> tuple[str, str]:
    """(plain text, html) of a message, decoding base64url parts."""
    text, html = [], []
    for p in walk(payload):
        data = (p.get("body") or {}).get("data")
        if not data or p.get("filename"):
            continue
        if p.get("mimeType") == "text/plain":
            text.append(unb64(data).decode("utf-8", "replace"))
        elif p.get("mimeType") == "text/html":
            html.append(unb64(data).decode("utf-8", "replace"))
    return "\n".join(text), "\n".join(html)


def attachments(payload: dict) -> list[dict]:
    return [{"id": p["body"]["attachmentId"], "name": p["filename"], "size": p["body"].get("size", 0), "type": p.get("mimeType", "")}
            for p in walk(payload) if p.get("filename") and (p.get("body") or {}).get("attachmentId")]


def strip_html(html: str) -> str:
    s = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", " ", html)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", s)
    s = re.sub(r"<[^>]+>", " ", s)
    s = htmllib.unescape(s)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n\s*\n+", "\n\n", s)).strip()


def best_text(payload: dict) -> str:
    text, html = bodies(payload)
    return text.strip() or strip_html(html)


def trim_quoted(text: str) -> str:
    """Cut the quoted history off a reply ("On … wrote:" and '>' blocks) so the model sees only the new part."""
    out = []
    for line in text.splitlines():
        if re.match(r"^On .{5,120} wrote:\s*$", line) or re.match(r"^-{2,}\s*Original Message", line, re.I):
            break
        if line.startswith(">"):
            continue
        out.append(line)
    return "\n".join(out).strip()


def when(msg: dict) -> str:
    try:
        return datetime.fromtimestamp(int(msg["internalDate"]) / 1000, timezone.utc).isoformat()
    except (KeyError, ValueError):
        return ""


def norm_message(m: dict, account: str, label: str, *, full: bool = False) -> dict:
    labels = m.get("labelIds", [])
    d = {"ref": f"{account}|{m['threadId']}|{m['id']}", "id": m["id"], "thread_id": m["threadId"], "account": account, "account_label": label,
         "from": addr(header(m, "From")), "to": addrs(header(m, "To")), "cc": addrs(header(m, "Cc")), "subject": header(m, "Subject") or "(no subject)",
         "date": when(m), "snippet": htmllib.unescape(m.get("snippet", "")), "unread": "UNREAD" in labels, "starred": "STARRED" in labels, "labels": labels,
         "message_id": header(m, "Message-ID"), "references": header(m, "References"), "reply_to": header(m, "Reply-To")}
    if full:
        text, html = bodies(m.get("payload", {}))
        d.update(body_text=text.strip() or strip_html(html), body_html=html, attachments=attachments(m.get("payload", {})))
    return d


def norm_thread(t: dict, account: str, label: str, me: str) -> dict:
    msgs = sorted(t.get("messages", []), key=lambda m: int(m.get("internalDate", 0)))
    first, last = msgs[0], msgs[-1]
    labels = sorted({x for m in msgs for x in m.get("labelIds", [])})
    sender = addr(header(last, "From"))
    return {"ref": f"{account}|{t['id']}", "id": t["id"], "account": account, "account_label": label, "subject": header(first, "Subject") or "(no subject)",
            "from": sender, "from_me": sender["email"].lower() == me.lower(), "snippet": htmllib.unescape(last.get("snippet", "")), "date": when(last),
            "unread": any("UNREAD" in m.get("labelIds", []) for m in msgs), "starred": "STARRED" in labels, "count": len(msgs), "labels": labels,
            "participants": sorted({addr(header(m, "From"))["name"] or addr(header(m, "From"))["email"] for m in msgs})[:4]}


def parse_recipients(v) -> list[str]:
    items = v if isinstance(v, list) else re.split(r"[,;]", v or "")
    return [x.strip() for x in items if x and x.strip()]


def valid_email(s: str) -> bool:
    return bool(re.fullmatch(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+", parseaddr(s)[1] or s))


def build_mime(frm: str, to: list[str], subject: str, body: str, *, cc: list[str] | None = None, bcc: list[str] | None = None,
               in_reply_to: str = "", references: str = "", name: str = "") -> bytes:
    msg = EmailMessage()
    msg["From"] = formataddr((name, frm)) if name else frm
    msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if bcc:
        msg["Bcc"] = ", ".join(bcc)
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()
    msg.set_content(body)
    return bytes(msg)


def quote(text: str, who: str, date: str) -> str:
    q = "\n".join("> " + ln for ln in text.strip().splitlines()[:40])
    return f"\n\nOn {date}, {who} wrote:\n{q}"
