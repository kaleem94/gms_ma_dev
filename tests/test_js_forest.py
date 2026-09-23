"""Behavioural test for the pure building-block forest module (Node ESM)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CODE = """
import { containmentForest } from "./gms_ma/assets/js/library/forest.js";
const parent = {pattern_id: 1, kind: "cover", length_bars: 4, bar_index: 0,
                occurrences: [{bar_index: 0}, {bar_index: 4}]};
const child = {pattern_id: 2, kind: "cover", length_bars: 1, bar_index: 0,
               occurrences: [{bar_index: 0}, {bar_index: 1}, {bar_index: 2}]};
const outside = {pattern_id: 3, kind: "cover", length_bars: 1, bar_index: 9,
                 occurrences: [{bar_index: 9}]};
const { roots, childMap } = containmentForest([parent, child, outside]);
const ids = roots.map(p => p.pattern_id).sort();
if (JSON.stringify(ids) !== "[1,3]") throw new Error("roots=" + JSON.stringify(ids));
const kids = (childMap.get(1) || []).map(p => p.pattern_id);
if (JSON.stringify(kids) !== "[2]") throw new Error("children=" + JSON.stringify(kids));
console.log("forest OK");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_containment_forest():
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", CODE],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "forest OK" in proc.stdout
