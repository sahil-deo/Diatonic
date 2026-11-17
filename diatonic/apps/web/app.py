"""Web search and page reading: the agent (and the user) can look things up and read pages as plain text.

No browser and no JavaScript: pages are fetched with httpx and cut down to text. Search uses DuckDuckGo's
HTML page by default (no key, no account), Wikipedia as a fallback, or a SearXNG server the user runs.
Everything this app returns is written by someone else, so a run that used it counts as untrusted.
"""
from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
import time
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..base import App, Tool, ToolError

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
MAX_BYTES = 2_000_000
SKIP = {"script", "style", "noscript", "svg", "nav", "header", "footer", "aside", "form", "iframe", "template"}
BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "pre", "blockquote", "table", "ul", "ol"}


def check_url(url: str) -> str:
    """Only public http(s) pages: no localhost, no private network addresses."""
    url = (url or "").strip()
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ToolError("That is not a web address. Use a full http or https URL.")
    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        raise ToolError(f"Could not find {p.hostname}. Check the address.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise ToolError("That address is on your own machine or network. The web app only opens public pages.")
    return url


class _Text(HTMLParser):
    """HTML to readable text plus a list of links."""

    def __init__(self, base: str):
        super().__init__(convert_charrefs=True)
        self.base, self.title, self.out, self.links = base, "", [], []
        self._skip, self._in_title, self._href, self._link_text = 0, False, None, []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in BLOCK:
            self.out.append("\n")
        if tag == "a" and not self._skip:
            self._href, self._link_text = dict(attrs).get("href"), []

    def handle_endtag(self, tag):
        if tag in SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in BLOCK:
            self.out.append("\n")
        if tag == "a" and self._href:
            text = " ".join("".join(self._link_text).split())
            url = urljoin(self.base, self._href)
            if text and url.startswith("http") and len(self.links) < 200 and not any(l["url"] == url for l in self.links):
                self.links.append({"text": text[:100], "url": url})
            self._href = None

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.out.append(data)
            if self._href:
                self._link_text.append(data)

    def text(self) -> str:
        t = re.sub(r"[ \t\r\f\v]+", " ", "".join(self.out))
        return re.sub(r"\n\s*\n+", "\n\n", t).strip()


def html_to_text(html: str, base: str) -> dict:
    p = _Text(base)
    p.feed(html)
    return {"title": " ".join(p.title.split()), "text": p.text(), "links": p.links}


def _strip_tags(s: str) -> str:
    return " ".join(unescape(re.sub(r"<[^>]+>", "", s)).split())


class WebApp(App):
    id = "web"
    name = "Web"
    icon = "search"
    description = "Search the web and read pages as plain text. No JavaScript, no logins."
    panel = True
    keywords = ["search the web", "web search", "google", "look up", "lookup", "browse", "website", "web page", "webpage", "url", "internet",
                "online", "news", "wikipedia", "duckduckgo", "search for", "find out"]
    keyword_patterns = [r"https?://\S+", r"\bwww\.\S+", r"\b[\w-]+\.(com|org|net|io|dev|edu|gov)\b"]
    default_allow = ["search", "read"]
    untrusted_tools = ["search", "read"]
    settings = [
        {"key": "backend", "label": "Search engine", "secret": False, "placeholder": "duckduckgo",
         "help": "duckduckgo (default), wikipedia, or searxng. DuckDuckGo and Wikipedia receive your search words."},
        {"key": "searxng_url", "label": "SearXNG address", "secret": False, "placeholder": "http://localhost:8080",
         "help": "Only used when the search engine is searxng. Your own server must allow the json format."},
        {"key": "max_chars", "label": "Page size limit", "secret": False, "placeholder": "6000",
         "help": "How many characters of a page the agent sees. Smaller is faster for a small model."},
    ]

    def __init__(self, ctx):
        super().__init__(ctx)
        self._last = 0.0
        self._lock = asyncio.Lock()

    def tools(self) -> list[Tool]:
        return [
            Tool("search", "Search the web for current information, facts or pages. Returns titles, links and short snippets. "
                 "Then use read on the best link.",
                 {"properties": {"query": {"type": "string", "description": "What to search for, in a few words"},
                                 "count": {"type": "integer", "description": "How many results, 1 to 10 (default 5)"}},
                  "required": ["query"]}, self.search, title="Search the web", describe=lambda a: f"Search the web for “{a.get('query')}”"),
            Tool("read", "Open a web page and return its text and links. Works for articles and documentation; not for pages that need "
                 "JavaScript or a login.",
                 {"properties": {"url": {"type": "string", "description": "Full address, https://..."}},
                  "required": ["url"]}, self.read, title="Read a web page", describe=lambda a: f"Read {a.get('url')}"),
        ]

    # --- fetching -------------------------------------------------------------------------------------------------

    async def _get(self, url: str, **kw) -> httpx.Response:
        """GET with our own redirect loop, so every hop is checked as a public address."""
        async with httpx.AsyncClient(timeout=15, headers={"User-Agent": UA, "Accept-Language": "en"}, follow_redirects=False) as c:
            for _ in range(5):
                url = await asyncio.to_thread(check_url, url)
                try:
                    r = await c.get(url, **kw) if not kw.get("data") else await c.post(url, **kw)
                except httpx.HTTPError as e:
                    raise ToolError(f"Could not load {urlparse(url).hostname}: {e.__class__.__name__}. Try again or use another page.")
                if r.is_redirect and r.headers.get("location"):
                    url, kw = urljoin(url, r.headers["location"]), {}
                    continue
                return r
        raise ToolError("Too many redirects.")

    async def read(self, a):
        url = await asyncio.to_thread(check_url, a.get("url", ""))
        r = await self._get(url)
        if r.status_code in (401, 403, 429):
            raise ToolError(f"{urlparse(url).hostname} refused the request ({r.status_code}). It may block automated reading or need a login.")
        if r.status_code >= 400:
            raise ToolError(f"The page returned an error ({r.status_code}). Check the address.")
        kind = r.headers.get("content-type", "")
        if not any(k in kind for k in ("html", "text", "xml", "json")):
            raise ToolError(f"This is a {kind.split(';')[0] or 'binary'} file, not a web page. Download it through Files instead.")
        body = r.text[:MAX_BYTES]
        page = html_to_text(body, str(r.url)) if "html" in kind or "xml" in kind else {"title": "", "text": body.strip(), "links": []}
        limit = int(self.setting("max_chars", "6000") or 6000)
        text, cut = page["text"][:limit], len(page["text"]) > limit
        if not text:
            raise ToolError("The page has no readable text. It probably needs JavaScript, which this app does not run.")
        return {"url": str(r.url), "title": page["title"], "text": text + ("\n[page cut short]" if cut else ""), "links": page["links"][:15]}

    # --- searching ------------------------------------------------------------------------------------------------

    async def search(self, a):
        q = (a.get("query") or "").strip()
        if not q:
            raise ToolError("Give some words to search for.")
        n = max(1, min(int(a.get("count") or 5), 10))
        async with self._lock:   # one search at a time, at least a second apart, so DuckDuckGo does not block us
            wait = 1.0 - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            backend = (self.setting("backend", "duckduckgo") or "duckduckgo").strip().lower()
            try:
                results = await {"wikipedia": self._wikipedia, "searxng": self._searxng}.get(backend, self._ddg)(q, n)
            except ToolError:
                if backend != "duckduckgo":
                    raise
                results = []
            if not results and backend == "duckduckgo":   # blocked or changed page: fall back so the agent still gets something
                results = await self._wikipedia(q, n)
        if not results:
            raise ToolError("No results. Try different words.")
        return results

    async def _ddg(self, q: str, n: int) -> list[dict]:
        r = await self._get("https://html.duckduckgo.com/html/", data={"q": q})
        if r.status_code != 200 or "result__a" not in r.text:
            return []
        out = []
        for m in re.finditer(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', r.text, re.S):
            href, title, rest = unescape(m.group(1)), _strip_tags(m.group(2)), m.group(3)
            if "uddg=" in href:
                href = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
            elif href.startswith("//"):
                href = "https:" + href
            snip = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
            if href.startswith("http") and "duckduckgo.com/y.js" not in href:
                out.append({"title": title, "url": href, "snippet": _strip_tags(snip.group(1)) if snip else ""})
            if len(out) >= n:
                break
        return out

    async def _wikipedia(self, q: str, n: int) -> list[dict]:
        r = await self._get(f"https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit={n}&srsearch={quote(q)}")
        if r.status_code != 200:
            raise ToolError("Wikipedia did not answer. Try again in a moment.")
        return [{"title": x["title"], "url": "https://en.wikipedia.org/wiki/" + quote(x["title"].replace(" ", "_")), "snippet": _strip_tags(x["snippet"])}
                for x in r.json().get("query", {}).get("search", [])]

    async def _searxng(self, q: str, n: int) -> list[dict]:
        base = self.setting("searxng_url", "").strip().rstrip("/")
        if not base:
            raise ToolError("Set the SearXNG address in Settings → Web, or pick another search engine.")
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(f"{base}/search", params={"q": q, "format": "json"})
            data = r.json()
        except (httpx.HTTPError, ValueError):
            raise ToolError("Could not get results from your SearXNG server. Check the address and that it allows the json format.")
        return [{"title": x.get("title", ""), "url": x.get("url", ""), "snippet": x.get("content", "")} for x in data.get("results", [])[:n]]

    # --- panel routes ---------------------------------------------------------------------------------------------

    async def _search_route(self, req: Request):
        try:
            return JSONResponse({"results": await self.search({"query": req.query_params.get("q", ""), "count": 10})})
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    async def _read_route(self, req: Request):
        try:
            return JSONResponse(await self.read({"url": req.query_params.get("url", "")}))
        except ToolError as e:
            return JSONResponse({"error": str(e)}, 400)

    def routes(self):
        return [Route("/search", self._search_route), Route("/read", self._read_route)]
