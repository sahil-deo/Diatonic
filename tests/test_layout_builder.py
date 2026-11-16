"""Unit tests for spatial layout tree builder."""
from diatonic.apps.ui.layout_builder import build_layout_tree, make_stack, make_split


def test_two_pane_side_by_side():
    tree = build_layout_tree({"left": "gmail", "right": "gcal"})
    assert tree["v"] == 1
    root = tree["root"]
    assert root["type"] == "split"
    assert root["dir"] == "row"
    assert len(root["children"]) == 2
    assert root["children"][0]["tabs"][0]["app"] == "gmail"
    assert root["children"][1]["tabs"][0]["app"] == "gcal"


def test_two_pane_stacked():
    tree = build_layout_tree({"top": "notes", "bottom": "todos"})
    assert tree["v"] == 1
    root = tree["root"]
    assert root["type"] == "split"
    assert root["dir"] == "col"
    assert len(root["children"]) == 2
    assert root["children"][0]["tabs"][0]["app"] == "notes"
    assert root["children"][1]["tabs"][0]["app"] == "todos"


def test_three_pane_left_with_two_right():
    tree = build_layout_tree({"left": "gmail", "top-right": "gcal", "bottom-right": "chat"})
    assert tree["v"] == 1
    root = tree["root"]
    assert root["type"] == "split"
    assert root["dir"] == "row"
    assert root["children"][0]["tabs"][0]["app"] == "gmail"
    right_split = root["children"][1]
    assert right_split["type"] == "split"
    assert right_split["dir"] == "col"
    assert right_split["children"][0]["tabs"][0]["app"] == "gcal"
    assert right_split["children"][1]["tabs"][0]["app"] == "chat"


def test_three_columns():
    tree = build_layout_tree({"left": "chat", "center": "files", "right": "shell"})
    assert tree["v"] == 1
    root = tree["root"]
    assert root["type"] == "split"
    assert root["dir"] == "row"
    assert len(root["children"]) == 3
    assert root["children"][0]["tabs"][0]["app"] == "chat"
    assert root["children"][1]["tabs"][0]["app"] == "files"
    assert root["children"][2]["tabs"][0]["app"] == "shell"


def test_four_pane_grid():
    tree = build_layout_tree({
        "top_left": "gmail",
        "top_right": "gcal",
        "bottom_left": "notes",
        "bottom_right": "todos"
    })
    assert tree["v"] == 1
    root = tree["root"]
    assert root["type"] == "split"
    assert root["dir"] == "row"
    assert len(root["children"]) == 2
    left_col = root["children"][0]
    right_col = root["children"][1]
    assert left_col["dir"] == "col"
    assert right_col["dir"] == "col"
    assert left_col["children"][0]["tabs"][0]["app"] == "gmail"
    assert left_col["children"][1]["tabs"][0]["app"] == "notes"
    assert right_col["children"][0]["tabs"][0]["app"] == "gcal"
    assert right_col["children"][1]["tabs"][0]["app"] == "todos"
