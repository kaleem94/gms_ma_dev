"""Behavioural test for the pure library pagination module (Node ESM)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CODE = """
import { pageCount, clampPage, pageSlice, rangeLabel, validPageSize, windowRange } from "./gms_ma/assets/js/library/pager.js";
const eq = (a, b, what) => { if (a !== b) throw new Error(what + ": " + JSON.stringify(a) + " != " + JSON.stringify(b)); };
eq(pageCount(0, 50), 1, "count empty");
eq(pageCount(120, 50), 3, "count 120/50");
eq(pageCount(100, 50), 2, "count exact");
eq(pageCount(120, 0), 1, "count All");
eq(clampPage(0, 3), 1, "clamp low");
eq(clampPage(9, 3), 3, "clamp high");
eq(clampPage(2, 3), 2, "clamp mid");
const items = Array.from({ length: 120 }, (_, i) => i);
eq(pageSlice(items, 2, 50).length, 50, "slice len");
eq(pageSlice(items, 2, 50)[0], 50, "slice start");
eq(pageSlice(items, 99, 50)[0], 100, "slice clamps page");
eq(pageSlice(items, 1, 0).length, 120, "slice All");
eq(rangeLabel(2, 50, 120), "51\\u2013100 of 120", "range mid");
eq(rangeLabel(1, 0, 120), "1\\u2013120 of 120", "range All");
eq(rangeLabel(1, 50, 0), "0 of 0", "range empty");
// default page size must never be "All" (0) when there is no stored preference
eq(validPageSize(null), 25, "default page size");
eq(validPageSize(""), 25, "empty -> default");
eq(validPageSize("abc"), 25, "invalid -> default");
eq(validPageSize("50"), 50, "valid 50");
eq(validPageSize("0"), 0, "explicit All");
// sliding window range clamped to [1, pages]
const w1 = windowRange(1, 20, 5); eq(w1.start, 1, "win low start"); eq(w1.end, 6, "win low end");
const w2 = windowRange(10, 20, 5); eq(w2.start, 5, "win mid start"); eq(w2.end, 15, "win mid end");
const w3 = windowRange(20, 20, 5); eq(w3.start, 15, "win high start"); eq(w3.end, 20, "win high end");
const w4 = windowRange(1, 1, 5); eq(w4.start, 1, "win single start"); eq(w4.end, 1, "win single end");
console.log("pager OK");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_pagination_math():
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", CODE],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "pager OK" in proc.stdout
