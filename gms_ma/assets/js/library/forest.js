// Pure building-block containment: given catalog rows for one track, work out
// which shorter loops sit (bar-aligned) inside longer ones.  No DOM/state here,
// so the algorithm is trivially unit-testable and reusable.

let intCache = new Map();

export function patInts(p) {
  if (intCache.has(p.pattern_id)) return intCache.get(p.pattern_id);
  const arr = [];
  if (p.occurrences && p.occurrences.length) {
    for (const o of p.occurrences) arr.push([o.bar_index, o.bar_index + p.length_bars]);
  } else if (typeof p.bar_index === "number") {
    arr.push([p.bar_index, p.bar_index + p.length_bars]);
  }
  intCache.set(p.pattern_id, arr);
  return arr;
}

export function alignedInside(small, big, smallLen) {
  return big.some((biv) => small.some((siv) =>
    biv[0] <= siv[0] && siv[1] <= biv[1] && (siv[0] - biv[0]) % smallLen === 0));
}

export function containmentForest(items) {
  intCache = new Map();
  const childMap = new Map(), parent = new Map();
  const prefer = (a, b) => {
    if (!b) return true;
    const ka = a.kind === "variant" ? 1 : 0, kb = b.kind === "variant" ? 1 : 0;
    if (ka !== kb) return ka < kb;
    if (a.length_bars !== b.length_bars) return a.length_bars < b.length_bars;
    return a.pattern_id < b.pattern_id;
  };
  for (const c of items) {
    const ci = patInts(c);
    if (!ci.length) continue;
    let best = null;
    for (const a of items) {
      if (a.pattern_id === c.pattern_id || a.length_bars <= c.length_bars) continue;
      if (!alignedInside(ci, patInts(a), c.length_bars)) continue;
      if (prefer(a, best)) best = a;
    }
    if (best) {
      parent.set(c.pattern_id, best.pattern_id);
      if (!childMap.has(best.pattern_id)) childMap.set(best.pattern_id, []);
      childMap.get(best.pattern_id).push(c);
    }
  }
  const sorter = (a, b) => (a.kind === "variant" ? 1 : 0) - (b.kind === "variant" ? 1 : 0) ||
                  b.length_bars - a.length_bars || a.bar_index - b.bar_index ||
                  a.pattern_id - b.pattern_id;
  const roots = items.filter((p) => !parent.has(p.pattern_id)).sort(sorter);
  for (const arr of childMap.values()) arr.sort(sorter);
  return { childMap, roots };
}
