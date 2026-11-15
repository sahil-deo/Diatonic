"""API apps: small apps that are data, not code.

An API app is a manifest (base URL, where the key goes, a list of actions) stored with the other interface changes.
One runtime, written and tested once, turns it into agent tools, a panel and server-side calls. That is what makes
app-building workable for a 4B model: it fills in a few checked fields instead of writing a program.

Calls run on the server: no CORS, and the API key stays in the keychain (never in the page or the model's context).
Only HTTPS to the app's own host, never to a local or private address, no redirects, bounded size and time.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from urllib.parse import quote, urlparse

import httpx

from .apps.base import READ, WRITE, App, Tool, ToolError

NAME = re.compile(r"[a-z][a-z0-9-]{1,30}")
ACTION = re.compile(r"[a-z][a-z0-9_]{0,30}")
KEY_IN = ("none", "query", "header", "bearer")
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
MAX_BYTES = 300_000
MAX_ACTIONS = 12


class AppSpecError(ValueError):
    pass


# ---- the manifest ------------------------------------------------------
def parse_params(spec) -> list[dict]:
    """Parameters as a small model writes them: 'q*: City = London, days: Days ahead = 1'.

    `*` marks required, the text after ':' is the label shown in the panel, '= x' is the default. A list of
    {name, label, required, default} dicts is accepted too.
    """
    if isinstance(spec, list):
        items = [p if isinstance(p, dict) else {"name": str(p)} for p in spec]
    else:
        items = []
        for part in re.split(r"\s*,\s*", str(spec or "").strip()):
            if not part:
                continue
            m = re.fullmatch(r"([A-Za-z_][\w.\-\[\]]*)(\*)?\s*(?::\s*([^=]*?))?\s*(?:=\s*(.*))?", part)
            if not m:
                raise AppSpecError(f"Can't read the parameter '{part}'. Write it like: q*: City = London")
            items.append({"name": m.group(1), "required": bool(m.group(2)), "label": (m.group(3) or "").strip(),
                          "default": (m.group(4) or "").strip()})
    out = []
    for p in items:
        name = str(p.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][\w.\-\[\]]{0,40}", name):
            raise AppSpecError(f"'{name}' isn't a valid parameter name.")
        out.append({"name": name, "label": str(p.get("label") or name)[:40], "required": bool(p.get("required")),
                    "default": str(p.get("default") if p.get("default") is not None else "")[:200]})
    return out[:15]


def check_manifest(m) -> dict:
    """Validate and normalise a manifest. Raises AppSpecError with a message the model can act on."""
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            raise AppSpecError("The app definition isn't valid JSON.")
    if not isinstance(m, dict):
        raise AppSpecError("The app definition must be an object.")
    url = str(m.get("base_url") or "").strip().rstrip("/")
    u = urlparse(url)
    if u.scheme != "https" or not u.hostname:
        raise AppSpecError("base_url must be a full https:// address, e.g. https://api.weatherapi.com/v1")
    auth = m.get("auth") or {}
    key_in = str(auth.get("type") or "none")
    if key_in not in KEY_IN:
        raise AppSpecError(f"key_in must be one of {', '.join(KEY_IN)}.")
    key_name = str(auth.get("name") or ("Authorization" if key_in == "bearer" else "")).strip()
    if key_in in ("query", "header") and not re.fullmatch(r"[A-Za-z][\w\-]{0,40}", key_name):
        raise AppSpecError("key_name is needed: the query parameter or header that carries the key, e.g. key or X-API-Key.")
    actions = {}
    for name, a in (m.get("actions") or {}).items():
        if not ACTION.fullmatch(name):
            raise AppSpecError(f"Action name '{name}' must be lowercase letters, digits or _ (e.g. current_weather).")
        method = str(a.get("method") or "GET").upper()
        if method not in METHODS:
            raise AppSpecError(f"method must be one of {', '.join(METHODS)}.")
        path = "/" + str(a.get("path") or "").strip().lstrip("/")
        if "://" in path or ".." in path:
            raise AppSpecError("path is the part after base_url, like /current.json")
        actions[name] = {"title": str(a.get("title") or name.replace("_", " ").capitalize())[:60], "method": method, "path": path,
                         "params": parse_params(a.get("params") or []),
                         "fields": [str(f)[:80] for f in (a.get("fields") or [])][:12],
                         "sample_keys": [str(k)[:80] for k in (a.get("sample_keys") or [])][:60]}
    if len(actions) > MAX_ACTIONS:
        raise AppSpecError(f"An app can have at most {MAX_ACTIONS} actions.")
    return {"title": str(m.get("title") or "API app")[:40], "base_url": url, "auth": {"type": key_in, "name": key_name},
            "description": str(m.get("description") or "")[:200], "actions": actions}


def summary(name: str, m: dict) -> str:
    """One line per app for tool descriptions, so the model can edit what exists instead of guessing."""
    acts = "; ".join(f"{n}: {a['method']} {a['path']}" + (" ?" + ",".join(p["name"] + ("*" if p["required"] else "") for p in a["params"]) if a["params"] else "")
                     for n, a in m["actions"].items()) or "no actions yet"
    return f"{name} ('{m['title']}', {urlparse(m['base_url']).hostname}, key: {m['auth']['type']}): {acts}"


def pick(data, path: str):
    """'current.condition.text' or 'forecast.forecastday.0.day.maxtemp_c' out of a JSON response."""
    cur = data
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def key_paths(data, prefix: str = "", depth: int = 0, out: list | None = None) -> list[str]:
    """Paths to the simple values in a response, for choosing what to show (lists contribute their first item)."""
    out = [] if out is None else out
    if len(out) >= 60 or depth > 4:
        return out
    if isinstance(data, dict):
        for k, v in data.items():
            key_paths(v, f"{prefix}{k}.", depth + 1, out)
    elif isinstance(data, list) and data:
        key_paths(data[0], f"{prefix}0.", depth + 1, out)
    else:
        out.append(prefix.rstrip("."))
    return out


# ---- calling -----------------------------------------------------------
async def _public_host(host: str) -> None:
    """Refuse hosts that resolve to this machine or a private network (no reaching the router or other local services)."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ToolError(f"Can't find the server {host}.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ToolError(f"{host} points to a local or private address, which API apps may not reach.")


async def call(http: httpx.AsyncClient, m: dict, action: str, args: dict, key: str | None) -> dict:
    a = m["actions"].get(action)
    if a is None:
        raise ToolError(f"No action '{action}'. Actions: {', '.join(m['actions']) or 'none'}.")
    args = {k: v for k, v in (args or {}).items() if v not in (None, "")}
    for p in a["params"]:
        if p["name"] not in args and p["default"]:
            args[p["name"]] = p["default"]
    missing = [p["label"] for p in a["params"] if p["required"] and p["name"] not in args]
    if missing:
        raise ToolError(f"Missing: {', '.join(missing)}.", bad_args=True)
    path = a["path"]
    for k in re.findall(r"\{(\w+)\}", path):          # /users/{id}
        if k not in args:
            raise ToolError(f"Missing: {k}.", bad_args=True)
        path = path.replace("{" + k + "}", quote(str(args.pop(k)), safe=""))
    url = m["base_url"] + path
    host = urlparse(url).hostname
    if host != urlparse(m["base_url"]).hostname:
        raise ToolError("That call would leave the app's own server.")
    await _public_host(host)
    headers, params, body = {"Accept": "application/json"}, {}, None
    if a["method"] in ("GET", "DELETE"):
        params = {k: str(v) for k, v in args.items()}
    else:
        body = args
    auth = m["auth"]
    if auth["type"] != "none":
        if not key:
            raise ToolError("This app needs its API key: open the app and add it (or Settings → Apps).")
        if auth["type"] == "query":
            params[auth["name"]] = key
        elif auth["type"] == "header":
            headers[auth["name"]] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    try:
        req = http.build_request(a["method"], url, params=params, json=body, headers=headers, timeout=15)
        r = await http.send(req, stream=True, follow_redirects=False)
        try:
            raw = b""
            async for chunk in r.aiter_bytes():
                raw += chunk
                if len(raw) > MAX_BYTES:
                    raise ToolError("The response was too large.")
        finally:
            await r.aclose()
    except httpx.TimeoutException:
        raise ToolError(f"{host} didn't answer in time.")
    except httpx.HTTPError as e:
        raise ToolError(f"Couldn't reach {host}: {type(e).__name__}.")
    text = raw.decode("utf-8", "replace")
    try:
        data = json.loads(text)
    except ValueError:
        data = text[:4000]
    if r.status_code >= 400:
        msg = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(msg, dict):
            msg = msg.get("message") or json.dumps(msg)[:300]
        safe = str(msg).replace(key, "***") if key else str(msg)   # some APIs echo the key back in errors
        raise ToolError(f"{host} said {r.status_code}: {safe[:300]}", bad_args=r.status_code in (400, 404, 422))
    return {"status": r.status_code, "data": data}


def shaped(a: dict, data):
    """What the agent sees: the chosen fields when there are some, else the whole (clipped) response."""
    if a["fields"] and isinstance(data, (dict, list)):
        return {f: pick(data, f) for f in a["fields"]}
    return data


# ---- one App per manifest -----------------------------------------------
class ApiApp(App):
    icon = "box"
    panel = True
    api_app = True
    module = "/apps/_api.js"     # one generic panel for every API app

    def __init__(self, ctx, name: str, manifest: dict):
        super().__init__(ctx)
        self.id, self.m = name, manifest
        self.name = manifest["title"]
        host = urlparse(manifest["base_url"]).hostname
        self.description = manifest["description"] or f"Made by the agent; calls {host}."
        words = {w.lower() for w in re.findall(r"[A-Za-z]{4,}", manifest["title"] + " " + name.replace("-", " "))}
        self.keywords = sorted(words)
        self.settings = [] if manifest["auth"]["type"] == "none" else [
            {"key": "api_key", "label": "API key", "secret": True, "help": f"Sent only to {host}, from this computer."}]
        self.untrusted_tools = list(manifest["actions"])     # third-party text: such a run can't change the interface
        self.default_allow = []

    def key(self) -> str | None:
        return self.secret("api_key") if self.settings else None

    async def run(self, action: str, args: dict):
        r = await call(self.ctx.http, self.m, action, args, self.key())
        a = self.m["actions"][action]
        if not a["sample_keys"] and isinstance(r["data"], (dict, list)):
            a["sample_keys"] = key_paths(r["data"])
            self.ctx.mods.set_code(self.id, json.dumps(self.m))
        return shaped(a, r["data"])

    async def snapshot(self, n: int) -> dict:
        """The first action that can run without input, showing its chosen values."""
        act = next((k for k, a in self.m["actions"].items() if a["method"] == "GET" and all(p["default"] or not p["required"] for p in a["params"])), None)
        if act is None:
            return {"items": [], "empty": "Open the app to run it (its actions need input)."}
        r = await call(self.ctx.http, self.m, act, {}, self.key())
        a = self.m["actions"][act]
        vals = {f: pick(r["data"], f) for f in a["fields"]} if a["fields"] else {k: v for k, v in (r["data"] or {}).items() if not isinstance(v, (dict, list))} if isinstance(r["data"], dict) else {}
        nice = lambda k: k.split(".")[-1].replace("_", " ")
        return {"stats": [{"label": nice(k), "value": v} for k, v in list(vals.items())[:4]],
                "items": [{"text": f"{nice(k)}: {v}"} for k, v in list(vals.items())[4:4 + n]], "empty": "No values to show."}

    async def status(self) -> dict:
        if self.settings and not self.key():
            return {"connected": False, "detail": "Add the API key in the app."}
        return {"connected": True, "detail": ""}

    def tools(self) -> list[Tool]:
        out = []
        for name, a in self.m["actions"].items():
            props = {p["name"]: {"type": "string", "description": p["label"] + (f" (default {p['default']})" if p["default"] else "")}
                     for p in a["params"]}
            req = [p["name"] for p in a["params"] if p["required"] and not p["default"]]
            out.append(Tool(name, f"{self.name}: {a['title']}.", {"properties": props, "required": req},
                            (lambda n: lambda args: self.run(n, args))(name), READ if a["method"] == "GET" else WRITE,
                            title=a["title"], describe=(lambda t: lambda args: f"{self.name}: {t} {json.dumps(args)[:120]}")(a["title"])))
        return out
