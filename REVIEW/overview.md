# Code Review: gms-ma (GMS MIDI analyzer)

## Overview
<!-- ref: overall -->
`gms-ma` is a computational-musicology workbench that turns a folder of General-MIDI files into a structured, queryable analysis. It is deliberately dependency-light: the runtime is the Python standard library plus `mido`, and the browser UI is native ES modules with no build step. The pipeline is a straight line of single-responsibility modules connected by plain dataclasses: `parse` (mido only) → `bars` (pure tick/bar/second math) → `segment` (loop/cover detection) and `motif` (grid-free catalogue) → `export` (mido rendering) → `store` (SQLite repository) with `features` / `music_theory` / `harmony` / `tag` providing analysis and `indexer` orchestrating it all.

The database (`db/midi_loops.db`) is the single source of truth: every generated MIDI loop and rhythm/harmony stem is stored as a BLOB in `midi_assets`, manifests are stored as JSON inside the DB, and every artefact (placements, motif hits, tags, features) is a row. This makes the DB self-contained and directly queryable with standard SQL. `library` builds a denormalised cross-song catalogue for browsing, and `viewer` is a threaded stdlib HTTP server that serves JSON APIs plus the `assets/` web UI (a timeline viewer and a loop library).

The recent additions are a chord/harmony layer (chord detection, roman numerals, harmony/melody separation stems, per-layer motif families), a new "Harmony" timeline view, and a shared bottom transport with a 0.25–3× speed multiplier and an export-BPM feature that retempoes downloaded MIDI. A latent class of issues worth reviewing is the amount of logic now living in `assets/js/timeline/app.js` (over 1,200 lines) and the coupling between the shared `miniPlayer` component and per-page export state.

## Data Flow
<!-- ref: overall -->
```
.mid/.midi
   │  parse.parse_file
   ▼
model.Song ──→ bars.BarMap ──→ segment.segment_song ──→ PatternContent ──→ export.pattern_to_bytes
   │                       │                              │
   │                       │                              ▼
   │                       │                     motif.mine_channel ──→ Motif families
   │                       │                              │
   │                       │         features / music_theory / harmony / tag
   │                       │                              │
   │                       │         store.Repository ──→ db/midi_loops.db
   │                       │         (patterns, placements,
   │                       │          motif_hits, assets,
   │                       │          stems, manifests, tags, features)
   │                       │
   └───────────────────────┴──→ indexer.run_index ──→ process_song ──→ build_manifest
                                                                              │
                                                                              ▼
                                                                    viewer._Handler (HTTP)
                                                                              │
                                                                    ┌─────────────┼─────────────┐
                                                                    ▼             ▼             ▼
                                                              /api/song    /api/library   /assets/*
                                                                    │             │
                                                                    ▼             ▼
                                                            assets/js/timeline/  assets/js/library/
                                                            app.js (1200+ lines)  app.js
```

## gms_ma/model.py
### Note / PitchBend / Instrument <!-- ref:gms_ma/model.py:16-90 -->
**Purpose**: The neutral in-memory MIDI model — one sounding `Note` (`start`/`end` ticks, `pitch`, `velocity`), a `PitchBend` event, and an `Instrument` that aggregates all notes/bends/programs for one `(track_index, channel)` pair.
**Why**: Keeping the model free of mido/sqlite/file concerns is the project's SOLID seam: every later stage talks through these dataclasses, so swapping the parser or the exporter is localised.
**Data Flow**: `parse` produces `Instrument.notes`; `__post_init__` sorts notes by `(start, pitch)`, bends by tick and programs by tick, so downstream code can assume ordering. `program_at(tick)` scans the program list (last change at or before `tick`); `is_drums` is `channel == 9`.
**Relationships**: Consumed by `segment`, `motif`, `features`, `indexer`. See `Song` below.

### Song <!-- ref:gms_ma/model.py:107-141 -->
**Purpose**: Root aggregate: song identity plus tempo/time-signature maps and the list of `Instrument`s.
**Why**: Tempo/time-signature events are kept as sorted lists rather than a compiled map so the pure `BarMap` can do the heavy lifting; `initial_tempo` / `initial_time_signature` expose the common single-tempo case.
**Data Flow**: Built by `parse._song_from_mf`; `__post_init__` sorts tempos, time signatures and instruments. `total_note_ons()` and `drum_notes` are cheap aggregate counters used for the `songs` row.
**Relationships**: Input to `bars.BarMap`, `segment.segment_song`, `indexer.song_analysis`, `export`.

## gms_ma/parse.py
### parse_file / parse_bytes / _song_from_mf <!-- ref:gms_ma/parse.py:16-110 -->
**Purpose**: The only module that knows mido's file format on the read side. `parse_file` wraps `mido.MidiFile`, `parse_bytes` reads from memory, and `_song_from_mf` walks tracks/messages into the neutral model.
**Why**: mido raises on genuinely corrupt files; those are converted to a single `ParseError` so `run_index` can skip a bad file with a warning instead of aborting the whole run.
**Data Flow**: For each track it tracks a running `tick` from message deltas, records `note_on`/`note_off` pairs into `Instrument.notes`, tempo and time-signature meta into `Song`, and program changes into `Instrument.programs`. Defaults a `4/4` time signature when none is present.
**Relationships**: Feeds `model.Song`; errors surfaced by `indexer.run_index` (`ParseError` → skipped list) and `cli`.

## gms_ma/bars.py
### _bar_ticks <!-- ref:gms_ma/bars.py:10-20 -->
**Purpose**: Compute the tick length of one bar from the song's ticks-per-beat and the time signature.
**Why**: Encodes the `4/4`-style `ticks_per_beat * 4 / denominator * numerator` relationship in one place. A previous bug where non-4/4 meters produced zero-length bars (and hung the segmenter) lives at this boundary.
**Data Flow**: `(ticks_per_beat, TimeSigEvent)` → `int` ticks per bar.
**Relationships**: Used by `BarMap._build`.

### BarMap <!-- ref:gms_ma/bars.py:21-143 -->
**Purpose**: Piecewise tick→bar→second conversion across the song's tempo and time-signature maps.
**Why**: Tempo can change mid-song, so seconds are not linear in ticks; `_tempo_segments` caches a piecewise `(tick_from, tick_to, seconds_per_tick)` table and `second_at_tick` / `tick_at_second` binary-search it. The `bars` list is memoised so repeated layout calls stay cheap.
**Data Flow**: `Song` → `bars list`, and `tick` → `bar`, `tick` → `seconds`, `seconds` → `tick`, `(start,end)` → `span_bars`.
**Relationships**: Used by `segment`, `motif`, `indexer`, `viewer.TickClock` (which reimplements the same idea for playback), and the timeline JS via the manifest's `bars` array.

## gms_ma/segment.py
### AnalysisConfig <!-- ref:gms_ma/segment.py:89-123 -->
**Purpose**: The single tuning surface for loop/cover detection and motif extraction (engine, lens, grid coverage, gap thresholds, stems, harmony split).
**Why**: A dataclass keeps the strategy seam explicit and lets the CLI, GUI and tests construct the same object; `variant_lengths` derives the 1×/2×/4×/8× ladder.
**Data Flow**: Passed from `cli.cmd_index` / `gui._start` / tests into `segment_song` and `indexer`.
**Relationships**: Read throughout `indexer.process_song`, `_persist_pattern`, `_persist_motifs`; also imported by the GUI.

### PatternContent <!-- ref:gms_ma/segment.py:32-49 -->
**Purpose**: The serialisable musical content of one window — `notes` as `(start_delta, duration, pitch, velocity)`, `pitch_bends`, `length_ticks`, `length_bars`, channel/track.
**Why**: A content-addressed id (`content_id`) hashes the window so identical repeats deduplicate into one `patterns` row across all placements. Removing static pitch-bend runs keeps ids stable and files small.
**Data Flow**: Built by `segment`/`motif`; hashed into `content_id`; rendered by `export`.
**Relationships**: Central currency between `segment`, `motif`, `export`, `indexer`, `store`.

### segment_instrument / segment_song <!-- ref:gms_ma/segment.py:551-655 -->
**Purpose**: Detect repeating bar-aligned material per instrument and return a `SegResult` of cover segments, variants and confidence.
**Why**: Uses an adaptive grid (coarsest subdivision matching `grid_cov` of onsets) plus two match lenses (`pitch` vs `rhythm`) and three engines (`period` tiles-from-start, `repeat` largest-block, `hybrid` = period → repeat → silence-gap splitting). This avoids forcing a fixed 16th grid on swing/humanised material.
**Data Flow**: `(Song, BarMap, AnalysisConfig)` → `[SegResult]`; `_active_blocks` finds silence-delimited sound blocks so loops never bridge a full-bar rest.
**Relationships**: Called by `indexer.process_song`; motif mining reuses `_active_blocks`.

## gms_ma/motif.py
### MotifConfig / Motif / MotifHit <!-- ref:gms_ma/motif.py:38-93 -->
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

## gms_ma/music_theory.py
### detect_key <!-- ref:gms_ma/music_theory.py:17-30 -->
**Purpose**: Krumhansl–Schmuckler key/mode detection from weighted pitch-class counts.
**Why**: A well-understood, dependency-free heuristic; returns the correlation so callers can express confidence.
**Data Flow**: `Counter(pc → weight)` → `(tonic, "major"|"minor", correlation)`.
**Relationships**: Used by `features.analyze_notes` and therefore every pattern/song tag.

### detect_chord <!-- ref:gms_ma/music_theory.py:87-120 -->
**Purpose**: Name the chord formed by a set of sounding MIDI pitches.
**Why**: Template matching over maj/min/dim/aug/sus/power/6/add9/7th/9th shapes is simple, explainable and fast; the score rewards matched tones and penalises foreign tones harder than omissions, and the lowest sounding pitch yields slash-chord inversions. Fewer than two distinct pitch classes returns `N.C.`.
**Data Flow**: `pitches` → `(root, quality, label, bass, confidence)`.
**Relationships**: Used by `harmony.analyze_harmony`; `roman_numeral` labels the result against the key.

```python
def detect_chord(pitches) -> tuple[int, str, str, int | None, float]:
    """Best chord for a set of sounding MIDI pitches.

    Returns ``(root, quality, label, bass, confidence)`` — pitch classes for
    ``root``/``bass``, ``label`` like ``"Cmaj"``, ``"Am"``, ``"G7"``, ``"C/E"``.
    Fewer than two distinct pitch classes (or no plausible match) yields
    ``(0, "", "N.C.", bass, 0.0)``.
    """
    pcs = {int(p) % 12 for p in pitches}
    bass = min(int(p) for p in pitches) % 12 if pitches else None
    if len(pcs) < 2:
        return 0, "", "N.C.", bass, 0.0
    best = None  # (score, root, quality, suffix, missing)
    for root in range(12):
        rel = {(pc - root) % 12 for pc in pcs}
        for quality, tmpl, suffix in _CHORD_TEMPLATES:
            ts = set(tmpl)
            matched = len(rel & ts)
            if matched == 0:
                continue
            extra = len(rel - ts)
            missing = len(ts - rel)
            score = (matched / len(ts)
                     - 0.5 * extra / max(1, len(rel))
                     - 0.15 * missing)
            if best is None or score > best[0]:
                best = (score, root, quality, suffix, missing)
    if best is None or best[0] <= 0:
        return 0, "", "N.C.", bass, 0.0
    score, root, quality, suffix, _missing = best
    label = NOTE_NAMES[root] + suffix
    if bass is not None and bass != root:
        label += "/" + NOTE_NAMES[bass]
    return root, quality, label, bass, round(max(0.0, min(1.0, score)), 3)
```

### roman_numeral <!-- ref:gms_ma/music_theory.py:128-143 -->
**Purpose**: Functional roman numeral of a chord relative to the detected key (e.g. `V7`, `vi`, `bIII`).
**Why**: Maps the root to the nearest scale degree, adds a sharp/flat accidental when off by one semitone, and uses case + suffix maps for quality.
**Data Flow**: `(root, quality, key_tonic, key_mode)` → `str`.
**Relationships**: Used by `harmony.analyze_harmony`.

## gms_ma/harmony.py
### merge_events <!-- ref:gms_ma/harmony.py:14-24 -->
**Purpose**: Chord-merge simultaneous/near onsets into `(tick, [pitches], end_tick)` events.
**Why**: Mirrors the motif miner's event model so chord analysis works on the same representation; accepts both note tuples and `model.Note` objects.
**Data Flow**: `notes, eps` → `[(tick, [pitch…], end_tick)]`.
**Relationships**: Used by `analyze_harmony`.

### analyze_harmony <!-- ref:gms_ma/harmony.py:26-67 -->
**Purpose**: Produce a chord sequence and summary for one note window: per-event labels + roman numerals, the collapsed progression, top chord, changes-per-bar and chordal ratio.
**Why**: Chords with confidence below 0.5 are dropped so monophonic passages do not generate spurious chords; consecutive identical labels collapse so the progression reads musically.
**Data Flow**: `notes, ppq, key` → `{chords, progression, top_chord, changes_per_bar, chordal_ratio}`.
**Relationships**: Called by `features.analyze_notes`; its summary feeds `tag.pattern_tags` (`chord`/`harmony` tags).

```python
def analyze_harmony(notes, ppq: int, key_tonic: int = 0, key_mode: str = "major",
                    length_bars: int = 1, chord_eps_ppq: float = 0.04) -> dict:
    """Chord sequence + summary for one note window.

    Returns a dict with ``chords`` (``[{tick,label,roman,confidence}]``),
    ``progression`` (collapsed ``"C - Am - F - G"``), ``top_chord``,
    ``changes_per_bar`` and ``chordal_ratio``.
    """
    empty = {"chords": [], "progression": "", "top_chord": "",
             "changes_per_bar": 0.0, "chordal_ratio": 0.0}
    if not notes:
        return empty
    eps = max(2, int(round(max(1, ppq) * chord_eps_ppq)))
    evs = merge_events(notes, eps)
    if not evs:
        return empty

    chords = []
    for (tick, pitches, _end) in evs:
        root, quality, label, _bass, conf = detect_chord(pitches)
        if label == "N.C." or conf < 0.5:
            continue
        chords.append({"tick": tick, "label": label, "confidence": conf,
                       "roman": roman_numeral(root, quality, key_tonic, key_mode)})

    if not chords:
        return {**empty, "chordal_ratio": 0.0}

    collapsed = []
    for c in chords:
        if collapsed and collapsed[-1]["label"] == c["label"]:
            continue
        collapsed.append(c)
    counts = Counter(c["label"] for c in chords)
    top_chord = counts.most_common(1)[0][0]
    return {
        "chords": chords,
        "progression": " - ".join(c["label"] for c in collapsed),
        "top_chord": top_chord,
        "changes_per_bar": round(len(collapsed) / max(1, length_bars), 3),
        "chordal_ratio": round(len(chords) / len(evs), 3),
    }
```

### split_layers <!-- ref:gms_ma/harmony.py:78-107 -->
**Purpose**: Split a note set into `(melody, harmony)` subsets under a configurable rule (`off`/`top`/`onset`).
**Why**: Separation powers both the harmony/melody stems and the per-layer motif families. `top` treats the highest note at each onset as melody and the rest as harmony; `onset` treats multi-pitch onsets as harmony and single-note onsets as melody. Accepts tuples or `Note` objects and returns the originals unchanged.
**Data Flow**: `notes, rule` → `(melody[], harmony[])`.
**Relationships**: Used by `indexer._persist_motifs` (per-layer mining) and `indexer._persist_pattern` (stems).

```python
def split_layers(notes, rule: str = "top"):
    """Split notes into ``(melody, harmony)`` subsets.

    Accepts both ``(start, dur, pitch, vel)`` tuples and :class:`model.Note`
    objects (the originals are returned unchanged).  Rules:

    * ``off``   -> no split (everything melody).
    * ``top``   -> the highest note at each onset is melody, the rest harmony.
    * ``onset`` -> onsets with >= 2 simultaneous pitches are harmony, single-note
      onsets are melody.
    """
    notes = list(notes)
    if rule == "off" or not notes:
        return notes, []
    by_onset: dict[int, list] = {}
    for n in notes:
        by_onset.setdefault(_onset(n), []).append(n)
    melody: list = []
    harmony: list = []
    if rule == "onset":
        for group in by_onset.values():
            (harmony if len(group) > 1 else melody).extend(group)
    else:  # "top"
        for group in by_onset.values():
            ordered = sorted(group, key=_pitch)
            melody.append(ordered[-1])
            harmony.extend(ordered[:-1])
    melody.sort(key=lambda n: (_onset(n), _pitch(n)))
    harmony.sort(key=lambda n: (_onset(n), _pitch(n)))
    return melody, harmony
```

## gms_ma/features.py
### PatternMetrics / analyze_notes <!-- ref:gms_ma/features.py:17-151 -->
**Purpose**: Numeric descriptors for a note window or song — onsets, density, occupancy, legato/staccato, syncopation, polyphony, pitch stats, key, and (when `ppq` is given) harmony summary.
**Why**: Pure functions over `(start_rel, duration, pitch, velocity)` tuples keep this trivially unit-testable and free of mido/sqlite. Key detection runs first, then `analyze_harmony` is given that key so roman numerals are correct.
**Data Flow**: `notes, length_ticks, length_bars, pitched, ppq` → `PatternMetrics` (with `pcs`, key, progression, chords).
**Relationships**: `indexer.pattern_metrics` calls it with the song PPQ; `tag.pattern_tags` and `PatternMetrics.to_dict` (stored in `features`) consume it. Song-level calls omit `ppq`, so only patterns carry chords.

## gms_ma/tag.py
### pattern_tags / _style <!-- ref:gms_ma/tag.py:63-134 -->
**Purpose**: Heuristic tags for a window: `key`, `mode`, `chord`, `harmony`, `energy` and up to three `style` labels.
**Why**: Deliberately coarse and overridable — manual tags beat heuristic ones everywhere via `store.tag_groups` source precedence (`manual > model > heuristic`).
**Data Flow**: `PatternMetrics, is_drums` → `[{kind, value, confidence}]`.
**Relationships**: Called by `indexer._persist_pattern`; surfaced as tag chips in the viewer/library.

### genre_for / emotion_for <!-- ref:gms_ma/tag.py:205-228 -->
**Purpose**: Song-level `genre` (instrument-family weights + tempo/drum modifiers) and a 2-D valence/arousal `emotion` label.
**Why**: Provides a starting classification without an ML stack; `genre_scores` is a weighted matrix so it is easy to tune.
**Data Flow**: `(program counts, has_drums, bpm)` → `(genre, conf)`; `(energy, bpm, is_major, centroid)` → `(valence, arousal, label)`.
**Relationships**: Used by `indexer.song_analysis`.

## gms_ma/export.py
### subset_content / pattern_to_bytes / render_rhythm_skeleton <!-- ref:gms_ma/export.py:55-163 -->
**Purpose**: Render a `PatternContent` back to MIDI bytes, either the full loop, a note subset (harmony/melody), or a rhythm-only skeleton.
**Why**: This is the only place (besides `parse`/`reconstruct`) that knows mido's write side, keeping the model DB-agnostic. `pattern_to_bytes` asserts the program once at the top and adds `LOOP_START`/`LOOP_END` markers for DAW looping; `render_rhythm_skeleton` collapses chords to one attack per onset and clips clap hits to ~a 16th.
**Data Flow**: `PatternContent (+ Song, program, subset)` → `bytes`.
**Relationships**: Called by `indexer._persist_pattern`/`_store_stem` and `reconstruct`.

## gms_ma/store.py
### Repository / schema <!-- ref:gms_ma/store.py:15-89 -->
**Purpose**: SQLite repository owning the schema (`songs`, `tracks`, `patterns`, `placements`, `tempos`, `timesigs`, `tags`, `features`, `stems`, `motif_hits`, `midi_assets`, `index_runs`, `manifests`) and all CRUD.
**Why**: A single file is the source of truth; `check_same_thread=False` plus an explicit `lock` lets the threaded viewer share one connection safely. `_ensure_column` gives cheap forward-compatible migrations.
**Data Flow**: Dataclasses → rows; rows → dicts for the manifest/library.
**Relationships**: Used by `indexer`, `library`, `viewer`, `doctor`, `cli`.

### delete_song <!-- ref:gms_ma/store.py:108-162 -->
**Purpose**: Remove a song and every dependent row (tracks, patterns, placements, assets, stems, motif hits, tags, features, manifests), returning per-table counts.
**Why**: Re-indexing and `delete`/`doctor` must never leave orphans; centralising the cascade avoids drift. Optional `--files` removes the generated `out/<song>/` folder.
**Data Flow**: `name` → `counts dict`.
**Relationships**: Used by `run_index` (delete-before-reindex), `cli.cmd_delete`, `doctor`.

### pattern_bytes / tag_groups <!-- ref:gms_ma/store.py:304-399 -->
**Purpose**: `pattern_bytes` returns a pattern's or stem's MIDI BLOB (falling back to the legacy on-disk file); `tag_groups` returns effective tags per kind with `manual > model > heuristic` precedence.
**Why**: Keeping the BLOB in `midi_assets` makes the DB self-contained; the precedence rule lets manual overrides win everywhere without special-casing readers.
**Data Flow**: `(pattern_id, render)` → `bytes|None`; `(target_type, target_id)` → `{kind: [tag…]}`.
**Relationships**: Used by `library`, `viewer`, `reconstruct`, and the manifest builder.

## gms_ma/indexer.py
### run_index <!-- ref:gms_ma/indexer.py:446-546 -->
**Purpose**: The `index` composition root — parse each file, build the bar map, upsert the song, process it, store the manifest, and report progress/timing via callbacks.
**Why**: Per-file timing (`_fmt_dur`) and a `total time:` line give the CLI/GUI useful feedback; a failed analysis rolls the partial song back with `delete_song` so a corrupt row never persists; `cancel` allows the GUI Stop button.
**Data Flow**: `files, db, out, cfg` → `{total, indexed, placements, unique_patterns, motif_families, elapsed, skipped}`.
**Relationships**: Called by `cli.cmd_index` and `gui._worker`; uses `parse`, `BarMap`, `process_song`, `build_manifest`.

### process_song / _persist_pattern / _persist_motifs / _store_stem <!-- ref:gms_ma/indexer.py:81-279 -->
**Purpose**: Segment one song, compute song-level tags/features, then persist cover segments, variants, motif families, and their stems/tags/features.
**Why**: `_persist_pattern` is idempotent by `(kind, content_id)` so repeats are written once; stems (piano/clap plus harmony/melody when `harmony_split != off`) are stored as BLOBs and optionally written to disk. `_persist_motifs` runs a mixed pass plus melody/harmony layer passes and tags each family with its `layer`.
**Data Flow**: `Song/BarMap/PatternContent` → patterns + placements + motif_hits + stems + tags + features.
**Relationships**: Uses `segment`, `motif`, `harmony.split_layers`, `export`, `features`, `tag`, `store`.

### build_manifest <!-- ref:gms_ma/indexer.py:281-350 -->
**Purpose**: Assemble the JSON view of a song straight from the DB (song row, tempos, timesigs, bars, tags, stats, tracks with patterns/tags/stems/motifs, placements).
**Why**: The viewer reads only this shape, so it stays a stable contract; it is stored in the `manifests` table rather than on disk.
**Data Flow**: `repo, name` → `dict`.
**Relationships**: Served by `viewer.do_GET` `/api/song`; consumed by the timeline JS.

### classify_indexed_ex <!-- ref:gms_ma/indexer.py:394-433 -->
**Purpose**: Split input files into pending / already-indexed / corrupt, treating a corrupt stored song as pending so it is re-parsed.
**Why**: Makes re-indexing idempotent while self-healing corrupt rows; used by the GUI's "Re-index already processed" and "Fix corrupt" flows.
**Data Flow**: `(db_path, files, check_corrupt)` → `(pending, skipped, corrupt)`.
**Relationships**: Uses `corrupt_songs`/`doctor.scan`; used by `gui._start`/`_refresh_processed`.

## gms_ma/reconstruct.py
### reconstruct <!-- ref:gms_ma/reconstruct.py:52-123 -->
**Purpose**: Rebuild a song note-for-note from its stored placements and diff it against the original, printing a match report.
**Why**: This is the validation harness for the whole segmentation idea — if reconstruction matches, the placement model is sound. It reads pattern notes from the DB (BLOB first, legacy file fallback).
**Data Flow**: `repo, song name, out_path` → `report dict (match, mismatches…)`.
**Relationships**: Uses `store.pattern_bytes` / `read_pattern_notes`; exposed via `cli.cmd_reconstruct`.

## gms_ma/library.py
### catalog_rows / cached_catalog <!-- ref:gms_ma/library.py:77-231 -->
**Purpose**: Denormalise every unique pattern into a browsable row (song, track, tags, stems, occurrences, duration) and cache the list per DB mtime.
**Why**: Filtering/facet counts are pure-Python over a small catalog, which keeps OR-within-facet / AND-across-facet semantics simple and testable; the mtime cache avoids rebuilding on every request while staying correct after an index.
**Data Flow**: `repo` → `[row]`; `cached_catalog` memoises by `st_mtime_ns`.
**Relationships**: Used by `viewer._library_get`/`_library_count` and the library JS.

### apply_filters / facet_counts <!-- ref:gms_ma/library.py:244-293 -->
**Purpose**: Filter the catalogue and compute live facet counts (excluding a facet's own selection).
**Why**: Keeps the JIT server paging cheap: the full match set drives counts/songs, but only the requested page of patterns is serialised.
**Data Flow**: `rows, filters` → `matched rows`; `rows, filters` → `{facet: {value: count}}`.
**Relationships**: Used by `_library_get`/`_library_count`.

### resolve_pattern_file / sanitize_midi / retempo_midi <!-- ref:gms_ma/library.py:300-406 -->
**Purpose**: Materialise a pattern/stem to a real path (on-disk file or DB BLOB → temp cache), rewrite it to a single DAW channel, and rewrite its tempo.
**Why**: `resolve_pattern_file` lets download/clipboard flows work even for DB-only stores. `retempo_midi` sets the `set_tempo` meta (inserting one if absent) while leaving note ticks untouched, so an exported file at the export BPM plays at the speed the viewer auditioned.
**Data Flow**: `(repo, pattern_id, render)` → `(row, path)`; `data, bpm` → `bytes`; `src, bpm` → `Path`.
**Relationships**: Used by `viewer._library_download`, the clipboard/copy-ch1/sanitize endpoints, and the DAW export.

```python
def retempo_midi(data: bytes, bpm: float) -> bytes:
    """Return ``data`` (a .mid) with its tempo set so it plays at ``bpm``.

    Note ticks are untouched, so a DAW set to ``bpm`` hears the same speed the
    viewer auditioned (the export-BPM feature).
    """
    us = int(round(60_000_000.0 / max(1.0, float(bpm))))
    mf = mido.MidiFile(file=io.BytesIO(data))
    found = False
    for track in mf.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                msg.tempo = us
                found = True
                break
        if found:
            break
    if not found and mf.tracks:
        mf.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=us, time=0))
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()
```

### pattern_events / pattern_span_seconds / pattern_time_base <!-- ref:gms_ma/library.py:409-481 -->
**Purpose**: Build absolute-time play events for one DB pattern and its loop span.
**Why**: Mirrors the timeline viewer's event building but sources everything from the repository (song PPQ/initial tempo, track channel/program, stem file/BLOB), so library audition and DAW export agree on timing.
**Data Flow**: `(repo, pattern_id, render)` → `[(sec, kind, ch, a, b)]`; `(repo, pattern_id)` → `seconds`.
**Relationships**: Used by `viewer` `/api/library/play` and `/api/play-pattern`.

## gms_ma/viewer.py
### _clamp_speed <!-- ref:gms_ma/viewer.py:52-59 -->
**Purpose**: Clamp a playback speed multiplier to the UI range 0.25–3.
**Why**: The client can send anything; the server must be defensive.
**Data Flow**: `any` → `float in [0.25, 3]` (default 1.0 on parse failure).
**Relationships**: Used by all three play endpoints.

### TickClock <!-- ref:gms_ma/viewer.py:61-88 -->
**Purpose**: Piecewise tempo-map-aware tick→seconds conversion for playback event scheduling.
**Why**: Mirrors `bars.BarMap` timing but is self-contained for the viewer, so it does not depend on the analysis objects.
**Data Flow**: `(ppq, tempos, total_ticks)` → `sec(tick)`.
**Relationships**: Used by `song_play_events`.

### _Player <!-- ref:gms_ma/viewer.py:89-171 -->
**Purpose**: Background thread that schedules MIDI events on a winmm port, honouring a start offset, an optional end hold, and a speed multiplier.
**Why**: The Windows Multimedia API is event-based, so timing is a `sleep` loop against `wall0 + (t - t0)/speed`. A `threading.Event` gives prompt cancellation; `all_notes_off` on exit avoids stuck notes. The port is opened per `play()` so device selection is per-request.
**Data Flow**: `events, t0, end_s, speed, device` → live MIDI; `stop()` cancels.
**Relationships**: Used by `_do_POST` play endpoints; `midi_out` does the raw wire calls.

```python
    def play(self, events: list, t0: float = 0.0, device_id: int = 0,
             end_s: float | None = None, speed: float = 1.0):
        """Schedule ``events`` from ``t0``; keep the port open until ``end_s``.

        ``end_s`` is the transport time (seconds, same basis as ``events`` /
        ``t0``) at which playback should finish — used so a loop audition runs
        for its full loop span even when its last note ends early.  ``speed``
        (0.25..3) scales wall-clock time: 2.0 plays twice as fast.
        """
        self.stop()
        self._stop.clear()
        out = midi_out.WinMidiOut(device_id)
        self._out = out
        self._thread = threading.Thread(
            target=self._run, args=(events, t0, end_s, out, max(0.01, speed)),
            daemon=True,
        )
        self._thread.start()
```

### _Handler (HTTP request handler) <!-- ref:gms_ma/viewer.py:173-600 -->
**Purpose**: A threaded stdlib HTTP server that serves the timeline viewer, the loop library, JSON APIs for playback/clipboard, and static assets.
**Why**: No external web framework or build step is needed; the entire UI is served from a single `assets/` directory. SQLite access is serialised through `repo.lock` to avoid corruption from concurrent requests.
**Data Flow**: HTTP request → `do_GET`/`do_POST` → DB query or file serve → JSON/HTML response.
**Relationships**: Uses `store.Repository`, `indexer.build_manifest`, `library.*`, `midi_out`, `clipboard`; serves `assets/timeline.html` and `assets/library.html`.

## gms_ma/cli.py
### cmd_index <!-- ref:gms_ma/cli.py:30-60 -->
**Purpose**: The `index` subcommand: gather MIDI files, construct an `AnalysisConfig`, run `run_index`, and print a JSON summary.
**Why**: The CLI is the primary non-GUI entry point; it mirrors the GUI's configuration surface so both paths produce identical analyses.
**Data Flow**: `args.paths, args.db, args.out, args.*` → `{total, indexed, placements, unique_patterns, motif_families, elapsed, skipped}`.
**Relationships**: Calls `indexer.gather_midi`, `indexer.run_index`, `library.refresh_library_cache`.

### cmd_list / cmd_manifest / cmd_reconstruct / cmd_export / cmd_delete / cmd_doctor / cmd_optimize <!-- ref:gms_ma/cli.py:62-280 -->
**Purpose**: The remaining subcommands for listing patterns, printing manifests, reconstructing songs, exporting MIDI, deleting songs, scanning DB integrity, and post-processing optimisation.
**Why**: Each subcommand is a thin wrapper around the corresponding module function, keeping the CLI surface flat and testable.
**Data Flow**: `args` → module function → stdout JSON / exit code.
**Relationships**: Each delegates to `store.Repository`, `indexer`, `library`, `reconstruct`, or `doctor`.

## gms_ma/gui.py
### IndexerGUI <!-- ref:gms_ma/gui.py:30-300 -->
**Purpose**: A tkinter desktop GUI for batch-indexing a MIDI folder with a progress bar, log pane, and configuration controls.
**Why**: Provides a visual alternative to the CLI for users who prefer a graphical interface; the worker thread runs `run_index` in the background so the UI stays responsive.
**Data Flow**: UI events → `_start` → `_worker` thread → `_poll` → UI updates; `_open_viewer` launches the HTTP server as a detached process.
**Relationships**: Uses `indexer.gather_midi`, `indexer.classify_indexed_ex`, `indexer.run_index`, `store.Repository`, `library.refresh_library_cache`.

## gms_ma/doctor.py
### song_counts / scan / fixable <!-- ref:gms_ma/doctor.py:14-100 -->
**Purpose**: Scan the DB for corrupted songs (missing tracks, dangling placements, missing assets, invalid manifests) and optionally delete them.
**Why**: Indexing can be interrupted or analysis can fail part-way, leaving orphaned rows; `scan` finds those so the CLI can report or delete them.
**Data Flow**: `repo` → `[{id, name, path, issues}]`; `findings` → fixable subset.
**Relationships**: Used by `cli.cmd_doctor`, `gui._check_db`, `indexer.classify_indexed_ex`.

## gms_ma/clipboard.py
### supported / copy_file_to_clipboard <!-- ref:gms_ma/clipboard.py:14-90 -->
**Purpose**: Windows clipboard file copy via ctypes (`CF_HDROP`), letting a MIDI loop be "copied to the clipboard" as a real file for Ctrl+V into Explorer or a DAW.
**Why**: Avoids third-party dependencies; degrades gracefully on non-Windows (`supported()` returns False).
**Data Flow**: `path` → Windows clipboard (CF_HDROP).
**Relationships**: Used by `viewer._library_download` and `viewer._do_POST` clipboard endpoints.

## gms_ma/midi_out.py
### available / list_outputs / WinMidiOut <!-- ref:gms_ma/midi_out.py:14-100 -->
**Purpose**: Minimal live MIDI output via the Windows Multimedia API (winmm) using ctypes, avoiding third-party MIDI backends.
**Why**: `python-rtmidi` currently has no stable cp314 wheel and can crash on import; the winmm approach is a defensive fallback. If this module is not usable, viewers degrade to visual-only mode automatically.
**Data Flow**: `device_id, events` → live MIDI on a winmm output port.
**Relationships**: Used by `viewer._Player` for live audition; `viewer` checks `midi_out.available()` before enabling audio controls.