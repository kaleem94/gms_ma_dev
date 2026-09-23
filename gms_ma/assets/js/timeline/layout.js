// Pure layout math for the timeline's Motifs view.
//
// Motif hits can overlap (nested families: a 1-bar motif inside a 4-bar one).
// Two ways to turn them into clean, non-overlapping blocks:
//   * "lanes"   -> stack overlapping hits into separate sub-rows
//   * "largest" -> keep only the biggest motifs per region (one band)
// No DOM / state here, so it is trivially unit-testable.

function endOf(hit) {
  return hit.start_tick + Math.max(1, hit.length_ticks);
}

/** Greedy interval colouring: return [[hit, ...], ...] with no lane overlaps. */
export function packLanes(hits) {
  const ordered = hits.slice().sort((a, b) =>
    a.start_tick - b.start_tick || b.length_ticks - a.length_ticks);
  const lanes = [];
  for (const hit of ordered) {
    const start = hit.start_tick;
    let placed = false;
    for (const lane of lanes) {
      if (endOf(lane[lane.length - 1]) <= start) {
        lane.push(hit);
        placed = true;
        break;
      }
    }
    if (!placed) lanes.push([hit]);
  }
  return lanes;
}

/** Keep the longest hits that do not overlap; nested shorter ones are hidden. */
export function largestPerRegion(hits) {
  const ordered = hits.slice().sort((a, b) =>
    b.length_ticks - a.length_ticks || a.start_tick - b.start_tick);
  const kept = [];
  for (const hit of ordered) {
    const s = hit.start_tick, e = endOf(hit);
    const clash = kept.some((k) => s < endOf(k) && k.start_tick < e);
    if (!clash) kept.push(hit);
  }
  kept.sort((a, b) => a.start_tick - b.start_tick);
  return kept;
}

/** mode: "lanes" (default) | "largest". Returns an array of lanes. */
export function layoutMotifs(hits, mode) {
  if (!hits || !hits.length) return [];
  return mode === "largest" ? [largestPerRegion(hits)] : packLanes(hits);
}

// -- harmony / layer grouping ------------------------------------------------
const LAYERS = ["mixed", "melody", "harmony"];

/** Group blocks into {mixed, melody, harmony} by their `layer` field. */
export function groupByLayer(blocks) {
  const out = { mixed: [], melody: [], harmony: [] };
  for (const b of blocks) {
    const key = LAYERS.includes(b.layer) ? b.layer : "mixed";
    out[key].push(b);
  }
  return out;
}

/** Most common non-empty value of `key` among blocks ("" if none). */
function modeOf(blocks, key) {
  const counts = new Map();
  for (const b of blocks) {
    const v = b[key];
    if (v) counts.set(v, (counts.get(v) || 0) + 1);
  }
  let best = "", n = 0;
  for (const [v, c] of counts) if (c > n) { best = v; n = c; }
  return best;
}

/** One-line summary of a lane: hit/family counts + dominant layer/chord/harmony. */
export function summarizeLane(blocks) {
  const families = new Set(blocks.map((b) => b.pattern_id)).size;
  return {
    hits: blocks.length,
    families,
    layer: modeOf(blocks, "layer"),
    chord: modeOf(blocks, "chord"),
    harmony: modeOf(blocks, "harmony"),
  };
}

// -- adaptive time axis ------------------------------------------------------
// The ruler must not crowd when zoomed out nor stretch when zoomed in, so we
// pick a "nice" seconds step whose on-screen width is at least minPx.
const NICE_SECONDS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600];

/** Smallest nice seconds step whose width (seconds / secPerPx) is >= minPx. */
export function timeStep(secPerPx, minPx = 60) {
  const need = Math.max(0, secPerPx) * minPx;
  for (const s of NICE_SECONDS) if (s >= need) return s;
  return Math.ceil(need / 3600) * 3600;
}

/** Label times [0, step, 2*step, ...] up to totalSec (whole seconds only). */
export function timeTicks(totalSec, secPerPx, minPx = 60) {
  const step = timeStep(secPerPx, minPx);
  const out = [];
  for (let s = 0; s <= totalSec; s += step) out.push(s);
  return out;
}
