"""The `ctx` methods an app panel can call, as catalog entries (`ctx-api`), so the agent finds them with platform_search.

Each Phase 2 item adds its entries here. A test checks that every store call the server accepts has an entry with a doc and an example.
"""
from __future__ import annotations

from .catalog import Entry


def _a(name: str, type_: str, doc: str, required: bool = True) -> dict:
    return {"name": name, "type": type_, "doc": doc, "required": required}


_TABLE = _a("table", "string", "Name of the table, for example \"topics\". It is created by the first add.")
_STORE = [
    ("get", "Read one saved value by key. Returns the default when nothing is saved under it.", [_a("key", "string", "Name of the value."), _a("default", "any", "Returned when the key is empty.", False)],
     'const prefs = await ctx.store.get("prefs", { unit: "min" });'),
    ("set", "Save one value (any JSON) under a key, replacing the old one. Use it for settings and small state that must survive closing the panel.", [_a("key", "string", "Name of the value."), _a("value", "any", "Plain JSON.")],
     'await ctx.store.set("prefs", { unit: "sec" });'),
    ("keys", "List the keys that have a saved value.", [], "const names = await ctx.store.keys();"),
    ("remove", "Delete one saved value by key.", [_a("key", "string", "Name of the value.")], 'await ctx.store.remove("prefs");'),
    ("add", "Add one row to a table. The row is an object; it gets a number id back. For many rows use addMany.", [_TABLE, _a("row", "object", "The fields, for example {name: \"Limits\", done: false}.")],
     'const row = await ctx.store.add("topics", { name: "Limits", done: false });'),
    ("addMany", "Add many rows to a table in one call (up to 1000), all or none. Use it for imports instead of one add per row.", [_TABLE, _a("rows", "array", "A list of row objects.")],
     'await ctx.store.addMany("topics", [{ name: "Limits" }, { name: "Series" }]);'),
    ("update", "Change fields of one row by its id; other fields stay.", [_TABLE, _a("id", "number", "The row id."), _a("patch", "object", "The fields to change.")],
     'await ctx.store.update("topics", 3, { done: true });'),
    ("updateMany", "Change several rows in one call. Each object has the row id and the fields to change; if one id is missing nothing is changed.", [_TABLE, _a("rows", "array", "Objects like {id: 3, done: true}.")],
     'await ctx.store.updateMany("topics", [{ id: 1, done: true }, { id: 2, done: true }]);'),
    ("delete", "Delete a row by id, several rows with a list of ids, or every row with \"all\". Resolves to how many were removed.", [_TABLE, _a("ids", "number | number[] | \"all\"", "Which rows.")],
     'await ctx.store.delete("topics", [4, 5]);'),
    ("query", "Read rows of a table. Optional where (fields that must equal a value), order (a field; \"-due\" for newest first), limit (default 200, at most 1000) and offset.",
     [_TABLE, _a("opts", "object", "{where?, order?, limit?, offset?}", False)], 'const open = await ctx.store.query("topics", { where: { done: false }, order: "name" });'),
    ("tables", "List the tables the app has and how many rows each holds.", [], "const sizes = await ctx.store.tables();  // {topics: 24}"),
]



_MANIFEST = ('Declare it in manifest.json: "permissions": {"notify": true, "timer": true, "fetch": ["youtube.com"], "tools": ["todos_add_todo"]}. '
             "The user approves the list once; using something that is not declared is refused.")


def _t(name: str, doc: str, args: list[dict], example: str, effects=("write",)) -> Entry:
    return Entry("ctx-api", name, doc, args, example, effects=list(effects), domain="timer")


_FETCH = Entry(
    "ctx-api", "ctx.fetch",
    "Read a web page or public API through the server (the panel has no network of its own). Resolves to {status, ok, text, json()}. GET only, text, HTML and JSON only, 2 MB and 15 seconds at most, never a local or private address. "
    "The domain must be in permissions.fetch (the name only, for example \"youtube.com\"). A refusal throws an Error with a readable message, so catch it and show it. Images are shown with an img tag, not fetched. " + _MANIFEST,
    [_a("url", "string", "A full http or https address, with the query already in it.")],
    'const r = await ctx.fetch("https://example.com/api/search?q=" + encodeURIComponent(q));\nconst data = r.json();', effects=["read", "external"], domain="network")
_NOTIFY = Entry(
    "ctx-api", "ctx.notify", "Tell the user something: it goes to the inbox and shows as a system banner. At most 20 a minute. Needs permissions.notify. " + _MANIFEST,
    [_a("title", "string", "Short headline."), _a("body", "string", "One more line.", False)], 'await ctx.notify("Break over", "Time to start again.");', effects=["display"], domain="notify")
_TIMER = [
    _t("ctx.timer.start", "Start a countdown that runs on the server, so it keeps going when the panel or the browser is closed. When it ends the user gets a notification and onDone fires. Resolves to {id, label, seconds, state, remaining, ends_at}. Needs permissions.timer. " + _MANIFEST,
       [_a("seconds", "number", "1 to 604800. Convert minutes and hours to seconds."), _a("opts", "object", "{label?}", False)], 'const t = await ctx.timer.start(25 * 60, { label: "Focus" });'),
    _t("ctx.timer.pause", "Pause a running timer; its remaining time is kept.", [_a("id", "number", "Timer id from start or list.")], "await ctx.timer.pause(t.id);"),
    _t("ctx.timer.resume", "Continue a paused timer from where it stopped.", [_a("id", "number", "Timer id.")], "await ctx.timer.resume(t.id);"),
    _t("ctx.timer.cancel", "Remove a timer in any state (running, paused or finished).", [_a("id", "number", "Timer id.")], "await ctx.timer.cancel(t.id);"),
    _t("ctx.timer.list", "List the app's timers, each with its state (running, paused, done) and the seconds remaining right now. Call it when the panel opens to show timers that kept running.", [],
       "const timers = await ctx.timer.list();  // [{id, label, state, remaining}]", effects=("read",)),
    _t("ctx.timer.onDone", "Run a function when one of this app's timers ends while the panel is open. Removed when the panel closes.", [_a("fn", "function", "Called with {id, label}.")],
       "ctx.timer.onDone(({ id }) => showDone(id));", effects=("read",)),
]
_ON = Entry(
    "ctx-api", "ctx.on", "Listen for something that happens elsewhere: \"timer:done\" (this app's timers), \"todo:changed\" (a to-do was added, changed or deleted), or \"<app>:changed\" for another app whose data changed (for example \"notes:changed\"). "
    "Mail has no event yet. Removed when the panel closes.", [_a("event", "string", "The event name."), _a("fn", "function", "Called with the event's data.")],
    'ctx.on("todo:changed", () => reload());', effects=["read"], domain="events")
_TOOL = Entry(
    "ctx-api", "ctx.tool", "Run another app's tool: ctx.tool(\"<app>_<tool>\", args). Read tools just run. A tool that changes things must be listed in permissions.tools, and every call shows the user an approval card naming this app. "
    "The terminal and the interface tools are never available. " + _MANIFEST, [_a("name", "string", "<app>_<tool>, for example todos_list_todos."), _a("args", "object", "The tool's arguments.", False)],
    'const todos = await ctx.tool("todos_list_todos", {});', effects=["read", "write"], domain="tools")
_UI = [
    Entry("ctx-api", "ctx.ui.listApp",
          "A whole list view in one call: search, filter buttons, rows (or a table when columns are given), check marks, a detail pane and the same actions on the row and in the detail. Arrow keys and Enter step through the rows. "
          "Returns an element; call el.set(rows) to show new data and el.selected() for the chosen row. Each row is an object with a unique id and a title (optionally subtitle, badge, right). "
          "An action's run may throw: the failure is shown to the user and goes to the app's log, and a returned string is shown as the result.",
          [_a("rows", "Array<object>", "The data; every row needs an id and a title (or name).", False),
           _a("columns", "Array<{key, label, align?, render?}>", "Show a table instead of rows.", False),
           _a("detail", "function", "detail(row) returns what to show in the pane beside the list.", False),
           _a("actions", "Array<{label, run, kind?, when?}>", "Buttons on each row and in the detail. run(row) may be async.", False),
           _a("filters", "Array<{id, label, test}>", "Buttons that narrow the rows; test(row) is true to keep a row.", False),
           _a("search", "boolean", "Add a search box that matches any field.", False),
           _a("marks", "{key, label, onChange}", "A check box per row bound to row[key]; onChange(row, checked).", False),
           _a("empty", "string", "Text when there are no rows.", False)],
          'const view = ctx.ui.listApp({ rows, search: true, marks: { key: "done", onChange: (r, v) => ctx.store.update("topics", r.id, { done: v }) },\n  actions: [{ label: "Delete", kind: "danger", run: (r) => ctx.store.delete("topics", r.id) }], detail: (r) => ctx.ui.kv({ items: [{ label: "Unit", value: r.unit }] }) });\nel.append(view);',
          effects=["display"], domain="ui"),
    Entry("ctx-api", "ctx.ui.run",
          "Run an action the way every app should: disables the button while it runs, shows a returned string as the result, and on failure shows the error and writes it to the app's log (where read_app_logs finds it). Use it for every click handler that does work.",
          [_a("label", "string", "Name of the action, used in the error message."), _a("fn", "function", "The work; may be async."), _a("button", "Node", "The button to disable meanwhile.", False)],
          'ctx.ui.button({ label: "Save", onclick: (e) => ctx.ui.run("Save", () => ctx.store.set("k", v), e.currentTarget) })', effects=["display"], domain="ui"),
]
_BASICS = [
    Entry("ctx-api", "ctx.h", "Build an element: ctx.h(tag, {attrs, onclick}, ...children). Text children are escaped. Use class names from the harness (btn, btn primary).", [_a("tag", "string", "Element name."), _a("attrs", "object", "Attributes and on* handlers.", False)],
          "el.append(ctx.h('button', {class: 'btn', onclick: go}, 'Go'));", effects=["display"], domain="ui"),
    Entry("ctx-api", "ctx.state", "ctx.state.get(key, default) and ctx.state.set(key, value): small settings kept in the browser between visits (view, filter). For real data use ctx.store.", [_a("key", "string", "Name."), _a("value", "any", "Plain JSON.", False)],
          "ctx.state.set('view', 'week');", effects=["write"], domain="state"),
    Entry("ctx-api", "ctx.onClose", "Run a function when the panel is closed: clear timers (setInterval) and remove document or window listeners. The panel must keep no module-level variables.", [_a("fn", "function", "Cleanup.")],
          "const t = setInterval(tick, 1000); ctx.onClose(() => clearInterval(t));", effects=["read"], domain="ui"),
]


def _store_entries() -> list[Entry]:
    return [Entry("ctx-api", f"ctx.store.{name}", doc + " Data is kept on the server for this app only and is deleted with the app. No approval needed.", args, example,
                  effects=["read"] if name in ("get", "keys", "query", "tables") else ["write"], domain="store") for name, doc, args, example in _STORE]


def entries() -> list[Entry]:
    return [_FETCH, _NOTIFY, _ON, _TOOL, *_TIMER, *_UI, *_BASICS, *_store_entries()]
