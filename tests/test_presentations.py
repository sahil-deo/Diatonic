import asyncio
from pathlib import Path
import pytest
from starlette.testclient import TestClient

from diatonic.apps.base import ToolError
from diatonic.apps.presentations import create as make_presentations
from diatonic.apps.presentations.app import dump_markdown, parse_frontmatter, split_slides
from diatonic.server import create_app


def call(app, tool_name, /, **args):
    t = next(t for t in app.tools() if t.name == tool_name)
    return asyncio.run(t.handler(args))


def test_split_slides_basic_and_frontmatter():
    raw = """---
title: My Deck
author: Diatonic
---
# Welcome

Introduction slide

---

## Second Slide

- Point 1
- Point 2

<!-- note: Remember to pause here -->

---

# Code Slide

```python
# code block with dashes:
# ---
val = 42
```

Note: Explain the variable
"""
    meta, slides = split_slides(raw)
    assert meta.get("title") == "My Deck"
    assert meta.get("author") == "Diatonic"
    assert len(slides) == 3

    assert slides[0]["title"] == "Welcome"
    assert "Introduction slide" in slides[0]["content"]
    assert slides[0]["notes"] == ""

    assert slides[1]["title"] == "Second Slide"
    assert "Point 1" in slides[1]["content"]
    assert slides[1]["notes"] == "Remember to pause here"
    assert "<!-- note:" not in slides[1]["content"]

    assert slides[2]["title"] == "Code Slide"
    assert "val = 42" in slides[2]["content"]
    assert "# ---" in slides[2]["content"]  # didn't split inside code block
    assert "Explain the variable" in slides[2]["notes"]


def test_presentations_app_tools_lifecycle(env, tmp_path):
    ctx, reg, _, db = env
    folder = tmp_path / "slides"
    ctx.config.data["apps"]["presentations"] = {"enabled": True, "settings": {"folder": str(folder)}}
    app = make_presentations(ctx)

    # 1. Create presentation
    res = call(app, "create_presentation", title="AI Roadmap",
               content="# AI Roadmap 2027\n\n---\n\n## Goals\n\n- Build Diatonic")
    assert res["created"] == "AI Roadmap"
    assert (folder / "ai-roadmap.md").exists()

    # 2. List presentations
    decks = call(app, "list_presentations")
    assert len(decks) == 1
    assert decks[0]["title"] == "AI Roadmap"
    assert decks[0]["slides_count"] == 2

    # 3. Get presentation
    deck = call(app, "get_presentation", deck="AI Roadmap")
    assert deck["title"] == "AI Roadmap"
    assert len(deck["slides"]) == 2
    assert deck["slides"][0]["title"] == "AI Roadmap 2027"

    # 4. Update presentation
    call(app, "update_presentation", deck="ai-roadmap", title="Updated AI Roadmap",
         content="# Updated 2027\n\n---\n\n## Slide 2\n\n---\n\n## Slide 3")
    deck2 = call(app, "get_presentation", deck="ai-roadmap")
    assert deck2["title"] == "Updated AI Roadmap"
    assert deck2["slides_count"] == 3

    # 5. Snapshot for Home screen
    snap = asyncio.run(app.snapshot(5))
    assert snap["stats"][0]["value"] == 1
    assert snap["items"][0]["text"] == "Updated AI Roadmap"

    # 6. Delete presentation
    del_res = call(app, "delete_presentation", deck="ai-roadmap")
    assert del_res["deleted"] == "Updated AI Roadmap"
    assert not (folder / "ai-roadmap.md").exists()
    assert (folder / ".trash").exists()


def test_presentations_http_routes(env, tmp_path):
    ctx, reg, _, db = env
    folder = tmp_path / "http_slides"
    ctx.config.data["apps"]["presentations"] = {"enabled": True, "settings": {"folder": str(folder)}}
    papp = make_presentations(ctx)
    reg.register(papp)

    server = create_app(ctx.config, db, registry=reg, allowed_hosts={"testserver"})
    with TestClient(server) as client:
        # POST new deck
        r = client.post("/api/apps/presentations/decks", json={"title": "Test Deck", "content": "# Slide 1\n\n---\n\n# Slide 2"})
        assert r.status_code == 200
        slug = r.json()["slug"]
        assert slug == "test-deck"

        # GET deck
        r = client.get(f"/api/apps/presentations/decks/{slug}")
        assert r.status_code == 200
        data = r.json()
        assert data["title"] == "Test Deck"
        assert data["slides_count"] == 2

        # GET all decks
        r = client.get("/api/apps/presentations/decks")
        assert r.status_code == 200
        assert len(r.json()["decks"]) == 1

        # PUT update deck
        r = client.put(f"/api/apps/presentations/decks/{slug}", json={"title": "Test Deck Renamed", "content": "# S1\n\n---\n\n# S2\n\n---\n\n# S3"})
        assert r.status_code == 200
        assert r.json()["title"] == "Test Deck Renamed"

        # POST parse file on the fly
        test_file = tmp_path / "standalone.md"
        test_file.write_text("# Standalone Slide\n\nHello\n\n---\n\n# Second Standalone")
        r = client.post("/api/apps/presentations/parse", json={"path": str(test_file)})
        assert r.status_code == 200
        parsed = r.json()
        assert parsed["slides_count"] == 2
        assert parsed["slides"][0]["title"] == "Standalone Slide"

        # DELETE deck
        r = client.delete(f"/api/apps/presentations/decks/{slug}")
        assert r.status_code == 200
        assert r.json()["ok"] is True


# ---------- the editor must not add a title slide every time a deck is opened and saved ----------
def front_blocks(text: str) -> int:
    return text.count("---\ntitle:")


def client_for(env, tmp_path, name="rt"):
    ctx, reg, _, db = env
    folder = tmp_path / name
    ctx.config.data["apps"]["presentations"] = {"enabled": True, "settings": {"folder": str(folder)}}
    reg.register(make_presentations(ctx))
    return TestClient(create_app(ctx.config, db, registry=reg, allowed_hosts={"testserver"})), folder


def test_open_and_save_round_trips_do_not_stack_front_matter(env, tmp_path):
    client, folder = client_for(env, tmp_path)
    with client:
        client.post("/api/apps/presentations/decks", json={"title": "Pitch", "content": "# Hello\n\n---\n\n## Two"})
        for _ in range(3):                                          # open the editor, save what it shows, again and again
            deck = client.get("/api/apps/presentations/decks/pitch").json()
            assert not deck["raw"].startswith("---")                # the editor is handed the slides, not the front matter block
            r = client.put("/api/apps/presentations/decks/pitch", json={"title": deck["title"], "content": deck["raw"]})
            assert r.status_code == 200
        text = (folder / "pitch.md").read_text()
        assert front_blocks(text) == 1
        deck = client.get("/api/apps/presentations/decks/pitch").json()
        assert deck["slides_count"] == 2 and [s["title"] for s in deck["slides"]] == ["Hello", "Two"]


def test_an_older_editor_that_sends_the_front_matter_back_is_still_safe(env, tmp_path):
    client, folder = client_for(env, tmp_path)
    with client:
        client.post("/api/apps/presentations/decks", json={"title": "Pitch", "content": "# Hello"})
        whole_file = (folder / "pitch.md").read_text()
        for _ in range(3):
            client.put("/api/apps/presentations/decks/pitch", json={"title": "Pitch", "content": whole_file})
        text = (folder / "pitch.md").read_text()
        assert front_blocks(text) == 1 and client.get("/api/apps/presentations/decks/pitch").json()["slides_count"] == 1


def test_decks_already_damaged_by_the_bug_load_cleanly(env, tmp_path):
    client, folder = client_for(env, tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "old.md").write_text("---\ntitle: Old\n---\n---\ntitle: Old\n---\n---\ntitle: Old\n---\n# Welcome\n\n---\n\n## Two\n")
    with client:
        deck = client.get("/api/apps/presentations/decks/old").json()
        assert deck["title"] == "Old" and deck["slides_count"] == 2 and deck["slides"][0]["title"] == "Welcome"
        assert "title:" not in deck["raw"]
        client.put("/api/apps/presentations/decks/old", json={"title": "Old", "content": deck["raw"]})
        assert front_blocks((folder / "old.md").read_text()) == 1                       # saving cleans the file up


def test_a_deck_that_opens_with_a_slide_break_is_not_mistaken_for_front_matter():
    from diatonic.apps.presentations.app import parse_frontmatter, split_slides
    meta, body = parse_frontmatter("---\n# Not a setting\n---\ntext")
    assert meta == {} and body.startswith("---\n# Not")
    assert parse_frontmatter("---\ntitle: A\ntheme: dark\n---\nbody") == ({"title": "A", "theme": "dark"}, "body")
    assert len(split_slides("---\ntitle: A\n---\n# One\n\n---\n\n# Two")[1]) == 2


def test_update_presentation_without_content_does_not_touch_the_slides(env, tmp_path):
    import asyncio
    ctx, reg, _, db = env
    ctx.config.data["apps"]["presentations"] = {"enabled": True, "settings": {"folder": str(tmp_path / "upd")}}
    app = make_presentations(ctx)
    asyncio.run(app.create_presentation({"title": "Deck", "content": "# One\n\n---\n\n# Two"}))
    for _ in range(3):
        asyncio.run(app.update_presentation({"deck": "deck", "title": "Deck"}))
    text = (tmp_path / "upd" / "deck.md").read_text()
    assert front_blocks(text) == 1 and len(split_slides(text)[1]) == 2


def test_presentations_image_upload_and_asset_serving(env, tmp_path):
    import base64
    client, folder = client_for(env, tmp_path)
    with client:
        # 1. Upload a PNG image
        sample_png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        b64_data = base64.b64encode(sample_png).decode("ascii")
        res = client.post("/api/apps/presentations/upload", json={"name": "test-diagram.png", "data": b64_data})
        assert res.status_code == 200
        data = res.json()
        assert data["name"] == "test-diagram.png"
        assert data["path"] == "images/test-diagram.png"
        assert (folder / "images" / "test-diagram.png").exists()

        # 2. Serve the uploaded asset
        asset_res = client.get(f"/api/apps/presentations/asset?path={data['path']}")
        assert asset_res.status_code == 200
        assert asset_res.headers["content-type"] == "image/png"
        assert "sandbox" in asset_res.headers.get("content-security-policy", "")
        assert asset_res.content == sample_png

        # 3. Path traversal or missing image returns 404
        bad_res = client.get("/api/apps/presentations/asset?path=../../nonexistent.png")
        assert bad_res.status_code == 404


def test_parse_route_splits_headings_and_returns_404_for_missing(env, tmp_path):
    client, folder = client_for(env, tmp_path)
    with client:
        # Missing file returns 404 JSON, not a 500 error
        r_miss = client.post("/api/apps/presentations/parse", json={"path": "/nonexistent/path/doc.md"})
        assert r_miss.status_code == 404
        assert "error" in r_miss.json()

        # Content without '---' but with multiple headings splits cleanly into slides
        md_text = "# Slide One\n\nIntro content.\n\n# Slide Two\n\nSecond point."
        r_content = client.post("/api/apps/presentations/parse", json={"content": md_text})
        assert r_content.status_code == 200
        parsed = r_content.json()
        assert parsed["slides_count"] == 2
        assert parsed["slides"][0]["title"] == "Slide One"
        assert parsed["slides"][1]["title"] == "Slide Two"

