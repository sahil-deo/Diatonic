"""The agent can move the screen: open apps and items, change the layout. Only the asking window is moved."""
import asyncio

from conftest import ScriptedProvider

from diatonic.agent import loop
from diatonic.agent.runs import RunManager
from diatonic.apps.base import App, Tool
from diatonic.apps.ui import create as create_ui


class StubCal(App):
    id, name, panel = "gcal", "Calendar", True
    keywords = ["calendar", "event"]


class StubMail(App):
    id, name, panel = "gmail", "Mail", True
    keywords = ["mail", "email"]
    seen: list

    def tools(self):
        self.seen = getattr(self, "seen", [])

        async def list_emails(a):
            self.seen.append(a)
            return {"emails": [{"ref": "me@x.com|t9|m9", "subject": "Lunch?"}]} if a.get("query") != "nothing" else {"emails": "No emails match."}
        return [Tool("list_emails", "list", {"properties": {"query": {"type": "string"}, "limit": {"type": "integer"}, "unread_only": {"type": "boolean"}}}, list_emails)]


class StubNotes(App):
    id, name, panel = "notes", "Notes", True
    keywords = ["note"]

    def tools(self):
        async def search_notes(a):
            return [{"title": "Diatonic Apps"}] if "diatonic" in a["query"].lower() else "No notes match."
        return [Tool("search_notes", "s", {"properties": {"query": {"type": "string"}}, "required": ["query"]}, search_notes)]


def call(name, **args):
    return {"tool_calls": [{"name": name, "arguments": args}]}


def setup(env):
    ctx, reg, app, db = env
    runs = RunManager()
    ctx.send_ui, ctx.registry = runs.send_ui, reg
    for a in (StubCal(ctx), StubMail(ctx), StubNotes(ctx), create_ui(ctx)):
        reg.register(a)
    return ctx, reg, db, runs


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def run_chat(env, monkeypatch, script, client="tab-B", text="show my calendar"):
    ctx, reg, db, runs = setup(env)

    async def go():
        a, b = runs.subscribe("tab-A"), runs.subscribe("tab-B")
        monkeypatch.setattr(loop, "make_provider", lambda c: ScriptedProvider(script))
        run = runs.start(ctx, reg, db.create_conversation("S"), text, False, client=client)
        await asyncio.wait([run.task])
        return drain(a), drain(b)

    return asyncio.run(go())


def test_open_app_moves_only_the_window_that_asked(env, monkeypatch):
    a, b = run_chat(env, monkeypatch, [[call("ui_open_app", app="calendar", view="month", date="2026-11-16")], [{"content": "Done."}]])
    assert [e for e in a if e["type"] == "ui"] == []                    # the other tab is untouched
    (ev,) = [e for e in b if e["type"] == "ui"]
    assert ev["action"] == "open" and ev["app"] == "gcal" and ev["target"] == "tab-B"
    assert ev["params"] == {"view": "month", "date": "2026-11-16"}
    card = [e for e in b if e["type"] == "tool_call"][0]
    assert card["needs_approval"] is False                              # navigation never asks


def test_open_item_and_loose_app_names(env, monkeypatch):
    _, b = run_chat(env, monkeypatch, [[call("ui_open_item", app="my email", what="latest")], [{"content": "Opened."}]])
    (ev,) = [e for e in b if e["type"] == "ui"]
    assert ev["app"] == "gmail" and ev["params"] == {"item": "me@x.com|t9|m9"}      # the tool found the newest mail itself
    _, b = run_chat(env, monkeypatch, [[call("ui_open_item", app="notes", what="the diatonic apps note")], [{"content": "Opened."}]])
    (ev,) = [e for e in b if e["type"] == "ui"]
    assert ev["params"] == {"item": "Diatonic Apps"}
    _, b = run_chat(env, monkeypatch, [[call("ui_open_item", app="notes", what="grocery")], [{"content": "None."}]])
    res = [e for e in b if e["type"] == "tool_result"][0]
    assert not [e for e in b if e["type"] == "ui"] and not res["ok"] and "No note matches" in res["preview"]


def test_layout(env, monkeypatch):
    _, b = run_chat(env, monkeypatch, [[call("ui_set_layout", mode="focus", sidebar="hide")], [{"content": "ok"}]])
    (ev,) = [e for e in b if e["type"] == "ui"]
    assert ev["action"] == "layout" and ev["mode"] == "focus" and ev["sidebar"] == "hide"


def test_background_runs_cannot_move_a_screen(env, monkeypatch):
    a, b = run_chat(env, monkeypatch, [[call("ui_open_app", app="calendar")], [{"content": "I can't."}]], client=None)
    assert not [e for e in a + b if e["type"] == "ui"]
    res = [e for e in b if e["type"] == "tool_result"][0]
    assert not res["ok"] and "background run" in res["preview"]


def test_bad_arguments_get_a_helpful_error(env, monkeypatch):
    ctx, reg, db, runs = setup(env)
    ui = reg.apps["ui"]

    async def go():
        from diatonic.agent.session import RUN_CLIENT
        RUN_CLIENT.set("tab-A")
        runs.subscribe("tab-A")
        for args, needle in [({"app": "weather"}, "no app called"), 
                             ({"app": "gcal", "date": "next tuesday"}, "YYYY-MM-DD"), ({"app": "gmail", "date": "2026-11-16"}, "Only the calendar")]:
            try:
                await ui.open_app(args)
            except Exception as e:
                assert needle in str(e), (args, str(e))
            else:
                raise AssertionError(args)

        r = await ui.open_app({"app": "gcal", "view": "decade"})         # a wrong view name still shows the app
        assert "no view 'decade'" in r and "week" in r

    asyncio.run(go())


def test_closed_window_is_reported(env):
    ctx, reg, db, runs = setup(env)

    async def go():
        from diatonic.agent.session import RUN_CLIENT
        RUN_CLIENT.set("gone")
        try:
            await reg.apps["ui"].open_app({"app": "gcal"})
        except Exception as e:
            assert "no longer open" in str(e)
        else:
            raise AssertionError

    asyncio.run(go())


def test_routing_offers_ui_tools_for_show_requests(env):
    ctx, reg, db, runs = setup(env)
    for text in ["show my calendar", "open this mail", "go to my notes", "hide the sidebar"]:
        tools, matched = loop.select_tools(reg, text, set())
        assert "ui_open_app" in tools, text
    tools, _ = loop.select_tools(reg, "what's on my calendar today", set())
    assert "ui_open_app" not in tools                                   # plain lookups don't pay for the extra tools


def test_navigation_requests_offer_only_interface_tools(env):
    ctx, reg, db, runs = setup(env)
    for text in ["show my calendar", "open my latest email", "Hide the sidebar", "please open the README file", "go to my notes"]:
        tools, matched = loop.select_tools(reg, text, set())
        assert set(tools) == {"ui_open_app", "ui_open_item", "ui_set_layout"} and matched, text
    for text in ["show me my 3 most recent repos", "open my mail and summarize it", "what's on my calendar", "show my calendar and then email Sam",
                 "open issue 12", "show me what is in my inbox"]:
        assert not loop.is_navigation(text), text                       # these want data, so the app's own tools stay available


def test_screen_context_is_added_only_when_the_user_points_at_it(env, monkeypatch):
    ctx, reg, db, runs = setup(env)
    screen = {"app": "gmail", "kind": "email", "ref": "me@x.com|t9", "title": "Your submission is still waiting"}
    seen = {}

    class P(ScriptedProvider):
        async def stream(self, messages, tools=None, model=None, options=None):
            if "user" not in seen:   # the first call is the user's turn (later ones may be nudges)
                seen["user"], seen["tools"] = messages[-1]["content"], [t["function"]["name"] for t in tools or []]
            async for c in super().stream(messages, tools, model, options):
                yield c

    async def go(text):
        seen.clear()
        monkeypatch.setattr(loop, "make_provider", lambda c: P([[{"content": "ok"}]] * 4))
        async for _ in loop.run_agent(ctx, reg, db.create_conversation()["id"], text, screen=screen):
            pass

    asyncio.run(go("summarize this email"))
    assert "me@x.com|t9" in seen["user"] and "Your submission is still waiting" in seen["user"]
    assert "gmail_list_emails" in seen["tools"]                          # the app on screen is offered even though "mail" wasn't said
    asyncio.run(go("show my calendar"))
    assert "me@x.com|t9" not in seen["user"]                             # unrelated requests don't carry it
    assert loop.screen_line(reg, {"app": "nope"}) == ("", None)


def test_buttons_and_rail_icons(env, tmp_path):
    from diatonic.mods import ModStore
    ctx, reg, db, runs = setup(env)
    ctx.mods = ModStore(tmp_path)
    ctx.broadcast = lambda ev: None
    ui = reg.apps["ui"]

    async def go():
        from diatonic.agent.session import RUN_CLIENT
        RUN_CLIENT.set("tab-A")
        await ui.add_button({"label": "Files", "app": "my email"})
        await ui.set_theme({"hide_apps": ["calendar"], "sidebar_width": "wide"})
        assert ctx.config.data["appearance"]["rail_hidden"] == ["gcal"] and ctx.config.data["appearance"]["sidebar_width"] == "wide"
        await ui.set_theme({"show_apps": "calendar"})
        assert ctx.config.data["appearance"]["rail_hidden"] == []
        ctx.mods.revert(ctx.config); ctx.mods.revert(ctx.config)          # undo both theme changes
        assert ctx.config.data["appearance"]["sidebar_width"] == "default"

        await ui.add_button({"label": "Inbox", "app": "gmail"})           # same action, new label: replaces, doesn't duplicate
        assert [m["title"] for m in ctx.mods.list() if m["kind"] == "button"] == ["Inbox"]
        await ui.add_button({"name": "btn-files", "label": "Mail"})        # relabel by name keeps the action
        await ui.add_button({"label": "Files", "app": "my email"})

    asyncio.run(go())
    import json as _json
    b = ctx.mods.get("btn-files")
    assert b["kind"] == "button" and _json.loads(b["code"]) == {"label": "Files", "app": "gmail"}
