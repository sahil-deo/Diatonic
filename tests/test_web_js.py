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
