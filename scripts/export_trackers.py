"""One-off for v3 Phase 0: save the tracker ("data app") specs and rows to exports/trackers.json, then optionally delete them.

    python scripts/export_trackers.py --home ~/.diatonic            export only, changes nothing
    python scripts/export_trackers.py --home ~/.diatonic --delete   export, then remove the trackers and every row

Run it with the server stopped. It reads mods.json and the app_items table directly, so it does not need the code that Phase 0 removes.
The export is never overwritten: a second run stops and says so.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path


def export_trackers(home: Path, delete: bool = False) -> dict:
    home = Path(home)
    out = home / "exports" / "trackers.json"
    if out.exists():
        raise SystemExit(f"{out} already exists. Move it away if you really want to export again.")
    mods_path, db_path = home / "mods.json", home / "diatonic.db"
    mods = json.loads(mods_path.read_text()) if mods_path.exists() else {"mods": {}, "changes": []}
    apps = {n: m for n, m in mods.get("mods", {}).items() if m.get("kind") == "data"}
    rows: list[dict] = []
    conn = None
    if db_path.exists():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_items'").fetchone():
            rows = [{**dict(r), "data": json.loads(r["data"])} for r in conn.execute("SELECT * FROM app_items ORDER BY app, pos, id")]
        else:
            conn.close()
            conn = None
    result = {"exported_at": time.time(),
              "apps": {n: {"title": m.get("title", n), "manifest": json.loads(m["code"]),
                           "items": [r for r in rows if r["app"] == n]} for n, m in apps.items()},
              "orphans": [r for r in rows if r["app"] not in apps]}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    if json.loads(out.read_text()) != result:   # read it back before anything is deleted
        raise SystemExit("The export did not read back the same. Nothing was deleted.")
    if delete:
        if conn is not None:
            conn.execute("DELETE FROM app_items")
            conn.commit()
        if apps:
            mods["mods"] = {n: m for n, m in mods["mods"].items() if n not in apps}
            mods_path.write_text(json.dumps(mods, indent=1))
    if conn is not None:
        conn.close()
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--home", required=True, help="the Diatonic data folder (there is no default, on purpose)")
    p.add_argument("--delete", action="store_true", help="after exporting, delete the trackers and all their rows")
    a = p.parse_args()
    r = export_trackers(Path(a.home).expanduser(), a.delete)
    n = sum(len(x["items"]) for x in r["apps"].values())
    print(f"exported {len(r['apps'])} tracker(s) with {n} row(s) and {len(r['orphans'])} orphan row(s) to {Path(a.home).expanduser() / 'exports' / 'trackers.json'}"
          + ("; deleted them" if a.delete else "; nothing deleted"))


if __name__ == "__main__":
    sys.exit(main())
