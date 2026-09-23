"""Loop / pattern detection and cover segmentation.

Strategy seam: every detector exposes the same interface and returns a
:class:`SegResult`; swapping heuristics later does not touch the rest of the
pipeline.

Detection model
---------------
* Quantize each instrument's onsets onto an **adaptive grid**: the coarsest
  regular beat-division that still lines up >= ``grid_cov`` of the onsets
  (default 0.9).  Outlier bars whose notes need a finer subdivision are
  analysed at their own local unit, so rare 32nds/triplets don't force a
  tiny global grid (the "limited scope, appended" behaviour).
* Two **lenses**: ``pitch`` (a bar equals the pitch-sets attacked in each grid
  cell) and ``rhythm`` (only the cell positions/multiplicity of onsets).
* Two engines on the resulting per-bar sequence:
    - ``period``  -> smallest bar-period that tiles from the first event.
    - ``repeat``  -> the largest exact repeating block anywhere
      (longest-first, then occurrences placed; leftover bars are phrase-split).
  ``hybrid`` tries period first, then repeat, then falls back to gap-splitting.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from .bars import BarMap
from .model import Instrument, Song


@dataclass
class PatternContent:
    """Serialisable musical content of one window of one instrument."""

    track_index: int
    channel: int
    length_ticks: int
    notes: list  # (start_delta, duration, pitch, velocity)
    pitch_bends: list  # (tick_delta, value) with static runs removed
    length_bars: int = 1

    def content_id(self) -> str:
        h = hashlib.sha1()
        h.update(f"{self.length_ticks}\x00".encode())
        for n in self.notes:
            h.update(f"{n[0]},{n[1]},{n[2]},{n[3]}\x00".encode())
        for b in self.pitch_bends:
            h.update(f"{b[0]},{b[1]}\x00".encode())
        return h.hexdigest()[:16]


@dataclass
class CoverSegment:
    """An atomic, non-overlapping cover chunk used for reconstruction."""

    bar_index: int  # song bar where the segment starts
    start_tick: int
    length_ticks: int
    length_bars: int
    content: PatternContent
    confidence: float = 1.0


@dataclass
class LoopVariant:
    """An alternative (usually longer) loop derived from a cover segment."""

    bar_index: int
    start_tick: int
    length_ticks: int
    length_bars: int
    content: PatternContent
    confidence: float = 1.0
    kind: str = "variant"


@dataclass
class SegResult:
    instrument: Instrument
    period_bars: int | None = None  # detected fundamental period, if repetitive
    engine: str = "gap"             # 'period' | 'repeat' | 'gap'
    lens: str = "pitch"
    cover: list[CoverSegment] = field(default_factory=list)
    variants: list[LoopVariant] = field(default_factory=list)
    notes_covered: int = 0


@dataclass
class AnalysisConfig:
    engine: str = "hybrid"            # 'period' | 'repeat' | 'hybrid'
    lens: str = "pitch"              # 'pitch' | 'rhythm'
    min_period_conf: float = 0.55    # min self-similarity to trust a period
    max_period_bars: int = 64
    variant_multiples: tuple = (1, 2, 4, 8)  # ladder relative to the period
    grid_cov: float = 0.9            # onsets that must line up on the grid
    grid_cap: int = 32               # finest subdivision of a beat to consider
    gap_beats: float = 4.0           # min silent run (in quarter-note beats)
                                     # that counts as a phrase gap for
                                     # non-repeating material
    loop_gap_bars: float = 1.0       # a silence of >= this many bars splits a
                                     # loop window into silence-free block
                                     # loops (0 disables splitting/trimming)
    stems: bool = True               # also export piano + clap rhythm skeletons
    stem_note: int = 60              # C4 for the piano skeleton
    stem_drum: int = 39              # GM Hand Clap for the clap skeleton
    write_files: bool = True         # also save .mid to out/ (DB assets always stored)
    motifs: bool = True              # also mine + store the grid-free motif catalog
    motif_max_families: int = 80     # cap motif families stored per channel
    harmony_split: str = "top"       # harmony/melody separation: off | top | onset
    grid_resolution: int = 4         # kept for callers/tests (legacy 16th)
    fallback_grid_bars: int = 4      # kept for callers/tests

    def variant_lengths(self, period_bars: int, max_bars: int) -> list[int]:
        out: list[int] = []
        for m in self.variant_multiples:
            v = period_bars * m
            if v <= max_bars and v not in out:
                out.append(v)
        return out


# --------------------------------------------------------------------------
# content extraction helpers
# --------------------------------------------------------------------------
def _filter_static_bends(bends) -> list:
    """Drop bend values that don't change pitch (identical consecutive values)."""
    out = []
    last: int | None = None
    for b in sorted(bends, key=lambda b: b.tick):
        if last is None or b.value != last:
            out.append(b)
            last = b.value
    return out


def extract_content(
    instrument: Instrument,
    start_tick: int,
    end_tick: int,
    window_start_tick: int,
    length_bars: int = 1,
) -> PatternContent:
    """Notes whose attack lies in [start_tick, end_tick), relative to window."""
    notes = [
        (n.start - window_start_tick, n.duration, n.pitch, n.velocity)
        for n in instrument.notes
        if start_tick <= n.start < end_tick
    ]
    raw_bends = [b for b in instrument.pitch_bends if start_tick <= b.tick < end_tick]
    bends = [(b.tick - window_start_tick, b.value) for b in _filter_static_bends(raw_bends)]
    return PatternContent(
        track_index=instrument.track_index,
        channel=instrument.channel,
        length_ticks=end_tick - start_tick,
        notes=notes,
        pitch_bends=bends,
        length_bars=length_bars,
    )


def _onset_bar_span(bm: BarMap, instrument: Instrument) -> tuple[int, int] | None:
    if not instrument.notes:
        return None
    first = min(n.start for n in instrument.notes)
    last = max(n.start for n in instrument.notes)
    return bm.tick_to_bar(first), bm.tick_to_bar(last)


# --------------------------------------------------------------------------
# adaptive grid + two-lens bar sequence
# --------------------------------------------------------------------------
def _grid_units(ppq: int, cap: int) -> list[int]:
    """Beat-subdivision cell sizes in ticks, coarsest first (never below cap)."""
    units = {round(ppq / d) for d in range(1, max(1, cap) + 1)}
    units = {u for u in units if u >= 1}
    return sorted(units, reverse=True) or [1]


def _bar_onsets(bm: BarMap, instrument: Instrument, b: int) -> list[tuple[int, int]]:
    """(offset, pitch) pairs of note attacks inside bar ``b``."""
    start = bm.bar_start_tick(b)
    end = start + bm.bar_length_ticks(b)
    return sorted((n.start - start, n.pitch) for n in instrument.notes
                  if start <= n.start < end)


def choose_grid_ticks(bm: BarMap, instrument: Instrument, cfg: AnalysisConfig) -> int:
    """Coarsest grid unit whose lines hold >= grid_cov of the onsets."""
    span = _onset_bar_span(bm, instrument)
    units = _grid_units(bm.song.ticks_per_beat, cfg.grid_cap)
    if span is None:
        return units[0]
    b0, bl = span
    all_onsets: list[tuple[int, int]] = []
    for b in range(b0, bl + 1):
        all_onsets.extend(_bar_onsets(bm, instrument, b))
    total = len(all_onsets)
    if total == 0:
        return units[0]
    for u in units:
        fit = sum(1 for (off, _p) in all_onsets if off % u == 0)
        if fit / total >= cfg.grid_cov:
            return u
    return units[-1]  # finest fallback


def _bar_key(ons: list[tuple[int, int]], bar_ticks: int, u: int,
             lens: str) -> tuple | None:
    """Immutable per-bar identity under the given lens."""
    if not ons:
        return None
    cells = max(1, round(bar_ticks / u))
    if lens == "pitch":
        d: dict[int, list[int]] = {}
        for off, pitch in ons:
            idx = min(cells - 1, int(round(off / u)))
            d.setdefault(idx, []).append(pitch)
        return tuple(sorted((i, tuple(sorted(set(ps)))) for i, ps in d.items()))
    # rhythm lens: only *where* (and how many) onsets fall
    idxs = sorted(min(cells - 1, int(round(off / u))) for off, _ in ons)
    return tuple(idxs)


def _bar_ids(bm: BarMap, instrument: Instrument, cfg: AnalysisConfig,
             b0: int, bl: int, lens: str, unique_empty: bool = False) -> list[int]:
    """Per-bar ids over [b0..bl].

    Each bar may use a slightly finer local grid when its own onsets require
    it (the "smaller timing, limited scope" refinement); empty bars are id 0
    unless ``unique_empty`` (used by the repeat engine so rests don't masquerade
    as musical repeats).
    """
    units = _grid_units(bm.song.ticks_per_beat, cfg.grid_cap)
    ids: list[int] = []
    table: dict[tuple | int, int] = {}
    for b in range(b0, bl + 1):
        ons = _bar_onsets(bm, instrument, b)
        if not ons:
            if unique_empty:
                ids.append(-(b + 1))          # unique "empty" marker
            else:
                ids.append(0)
            continue
        # local grid: coarsest unit that divides every offset in this bar
        bar_ticks = bm.bar_length_ticks(b)
        u = next((c for c in units if all(off % c == 0 for off, _ in ons)), units[-1])
        key = _bar_key(ons, bar_ticks, u, lens)
        if key not in table:
            table[key] = len(table) + 1
        ids.append(table[key])
    return ids


# --------------------------------------------------------------------------
# engines
# --------------------------------------------------------------------------
def tile_period(ids: list[int], cfg: AnalysisConfig) -> int | None:
    """Smallest bar-period p whose bars repeat with score >= min_period_conf."""
    n = len(ids)
    max_p = min(cfg.max_period_bars, n // 2)
    if max_p < 1:
        return None

    def score(p: int) -> float:
        matched = total = 0
        for i in range(n - p):
            a, b = ids[i], ids[i + p]
            if not a and not b:      # silence vs silence carries no signal
                continue
            total += 1
            if a == b:
                matched += 1
        return matched / total if total else 0.0

    for p in range(1, max_p + 1):
        if score(p) >= cfg.min_period_conf:
            return p
    return None


def _lrs(ids: list[int]) -> tuple[int, int, int] | None:
    """Longest repeated (possibly overlapping) block: (L, i, j) with i < j."""
    n = len(ids)
    if n < 3:
        return None
    # fast negative: nothing repeats even at length 1
    if len(set(ids)) == n and 0 not in ids:
        return None
    MOD = (1 << 61) - 1
    B = 911382323 % MOD
    pref = [0] * (n + 1)
    pw = [1] * (n + 1)
    for i, v in enumerate(ids):
        pref[i + 1] = (pref[i] * B + (v + 7)) % MOD
        pw[i + 1] = (pw[i] * B) % MOD

    def wh(l: int, r: int) -> int:
        return (pref[r] - pref[l] * pw[r - l]) % MOD

    def good(L: int) -> bool:
        seen: dict[int, int] = {}
        for i in range(0, n - L + 1):
            hv = wh(i, i + L)
            j = seen.get(hv)
            if j is not None and ids[j:j + L] == ids[i:i + L]:
                return True
            seen[hv] = i
        return False

    lo, hi = 1, n
    while lo <= hi:
        mid = (lo + hi) // 2
        if good(mid):
            lo = mid + 1
        else:
            hi = mid - 1
    L = hi
    if L < 2:
        return None
    seen: dict[int, int] = {}
    for i in range(0, n - L + 1):
        hv = wh(i, i + L)
        j = seen.get(hv)
        if j is not None and ids[j:j + L] == ids[i:i + L]:
            return L, j, i
        seen[hv] = i
    return None


def _occurrences(ids: list[int], start: int, length: int) -> list[int]:
    pat = ids[start:start + length]
    return [i for i in range(len(ids) - length + 1) if ids[i:i + length] == pat]


def repeat_motif(ids: list[int]) -> tuple[int, int, list[tuple[int, int]]] | None:
    """(L, anchor, non-overlapping windows [(start,end),...]) of the largest
    repeating block found anywhere, or None."""
    r = _lrs(ids)
    if r is None:
        return None
    L, anchor, _j = r
    occ = _occurrences(ids, anchor, L)
    chosen: list[tuple[int, int]] = []
    for s in occ:
        e = s + L
        if not chosen or s >= chosen[-1][1]:
            chosen.append((s, e))
    if len(chosen) < 2:
        return None
    return L, anchor, chosen


# --------------------------------------------------------------------------
# phrase-splitting (gap) helper
# --------------------------------------------------------------------------
def _gap_bounds(bm: BarMap, instrument: Instrument, first_bar: int, last_bar: int,
                cfg: AnalysisConfig) -> list[int]:
    """Bar-aligned phrase boundaries for non-repeating material."""
    ppq = max(1, bm.song.ticks_per_beat)
    active: list[bool] = []
    for b in range(first_bar, last_bar + 1):
        start = bm.bar_start_tick(b)
        end = start + bm.bar_length_ticks(b)
        sounding = any(n.start < end and n.end > start for n in instrument.notes)
        active.append(sounding)

    bounds = [first_bar]
    i = 0
    n = len(active)
    while i < n:
        if not active[i]:
            j = i
            while j < n and not active[j]:
                j += 1
            run_beats = (sum(bm.bar_length_ticks(b)
                             for b in range(first_bar + i, first_bar + j)) / ppq)
            if run_beats >= cfg.gap_beats:
                mid = first_bar + i + (j - i) // 2
                if bounds[-1] < mid < last_bar + 1:
                    bounds.append(mid)
            i = j
        else:
            i += 1
    if bounds[-1] != last_bar + 1:
        bounds.append(last_bar + 1)
    return bounds


# --------------------------------------------------------------------------
# bar-gap loop boundaries + silence trimming
# --------------------------------------------------------------------------
def _sounding_intervals(notes) -> list[tuple[int, int]]:
    """Merged [start, end) tick ranges where any note is ringing."""
    iv: list[list[int]] = []
    for n in sorted(notes, key=lambda n: n.start):
        if iv and n.start <= iv[-1][1]:
            iv[-1][1] = max(iv[-1][1], n.end)
        else:
            iv.append([n.start, n.end])
    return [(s, e) for s, e in iv]


def _bar_ticks_at(bm: BarMap, tick: int) -> int:
    b = bm.tick_to_bar(tick)
    return bm.bar_length_ticks(b)


def _active_blocks(bm: BarMap, instrument: Instrument,
                   min_gap_bars: float) -> list[tuple[int, int]]:
    """Sound blocks split wherever a silence lasts >= ``min_gap_bars`` bars.

    Each returned block spans its first note-on to its last note-off (no
    leading/trailing silence).  The bar length used for the threshold is the
    time signature in force at the middle of each silence.
    """
    intervals = _sounding_intervals(instrument.notes)
    if not intervals:
        return []
    blocks: list[tuple[int, int]] = []
    cs, ce = intervals[0]
    for (s, e) in intervals[1:]:
        if s - ce >= _bar_ticks_at(bm, (s + ce) // 2) * min_gap_bars:
            blocks.append((cs, ce))
            cs, ce = s, e
        else:
            ce = max(ce, e)
    blocks.append((cs, ce))
    return blocks


def _notes_between(instrument: Instrument, a: int, b: int):
    return [n for n in instrument.notes if a <= n.start < b]


def _trim_content(content: PatternContent) -> tuple[int, PatternContent]:
    """Re-base a window to its note span.  Returns (tick_shift, new_content)."""
    if not content.notes:
        return 0, content
    s = min(n[0] for n in content.notes)
    e = max(n[0] + n[1] for n in content.notes)
    if s == 0 and e == content.length_ticks:
        return 0, content
    notes = [(n[0] - s, n[1], n[2], n[3]) for n in content.notes]
    bends = [(b[0] - s, b[1]) for b in content.pitch_bends if s <= b[0] < e]
    return s, PatternContent(content.track_index, content.channel, e - s,
                              notes, bends, content.length_bars)


def _finalize_bar_gap_loops(result: SegResult, bm: BarMap,
                            instrument: Instrument, cfg: AnalysisConfig) -> SegResult:
    """Apply the bar-gap loop rule to a finished engine result.

    * Every cover window is trimmed to its note span (leading/trailing silence
      removed; the segment's ``start_tick`` moves to the trimmed origin so
      reconstruction stays tick-exact).
    * A cover window containing an internal silence of >= ``loop_gap_bars``
      bars is split into one silence-free block loop per side.
    * Each split window is additionally kept as a longer whole-span variant.
    * Engine ladder variants are edge-trimmed too.
    """
    if not cfg.loop_gap_bars or not result.cover:
        return result
    blocks = _active_blocks(bm, instrument, cfg.loop_gap_bars)

    import bisect
    starts = [b[0] for b in blocks]

    def block_of(tick: int) -> int:
        i = bisect.bisect_right(starts, tick) - 1
        return i if 0 <= i < len(blocks) else -1

    new_cover: list[CoverSegment] = []
    added: list[LoopVariant] = []
    for seg in result.cover:
        ws, we = seg.start_tick, seg.start_tick + seg.length_ticks
        notes = _notes_between(instrument, ws, we)
        if not notes:
            continue
        groups: list = []          # [(block_index, [Note, ...]), ...]
        for n in notes:
            bi = block_of(n.start)
            if groups and bi == groups[-1][0]:
                groups[-1][1].append(n)
            else:
                groups.append((bi, [n]))
        for _bi, gn in groups:
            b0 = gn[0].start
            e0 = max(n.end for n in gn)
            content = extract_content(instrument, b0, e0, b0, bm.span_bars(b0, e0))
            if not content.notes:
                continue
            bar_index = seg.bar_index if len(groups) == 1 else bm.tick_to_bar(b0)
            new_cover.append(CoverSegment(
                bar_index=bar_index, start_tick=b0,
                length_ticks=e0 - b0, length_bars=content.length_bars,
                content=content, confidence=seg.confidence))
        if len(groups) > 1:
            s0 = notes[0].start
            e0 = max(n.end for n in notes)
            content = extract_content(instrument, s0, e0, s0, bm.span_bars(s0, e0))
            if content.notes:
                added.append(LoopVariant(
                    bar_index=seg.bar_index, start_tick=s0,
                    length_ticks=e0 - s0, length_bars=content.length_bars,
                    content=content, confidence=seg.confidence, kind="variant"))
    result.cover = new_cover

    for v in result.variants:
        shift, content = _trim_content(v.content)
        if not content.notes:
            continue
        v.content = content
        v.start_tick += shift
        v.length_ticks = content.length_ticks
    result.variants.extend(added)
    return result


# --------------------------------------------------------------------------
# main entry
# --------------------------------------------------------------------------
def _append_ranges(result, ranges, bm, instrument, confidence):
    for s, e in ranges:
        if e <= s:
            continue
        start_tick = bm.bar_start_tick(s)
        end_tick = _edge_tick(bm, e)
        if end_tick <= start_tick:
            end_tick = start_tick + 1
        content = extract_content(instrument, start_tick, end_tick, start_tick, e - s)
        result.cover.append(CoverSegment(
            bar_index=s, start_tick=start_tick,
            length_ticks=end_tick - start_tick, length_bars=e - s,
            content=content, confidence=confidence))


def _leftover_ranges(first_bar: int, last_bar: int,
                     covered: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Gaps (bar-index ranges) not covered by the chosen motif windows."""
    out: list[tuple[int, int]] = []
    cur = first_bar
    for s, e in sorted(covered):
        if s > cur:
            out.append((cur, s))
        cur = max(cur, e)
    if cur <= last_bar:
        out.append((cur, last_bar + 1))
    return out


def segment_instrument(
    instrument: Instrument, song: Song, bm: BarMap, cfg: AnalysisConfig
) -> SegResult:
    result = SegResult(instrument=instrument)
    if not instrument.notes:
        return result
    span = _onset_bar_span(bm, instrument)
    if span is None:
        return result
    first_bar, last_bar = span
    span_bars = last_bar - first_bar + 1

    engine = cfg.engine if cfg.engine in ("period", "repeat", "hybrid") else "hybrid"
    lens = cfg.lens if cfg.lens in ("pitch", "rhythm") else "pitch"
    result.lens = lens

    # two-lens / adaptive-grid sequence of bar ids
    ids = _bar_ids(bm, instrument, cfg, first_bar, last_bar, lens)
    ids_motif = _bar_ids(bm, instrument, cfg, first_bar, last_bar, lens,
                         unique_empty=True)

    period = None
    if engine in ("period", "hybrid"):
        period = tile_period(ids, cfg)
    if period is not None:
        result.engine = "period"
        result.period_bars = period
        bounds = list(range(first_bar, last_bar + 1, period))
        if bounds[-1] != last_bar + 1:
            bounds.append(last_bar + 1)
        _append_ranges(result, list(zip(bounds, bounds[1:])), bm, instrument, 1.0)
        # ladder variants for the true repeating unit
        base = result.cover[0].bar_index
        start_tick = bm.bar_start_tick(base)
        remaining_bars = last_bar + 1 - base
        seen_lengths: set[int] = set()
        for mult in sorted(cfg.variant_multiples):
            length = period * mult
            if length <= period:
                continue
            actual_bars = min(length, remaining_bars)
            if actual_bars in seen_lengths:
                continue
            seen_lengths.add(actual_bars)
            end_tick = _edge_tick(bm, min(base + actual_bars, last_bar + 1))
            content = extract_content(instrument, start_tick, end_tick,
                                      start_tick, actual_bars)
            if content.notes:
                result.variants.append(LoopVariant(
                    bar_index=base, start_tick=start_tick,
                    length_ticks=end_tick - start_tick, length_bars=actual_bars,
                    content=content))
        result.notes_covered = len(instrument.notes)
        return _finalize_bar_gap_loops(result, bm, instrument, cfg)

    motif = None
    if engine in ("repeat", "hybrid") and span_bars >= 4:
        motif = repeat_motif(ids_motif)
    if motif is not None:
        result.engine = "repeat"
        L, _anchor, windows = motif
        covered_global = [(first_bar + s, first_bar + e) for s, e in windows]
        _append_ranges(result, covered_global, bm, instrument, 0.85)
        for (s, e) in _leftover_ranges(first_bar, last_bar, covered_global):
            bounds = _gap_bounds(bm, instrument, s, e - 1, cfg)
            _append_ranges(result, list(zip(bounds, bounds[1:])), bm, instrument, 0.5)
        result.notes_covered = len(instrument.notes)
        return _finalize_bar_gap_loops(result, bm, instrument, cfg)

    # gap-split fallback
    result.engine = "gap"
    bounds = _gap_bounds(bm, instrument, first_bar, last_bar, cfg)
    _append_ranges(result, list(zip(bounds, bounds[1:])), bm, instrument, 0.5)
    result.notes_covered = len(instrument.notes)
    return _finalize_bar_gap_loops(result, bm, instrument, cfg)


def _edge_tick(bm, idx_excl: int) -> int:
    """Tick of the boundary after bar ``idx_excl - 1`` (full-bar aligned)."""
    if idx_excl < bm.num_bars:
        return bm.bar_start_tick(idx_excl)
    last = bm.num_bars - 1
    return bm.bar_start_tick(last) + bm.bar_length_ticks(last)


def segment_song(song: Song, bm: BarMap, cfg: AnalysisConfig) -> list[SegResult]:
    return [segment_instrument(i, song, bm, cfg) for i in song.instruments]
