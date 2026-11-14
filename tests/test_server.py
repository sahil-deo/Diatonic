import json

from starlette.testclient import TestClient

from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app


def client(tmp_path):
    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    return TestClient(app)


def test_state_lists_builtin_apps(tmp_path):
    with client(tmp_path) as c:
        s = c.get("/api/state").json()
        ids = {a["id"] for a in s["apps"]}
        assert {"github", "gcal"} <= ids
        assert s["config"]["agent"]["model"] == "qwen3:4b-instruct"
        assert s["config"]["server"]["port"] == 5005
        gh = next(a for a in s["apps"] if a["id"] == "github")
        assert {t["risk"] for t in gh["tools"]} == {"read", "write", "destructive"}


def test_host_and_origin_checks(tmp_path):
    with client(tmp_path) as c:
        assert c.get("/api/state", headers={"host": "evil.com"}).status_code == 403
        assert c.put("/api/config", json={}, headers={"origin": "http://evil.com"}).status_code == 403
        assert c.put("/api/config", json={}, headers={"origin": "http://testserver"}).status_code == 200


def test_config_update_and_server_is_protected(tmp_path):
    with client(tmp_path) as c:
        r = c.put("/api/config", json={"voice": {"speak_summary": True}, "server": {"port": 1}}).json()
        assert r["voice"]["speak_summary"] is True and r["server"]["port"] == 5005
        assert r["voice"]["hotkey"] == "Alt+Shift+Space"  # deep merge keeps siblings


def test_app_secrets_never_returned(tmp_path):
    with client(tmp_path) as c:
        r = c.put("/api/apps/github/config", json={"secrets": {"token": "ghp_secret"},
                                                    "settings": {"default_owner": "me"}}).json()
        f = {x["key"]: x for x in r["fields"]}
        assert f["token"]["set"] is True and f["token"]["value"] is None
        assert "ghp_secret" not in json.dumps(c.get("/api/state").json())
        assert f["default_owner"]["value"] == "me"


def test_conversations_crud_and_ui_served(tmp_path):
    with client(tmp_path) as c:
        cid = c.post("/api/conversations").json()["id"]
        c.patch(f"/api/conversations/{cid}", json={"title": "Hi"})
        assert c.get(f"/api/conversations/{cid}").json()["title"] == "Hi"
        c.delete(f"/api/conversations/{cid}")
        assert c.get(f"/api/conversations/{cid}").status_code == 404
        assert "Diatonic" in c.get("/").text


def test_voice_endpoints_degrade_cleanly(tmp_path):
    with client(tmp_path) as c:
        r = c.post("/api/voice/tts", json={"text": "hi"})
        assert r.status_code == 501 and r.json()["fallback"] == "system"
        assert c.post("/api/voice/transcribe", content=b"\0" * 4000).status_code in (409, 501)
