"""Behavioural tests for the pure Motifs-view layout module (Node ESM)."""
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CODE = """
import { packLanes, largestPerRegion, layoutMotifs, groupByLayer, summarizeLane, timeStep, timeTicks } from "./gms_ma/assets/js/timeline/layout.js";
const eq = (a, b, what) => { if (JSON.stringify(a) !== JSON.stringify(b)) throw new Error(what + ": " + JSON.stringify(a) + " != " + JSON.stringify(b)); };
const h = (s, l, id) => ({ start_tick: s, length_ticks: l, pattern_id: id });

// three overlapping hits -> three lanes
const hits = [h(0, 100, 1), h(50, 100, 2), h(60, 40, 3)];
const lanes = packLanes(hits);
eq(lanes.length, 3, "packLanes lanes");
for (const lane of lanes) {
  for (let i = 1; i < lane.length; i++) {
    const prev = lane[i-1], cur = lane[i];
    if (prev.start_tick + prev.length_ticks > cur.start_tick) throw new Error("lane overlap");
  }
}
// non-overlapping hits share one lane
eq(packLanes([h(0, 10, 1), h(20, 10, 2), h(40, 10, 3)]).length, 1, "single lane");
// largestPerRegion keeps the biggest and drops nested
const largest = largestPerRegion([h(0, 400, 1), h(10, 40, 2), h(200, 20, 3)]);
eq(largest.map(x => x.pattern_id), [1], "largest keeps biggest");
eq(layoutMotifs([], "lanes"), [], "empty");
eq(layoutMotifs(hits, "largest").length, 1, "layout largest one lane");
eq(layoutMotifs(hits, "lanes").length, 3, "layout lanes");

// harmony/layer grouping + lane summary
const gb = groupByLayer([
  {pattern_id: 1, layer: "melody", chord: ""},
  {pattern_id: 2, layer: "harmony", chord: "C"},
  {pattern_id: 3, layer: "harmony", chord: "Am"},
  {pattern_id: 4, chord: "G"},              // no layer -> mixed
]);
eq(gb.melody.length, 1, "one melody");
eq(gb.harmony.length, 2, "two harmony");
eq(gb.mixed.length, 1, "one mixed");
const sum = summarizeLane([
  {pattern_id: 1, layer: "harmony", chord: "C", harmony: "C - G"},
  {pattern_id: 1, layer: "harmony", chord: "C", harmony: "C - G"},
  {pattern_id: 2, layer: "harmony", chord: "Am", harmony: "Am - F"},
]);
eq(sum.hits, 3, "lane hits");
eq(sum.families, 2, "lane families");
eq(sum.layer, "harmony", "dominant layer");
eq(sum.chord, "C", "dominant chord");
eq(sum.harmony, "C - G", "dominant harmony");
eq(summarizeLane([]).hits, 0, "empty lane");

// adaptive seconds axis: pick a nice step >= minPx wide
eq(timeStep(0.01, 60), 1, "tiny step -> 1s");
eq(timeStep(0.2, 60), 15, "0.2s/px -> 15s");
eq(timeStep(1, 60), 60, "1s/px -> 60s");
if (!(timeStep(2, 60) >= timeStep(0.5, 60))) throw new Error("step not monotonic");
eq(timeTicks(100, 0.5, 60), [0, 30, 60, 90], "timeTicks 30s");
eq(timeTicks(10, 0.001, 60), [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], "timeTicks 1s");
const gap = timeTicks(1000, 0.25, 60);   // need 15 -> step 15
for (let i = 1; i < gap.length; i++) {
  if (gap[i] - gap[i-1] < 60 * 0.25) throw new Error("tick gap below minimum");
}
console.log("layout OK");
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_motif_layout():
    proc = subprocess.run(
        ["node", "--input-type=module", "-e", CODE],
        cwd=str(ROOT), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "layout OK" in proc.stdout
