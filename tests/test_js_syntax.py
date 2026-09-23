"""Syntax gate for the browser ES modules (skipped when node is unavailable)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ASSETS = Path(__file__).resolve().parent.parent / "gms_ma" / "assets" / "js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_modules_parse(tmp_path):
    modules = sorted(ASSETS.rglob("*.js"))
    assert modules, "expected JS modules under assets/js"
    for js in modules:
        # node --check treats a bare .js as CommonJS; copy to .mjs for module parse
        target = tmp_path / (js.stem + ".mjs")
        target.write_text(js.read_text(encoding="utf-8"), encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(target)],
                              capture_output=True, text=True)
        assert proc.returncode == 0, f"{js}: {proc.stderr}"
