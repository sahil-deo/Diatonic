"""Builds valid layout trees from spatial positions (left, right, top, bottom, grid, etc.)."""
from __future__ import annotations

import re

POSITION_ALIASES = {
    "left": "left",
    "l": "left",
    "west": "left",
    "right": "right",
    "r": "right",
    "east": "right",
    "top": "top",
    "t": "top",
    "north": "top",
    "bottom": "bottom",
    "b": "bottom",
    "south": "bottom",
    "center": "center",
    "middle": "center",
    "c": "center",
    "top_left": "top_left",
    "top-left": "top_left",
    "tl": "top_left",
    "top_right": "top_right",
    "top-right": "top_right",
    "tr": "top_right",
    "bottom_left": "bottom_left",
    "bottom-left": "bottom_left",
    "bl": "bottom_left",
    "bottom_right": "bottom_right",
    "bottom-right": "bottom_right",
    "br": "bottom_right",
}


def make_stack(app_id: str, tab_id: str | None = None, stack_id: str | None = None) -> dict:
    clean_app = str(app_id or "").strip()
    return {
        "type": "stack",
        "id": stack_id or f"s_{clean_app}",
        "active": 0,
        "tabs": [{"id": tab_id or f"t_{clean_app}", "app": clean_app, "params": {}}]
    }


def make_split(dir_: str, children: list[dict], sizes: list[float] | None = None) -> dict:
    n = len(children)
    if not sizes or len(sizes) != n:
        sizes = [round(1.0 / n, 2)] * n
    return {
        "type": "split",
        "dir": "col" if dir_ == "col" else "row",
        "sizes": sizes,
        "children": children
    }


def build_layout_tree(positions: dict[str, str]) -> dict:
    """Build a complete layout tree from a normalized dictionary of spatial positions.

    Supports:
    - 2 panes: (left, right) or (top, bottom)
    - 3 panes: (left, top_right, bottom_right) or (top_left, bottom_left, right)
               or (top, bottom_left, bottom_right) or (left, center, right)
    - 4 panes: (top_left, top_right, bottom_left, bottom_right)
    """
    clean_pos: dict[str, str] = {}
    for k, v in positions.items():
        if not v:
            continue
        canon = POSITION_ALIASES.get(k.lower().strip())
        if canon:
            clean_pos[canon] = str(v).strip()

    # 4-pane 2x2 grid
    if all(k in clean_pos for k in ("top_left", "top_right", "bottom_left", "bottom_right")):
        left_col = make_split("col", [make_stack(clean_pos["top_left"]), make_stack(clean_pos["bottom_left"])], [0.5, 0.5])
        right_col = make_split("col", [make_stack(clean_pos["top_right"]), make_stack(clean_pos["bottom_right"])], [0.5, 0.5])
        root = make_split("row", [left_col, right_col], [0.5, 0.5])
        return {"v": 1, "root": root}

    # 3-pane: left with split top_right / bottom_right
    if "left" in clean_pos and "top_right" in clean_pos and "bottom_right" in clean_pos:
        right_col = make_split("col", [make_stack(clean_pos["top_right"]), make_stack(clean_pos["bottom_right"])], [0.5, 0.5])
        root = make_split("row", [make_stack(clean_pos["left"]), right_col], [0.5, 0.5])
        return {"v": 1, "root": root}

    # 3-pane: split top_left / bottom_left with right
    if "top_left" in clean_pos and "bottom_left" in clean_pos and "right" in clean_pos:
        left_col = make_split("col", [make_stack(clean_pos["top_left"]), make_stack(clean_pos["bottom_left"])], [0.5, 0.5])
        root = make_split("row", [left_col, make_stack(clean_pos["right"])], [0.5, 0.5])
        return {"v": 1, "root": root}

    # 3-pane: top with split bottom_left / bottom_right
    if "top" in clean_pos and "bottom_left" in clean_pos and "bottom_right" in clean_pos:
        bottom_row = make_split("row", [make_stack(clean_pos["bottom_left"]), make_stack(clean_pos["bottom_right"])], [0.5, 0.5])
        root = make_split("col", [make_stack(clean_pos["top"]), bottom_row], [0.5, 0.5])
        return {"v": 1, "root": root}

    # 3 columns: left, center, right
    if "left" in clean_pos and "center" in clean_pos and "right" in clean_pos:
        root = make_split("row", [
            make_stack(clean_pos["left"]),
            make_stack(clean_pos["center"]),
            make_stack(clean_pos["right"])
        ], [0.33, 0.34, 0.33])
        return {"v": 1, "root": root}

    # 2 panes: left and right (side-by-side)
    if "left" in clean_pos and "right" in clean_pos:
        root = make_split("row", [make_stack(clean_pos["left"]), make_stack(clean_pos["right"])], [0.5, 0.5])
        return {"v": 1, "root": root}

    # 2 panes: top and bottom (stacked)
    if "top" in clean_pos and "bottom" in clean_pos:
        root = make_split("col", [make_stack(clean_pos["top"]), make_stack(clean_pos["bottom"])], [0.5, 0.5])
        return {"v": 1, "root": root}

    # Fallback: if only a list of apps or unexpected keys, chain in row or single
    apps = list(clean_pos.values())
    if len(apps) == 1:
        root = make_stack(apps[0])
    elif len(apps) == 2:
        root = make_split("row", [make_stack(apps[0]), make_stack(apps[1])], [0.5, 0.5])
    elif len(apps) >= 3:
        root = make_split("row", [make_stack(a) for a in apps], [round(1.0 / len(apps), 2)] * len(apps))
    else:
        root = make_stack("chat")

    return {"v": 1, "root": root}
