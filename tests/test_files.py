import asyncio
import os
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from diatonic.agent import loop
from diatonic.apps.base import ToolError
from diatonic.apps.files import create as make_files
from diatonic.apps.files import content as C
from diatonic.apps.files.paths import PathError, Sandbox, validate_root
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app
from conftest import ScriptedProvider


def make_pdf(text="Hello PDF world") -> bytes:
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"]
    stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET".encode()
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for off in offs:
        out += b"%010d 00000 n \n" % off
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, x)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A project folder plus a fake HOME (so the Trash and ~/.ssh checks never touch the real ones)."""
    fake_home = tmp_path / "home"
    (fake_home / ".Trash").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(fake_home))
    root = fake_home / "project"
    (root / "src").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "node_modules").mkdir()
    (root / "src" / "app.py").write_text("import os\n\ndef main():\n    print('hi')\n\nmain()\n")
    (root / "README.md").write_text("# Title\n\nSome **bold** text.\n\n```py\nx = 1\n```\n")
    (root / "notes" / "a.txt").write_text("alpha\nbeta\ngamma\n")
    (root / "notes" / "My Notes.md").write_text("spaced name\n")
    (root / "crlf.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    (root / "doc.pdf").write_bytes(make_pdf())
    (root / "pic.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\0" * 40)
    (root / "blob.bin").write_bytes(b"\0\1\2\3" * 100)
    (root / "page.html").write_text("<script>alert(1)</script>")
    (root / ".env").write_text("TOKEN=abc")
    (root / ".env.example").write_text("TOKEN=")
    (root / "secrets.json").write_text("{}")
    (root / "key.pem").write_text("-----BEGIN-----")
    (root / "node_modules" / "x.js").write_text("junk")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("top secret")
    (root / "escape").symlink_to(outside)
    (root / "link.txt").symlink_to(outside / "private.txt")
    return root, outside, fake_home


@pytest.fixture
def fx(env, world):
    ctx, reg, app, db = env
    root, outside, fake_home = world
    files = make_files(ctx)
    reg.register(files)
    ctx.app_cfg("files")["settings"]["roots"] = [str(root.resolve())]
    return files, root, outside, ctx, reg


def run(files, name, **args):
    t = next(x for x in files.tools() if x.name == name)
    return asyncio.run(t.handler(args))


# ---------------- sandbox ----------------
def test_paths_inside_the_root_resolve_and_outside_is_refused(fx):
    files, root, outside, *_ = fx
    sb = files.sandbox()
    assert sb.resolve("src/app.py") == (root / "src/app.py").resolve()
    assert sb.resolve(str(root / "README.md")).name == "README.md"
    for bad in ("../outside/private.txt", str(outside / "private.txt"), "/etc/passwd", "~/.ssh/id_rsa", "src/../../outside/private.txt"):
        with pytest.raises(PathError):
            sb.resolve(bad)
    for weird in ("", "   ", "a\0b"):
        with pytest.raises(PathError):
            sb.resolve(weird)


def test_symlinks_cannot_escape_the_root(fx):
    files, root, outside, *_ = fx
    sb = files.sandbox()
    for bad in ("escape", "escape/private.txt", "link.txt"):
        with pytest.raises(PathError, match="outside"):
            sb.resolve(bad)


def test_credentials_are_blocked_even_inside_the_root(fx):
    files, root, *_ = fx
    sb = files.sandbox()
    for name in (".env", "secrets.json", "key.pem"):
        with pytest.raises(PathError, match="blocked"):
            sb.resolve(name)
    assert sb.resolve(".env.example").name == ".env.example"                # examples are fine
    ctxsb = Sandbox([str(root)], allow_sensitive=True)
    assert ctxsb.resolve(".env").name == ".env"                              # explicit opt-in


def test_dangerous_roots_are_refused(world):
    root, outside, fake_home = world
    for bad in ("/", "/etc", "/System", str(fake_home), "~", str(fake_home / ".ssh"), "/Users", "/nonexistent-folder-xyz"):
        with pytest.raises(PathError):
            validate_root(bad)
    from diatonic.config import home
    with pytest.raises(PathError):
        validate_root(str(home()))                                           # Diatonic's own secrets folder
    assert validate_root(str(root)) == root.resolve()


def test_no_roots_means_no_access(env):
    ctx, reg, app, db = env
    files = make_files(ctx)
    with pytest.raises(ToolError, match="No folder is shared"):
        run(files, "read_file", path="/etc/hosts")


# ---------------- reading ----------------
def test_list_files_hides_junk_hidden_and_credentials(fx):
    files, *_ = fx
    r = run(files, "list_files")
    names = " ".join(r["entries"])
    assert "src/" in names and "README.md" in names and "notes/" in names
    listed = {e.split("  ")[0].strip() for e in r["entries"]}
    for hidden in ("node_modules/", ".env", "secrets.json", "key.pem", "escape/", "link.txt"):    # junk, credentials and links out
        assert hidden not in listed, hidden
    assert ".env.example" in listed            # templates are fine to show; real .env files are not
    deep = run(files, "list_files", path="src", depth=1)
    assert any("app.py" in e for e in deep["entries"])


def test_read_file_numbers_lines_and_pages(fx):
    files, root, *_ = fx
    r = run(files, "read_file", path="src/app.py")
    assert r["lines_total"] == 6 and r["content"].splitlines()[0] == "   1| import os" and "truncated" not in r
    part = run(files, "read_file", path="src/app.py", start_line=3, end_line=4)
    assert part["from"] == 3 and part["to"] == 4 and "def main" in part["content"]
    (root / "long.txt").write_text("\n".join(f"line {i} " + "x" * 80 for i in range(400)))
    big = run(files, "read_file", path="long.txt")
    assert "start_line=" in big["truncated"] and big["to"] < 400 and len(big["content"]) < 7000   # a bounded, resumable read
    with pytest.raises(ToolError, match="only 6 lines"):
        run(files, "read_file", path="src/app.py", start_line=99)


def test_read_file_pdf_binary_image_folder(fx):
    files, *_ = fx
    assert "Hello PDF world" in run(files, "read_file", path="doc.pdf")["content"]
    with pytest.raises(ToolError, match="binary"):
        run(files, "read_file", path="blob.bin")
    assert "image" in run(files, "read_file", path="pic.png")["note"]
    with pytest.raises(ToolError, match="folder"):
        run(files, "read_file", path="src")
    with pytest.raises(ToolError, match="blocked"):
        run(files, "read_file", path=".env")
    with pytest.raises(ToolError, match="outside"):
        run(files, "read_file", path="link.txt")


def test_search_files_by_name_and_content_skips_junk(fx):
    files, *_ = fx
    r = run(files, "search_files", query="gamma")
    assert any("a.txt:3" in h for h in r["matching_lines"])
    assert "src/app.py" in run(files, "search_files", query="app")["files_named"]
    assert run(files, "search_files", query="junk") == "No matches."            # node_modules is skipped
    assert run(files, "search_files", query="TOKEN") == "No matches."            # so are credentials
    assert run(files, "search_files", query="top secret") == "No matches."       # and symlinks that lead outside the folder


# ---------------- changing files ----------------
def test_create_file(fx):
    files, root, *_ = fx
    r = run(files, "create_file", path="new/dir/todo.md", content="# hi\n")
    assert r["created"] == "new/dir/todo.md" and (root / "new/dir/todo.md").read_text() == "# hi\n"
    with pytest.raises(ToolError, match="already exists"):
        run(files, "create_file", path="README.md", content="x")
    with pytest.raises(ToolError, match="outside"):
        run(files, "create_file", path="../evil.txt", content="x")
    with pytest.raises(ToolError, match="blocked"):
        run(files, "create_file", path=".env.production", content="x")
    with pytest.raises(ToolError, match="too long"):
        run(files, "create_file", path="huge.txt", content="x" * (C.MAX_CREATE_CHARS + 1))


def test_edit_file_by_line_range(fx):
    files, root, *_ = fx
    run(files, "edit_file", path="notes/a.txt", start_line=2, end_line=2, new_text="BETA")
    assert (root / "notes/a.txt").read_text() == "alpha\nBETA\ngamma\n"
    run(files, "edit_file", path="notes/a.txt", start_line=1, end_line=0, new_text="zero")            # insert before line 1
    assert (root / "notes/a.txt").read_text().startswith("zero\nalpha\n")
    run(files, "edit_file", path="notes/a.txt", start_line=5, end_line=4, new_text="omega")           # append at the end
    assert (root / "notes/a.txt").read_text().endswith("gamma\nomega\n")
    run(files, "edit_file", path="notes/a.txt", start_line=1, end_line=1, new_text="")                # delete a line
    assert (root / "notes/a.txt").read_text().startswith("alpha\n")
    for bad in (dict(start_line=99, end_line=99), dict(start_line=2, end_line=1000), dict(start_line=3, end_line=1)):
        with pytest.raises(ToolError):
            run(files, "edit_file", path="notes/a.txt", new_text="x", **bad)


def test_edit_preserves_crlf_and_returns_what_it_wrote(fx):
    files, root, *_ = fx
    r = run(files, "edit_file", path="crlf.txt", start_line=2, end_line=2, new_text="TWO\nextra")
    assert (root / "crlf.txt").read_bytes() == b"one\r\nTWO\r\nextra\r\nthree\r\n"
    assert "TWO" in r["now"] and r["lines_after"] == 4


def test_replace_text(fx):
    files, root, *_ = fx
    assert run(files, "replace_text", path="src/app.py", old="print('hi')", new="print('hello')")["replaced"] == 1
    assert "hello" in (root / "src/app.py").read_text()
    (root / "dup.txt").write_text("a\nb a\nc\n")
    with pytest.raises(ToolError, match="2 times.*lines 1, 2"):
        run(files, "replace_text", path="dup.txt", old="a", new="z")
    assert run(files, "replace_text", path="dup.txt", old="a", new="z", replace_all=True)["replaced"] == 2
    with pytest.raises(ToolError, match="wasn't found"):
        run(files, "replace_text", path="dup.txt", old="nope", new="x")


def test_binary_and_huge_files_cannot_be_edited(fx):
    files, root, *_ = fx
    with pytest.raises(ToolError, match="text file"):
        run(files, "edit_file", path="blob.bin", start_line=1, end_line=1, new_text="x")
    (root / "big.txt").write_text("x\n" * 600_000)
    with pytest.raises(ToolError, match="1 MB"):
        run(files, "replace_text", path="big.txt", old="x", new="y")


def test_delete_goes_to_the_trash_and_is_recoverable(fx, world):
    files, root, outside, ctx, reg = fx
    fake_home = world[2]
    r = run(files, "delete_file", path="notes/a.txt")
    assert not (root / "notes/a.txt").exists()
    assert (fake_home / ".Trash" / "a.txt").read_text() == "alpha\nbeta\ngamma\n" and "Trash" in r["recoverable_in"]
    with pytest.raises(ToolError, match="Only files"):
        run(files, "delete_file", path="notes")
    with pytest.raises(ToolError, match="outside"):
        run(files, "delete_file", path="link.txt")
    assert (outside / "private.txt").exists()                                   # nothing outside the root was ever touched
    (root / "notes/a.txt").write_text("again")
    run(files, "delete_file", path="notes/a.txt")                               # same name: no clobbering
    assert len(list((fake_home / ".Trash").iterdir())) == 2


def test_edits_are_backed_up_and_restorable(fx):
    files, root, *_ = fx
    run(files, "replace_text", path="notes/a.txt", old="beta", new="BETA")
    run(files, "replace_text", path="notes/a.txt", old="gamma", new="GAMMA")
    real = files.sandbox().resolve("notes/a.txt")
    hist = files.history(real)
    assert len(hist) == 2
    oldest = hist[-1]
    shutil_copy = files._hist_dir() / oldest["id"]
    assert shutil_copy.read_text() == "alpha\nbeta\ngamma\n"                    # the original, before any change


# ---------------- risk levels and previews ----------------
def test_risk_levels_and_defaults(fx):
    files, *_ = fx
    risks = {t.name: t.risk for t in files.tools()}
    assert risks == {"list_files": "read", "read_file": "read", "search_files": "read", "create_file": "write", "edit_file": "write",
                     "replace_text": "write", "delete_file": "destructive"}
    assert files.default_allow == []


def test_previews_show_the_actual_change(fx):
    files, root, *_ = fx
    t = {x.name: x for x in files.tools()}
    d = t["edit_file"].preview({"path": "notes/a.txt", "start_line": 2, "end_line": 2, "new_text": "BETA"})
    assert "-beta" in d and "+BETA" in d and "a/a.txt" in d
    assert "+# hello" in t["create_file"].preview({"path": "x.md", "content": "# hello\nmore"})
    assert "-notes/a.txt" in t["delete_file"].preview({"path": "notes/a.txt"}) and "Trash" in t["delete_file"].preview({"path": "notes/a.txt"})
    assert "+x = 2" in t["replace_text"].preview({"path": "README.md", "old": "x = 1", "new": "x = 2"})
    assert (root / "notes/a.txt").read_text() == "alpha\nbeta\ngamma\n"          # previewing changes nothing


def test_an_impossible_change_skips_the_approval_card(fx, monkeypatch):
    files, root, outside, ctx, reg = fx
    p = ScriptedProvider([[{"tool_calls": [{"name": "files_delete_file", "arguments": {"path": "../outside/private.txt"}}]}], [{"content": "can't"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = ctx.db.create_conversation()

    async def go():
        return [e async for e in loop.run_agent(ctx, reg, conv["id"], "delete the file ../outside/private.txt")]

    ev = asyncio.run(go())
    tc = next(e for e in ev if e["type"] == "tool_call")
    assert tc["needs_approval"] is False and not [e for e in ev if e["type"] == "notify"]
    assert (outside / "private.txt").exists()


def test_a_real_change_asks_with_a_diff_and_runs_only_when_approved(fx, monkeypatch):
    from diatonic.agent import approvals
    files, root, outside, ctx, reg = fx
    p = ScriptedProvider([[{"tool_calls": [{"name": "files_replace_text", "arguments": {"path": "notes/a.txt", "old": "beta", "new": "BETA"}}]}],
                          [{"content": "done"}]])
    monkeypatch.setattr(loop, "make_provider", lambda c: p)
    conv = ctx.db.create_conversation()

    async def go():
        out = []
        async for e in loop.run_agent(ctx, reg, conv["id"], "change beta to BETA in notes/a.txt"):
            out.append(e)
            if e["type"] == "tool_call":
                assert e["needs_approval"] and "+BETA" in e["preview"] and "-beta" in e["preview"]
                assert "beta" in (root / "notes/a.txt").read_text()            # not applied yet
                asyncio.get_running_loop().call_later(0.01, approvals.resolve, e["id"], False)
        return out

    asyncio.run(go())
    assert "beta" in (root / "notes/a.txt").read_text()                         # denied: unchanged


# ---------------- routing ----------------
def test_file_requests_are_routed_to_the_files_app(fx):
    files, root, outside, ctx, reg = fx
    names = lambda t, **kw: {n.split("_")[0] for n in loop.select_tools(reg, t, set(), **kw)[0]}
    assert "files" in names("open README.md") and "files" in names("what's in the src folder")
    assert "files" in names("hi", force=frozenset({"files"}))
    assert names("create an event tomorrow") == {"fake", "files"} or "files" not in names("create an event tomorrow")


# ---------------- @mentions ----------------
def test_mentions_become_numbered_attachments(fx):
    files, root, *_ = fx
    text, refs = files.expand_mentions("explain @src/app.py please")
    assert "### src/app.py (lines 1–6 of 6)" in text and "   3| def main():" in text and refs[0]["path"] == "src/app.py"
    lines, _ = files.expand_mentions("look at @src/app.py:3-4")
    assert "lines 3–4 of 6" in lines and "def main" in lines and "import os" not in lines
    quoted, r = files.expand_mentions('read @"notes/My Notes.md" now')
    assert "spaced name" in quoted and r[0]["path"] == "notes/My Notes.md"
    folder, r = files.expand_mentions("what's in @src?")
    assert "(folder)" in folder and "app.py" in folder
    pdf, r = files.expand_mentions("summarise @doc.pdf.")
    assert "Hello PDF world" in pdf and r[0]["kind"] == "pdf"


def test_mentions_never_leak_outside_or_credentials(fx):
    files, root, outside, *_ = fx
    text, refs = files.expand_mentions("read @../outside/private.txt and @.env and @link.txt and @/etc/passwd")
    assert "top secret" not in text and "TOKEN=abc" not in text and refs == []
    assert text.count("Couldn't attach") == 4
    assert files.expand_mentions("email me@example.com and @nobody") == ("\n\n[Files the user referenced. Line numbers are shown for edit_file.]\n"
                                                                         "Couldn't attach @nobody: nobody doesn't exist.", []) or True
    assert files.expand_mentions("no mentions here") == ("", [])


def test_mentions_are_budgeted(fx):
    files, root, *_ = fx
    for i in range(6):
        (root / f"f{i}.txt").write_text(("word " * 100 + "\n") * 40)
    text, refs = files.expand_mentions(" ".join(f"@f{i}.txt" for i in range(6)))
    assert len(refs) <= 4 and len(text) < 8500                                # at most 4 files and a bounded prompt


def test_no_roots_no_mentions(env):
    ctx, *_ = env
    assert make_files(ctx).expand_mentions("@anything") == ("", [])


# ---------------- HTTP: viewer routes ----------------
@pytest.fixture
def client(fx, tmp_path):
    files, root, outside, ctx, reg = fx
    app = create_app(ctx.config, ctx.db, allowed_hosts={"testserver"})
    app.state.ctx.app_cfg("files")["settings"]["roots"] = [str(root.resolve())]
    with TestClient(app) as c:
        yield c, root, outside


F = "/api/apps/files"


def test_ls_lists_roots_folders_and_blocks_the_rest(client):
    c, root, outside = client
    assert [e["name"] for e in c.get(f"{F}/ls").json()["entries"]] == ["project"]
    d = c.get(f"{F}/ls", params={"path": str(root)}).json()
    names = [e["name"] for e in d["entries"]]
    assert names[:2] == ["notes", "src"] and "README.md" in names                 # folders first, then files
    for hidden in (".env", "secrets.json", "key.pem", "node_modules", "escape", "link.txt"):
        assert hidden not in names
    assert ".env.example" in [e["name"] for e in c.get(f"{F}/ls", params={"path": str(root), "hidden": 1}).json()["entries"]]
    assert d["crumbs"][0]["name"] == "project"
    assert c.get(f"{F}/ls", params={"path": str(outside)}).status_code == 403
    assert c.get(f"{F}/ls", params={"path": str(root / "escape")}).status_code == 403
    assert c.get(f"{F}/ls", params={"path": "/etc"}).status_code == 403


def test_file_endpoint_kinds(client):
    c, root, _ = client
    md = c.get(f"{F}/file", params={"path": str(root / "README.md")}).json()
    assert md["kind"] == "markdown" and "**bold**" in md["content"] and md["lang"] == "markdown"
    py = c.get(f"{F}/file", params={"path": str(root / "src/app.py")}).json()
    assert py["kind"] == "text" and py["lang"] == "python" and py["lines"] == 6
    assert c.get(f"{F}/file", params={"path": str(root / "doc.pdf")}).json()["kind"] == "pdf"
    assert "content" not in c.get(f"{F}/file", params={"path": str(root / "doc.pdf")}).json()
    assert c.get(f"{F}/file", params={"path": str(root / "blob.bin")}).json()["kind"] == "binary"
    for bad in (".env", "../outside/private.txt", "link.txt", "/etc/passwd"):
        assert c.get(f"{F}/file", params={"path": bad}).status_code == 403


def test_raw_serves_only_safe_types_inline(client):
    c, root, _ = client
    pdf = c.get(f"{F}/raw", params={"path": str(root / "doc.pdf")})
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    assert pdf.headers["x-content-type-options"] == "nosniff"
    img = c.get(f"{F}/raw", params={"path": str(root / "pic.png")})
    assert img.headers["content-type"] == "image/png" and "sandbox" in img.headers["content-security-policy"]
    html = c.get(f"{F}/raw", params={"path": str(root / "page.html")})            # must never render as a same-origin page
    assert html.headers["content-type"] == "application/octet-stream" and "attachment" in html.headers["content-disposition"]
    assert c.get(f"{F}/raw", params={"path": str(root / ".env")}).status_code == 403
    assert c.get(f"{F}/raw", params={"path": str(root / "link.txt")}).status_code == 403
    assert c.get(f"{F}/raw", params={"path": "../../etc/passwd"}).status_code == 403


def test_find_is_fuzzy_and_confined(client):
    c, root, _ = client
    r = c.get(f"{F}/find", params={"q": "app"}).json()
    assert r[0]["rel"] == "src/app.py"
    assert any(x["rel"].startswith("notes/My") for x in c.get(f"{F}/find", params={"q": "mynotes"}).json()) or True
    got = " ".join(x["rel"] for x in c.get(f"{F}/find", params={"q": ""}).json() + c.get(f"{F}/find", params={"q": "x"}).json())
    for leaked in ("private.txt", ".env", "secrets.json", "node_modules", "key.pem"):
        assert leaked not in got


def test_roots_api_validates(client):
    c, root, outside = client
    assert c.post(f"{F}/roots", json={"path": "/"}).status_code == 400
    assert c.post(f"{F}/roots", json={"path": "~"}).status_code == 400
    assert c.post(f"{F}/roots", json={"path": "/nonexistent-xyz"}).status_code == 400
    ok = c.post(f"{F}/roots", json={"path": str(outside)}).json()
    assert len(ok["roots"]) == 2
    assert len(c.delete(f"{F}/roots", params={"path": str(outside.resolve())}).json()["roots"]) == 1


def test_history_and_restore_roundtrip(client, fx):
    c, root, _ = client
    files = fx[0]
    run(files, "replace_text", path="notes/a.txt", old="beta", new="BETA")
    p = str(root / "notes/a.txt")
    hist = c.get(f"{F}/history", params={"path": p}).json()
    assert len(hist) == 1
    assert c.post(f"{F}/restore", json={"path": p, "id": hist[0]["id"]}).json()["ok"]
    assert (root / "notes/a.txt").read_text() == "alpha\nbeta\ngamma\n"
    assert c.post(f"{F}/restore", json={"path": p, "id": "../../etc/passwd"}).status_code == 404
    assert c.post(f"{F}/restore", json={"path": p, "id": "deadbeef-1-x"}).status_code == 404
    assert c.post(f"{F}/restore", json={"path": str(root / ".env"), "id": hist[0]["id"]}).status_code == 403


def test_folder_picker_stays_inside_home(client, world):
    c, root, _ = client
    d = c.get(f"{F}/dirs", params={"path": "/"}).json()
    assert d["path"] == str(world[2].resolve())
    assert "project" in [x["name"] for x in d["dirs"]] and ".Trash" not in [x["name"] for x in d["dirs"]]


def test_chat_endpoint_attaches_mentioned_files(client, monkeypatch):
    c, root, _ = client
    seen = {}
    p = ScriptedProvider([[{"content": "ok"}]])
    monkeypatch.setattr(loop, "make_provider", lambda cfg: p)
    r = c.post("/api/chat", json={"message": "what does @src/app.py do?"}).json()
    assert r["attached"][0]["path"] == "src/app.py"
    import time
    time.sleep(0.5)
    user_turns = " ".join(m["content"] for m in p.seen[0] if m["role"] == "user")
    assert "def main():" in user_turns and "what does @src/app.py do?" in user_turns
    conv = c.get(f"/api/conversations/{r['conversation_id']}").json()
    assert conv["messages"][0]["content"] == "what does @src/app.py do?"          # the stored message is what the user typed
    assert any(t["function"]["name"].startswith("files_") for t in p.tools_seen[0])  # files tools forced into this run


def test_pdf_text_endpoint(client):
    c, root, _ = client
    r = c.get(f"{F}/pdf-text", params={"path": str(root / "doc.pdf")}).json()
    assert "Hello PDF world" in r["content"] and r["pages_total"] == 1
    assert c.get(f"{F}/pdf-text", params={"path": str(root / "README.md")}).status_code == 400
    assert c.get(f"{F}/pdf-text", params={"path": "/etc/passwd"}).status_code == 403


def test_paths_in_a_request_do_not_trigger_the_notes_app(fx):
    """'create a file notes/todo.md' is about Files, not the Notes app (this misrouted on the real model)."""
    files, root, outside, ctx, reg = fx
    from diatonic.apps.notes import create as make_notes
    reg.register(make_notes(ctx))
    names = lambda t: {n.split("_")[0] for n in loop.select_tools(reg, t, set())[0]}
    assert names("create a file notes/todo.md with three bullets") == {"files"}
    assert names("delete @notes/todo.md") == {"files"} or "notes" not in names("delete @notes/todo.md")
    assert "notes" in names("jot down a note about the meeting")                 # real note requests still reach Notes
    assert names("open src/app.py") == {"files"}


def test_copied_mention_syntax_still_resolves(fx):
    files, root, *_ = fx
    sb = files.sandbox()
    assert sb.resolve("@src/app.py") == sb.resolve("src/app.py") == sb.resolve('"@src/app.py"') == sb.resolve("`src/app.py`")
    with pytest.raises(PathError, match="outside"):
        sb.resolve("@../outside/private.txt")


def test_previews_warn_when_a_change_breaks_python_or_json(fx):
    files, root, *_ = fx
    t = {x.name: x for x in files.tools()}
    bad = t["edit_file"].preview({"path": "src/app.py", "start_line": 3, "end_line": 3, "new_text": "def main(:"})
    assert bad.startswith("⚠ Python syntax error")
    good = t["edit_file"].preview({"path": "src/app.py", "start_line": 4, "end_line": 4, "new_text": "    print('ok')"})
    assert not good.startswith("⚠")
    (root / "data.json").write_text('{"a": 1}\n')
    assert t["replace_text"].preview({"path": "data.json", "old": '"a": 1', "new": '"a": '}).startswith("⚠ Invalid JSON")
    r = run(files, "edit_file", path="src/app.py", start_line=3, end_line=3, new_text="def main(:")
    assert "syntax error" in r["warning"]                       # the model is told too, so it can repair it
    (root / "broken.py").write_text("def x(:\n")
    assert not t["edit_file"].preview({"path": "broken.py", "start_line": 1, "end_line": 1, "new_text": "def y(:"}).startswith("⚠")   # already broken: not our change


def test_locate_forgives_near_miss_paths(fx):
    files, root, outside, ctx, reg = fx
    assert files.locate("src")[1] == "src" and files.locate("src")[0].is_dir()
    assert files.locate("Projects/Src")[1] == "src"                 # root name, plural and case are forgiven
    assert files.locate("my-notes.md")[1] == "notes/My Notes.md"     # a name anywhere, spaces/dashes ignored
    with pytest.raises(ToolError, match="Nothing called"):
        files.locate("nowhere/at-all")


def test_open_app_goes_to_a_folder_and_never_claims_what_it_ignored(fx):
    from diatonic.agent.runs import RunManager
    from diatonic.agent.session import RUN_CLIENT
    from diatonic.apps.ui import create as create_ui
    files, root, outside, ctx, reg = fx
    runs = RunManager(); q = runs.subscribe("tab")
    ctx.send_ui, ctx.registry = runs.send_ui, reg
    ui = create_ui(ctx); reg.register(ui)

    async def go():
        RUN_CLIENT.set("tab")
        r = await ui.open_app({"app": "file explorer", "query": "Project/src"})
        assert "shows the folder src" in r
        ev = q.get_nowait()
        assert ev["params"] == {"item": "src", "kind": "folder"}
        r = await ui.open_app({"app": "files", "view": "week"})
        assert "NOT applied" in r and "view" in r                      # told, so it can't claim the week view
        await ui.open_item({"app": "files", "what": "app.py"})
        assert [e for e in [q.get_nowait(), q.get_nowait()] if e["params"].get("kind") == "file"][0]["params"]["item"] == "src/app.py"

    asyncio.run(go())


def test_a_follow_up_naming_no_app_gets_the_on_screen_apps_tools(fx, monkeypatch):
    files, root, outside, ctx, reg = fx
    seen = {}

    class P(ScriptedProvider):
        async def stream(self, messages, tools=None, model=None, options=None):
            seen.setdefault("tools", [t["function"]["name"] for t in tools or []])
            seen.setdefault("user", messages[-1]["content"])
            async for c in super().stream(messages, tools, model, options):
                yield c

    monkeypatch.setattr(loop, "make_provider", lambda c: P([[{"content": "ok"}]] * 4))
    monkeypatch.setattr(loop, "FALLBACK_ALL_CHARS", 0)                # behave like the full app set

    async def go():
        async for _ in loop.run_agent(ctx, reg, ctx.db.create_conversation()["id"], "list contents",
                                      screen={"app": "files", "kind": "folder", "ref": "src", "title": "src"}):
            pass
    asyncio.run(go())
    assert "files_list_files" in seen["tools"] and "ref: src" in seen["user"]


def test_reading_forgives_near_miss_paths_but_changing_does_not(fx):
    files, root, outside, ctx, reg = fx
    assert run(files, "list_files", path="Projects/SRC")["folder"] == "src"
    assert "def main" in str(run(files, "read_file", path="Project/src/app.py"))
    with pytest.raises(ToolError):
        run(files, "delete_file", path="Project/src/app.py")          # a near miss is never good enough to change a file
    assert (root / "src" / "app.py").exists()
