"""The UI kit as catalog entries (`component`), generated from web/js/ui/components.json, the file the gallery and the tests read too.

Nothing is written twice: the props table, the docs and the example live in that one file next to the code that implements them.
"""
from __future__ import annotations

import json
from pathlib import Path

from .catalog import Entry

FILE = Path(__file__).parent / "web" / "js" / "ui" / "components.json"


def declarations() -> dict:
    return json.loads(FILE.read_text(encoding="utf-8"))


def entries() -> list[Entry]:
    out = []
    for name, d in declarations().items():
        args = [{"name": p["name"], "type": p["type"], "doc": p["doc"], "required": bool(p.get("required"))} for p in d["props"]]
        out.append(Entry("component", f"ctx.ui.{name}", d["doc"] + " Takes one object of props and returns an element: append it to el.", args,
                         f"el.append(ctx.ui.{name}({json.dumps(d['example'], ensure_ascii=False)}));", effects=["display"], domain="ui"))
    return out
