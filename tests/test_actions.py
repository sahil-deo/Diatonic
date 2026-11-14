import asyncio
import time

import pytest
from starlette.testclient import TestClient

from diatonic.actions import (ActionRunner, clean_actions, clean_appearance, export_bundle, import_bundle)
from diatonic.config import Config
from diatonic.db import Database
from diatonic.server import create_app


def P(**kw):
    return {"kind": "prompt", "label": "Standup", "name": "standup", "prompt": "Summarise {{input}}", **kw}


def S(**kw):
    return {"kind": "script", "label": "Backup", "name": "backup", "command": "echo hi", **kw}


# ---------------- validation ----------------
def test_valid_actions_are_normalised():
    out = clean_actions([P(send=1, name="/Standup "), S(timeout="5", icon="💾")])
    assert out[0]["name"] == "standup" and out[0]["send"] is True and out[0]["id"]
    assert out[1]["timeout"] == 5 and out[1]["icon"] == "💾" and out[1]["bar"] is True
    assert out[0]["id"] != out[1]["id"]


@pytest.mark.parametrize("bad,msg", [
    ([P(name="new")], "built-in"), ([P(name="Bad Name!")], "a-z"), ([P(), P()], "twice"),
    ([P(label="")], "label"), ([P(prompt="")], "empty"), ([S(command="")], "empty"), ([{"kind": "x", "label": "a"}], "kind"),
    ([P(shortcut="Alt+Shift+Space"), P(name="b", shortcut="Alt+Shift+Space")], "twice"), ([P(shortcut="nope")], "shortcut"),
    ("nope", "list"), ([P()] * 101, "At most"), ([S(timeout="abc")], "timeout"),
])
def test_invalid_actions_give_clear_errors(bad, msg):
    with pytest.raises(ValueError, match=msg):
        clean_actions(bad)


def test_long_fields_are_clipped_and_timeout_capped():
    out = clean_actions([S(label="x" * 99, timeout=99999, command="c" * 9000)])[0]
    assert len(out["label"]) == 40 and out["timeout"] == 900 and len(out["command"]) == 4000


def test_appearance_validation():
    assert clean_appearance({"accent": "#ff8800", "font_size": 99, "density": "compact"}) == \
        {"accent": "#ff8800", "font_size": 22, "density": "compact"}
    assert clean_appearance({"accent": ""}) == {"accent": ""}
    for bad in ({"accent": "red"}, {"density": "huge"}, {"nope": 1}, {"custom_css": "x" * 20001}, {"rail_hidden": "github"}):
        with pytest.raises(ValueError):
            clean_appearance(bad)


def test_export_import_roundtrip_and_bad_files_change_nothing(tmp_path):
    cfg = Config(tmp_path / "a.json")
    cfg.data["actions"] = clean_actions([P(), S()])
    cfg.data["appearance"]["accent"] = "#00ff00"
    bundle = export_bundle(cfg)
    other = Config(tmp_path / "b.json")
    import_bundle(other, bundle)
    assert other.data["actions"] == cfg.data["actions"] and other.data["appearance"]["accent"] == "#00ff00"
    before = dict(other.data["appearance"])
    with pytest.raises(ValueError):
        import_bundle(other, {"diatonic": 1, "actions": [P(name="new")]})
    with pytest.raises(ValueError):
        import_bundle(other, {"hello": "world"})
    assert other.data["appearance"] == before


# ---------------- runner ----------------
class Ctx:
    def __init__(self, cfg):
        self.config, self.events = cfg, []
        self.broadcast = self.events.append


def make(tmp_path, allow=True, **action):
    cfg = Config(tmp_path / "c.json")
    cfg.data["scheduler"]["allow_scripts"] = allow
    cfg.data["actions"] = clean_actions([S(**action)])
    ctx = Context = Ctx(cfg)
    return ctx, ActionRunner(ctx), cfg.data["actions"][0]


def drive(runner, action, text="", wait=5.0):
    async def go():
        rid = runner.start(action, text)
        await asyncio.wait_for(runner.tasks[rid], wait)
        return rid
    return asyncio.run(go())


def output(ctx):
    return "".join(e["text"] for e in ctx.events if e["type"] == "script_output")


def done(ctx):
    return next(e for e in ctx.events if e["type"] == "script_done")


def test_script_streams_output_and_reports_exit(tmp_path):
    ctx, r, a = make(tmp_path, command="echo one; echo two >&2; exit 3")
    drive(r, a)
    assert "one" in output(ctx) and "two" in output(ctx)  # stderr is merged
    d = done(ctx)
    assert d["status"] == "error" and d["code"] == 3 and d["action_id"] == a["id"]


def test_input_arrives_as_argument_and_env_not_spliced_into_the_command(tmp_path):
    ctx, r, a = make(tmp_path, command='echo "arg=[$1] env=[$INPUT]"')
    nasty = "$(touch pwned); `touch pwned2`; rm -rf /"
    drive(r, a, nasty)
    assert f"arg=[{nasty}]" in output(ctx) and f"env=[{nasty}]" in output(ctx)
    import os
    assert not os.path.exists(os.path.expanduser("~/pwned")) and not (tmp_path / "pwned").exists()


def test_environment_is_minimal_and_cwd_is_honoured(tmp_path):
    ctx, r, a = make(tmp_path, command="pwd; echo ${DIATONIC_HOME:-unset}", cwd=str(tmp_path))
    drive(r, a)
    assert str(tmp_path.resolve()) in output(ctx) and "unset" in output(ctx)


def test_timeout_and_cancel_kill_the_process_group(tmp_path):
    marker = tmp_path / "alive"
    ctx, r, a = make(tmp_path, command=f"(sleep 2; touch {marker}) & sleep 30", timeout=1)
    t0 = time.time()
    drive(r, a)
    assert done(ctx)["status"] == "timeout" and time.time() - t0 < 6

    ctx2, r2, a2 = make(tmp_path, command="sleep 30")

    async def go():
        rid = r2.start(a2)
        await asyncio.sleep(0.4)
        assert r2.cancel(rid)
        await asyncio.wait_for(r2.tasks[rid], 5)
    asyncio.run(go())
    assert done(ctx2)["status"] == "cancelled"
    time.sleep(2.2)
    assert not marker.exists()


def test_disabled_and_wrong_kind_and_missing_cwd(tmp_path):
    from diatonic.scheduler.scripts import ScriptsDisabled
    ctx, r, a = make(tmp_path, allow=False)
    with pytest.raises(ScriptsDisabled):
        r.start(a)
    with pytest.raises(ValueError):
        r.start({"kind": "prompt", "id": "x"})
    ctx2, r2, a2 = make(tmp_path, cwd=str(tmp_path / "nope"))
    drive(r2, a2)
    assert "not found" in output(ctx2) and done(ctx2)["status"] == "error"


def test_output_is_capped(tmp_path):
    ctx, r, a = make(tmp_path, command="yes x | head -c 400000")
    drive(r, a, wait=15)
    assert len(output(ctx)) < 205_000 and "truncated" in output(ctx)


# ---------------- API ----------------
@pytest.fixture
def client(tmp_path):
    app = create_app(Config(tmp_path / "c.json"), Database(tmp_path / "d.db"), allowed_hosts={"testserver"})
    with TestClient(app) as c:
        yield c


def test_actions_api_roundtrip_and_errors(client):
    r = client.put("/api/actions", json=[P(), S()])
    assert r.status_code == 200 and [a["name"] for a in r.json()] == ["standup", "backup"]
    assert [a["name"] for a in client.get("/api/state").json()["config"]["actions"]] == ["standup", "backup"]
    bad = client.put("/api/actions", json=[P(name="settings")])
    assert bad.status_code == 400 and "built-in" in bad.json()["error"]
    assert len(client.get("/api/state").json()["config"]["actions"]) == 2  # a rejected save changes nothing


def test_config_put_cannot_smuggle_actions_or_bad_appearance(client):
    client.put("/api/config", json={"actions": [S(command="rm -rf /")]})
    assert client.get("/api/state").json()["config"]["actions"] == []
    assert client.put("/api/config", json={"appearance": {"accent": "javascript:alert(1)"}}).status_code == 400
    ok = client.put("/api/config", json={"appearance": {"accent": "#112233", "density": "compact"}}).json()
    assert ok["appearance"]["accent"] == "#112233" and ok["appearance"]["theme"] == "system"  # siblings kept


def test_run_endpoint_only_runs_saved_actions_and_respects_the_master_switch(client):
    saved = client.put("/api/actions", json=[S(command="echo saved"), P()]).json()
    sid, pid = saved[0]["id"], saved[1]["id"]
    off = client.post(f"/api/actions/{sid}/run", json={})
    assert off.status_code == 403 and off.json()["code"] == "scripts_disabled"
    assert client.post("/api/actions/ghost/run", json={}).status_code == 404
    client.put("/api/config", json={"scheduler": {"allow_scripts": True}})
    assert client.post(f"/api/actions/{pid}/run", json={}).status_code == 400  # prompts aren't executable
    r = client.post(f"/api/actions/{sid}/run", json={"input": "x"})
    assert r.status_code == 200 and r.json()["run_id"]
    # a client-supplied command is ignored: only the saved one can run
    client.post(f"/api/actions/{sid}/run", json={"command": "touch /tmp/should-not-exist-diatonic"})
    time.sleep(0.5)
    import os
    assert not os.path.exists("/tmp/should-not-exist-diatonic")


def test_export_import_endpoints(client):
    client.put("/api/actions", json=[P()])
    exp = client.get("/api/customization/export")
    assert exp.status_code == 200 and "attachment" in exp.headers["content-disposition"]
    bundle = exp.json()
    client.put("/api/actions", json=[])
    assert client.post("/api/customization/import", json=bundle).status_code == 200
    assert len(client.get("/api/state").json()["config"]["actions"]) == 1
    assert client.post("/api/customization/import", json={"nope": 1}).status_code == 400
