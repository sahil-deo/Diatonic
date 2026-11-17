"""The web app: page text extraction and the public-address check."""
from __future__ import annotations

import pytest

from diatonic.apps.base import ToolError
from diatonic.apps.web.app import check_url, html_to_text


def test_a_page_is_cut_down_to_its_text_and_links():
    page = html_to_text('<title>Hi</title><nav>menu</nav><script>x()</script><p>Hello <a href="/a">there</a></p>', "https://e.com")
    assert page["title"] == "Hi" and page["text"] == "Hello there"
    assert page["links"] == [{"text": "there", "url": "https://e.com/a"}]


@pytest.mark.parametrize("url", ["http://localhost:5005", "http://127.0.0.1", "http://192.168.1.1/x", "file:///etc/passwd"])
def test_the_web_app_refuses_local_and_private_addresses(url):
    with pytest.raises(ToolError):
        check_url(url)
