"""User and agent named workspaces: saved layout trees stored in workspaces.json.

Stored in workspaces.json in the Diatonic home folder:
[
  {
    "id": "dev",
    "name": "Dev",
    "description": "Side-by-side chat and files",
    "tree": {
      "v": 1,
      "root": {
        "type": "split",
        "dir": "row",
        "sizes": [0.6, 0.4],
        "children": [
          {"type": "stack", "id": "s_1", "active": 0, "tabs": [{"id": "t_1", "app": "chat", "params": {}}]},
          {"type": "stack", "id": "s_2", "active": 0, "tabs": [{"id": "t_2", "app": "files", "params": {}}]}
        ]
      }
    },
    "created_at": 1700000000.0
  }
]
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path

from .config import home

MAX_WORKSPACES = 50
MAX_TEXT = 120
MAX_DESC = 300
MAX_DEPTH = 10
ID_RE = re.compile(r"^[a-zA-Z0-9_\-:]+$")


class WorkspaceError(ValueError):
    pass


def path() -> Path:
    return home() / "workspaces.json"


def _clean_node(node: dict, depth: int = 0) -> dict:
    if depth > MAX_DEPTH:
        raise WorkspaceError(f"Layout tree exceeds maximum depth of {MAX_DEPTH}.")
    if not isinstance(node, dict):
        raise WorkspaceError("Layout tree nodes must be objects.")
    ntype = node.get("type")
    if ntype == "stack":
        tabs = node.get("tabs") or []
        if not isinstance(tabs, list):
            raise WorkspaceError("Stack tabs must be a list.")
        clean_tabs = []
        for t in tabs:
            if not isinstance(t, dict):
                continue
            app = str(t.get("app") or "").strip()
            if not app:
                continue
            clean_tabs.append({
                "id": str(t.get("id") or f"t_{app}"),
                "app": app,
                "params": t.get("params") if isinstance(t.get("params"), dict) else {},
            })
        active = node.get("active")
        active_idx = int(active) if isinstance(active, (int, float)) and 0 <= active < max(1, len(clean_tabs)) else 0
        return {
            "type": "stack",
            "id": str(node.get("id") or "s_main"),
            "active": active_idx,
            "tabs": clean_tabs,
        }
    elif ntype == "split":
        dir_ = "col" if node.get("dir") == "col" else "row"
        raw_children = node.get("children")
        if not isinstance(raw_children, list) or len(raw_children) < 1:
            raise WorkspaceError("Split nodes must have at least one child.")
        children = [_clean_node(c, depth + 1) for c in raw_children]
        raw_sizes = node.get("sizes")
        if isinstance(raw_sizes, list) and len(raw_sizes) == len(children) and all(isinstance(s, (int, float)) for s in raw_sizes):
            total = sum(raw_sizes) or 1.0
            sizes = [round(s / total, 3) for s in raw_sizes]
        else:
            eq = round(1.0 / len(children), 3)
            sizes = [eq] * len(children)
        return {
            "type": "split",
            "dir": dir_,
            "sizes": sizes,
            "children": children,
        }
    else:
        raise WorkspaceError(f"Unknown layout node type '{ntype}'.")


def clean_tree(raw_tree: dict) -> dict:
    if not isinstance(raw_tree, dict):
        raise WorkspaceError("Workspace tree must be an object.")
    root = raw_tree.get("root")
    if not isinstance(root, dict):
        raise WorkspaceError("Workspace tree must have a root node object.")
    return {
        "v": int(raw_tree.get("v") or 1),
        "root": _clean_node(root, 0),
    }


def clean_workspace(raw) -> dict:
    if not isinstance(raw, dict):
        raise WorkspaceError("A workspace must be an object.")
    wid = raw.get("id")
    if not isinstance(wid, str) or not wid.strip() or len(wid) > MAX_TEXT or not ID_RE.match(wid.strip()):
        raise WorkspaceError("Workspace id must be letters, numbers, hyphens or underscores (at most 120 chars).")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > MAX_TEXT:
        raise WorkspaceError("Workspace name must be text of at most 120 characters.")
    desc = raw.get("description") or ""
    if not isinstance(desc, str) or len(desc) > MAX_DESC:
        raise WorkspaceError(f"Workspace description must be text of at most {MAX_DESC} characters.")

    tree = clean_tree(raw.get("tree"))
    created_at = raw.get("created_at")
    ts = float(created_at) if isinstance(created_at, (int, float)) else time.time()

    return {
        "id": wid.strip(),
        "name": name.strip(),
        "description": desc.strip(),
        "tree": tree,
        "created_at": ts,
    }


def clean_workspaces(raw) -> list[dict]:
    if not isinstance(raw, list):
        raise WorkspaceError("Workspaces must be a list.")
    if len(raw) > MAX_WORKSPACES:
        raise WorkspaceError(f"At most {MAX_WORKSPACES} workspaces allowed.")
    seen_ids = set()
    out = []
    for item in raw:
        cleaned = clean_workspace(item)
        if cleaned["id"] in seen_ids:
            raise WorkspaceError(f"Duplicate workspace id '{cleaned['id']}'.")
        seen_ids.add(cleaned["id"])
        out.append(cleaned)
    return out


def load() -> list[dict]:
    p = path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return clean_workspaces(data)
    except Exception:
        return []


def save(data) -> list[dict]:
    cleaned = clean_workspaces(data)
    p = path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", dir=p.parent, delete=False, encoding="utf-8") as tf:
        json.dump(cleaned, tf, indent=2)
        tf.write("\n")
        tmp_name = tf.name
    os.replace(tmp_name, p)
    return cleaned
