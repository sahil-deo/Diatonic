import time

import pytest

from diatonic import apptimers, appperms
from diatonic.db import Database
from appsupport import H, declare, user, web  # noqa: F401


def notify(web, title="Done", body="x"):
    return web.post("/api/notify/tube", json={"title": title, "body": body}, headers=H)


def timer(web, op, **body):
    return web.post(f"/api/timers/tube/{op}", json=body, headers=H)


def test_the_permissions_have_one_shape_and_a_bad_one_says_what_is_wrong():
    assert appperms.normalise(None) == {"notify": False, "timer": False, "fetch": [], "tools": []}
    ok = appperms.normalise({"fetch": ["https://YouTube.com/x", "youtube.com"], "notify": True})
    assert ok["fetch"] == ["youtube.com"] and ok["notify"] is True
    for bad, word in (([], "object"), ({"gps": True}, "Unknown permission"), ({"notify": "yes"}, "true or false"), ({"fetch": "a.com"}, "list"),
                      ({"fetch": ["not a domain"]}, "domain name"), ({"tools": [1]}, "list")):
        with pytest.raises(ValueError, match=word):
            appperms.normalise(bad)


def test_the_agent_cannot_write_a_manifest_with_bad_permissions(web):
    r = declare(web, {"gps": True})
    assert r.status_code == 400 and "manifest.json" in r.json()["error"]


def test_using_something_undeclared_is_refused_and_says_what_to_declare(web, user):
    declare(web, None)
    r = notify(web)
    assert r.status_code == 400 and '"permissions": {"notify": true}' in r.json()["error"] and not user.cards
    r = timer(web, "start", seconds=5)
    assert r.status_code == 400 and "timer" in r.json()["error"]


def test_the_user_is_asked_once_for_the_whole_list_and_again_when_the_list_grows(web, user):
    declare(web, {"notify": True, "timer": True})
    assert notify(web).status_code == 200 and len(user.cards) == 1
    ask = next(e for e in web.events if e["type"] == "perm_ask")
    assert ask["items"] == ["show notifications", "run timers"] and ask["name"] == "Tube"
    assert timer(web, "list").status_code == 200 and len(user.cards) == 1      # the timer permission was in the same list
    declare(web, {"notify": True, "timer": True, "fetch": ["a.test"]})
    assert notify(web).status_code == 200 and len(user.cards) == 2             # a new item: ask again
    asks = [e for e in web.events if e["type"] == "perm_ask"]
    assert asks[-1]["new"] == ["read a.test"]


def test_a_no_stops_the_call_and_nothing_is_remembered(web, user):
    declare(web, {"notify": True})
    user.says = False
    r = notify(web)
    assert r.status_code == 400 and "did not allow" in r.json()["error"]
    assert "permissions_ok" not in web.cfg.app("tube")


def test_the_approval_lives_in_settings_so_the_agent_cannot_grant_itself(web, user):
    declare(web, {"notify": True})
    user.says = None                                                           # nobody answers
    appperms.ASK_TIMEOUT = 0.1
    try:
        assert notify(web).status_code == 400
    finally:
        appperms.ASK_TIMEOUT = 120
    assert "permissions_ok" not in web.cfg.app("tube")
    assert "permissions_ok" not in web.get("/api/custom-apps/tube").json()


def test_notify_stores_in_the_inbox_and_is_rate_limited(web, user):
    declare(web, {"notify": True})
    assert notify(web, "Break over", "Back to work").status_code == 200
    assert any(e["type"] == "notification" and e["title"] == "Break over" for e in web.events)
    assert notify(web, "").status_code == 400
    codes = [notify(web, f"n{i}").status_code for i in range(25)]
    assert codes.count(200) == 19 and codes[-1] == 429                         # 20 a minute in all, one was used above


def wait_for(fn, seconds=3):
    end = time.time() + seconds
    while time.time() < end:
        if fn():
            return True
        time.sleep(0.02)
    return False


def test_a_timer_can_be_paused_resumed_listed_and_cancelled(web, user, monkeypatch):
    monkeypatch.setattr(apptimers, "MIN_SECONDS", 0.01)
    declare(web, {"timer": True})
    t = timer(web, "start", seconds=60, label="Tea").json()["result"]
    assert t["state"] == "running" and 59 < t["remaining"] <= 60
    p = timer(web, "pause", id=t["id"]).json()["result"]
    assert p["state"] == "paused" and p["ends_at"] is None
    time.sleep(0.3)
    assert timer(web, "list").json()["result"][0]["remaining"] == p["remaining"]   # a paused timer does not run down
    assert timer(web, "pause", id=t["id"]).status_code == 400
    assert timer(web, "resume", id=t["id"]).json()["result"]["state"] == "running"
    assert timer(web, "cancel", id=t["id"]).json()["result"] is True
    assert timer(web, "list").json()["result"] == [] and timer(web, "cancel", id=t["id"]).status_code == 400
    assert timer(web, "start", seconds=0).status_code == 400 and timer(web, "start", seconds="5").status_code == 400


def test_a_finished_timer_notifies_and_sends_an_event_without_any_panel(web, user, monkeypatch):
    monkeypatch.setattr(apptimers, "MIN_SECONDS", 0.01)
    declare(web, {"timer": True})
    t = timer(web, "start", seconds=0.05, label="Tea").json()["result"]
    assert wait_for(lambda: any(e.get("event") == "timer:done" for e in web.events))
    done = next(e for e in web.events if e.get("event") == "timer:done")
    assert done["app"] == "tube" and done["data"] == {"id": t["id"], "label": "Tea"}
    assert any(e["type"] == "notification" and e["title"] == "Tea" for e in web.events)
    assert timer(web, "list").json()["result"][0]["state"] == "done"


def test_a_running_timer_survives_a_restart_and_one_that_ended_while_off_fires_at_once(tmp_path, monkeypatch):
    import asyncio

    class Ctx:
        notifier, events = None, []
        def broadcast(self, ev): self.events.append(ev)

    db = Database(tmp_path / "d.db")
    first, ctx = apptimers.AppTimers(Ctx(), db), Ctx()

    async def go():
        a = first.start("tube", 60, "later")
        b = first.start("tube", 1, "soon")
        await first.stop()                                                      # the server goes away
        db.conn.execute("UPDATE app_timers SET ends_at=? WHERE id=?", (time.time() - 5, b["id"]))
        db.conn.commit()
        second = apptimers.AppTimers(ctx, db)
        second.start_all()                                                      # and comes back
        await asyncio.sleep(0.1)
        states = {t["label"]: t for t in second.list("tube")}
        await second.stop()
        return a, states

    a, states = asyncio.run(go())
    assert states["later"]["state"] == "running" and states["later"]["remaining"] > 50
    assert states["soon"]["state"] == "done" and any(e.get("event") == "timer:done" for e in ctx.events)


def test_deleting_the_app_stops_its_timers(web, user):
    declare(web, {"timer": True})
    timer(web, "start", seconds=60)
    assert web.delete("/api/custom-apps/tube", headers=H).status_code == 200
    assert web.ctx.apptimers.list("tube") == [] and not web.ctx.apptimers.tasks


def add(web, caller="tube", **kw):
    return web.post("/api/panel/tools/todos/add_todo", json={"title": "Buy milk"}, headers={**H, **({"X-Diatonic-App": caller} if caller else {})})


def test_a_panel_without_a_file_app_behind_it_still_cannot_change_things(web, user):
    assert add(web, caller=None).status_code == 403 and add(web, caller="ghost").status_code == 403
    assert not user.cards


def test_a_tool_that_changes_things_needs_a_declaration_and_a_card_every_time(web, user):
    declare(web, {"tools": []})
    r = add(web)
    assert r.status_code == 400 and '"todos_add_todo"' in r.json()["error"] and not user.cards
    declare(web, {"tools": ["todos_add_todo"]})
    assert add(web).status_code == 200
    kinds = [e["type"] for e in web.events if e["type"] in ("perm_ask", "tool_ask")]
    assert kinds == ["perm_ask", "tool_ask"]
    ask = next(e for e in web.events if e["type"] == "tool_ask")
    assert ask["name"] == "Tube" and ask["describe"]
    assert add(web).status_code == 200
    assert [e["type"] for e in web.events if e["type"] in ("perm_ask", "tool_ask")] == ["perm_ask", "tool_ask", "tool_ask"]   # the list once, the change every time
    assert web.ctx.registry.apps["todos"].c.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 2


def test_saying_no_to_the_change_leaves_the_data_alone(web, user):
    declare(web, {"tools": ["todos_add_todo"]})
    user.says = True
    first = add(web)
    assert first.status_code == 200
    user.says = False
    r = add(web)
    assert r.status_code == 403 and "did not allow" in r.json()["error"]
    assert web.ctx.registry.apps["todos"].c.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 1


def test_a_panel_can_never_run_the_terminal_or_interface_tools(web, user):
    declare(web, {"tools": ["shell_run", "ui_open_app"]})
    assert web.post("/api/panel/tools/shell/run", json={"command": "ls"}, headers={**H, "X-Diatonic-App": "tube"}).status_code == 404
    assert web.post("/api/panel/tools/ui/open_app", json={"app": "todos"}, headers={**H, "X-Diatonic-App": "tube"}).status_code == 404
