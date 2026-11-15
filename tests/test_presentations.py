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
