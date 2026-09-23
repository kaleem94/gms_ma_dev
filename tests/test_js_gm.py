"""Behavioural test for the render-only General-MIDI name module (Node ESM)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CODE = """
import { GM_NAMES, gmLabel, meaningfulName, trackDisplayName } from "./gms_ma/assets/js/common/gm.js";
const eq = (a, b, what) => { if (a !== b) throw new Error(what + ": " + JSON.stringify(a) + " != " + JSON.stringify(b)); };
eq(GM_NAMES.length, 128, "table size");
eq(gmLabel(0, false), "Acoustic Grand Piano", "piano");
eq(gmLabel(null, false), "Acoustic Grand Piano", "default patch");
eq(gmLabel(0, true), "Standard Drum Kit", "drum kit");
eq(gmLabel(81, false), "Lead 2 (sawtooth)", "lead");
eq(meaningfulName("Instrument 1"), "", "generic track name");
eq(meaningfulName("Track 3"), "", "generic track name 2");
eq(meaningfulName("Bass Line"), "Bass Line", "real name");
eq(trackDisplayName("Instrument 1", 0, false), "Acoustic Grand Piano", "fallback");
eq(trackDisplayName("Bass Line", 0, false), "Bass Line", "keep real name");
console.log("gm OK");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_gm_names():
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", CODE],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "gm OK" in proc.stdout
