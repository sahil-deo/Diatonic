"""One-off for v3 Phase 5: save the API apps and connected (tool) apps to exports/api_apps.json, then optionally delete them.

    python scripts/export_api_apps.py --home ~/.diatonic            export only, changes nothing
    python scripts/export_api_apps.py --home ~/.diatonic --delete   export, then remove them from mods.json

Run it with the server stopped. It reads mods.json directly, so it does not need the code that Phase 5 removed. A mod of kind
"app" or "tools" is ignored on load now, so deleting is optional: it only tidies the file. API keys live in the keychain and are not exported.
The export is never overwritten: a second run stops and says so.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

KINDS = ("app", "tools")


def export_api_apps(home: Path, delete: bool = False) -> dict:
    home = Path(home)
    out = home / "exports" / "api_apps.json"
    if out.exists():
        raise SystemExit(f"{out} already exists. Move it away if you really want to export again.")
    mods_path = home / "mods.json"
    mods = json.loads(mods_path.read_text()) if mods_path.exists() else {"mods": {}, "changes": []}
    found = {n: m for n, m in mods.get("mods", {}).items() if m.get("kind") in KINDS}
    result = {"exported_at": time.time(),
              "apps": {n: {"kind": m["kind"], "title": m.get("title", n), "manifest": json.loads(m["code"])} for n, m in found.items()}}
    if found:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1))
        if json.loads(out.read_text()) != result:   # read it back before anything is deleted
            raise SystemExit("The export did not read back the same. Nothing was deleted.")
    if delete and found:
        mods["mods"] = {n: m for n, m in mods["mods"].items() if n not in found}
        mods_path.write_text(json.dumps(mods, indent=1))
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--home", required=True, help="the Diatonic data folder (there is no default, on purpose)")
    p.add_argument("--delete", action="store_true", help="after exporting, remove the apps from mods.json")
    a = p.parse_args()
    r = export_api_apps(Path(a.home).expanduser(), a.delete)
    print(f"found {len(r['apps'])} API or connected app(s)" + (f"; exported to {Path(a.home).expanduser() / 'exports' / 'api_apps.json'}" if r["apps"] else "; nothing to export")
          + ("; removed them from mods.json" if a.delete and r["apps"] else ""))


if __name__ == "__main__":
    sys.exit(main())
