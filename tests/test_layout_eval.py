"""Eval checking realistic layout requests against the resulting layout tree."""
import pytest
from diatonic.apps.ui.layout_builder import build_layout_tree


CASES = [
    (
        "mail left, calendar right",
        {"left": "gmail", "right": "gcal"},
        lambda tree: (
            tree["root"]["type"] == "split"
            and tree["root"]["dir"] == "row"
            and tree["root"]["children"][0]["tabs"][0]["app"] == "gmail"
            and tree["root"]["children"][1]["tabs"][0]["app"] == "gcal"
        )
    ),
    (
        "mail left, calendar top-right, chat bottom-right",
        {"left": "gmail", "top-right": "gcal", "bottom-right": "chat"},
        lambda tree: (
            tree["root"]["type"] == "split"
            and tree["root"]["dir"] == "row"
            and tree["root"]["children"][0]["tabs"][0]["app"] == "gmail"
            and tree["root"]["children"][1]["type"] == "split"
            and tree["root"]["children"][1]["dir"] == "col"
            and tree["root"]["children"][1]["children"][0]["tabs"][0]["app"] == "gcal"
            and tree["root"]["children"][1]["children"][1]["tabs"][0]["app"] == "chat"
        )
    ),
    (
        "three columns with chat, files, and terminal",
        {"left": "chat", "center": "files", "right": "shell"},
        lambda tree: (
            tree["root"]["type"] == "split"
            and tree["root"]["dir"] == "row"
            and len(tree["root"]["children"]) == 3
            and tree["root"]["children"][0]["tabs"][0]["app"] == "chat"
            and tree["root"]["children"][1]["tabs"][0]["app"] == "files"
            and tree["root"]["children"][2]["tabs"][0]["app"] == "shell"
        )
    ),
    (
        "grid with mail, calendar, notes, todos",
        {"top_left": "gmail", "top_right": "gcal", "bottom_left": "notes", "bottom_right": "todos"},
        lambda tree: (
            tree["root"]["type"] == "split"
            and tree["root"]["dir"] == "row"
            and tree["root"]["children"][0]["dir"] == "col"
            and tree["root"]["children"][1]["dir"] == "col"
            and tree["root"]["children"][0]["children"][0]["tabs"][0]["app"] == "gmail"
            and tree["root"]["children"][0]["children"][1]["tabs"][0]["app"] == "notes"
            and tree["root"]["children"][1]["children"][0]["tabs"][0]["app"] == "gcal"
            and tree["root"]["children"][1]["children"][1]["tabs"][0]["app"] == "todos"
        )
    ),
]


@pytest.mark.parametrize("desc,positions,check", CASES)
def test_layout_eval_requests(desc, positions, check):
    tree = build_layout_tree(positions)
    assert check(tree), f"Layout request failed verification: {desc}"
