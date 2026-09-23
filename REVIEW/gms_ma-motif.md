# gms_ma/motif.py

## MotifConfig / Motif / MotifHit <!-- ref:gms_ma/motif.py:38-93 -->
**Purpose**: Config and result types for the grid-free motif catalogue. A `Motif` is a family with a canonical window, `events`/`notes` counts, similarity, and a `hits` list of `MotifHit`s; `layer` records `mixed`/`melody`/`harmony`.
**Why**: Families are the unit that becomes an ordinary `kind="motif"` pattern, so they are auditionable/exportable like any loop.
**Data Flow**: Produced by `mine_channel`, persisted by `indexer._persist_motifs`.
**Relationships**: `indexer`, `store.motif_hits`, timeline `tr.motifs`.

### _best_repeat_events <!-- ref:gms_ma/motif.py:219-268 -->
**Purpose**: Find the longest event window that recurs at least twice (non-overlapping).
**Why**: A rolling hash over the edge sequence (`_roll`/`_groups`) gives O(n) bucket candidates, then a binary search over window length finds the maximum feasible length; equal runs are hash-verified to avoid collisions.
**Data Flow**: `edges` → `(L_events, [event_start…])`.
**Relationships**: Core of `_discover`; used by `mine_channel` at the top level and for nested passes.

### _pitch_sim <!-- ref:gms_ma/motif.py:280-314 -->
**Purpose**: Score how well an occurrence matches the canonical window, allowing transposition.
**Why**: Rhythmic repeats may be transposed and octave/voicing variants differ; pitch-class overlap is searched over ±`transposition_range` semitones and the best `(score, shift)` is kept. Percussion compares exact key sets (shift 0).
**Data Flow**: `(canonical pitch sets, hit pitch sets, is_drums)` → `(sim, shift)`.
**Relationships**: Used by `_candidate_to_motif` to accept/reject hits.

### mine_channel <!-- ref:gms_ma/motif.py:400-523 -->
**Purpose**: Mine every recurring note window of one instrument, optionally restricted to a separated layer.
**Why**: Onsets are chord-merged and timing quantised to a data-driven tatum so tuplets/humanisation collapse cleanly; discovery is hierarchical (top-level repeats, then each family's own window re-mined for nested riffs) and content-keyed families are merged. The `notes=`/`layer=` parameters let `_persist_motifs` mine mixed plus melody/harmony layers.
**Data Flow**: `Instrument (+ optional notes override, layer)` → `[Motif]`, largest-first.
**Relationships**: Called by `indexer._persist_motifs`; uses `_best_repeat_events`, `_pitch_sim`, `segment._active_blocks`.

```python
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
```

### _discover / _candidate_to_motif <!-- ref:gms_ma/motif.py:310-400 -->
**Purpose**: `_discover` is the core discovery loop that finds repeating edge-runs of decreasing length; `_candidate_to_motif` scores each candidate family, accepts/rejects hits by similarity, and builds a `Motif` object.
**Why**: The `_discover` function uses a greedy masking strategy (`_mask_edges`) so each found family is excluded from subsequent searches, preventing redundant discoveries. `_candidate_to_motif` filters hits by `min_hits` and `min_beats` and computes the transposition shift for each hit.
**Data Flow**: `edges, min_events, cap` → `[(L_events, [starts])]`; `L, starts, evs, notes, ppq` → `Motif|None`.
**Relationships**: `_discover` is called by `mine_channel`; `_candidate_to_motif` is called by the `add` closure inside `mine_channel`.

### _ioi_clusters / _pick_unit / _edges <!-- ref:gms_ma/motif.py:100-220 -->
**Purpose**: `_ioi_clusters` groups inter-onset intervals into tolerance-clustered centroids; `_pick_unit` selects the finest common tatum; `_edges` produces a transposition-safe edge code per consecutive-event pair.
**Why**: The data-driven tatum (rather than a fixed grid) allows swing, humanisation, and tuplets to collapse cleanly. The edge code uses `(gap << 20) ^ (pit & 0xFFFFF)` so rhythm and pitch are combined into a single hashable token. For drums, the pitch component is a bitwise OR of all hit pitches so chord hits are distinguishable.
**Data Flow**: `notes, ppq` → `edges: [int]`; `edges` → `unit: int`.
**Relationships**: Called by `mine_channel` before the discovery loop.