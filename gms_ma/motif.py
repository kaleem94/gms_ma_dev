"""Data-driven, grid-free motif mining for a single MIDI channel.

Prototype "catalog" miner.  Unlike :mod:`segment` (which reasons only about
whole bars starting on a bar line, matched exactly on a rigid grid), this
module:

* merges chords and quantises timing to a **data-driven tatum** (the finest
  commonly repeated inter-onset spacing), so swing, humanisation, tuplets and
  low-PPQ files collapse cleanly instead of fighting a beat-divisor grid;
* discovers **repeated windows of any length at any start position**
  (sub-bar riffs, off-beat/pickup figures, long phrases) — no bar alignment,
  and windows are matched on their *internal* rhythm only (context-free);
* groups occurrences tolerantly: rhythmic repeats may be **transposed**
  (interval tokens) and octave/voicing variants survive a pitch-class overlap
  scorer.

The pipeline deliberately returns a *catalog* (families + occurrences) and does
not touch the cover/variant segmentation used for reconstruction.  Pure stdlib.

Representation
--------------
Onsets are merged into events (chords).  Every pair of consecutive events
defines an *edge* encoding the quantised inter-onset gap plus the transposed
interval between the events' primary (lowest) pitches.  Two windows of notes
are structural equals iff their edge sequences are equal, so a match never
depends on the rests/notes that happen to precede a window.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .model import Instrument

# ---- config ---------------------------------------------------------------


@dataclass
class MotifConfig:
    """Knobs for :func:`mine_channel` (prototype defaults)."""

    min_events: int = 2          # min events a motif window must span
    min_beats: float = 0.5       # min motif duration in quarter-note beats
    min_hits: int = 2            # min occurrences (hits) for a family to count
    chord_eps_ppq: float = 0.04  # chord-merge window as a fraction of a beat
    unit_sig: float = 0.20       # min cluster share of the mode to count as tatum
    tol_ppq: float = 0.12        # relative tolerance used to cluster IOIs
    pitched_min_sim: float = 0.70   # family accept threshold (pitched)
    pitched_hit_min: float = 0.55   # drop weaker individual hits
    drums_min_sim: float = 0.85
    max_families: int = 80
    transposition_range: int = 24    # +/- semitones searched per hit
    loop_gap_bars: float = 1.0   # also mine each >=-bar silence-delimited sound
                                 # block separately, so loop families never
                                 # silently bridge a full-bar rest (0 disables)


DEFAULT_MOTIF_CONFIG = MotifConfig()


# ---- results --------------------------------------------------------------

@dataclass
class MotifHit:
    """One occurrence of a discovered motif."""

    start_tick: int
    end_tick: int
    event_start: int = 0
    event_end: int = 0
    sim: float = 1.0     # pitch/percussion similarity vs canonical
    shift: int = 0       # transposition (semitones) that best aligned it


@dataclass
class Motif:
    """A family of recurring note windows in one instrument."""

    id: int
    start_tick: int          # canonical (first accepted) occurrence
    end_tick: int
    length_ticks: int
    events: int              # number of onset events in the window
    notes: int               # number of MIDI notes in the window
    hits: list[MotifHit] = field(default_factory=list)
    sim: float = 1.0
    shift: int = 0
    is_drums: bool = False
    content: list = field(default_factory=list)  # (rel_delta,dur,pitch,vel)
    layer: str = ""          # "" (mixed) | "melody" | "harmony"

    @property
    def reps(self) -> int:
        return len(self.hits)


# ---- helpers --------------------------------------------------------------

def _ioi_clusters(deltas, tol):
    """Greedy tolerance-clustered centroids of positive inter-onset deltas.

    Returns [(centroid, count)] sorted by centroid.  ``tol`` is a fraction of
    the running cluster mean, so both exact (grid-snapped) and humanised
    timings collapse into the same "unit" while real tuplets stay distinct.
    """
    out = []
    for d in sorted(x for x in deltas if x > 0):
        if out:
            c = sum(out[-1]) / len(out[-1])
            if d - c <= max(1.0, tol * c):
                out[-1].append(d)
                continue
        out.append([d])
    return [(sum(g) / len(g), len(g)) for g in out]


def _pick_unit(clusters, sig):
    """Finest IOI unit that is common enough to act as a stable tatum."""
    if not clusters:
        return 1
    maxc = max(c for _c, c in clusters)
    floor = max(2, int(sig * maxc))
    for centroid, count in clusters:
        if count >= floor:
            return max(1, int(round(centroid)))
    return max(1, int(round(clusters[0][0])))


def _chord_eps(ppq: int, frac: float) -> int:
    return max(2, int(round(ppq * frac)))


def _events(notes, eps: int):
    """Chord-merged onset events: [(tick, frozenset(pitches), end_tick)]."""
    evs = []
    for n in sorted(notes, key=lambda n: (n.start, n.pitch)):
        if evs and n.start - evs[-1][0] <= eps:
            prev = evs[-1]
            evs[-1] = (prev[0], prev[1] | {n.pitch}, max(prev[2], n.end))
        else:
            evs.append((n.start, {n.pitch}, n.end))
    return [(t, frozenset(ps), e) for (t, ps, e) in evs]


def _pcs(pitches) -> frozenset:
    return frozenset(p % 12 for p in pitches)


def _primary(ev):
    return min(ev[1])


def _edges(evs, unit, is_drums):
    """Edge code per consecutive-event pair (context-free, transposition-safe).

    code = (quantised gap, interval/keyset delta).  Edge ``k`` belongs to the
    pair (evs[k], evs[k+1]); a window starting at event index ``s`` spanning
    ``L`` events therefore uses edges ``s .. s+L-2``.
    """
    codes = []
    for k in range(len(evs) - 1):
        a, b = evs[k], evs[k + 1]
        gap = max(1, int(round((b[0] - a[0]) / unit)))
        if is_drums:
            pit = 0
            for p in b[1]:
                pit = (pit << 8) | (p & 0xFF)
        else:
            pit = _primary(b) - _primary(a)
        codes.append((gap << 20) ^ (pit & 0xFFFFF))
    return codes


def _window_content_key(evs, s, L):
    """Cell content key for events ``[s, s+L)`` (relative, tick-independent)."""
    base = evs[s][0]
    out = []
    for i in range(s, s + L):
        _t, ps, end = evs[i]
        out.append((evs[i][0] - base, tuple(sorted(ps)), end - evs[i][0]))
    return tuple(out)


# ---- repeat discovery -----------------------------------------------------

_MOD = (1 << 61) - 1
_BASE = 911382323


def _roll(seq):
    n = len(seq)
    pref = [0] * (n + 1)
    pw = [1] * (n + 1)
    for i, v in enumerate(seq):
        pref[i + 1] = (pref[i] * _BASE + (v + 7)) % _MOD
        pw[i + 1] = (pw[i] * _BASE) % _MOD
    return pref, pw


def _groups(seq, pref, pw, G):
    """Hash-verified groups of equal edge-runs of length ``G`` -> [starts...]."""
    n = len(seq)
    out = []
    if G <= 0:
        return out
    buckets = {}
    for i in range(0, n - G + 1):
        h = (pref[i + G] - pref[i] * pw[G]) % _MOD
        buckets.setdefault(h, []).append(i)
    for starts in buckets.values():
        if len(starts) < 2:
            continue
        ref = seq[starts[0]:starts[0] + G]
        verified = [s for s in starts if seq[s:s + G] == ref]
        if len(verified) >= 2:
            out.append(verified)
    return out


def _best_repeat_events(edges, min_events):
    """Longest repeating event window (>=2 non-overlapping occurrences).

    Returns (L_events, [event_start, ...]) or None.  Overlapping-only repeats
    (a purely periodic cell) fail here and are re-hunted as shorter windows by
    the caller's shrink pass... actually the caller re-scans content.  Edge
    runs of length L-1 are matched; occurrences are spaced by >= L events.
    """
    n_events = len(edges) + 1
    if n_events < 3 or min_events < 2:
        return None
    pref, pw = _roll(edges)

    def feasible(L: int) -> bool:
        G = L - 1
        for starts in _groups(edges, pref, pw, G):
            picked = 0
            last = -1
            for s in starts:
                if s >= last:
                    picked += 1
                    last = s + L
                    if picked >= 2:
                        return True
        return False

    lo, hi = min_events, n_events
    best = None
    while lo <= hi:
        mid = (lo + hi) // 2
        if feasible(mid):
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    if best is None:
        return None
    G = best - 1
    chosen = None
    for starts in _groups(edges, pref, pw, G):
        picks = []
        last = -1
        for s in starts:
            if s >= last:
                picks.append(s)
                last = s + best
        if len(picks) >= 2 and (chosen is None or len(picks) > len(chosen)):
            chosen = picks
    return (best, chosen) if chosen is not None else None


def _mask_edges(edges, event_starts, L):
    """Block cells (events) so they can never seed another equal repeat."""
    for s in event_starts:
        # internal edges of cell [s, s+L): s .. s+L-2
        for e in range(max(0, s - 1), min(len(edges), s + L - 1)):
            edges[e] = -(e + 1000)


# ---- similarity -----------------------------------------------------------

def _pitch_sim(canon, hits, drums, cfg: MotifConfig):
    """Best pitch-class overlap between canonical & occurrence events.

    canon / hits: list of frozenset pitches per aligned event.
    Returns (sim, shift).  Percussion compares exact keys (shift 0).
    """
    if drums:
        tot = 0
        match = 0
        for ca, h in zip(canon, hits):
            ca = set(ca)
            h = set(h)
            tot += len(ca) + len(h)
            match += 2 * len(ca & h)
        if tot == 0:
            return 1.0, 0
        return match / tot, 0
    ca_pc = [_pcs(c) for c in canon]
    h_pc = [_pcs(h) for h in hits]
    total = sum(len(c) for c in ca_pc)
    if total == 0:
        return 1.0, 0
    best = (0.0, 0)
    for shift in range(-cfg.transposition_range, cfg.transposition_range + 1):
        m = 0
        for c, h in zip(ca_pc, h_pc):
            if not c or not h:
                continue
            hs = frozenset(x + shift for x in h)
            m += len(c & hs)
        if m / total > best[0]:
            best = (m / total, shift)
    return best


# ---- main entry -----------------------------------------------------------

def _discover(edges, min_events, cap):
    """Discovery core: list of (L_events, event_starts), biggest first.

    Operates on whatever edge list it is given (full track, or the slice of a
    larger motif's canonical occurrence for the nested pass), masking each
    family out before hunting the next.
    """
    tk = list(edges)
    res = []
    guard = 0
    while len(res) < cap and guard < 2000:
        guard += 1
        found = _best_repeat_events(tk, min_events)
        if found is None:
            break
        L, starts = found
        res.append((L, starts))
        _mask_edges(tk, starts, L)
    return res


def _candidate_to_motif(L, starts, evs, notes, ppq, is_drums, cfg, mid):
    """Score & build a Motif from an event window family (or None).

    Starts may include transposed copies (edge-equal but shifted pitch); the
    pitch-class scorer aligns each hit and accepts/rejects it on similarity.
    """
    starts = sorted(set(starts))
    if len(starts) < cfg.min_hits:
        return None
    canon_s = starts[0]
    canon_p = [evs[i][1] for i in range(canon_s, canon_s + L)]

    scored = []
    for s in starts:
        if s == canon_s:
            scored.append((s, 1.0))
            continue
        hp = [evs[i][1] for i in range(s, s + L)]
        sc, _sh = _pitch_sim(canon_p, hp, is_drums, cfg)
        scored.append((s, sc))
    min_sim = cfg.drums_min_sim if is_drums else cfg.pitched_hit_min
    keep = [(s, sc) for s, sc in scored if sc >= min_sim]
    if len(keep) < cfg.min_hits:
        return None

    start_tick = evs[canon_s][0]
    end_tick = evs[canon_s + L - 1][2]
    window_notes = [
        (n.start - start_tick, n.duration, n.pitch, n.velocity)
        for n in notes if start_tick <= n.start < end_tick
    ]
    if (end_tick - start_tick) / max(1, ppq) < cfg.min_beats:
        return None

    motif = Motif(
        id=mid,
        start_tick=start_tick,
        end_tick=end_tick,
        length_ticks=end_tick - start_tick,
        events=L,
        notes=len(window_notes),
        is_drums=is_drums,
        sim=round(sum(sc for _s, sc in keep) / len(keep), 3),
        content=window_notes,
    )
    for s, sc in keep:
        shift = 0
        if s != canon_s and not is_drums:
            _sc, shift = _pitch_sim(canon_p, [evs[i][1] for i in range(s, s + L)],
                                    is_drums, cfg)
        motif.hits.append(MotifHit(
            start_tick=evs[s][0],
            end_tick=evs[s + L - 1][2],
            event_start=s,
            event_end=s + L,
            sim=round(sc, 3),
            shift=shift,
        ))
    motif.hits.sort(key=lambda h: h.event_start)
    return motif


def mine_channel(
    instrument: Instrument,
    ppq: int,
    cfg: MotifConfig = DEFAULT_MOTIF_CONFIG,
    bm=None,
    notes=None,
    layer: str = "",
):
    """Mine every recurring note window of ``instrument``.

    Returns a list of :class:`Motif` ordered largest/first-discovered first.
    ``ppq`` is the song's ticks-per-beat (used for beat-length filtering).
    ``bm`` (an optional :class:`bars.BarMap`) lets the miner additionally mine
    each >=-bar silence-delimited sound block independently (``loop_gap_bars``)
    so loop families never silently bridge a full-bar rest.  ``notes`` optionally
    overrides the instrument's notes (used to mine one separated layer), and
    ``layer`` stamps every returned family with its layer name.

    Discovery is hierarchical: top-level repeats are found first, then each
    motif's own window is mined again so riffs/fills nested inside a repeated
    section (and repeated inside *every* occurrence of that section) surface
    too.  Families with identical window content are merged.
    """
    notes = list(notes) if notes is not None else instrument.notes
    if not notes:
        return []
    is_drums = instrument.is_drums

    raw_onsets = sorted(n.start for n in notes)
    clusters = _ioi_clusters(
        [b - a for a, b in zip(raw_onsets, raw_onsets[1:])], cfg.tol_ppq)
    unit = _pick_unit(clusters, cfg.unit_sig)
    eps = _chord_eps(ppq, cfg.chord_eps_ppq)
    evs = _events(notes, eps)
    if len(evs) < cfg.min_events + 1:
        return []
    edges = _edges(evs, unit, is_drums)

    min_events = max(2, cfg.min_events)
    catalog: list[Motif] = []
    seen = {}

    def add(L, starts):
        """Insert (or merge into) a family keyed by exact window content."""
        if not starts or len(catalog) >= cfg.max_families:
            return None
        motif = _candidate_to_motif(L, starts, evs, notes, ppq,
                                    is_drums, cfg, len(catalog) + 1)
        if motif is None:
            return None
        c0 = motif.hits[0].event_start
        key = (motif.events, _window_content_key(evs, c0, motif.events))
        prev = seen.get(key)
        if prev is not None:
            existing = {x.event_start for x in prev.hits}
            prev.hits.extend(h for h in motif.hits if h.event_start not in existing)
            prev.hits.sort(key=lambda h: h.event_start)
            return prev
        catalog.append(motif)
        seen[key] = motif
        return motif

    # ---- top level -------------------------------------------------------
    # Candidate sources:
    #  * whole track (may bridge rests -> the "keep the full span" families);
    #  * a copy whose >=-bar rest edges are masked, so no family silently
    #    bridges a full-bar rest (the "silence-free block loops").
    candidates = list(_discover(edges, min_events, cfg.max_families))
    if bm is not None and cfg.loop_gap_bars:
        from .segment import _active_blocks

        blocks = _active_blocks(bm, instrument, cfg.loop_gap_bars)
        if len(blocks) > 1:
            blocked = list(edges)
            idx = 0
            for (bs, be) in blocks:
                # an event whose onset is inside this block
                while idx < len(evs) and evs[idx][0] < bs:
                    idx += 1
                start_ev = idx
                while idx < len(evs) and evs[idx][0] < be:
                    idx += 1
                end_ev = idx
                for e in (start_ev - 1, end_ev - 1):   # boundary edges
                    if 0 <= e < len(blocked):
                        blocked[e] = -(e + 1000)
            for L, starts in _discover(blocked, min_events,
                                       cfg.max_families - len(candidates)):
                candidates.append((L, starts))

    families = []
    for L, starts in candidates:
        m = add(L, starts)
        if m is not None:
            families.append(m)

    # ---- nested levels ---------------------------------------------------
    idx = 0
    while idx < len(families) and len(catalog) < cfg.max_families:
        parent = families[idx]
        idx += 1
        Lf = parent.events
        if Lf < 2 * min_events:
            continue
        ev0 = parent.hits[0].event_start
        if ev0 < 0 or ev0 + Lf > len(evs):
            continue
        slice_edges = edges[ev0:ev0 + Lf - 1]  # internal edges of the window
        for l, local_starts in _discover(slice_edges, min_events,
                                         cfg.max_families - len(catalog)):
            global_starts = set()
            for hit in parent.hits:
                base = hit.event_start
                for ls in local_starts:
                    g = base + ls
                    if 0 <= g and g + l <= len(evs):
                        global_starts.add(g)
            m = add(l, sorted(global_starts))
            if m is not None and m not in families:
                families.append(m)
    if layer:
        for m in catalog:
            m.layer = layer
    return catalog
