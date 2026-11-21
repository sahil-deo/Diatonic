import asyncio

import pytest

from diatonic import appfetch
from diatonic.apps.base import ToolError
from appsupport import H, declare, user, web, web_pages  # noqa: F401


def post(web, url, **kw):
    return web.post("/api/fetch/tube", json={"url": url, **kw}, headers=H)


def test_a_declared_and_approved_domain_comes_back_with_the_page(web, user, monkeypatch):
    web_pages(monkeypatch, {"https://videos.test/q?x=1": {"status_code": 200, "json": {"items": [1]}}})
    declare(web, {"fetch": ["videos.test"]})
    r = post(web, "https://videos.test/q?x=1").json()["result"]
    assert r["status"] == 200 and '"items"' in r["text"] and len(user.cards) == 1
    post(web, "https://videos.test/q?x=1")
    assert len(user.cards) == 1                                                # approved once, not asked again


def test_a_domain_that_was_not_declared_is_refused_with_what_to_write(web, user, monkeypatch):
    web_pages(monkeypatch, {})
    declare(web, {"fetch": ["videos.test"]})
    r = post(web, "https://evil.test/x")
    assert r.status_code == 400 and '"evil.test"' in r.json()["error"] and "manifest.json" in r.json()["error"] and not user.cards


def test_a_denied_card_stops_the_request(web, user, monkeypatch):
    web_pages(monkeypatch, {})
    declare(web, {"fetch": ["videos.test"]})
    user.says = False
    assert "did not allow" in post(web, "https://videos.test/").json()["error"]


def test_post_other_schemes_private_hosts_and_big_or_binary_answers_are_refused(web, user, monkeypatch):
    web_pages(monkeypatch, {
        "https://videos.test/big": {"status_code": 200, "text": "x" * (appfetch.MAX_BYTES + 1), "headers": {"content-type": "text/plain"}},
        "https://videos.test/img": {"status_code": 200, "content": b"\x89PNG", "headers": {"content-type": "image/png"}}})
    declare(web, {"fetch": ["videos.test", "localhost", "intranet.test"]})

    def err(url, **kw):
        r = post(web, url, **kw)
        assert r.status_code == 400, url
        return r.json()["error"]

    assert "can only read" in err("https://videos.test/q", method="POST")
    assert "http" in err("file:///etc/passwd") and "http" in err("ftp://videos.test/x")
    assert "private" in err("http://localhost:5005/api/state") and "private" in err("https://intranet.test/")
    assert "larger" in err("https://videos.test/big")
    assert "image/png" in err("https://videos.test/img")
    assert "user name" in err("https://me:pw@videos.test/")


def test_a_redirect_to_an_undeclared_domain_is_refused_and_a_private_one_too(web, user, monkeypatch):
    web_pages(monkeypatch, {"https://videos.test/go": {"status_code": 302, "headers": {"location": "https://other.test/x"}},
                            "https://videos.test/in": {"status_code": 302, "headers": {"location": "http://localhost:5005/api/state"}}})
    declare(web, {"fetch": ["videos.test", "localhost"]})
    assert '"other.test"' in post(web, "https://videos.test/go").json()["error"]
    assert "private" in post(web, "https://videos.test/in").json()["error"]


def test_the_real_check_refuses_loopback_and_private_addresses():
    for host in ("localhost", "127.0.0.1", "192.168.1.1", "10.0.0.5", "169.254.169.254", "::1"):
        with pytest.raises(ToolError):
            asyncio.run(appfetch._public(host))


def test_an_unknown_app_is_refused(web):
    assert web.post("/api/fetch/ghost", json={"url": "https://a.test/"}, headers=H).status_code == 404
