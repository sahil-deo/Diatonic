"""The page's pure logic (keys, search) is plain JavaScript modules with their own node tests in tests/js."""
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).parent / "js"


@pytest.mark.skipif(not shutil.which("node"), reason="node is not installed")
def test_node_suites_pass():
    files = sorted(str(p) for p in JS.glob("*.test.mjs"))
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1000:]


def test_layouts_and_macros_have_a_mouse_path_and_no_browser_prompt():
    """Everything a command does must also be reachable by mouse: Settings has the pages and the core commands ask in the page, not with prompt()."""
    import re
    web = Path(__file__).parent.parent / "diatonic" / "web" / "js"
    core = (web / "commands-core.js").read_text(encoding="utf-8")
    assert not re.search(r"(?<![.\w])prompt\(", core), "a core command opens a browser prompt: use a menu or Settings instead"
    tabs = (web / "settings.js").read_text(encoding="utf-8")
    assert '["layouts", "Layouts"' in tabs and '["macros", "Macros"' in tabs
    bar = (web / "statusbar.js").read_text(encoding="utf-8")
    assert 'id: "sb-layout"' in bar or '"sb-layout"' in bar and "sb-add" in bar     # the layout menu button and the pin button


def test_every_panel_command_has_a_menu_entry():
    """The panel menu is the mouse path for the panel commands; if one is added to the commands it must be added there too."""
    web = Path(__file__).parent.parent / "diatonic" / "web" / "js"
    core = (web / "commands-core.js").read_text(encoding="utf-8")
    menu = (web / "panel-menu.js").read_text(encoding="utf-8")
    for cmd, call in [("panel.zoom", "toggleZoom"), ("panel.duplicate", "duplicateTab"), ("panel.move.left", "moveTabToward"), ("panel.swap.left", "swapTabWith"), ("tab.close_others", "closeOtherTabs")]:
        assert f'"{cmd}"' in core and call in menu, f"{cmd} has no entry in the panel menu"


def test_panel_menu_has_reload_on_top_and_no_redundant_header():
    """The panel menu must put Reload on top and not duplicate the tab's name in a redundant header."""
    web = Path(__file__).parent.parent / "diatonic" / "web" / "js"
    menu = (web / "panel-menu.js").read_text(encoding="utf-8")
    assert "pm-head" not in menu, "panel-menu still contains pm-head header"
    pop_index = menu.index("openPopover(anchor,")
    reload_index = menu.index('row("Reload", "refresh"')
    assert reload_index > pop_index, "Reload button not found inside openPopover"
    assert reload_index < menu.index('row("Split to the right"'), "Reload button must be on top before splits"


def test_reload_tab_coalesces_concurrent_calls():
    """reloadTab must guard against overlapping concurrent reloads on the same instance."""
    ws = (Path(__file__).parent.parent / "diatonic" / "web" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert "currentInst.reloading" in ws, "reloadTab does not track active reload execution"
    assert "currentInst.pendingReload" in ws, "reloadTab does not queue pending reload requests"


def test_rebuilt_panel_containers_have_no_entrance_animation():
    """The panel area is rebuilt on every move and click; a fade-in that stalls leaves a panel invisible (it looked black) until the next event."""
    import re
    css = (Path(__file__).parent.parent / "diatonic" / "web" / "css" / "app.css").read_text(encoding="utf-8")
    for sel in (".workspace-stack", ".workspace-tabbar", ".workspace-panel-body", ".workspace-stack-content"):
        for rule in re.findall(re.escape(sel) + r"\s*\{([^}]*)\}", css):
            assert "animation" not in rule, f"{sel} has an animation"


def test_drag_and_drop_does_not_rebuild_the_page_inside_the_drop_event():
    """Rebuilding the panel area inside `drop` removes the dragged tab mid-drag; Safari and Firefox then leave the page unpainted (black) until the next click."""
    import re
    web = Path(__file__).parent.parent / "diatonic" / "web"
    ws = (web / "js" / "workspace.js").read_text(encoding="utf-8")
    for body in re.findall(r"\.ondrop = \(e\) => \{(.*?)\n    \};", ws, re.S):
        if "text/diatonic-tab" in body:
            assert "afterDrag(" in body and "applyTree(" not in body.replace("afterDrag(() => applyTree(", ""), "a tab drop changes the layout before the drag has ended"
    tiles = (web / "apps" / "tiles.js").read_text(encoding="utf-8")
    drop = tiles[tiles.index("card.ondrop"):tiles.index("// Header")]
    assert "renderGrid();" not in drop, "tile drop rebuilds the grid inside the drop event"
    css = (web / "css" / "app.css").read_text(encoding="utf-8")
    for sel in (".workspace-stack", ".rail, .sidebar", ".statusbar", ".composer"):
        rule = css[css.index(sel + " {") if sel + " {" in css else css.index(sel):].split("}")[0]
        assert "backdrop-filter" not in rule, f"{sel} blurs what is behind it; use blur only on floating things"
    assert "background-attachment: fixed" not in css


def test_workspace_only_calls_functions_it_has():
    """The tab drop handlers called moveTab() while the file imports it as treeMoveTab; a try/catch hid the ReferenceError and tab drops silently did nothing."""
    import re
    ws = (Path(__file__).parent.parent / "diatonic" / "web" / "js" / "workspace.js").read_text(encoding="utf-8")
    bare = re.findall(r"(?<![\w.])moveTab\(", ws)
    assert not bare, "workspace.js calls moveTab(), which is only imported as treeMoveTab"


def test_panel_bodies_are_never_moved_by_a_render():
    """A PDF is an <iframe>, and detaching an iframe reloads it (back to page 1). Rendering the layout must only position bodies, never re-attach them."""
    ws = (Path(__file__).parent.parent / "diatonic" / "web" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert "layerEl.append(body)" in ws, "bodies must be created in their own layer"
    assert "content.append(inst.body)" not in ws and "container.append(" not in ws, "a body is appended into a stack: the next render would re-attach it and reload any iframe"
    render = ws[ws.index("export function renderWorkspace"):]
    render = render[:render.index("\n}\n")]
    assert "slots.clear()" in render and "syncBodies()" in render
    apply = ws[ws.index("export function applyTree"):]
    apply = apply[:apply.index("\n}\n")]
    assert "disposeTab(id)" in apply and "instances.delete(id)" not in apply, "applyTree must remove the body of a panel it drops (a leftover body stays on screen)"
    assert "!instances.has(el.dataset.bodyFor)" in ws, "orphaned bodies must be swept from the layer"



def test_split_children_share_the_space_the_dividers_leave():
    """Children sized as fixed percentages plus 8px dividers add up to more than 100%, and overflow:hidden then cuts off the last panel (its right or bottom edge)."""
    ws = (Path(__file__).parent.parent / "diatonic" / "web" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert "flex: 0 0 ${" not in ws, "a panel is sized as a fixed share of the whole, so dividers push the last one out of view"
    assert ".style.flex = `${newSizes[i] * 100} 1 0px`" in ws


def test_panels_only_take_things_the_panel_context_provides():
    """tiles.js took `openPanel` from ctx, which has no such member, so every Home tile's open button threw and nothing opened."""
    import re
    web = Path(__file__).parent.parent / "diatonic" / "web"
    src = (web / "js" / "panel.js").read_text(encoding="utf-8")
    body = src[src.index("  const ctx = {\n    api, h, esc, toast,"):]
    body = body[:body.index("\n  };")]
    provided = set(re.findall(r"^    (\w+)\s*[:,(]", body, re.M)) | set(re.findall(r"\b(\w+),", body.split("\n")[1])) | set(re.findall(r"\bctx\.(\w+) = ", src))
    assert {"api", "h", "toast", "reload", "state", "onClose", "prompt", "replaceTab"} <= provided, provided
    for f in sorted((web / "apps").glob("*.js")):
        for names in re.findall(r"const \{([^}]*)\} = ctx;", f.read_text(encoding="utf-8")):
            for part in names.split(","):
                key = part.split(":")[0].strip()
                assert key in provided, f"{f.name} takes `{key}` from ctx, which does not provide it"


def test_workspace_tab_double_click_toggles_zoom():
    """Double-clicking a tab triggers toggleZoom (Zoom: fill the window)."""
    ws = (Path(__file__).parent.parent / "diatonic" / "web" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert "ondblclick" in ws, "workspace tab has no double-click handler"
    tab_btn_part = ws[ws.index("const tabBtn = h("):ws.index("tabsEl.append(tabBtn)")]
    assert "ondblclick:" in tab_btn_part and "toggleZoom(" in tab_btn_part, "tabBtn must trigger toggleZoom on double-click"
    close_btn_part = tab_btn_part[tab_btn_part.index('title: "Close"'):]
    assert "ondblclick:" in close_btn_part and "stopPropagation" in close_btn_part, "close button must stop dblclick propagation"


def test_todos_drag_and_drop_handlers():
    """todos.js must properly handle dropping files and emails without broken references."""
    todos_src = (Path(__file__).parent.parent / "diatonic" / "web" / "apps" / "todos.js").read_text(encoding="utf-8")
    assert "${B}/" not in todos_src, "todos.js references undefined variable B"
    assert "${BASE}/create" in todos_src[todos_src.index("ondrop"):], "todos.js must post to ${BASE}/create on drop"
    assert "toast" in todos_src[:todos_src.index("= ctx;")], "todos.js must take toast from ctx"
    assert '"Files"' in todos_src, "todos.js must accept OS Files in ondragover"


def test_launcher_search_and_highlight_styles():
    """Launcher search input must be stripped of leaked panel-body input borders, and card highlights must be softened."""
    css = (Path(__file__).parent.parent / "diatonic" / "web" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".launcher-search" in css[css.index(".composer textarea"):]
    card_sel = css[css.index(".launcher-card.sel {"):css.index(".launcher-card-icon {")]
    assert "color-mix" in card_sel, "launcher-card.sel should use a softened border with color-mix"


def test_workspace_tabs_horizontal_wheel_scroll():
    """Scrolling while hovering over tabs scrolls horizontally if tabs are overflowing."""
    ws = (Path(__file__).parent.parent / "diatonic" / "web" / "js" / "workspace.js").read_text(encoding="utf-8")
    assert 'tabsEl.addEventListener("wheel"' in ws
    part = ws[ws.index('tabsEl.addEventListener("wheel"'):ws.index("const content = h(")]
    assert "tabsEl.scrollWidth <= tabsEl.clientWidth" in part
    assert "tabsEl.scrollLeft +=" in part


def test_calendar_week_and_whichkey_styles_do_not_collide():
    """WhichKey grid styles must be scoped under .whichkey so they don't break Calendar's .wk-grid."""
    css = (Path(__file__).parent.parent / "diatonic" / "web" / "css" / "app.css").read_text(encoding="utf-8")
    assert ".wk-grid { display: flex;" in css
    assert ".whichkey .wk-grid {" in css
    assert ".whichkey .wk-head {" in css





