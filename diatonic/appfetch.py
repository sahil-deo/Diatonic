"""ctx.fetch: how an app panel reads a web page or public API through the server (the browser cannot, and a panel has no network of its own).

Rules: http(s) only, GET only, no headers or body from the app, no private or local addresses (checked on every redirect hop), 2 MB and 15
seconds at most. The domain must be in the app's declared permissions and approved by the user (see appperms.py), on every redirect hop.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

from . import appperms
from .apps.base import ToolError

MAX_BYTES = 2_000_000
TIMEOUT = 15
MAX_REDIRECTS = 3
TEXT_TYPES = ("text/", "application/json", "application/xml", "application/xhtml", "application/javascript", "+json", "+xml")


def _parse(url: str) -> tuple[str, str]:
    p = urlparse(str(url or "").strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("Give a full web address that starts with http:// or https://.")
    if p.username or p.password:
        raise ToolError("Web addresses with a user name or password are not allowed.")
    return p.hostname.lower(), p.geturl()


async def _public(host: str) -> None:
    """Refuse a host that is, or resolves to, this machine or a private network."""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ToolError(f"Can't find the server {host}. Check the address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ToolError(f"{host} is a local or private address, which apps may not reach.")


async def fetch(ctx, app_id: str, url: str, method: str = "GET") -> dict:
    if str(method or "GET").upper() != "GET":
        raise ToolError("ctx.fetch can only read (GET). Sending data to a web service is not supported yet.")
    host, url = _parse(url)
    for _ in range(MAX_REDIRECTS + 1):
        await _public(host)
        await appperms.require(ctx, app_id, "fetch", host)
        try:
            req = ctx.http.build_request("GET", url, headers={"Accept": "*/*", "User-Agent": "Diatonic"}, timeout=TIMEOUT)
            r = await asyncio.wait_for(ctx.http.send(req, stream=True, follow_redirects=False), TIMEOUT)
            try:
                if r.is_redirect and r.headers.get("location"):
                    host, url = _parse(urljoin(url, r.headers["location"]))
                    continue
                kind = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if kind and not any(t in kind for t in TEXT_TYPES):
                    raise ToolError(f"{host} sent {kind}, which ctx.fetch does not return. Use it only for text, HTML and JSON (images can be shown with an img tag).")
                raw = b""
                async for chunk in r.aiter_bytes():
                    raw += chunk
                    if len(raw) > MAX_BYTES:
                        raise ToolError(f"The response from {host} is larger than {MAX_BYTES // 1_000_000} MB.")
            finally:
                await r.aclose()
        except (httpx.TimeoutException, asyncio.TimeoutError):
            raise ToolError(f"{host} did not answer within {TIMEOUT} seconds.")
        except httpx.HTTPError as e:
            raise ToolError(f"Could not reach {host} ({type(e).__name__}). Check the address and the connection.")
        return {"status": r.status_code, "ok": r.is_success, "url": url, "type": kind, "text": raw.decode(r.encoding or "utf-8", "replace")}
    raise ToolError(f"Too many redirects from {host}.")
