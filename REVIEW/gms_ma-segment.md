# gms_ma/segment.py

## AnalysisConfig <!-- ref:gms_ma/segment.py:89-123 -->
**Purpose**: The single tuning surface for loop/cover detection and motif extraction (engine, lens, grid coverage, gap thresholds, stems, harmony split).
**Why**: A dataclass keeps the strategy seam explicit and lets the CLI, GUI and tests construct the same object; `variant_lengths` derives the 1×/2×/4×/8× ladder.
**Data Flow**: Passed from `cli.cmd_index` / `gui._start` / tests into `segment_song` and `indexer`.
**Relationships**: Read throughout `indexer.process_song`, `_persist_pattern`, `_persist_motifs`; also imported by the GUI.

### PatternContent <!-- ref:gms_ma/segment.py:32-49 -->
**Purpose**: The serialisable musical content of one window — `notes` as `(start_delta, duration, pitch, velocity)`, `pitch_bends`, `length_ticks`, `length_bars`, channel/track.
**Why**: A content-addressed id (`content_id`) hashes the window so identical repeats deduplicate into one `patterns` row across all placements. Removing static pitch-bend runs keeps ids stable and files small.
**Data Flow**: Built by `segment`/`motif`; hashed into `content_id`; rendered by `export`.
**Relationships**: Central currency between `segment`, `motif`, `export`, `indexer`, `store`.

### CoverSegment / LoopVariant / SegResult <!-- ref:gms_ma/segment.py:51-87 -->
**Purpose**: Result types for the segmentation pipeline: `CoverSegment` is an atomic non-overlapping cover chunk; `LoopVariant` is an alternative (usually longer) loop; `SegResult` aggregates all results for one instrument.
**Why**: Keeping these as dataclasses makes the pipeline testable and serialisable; `SegResult.notes_covered` provides a quick quality metric.
**Data Flow**: `segment_instrument` → `SegResult` → `indexer.process_song` persists cover/variant/motif patterns.
**Relationships**: Consumed by `indexer._persist_pattern` and `indexer._persist_motifs`.

### segment_instrument / segment_song <!-- ref:gms_ma/segment.py:551-655 -->
**Purpose**: Detect repeating bar-aligned material per instrument and return a `SegResult` of cover segments, variants and confidence.
**Why**: Uses an adaptive grid (coarsest subdivision matching `grid_cov` of onsets) plus two match lenses (`pitch` vs `rhythm`) and three engines (`period` tiles-from-start, `repeat` largest-block, `hybrid` = period → repeat → silence-gap splitting). This avoids forcing a fixed 16th grid on swing/humanised material.
**Data Flow**: `(Song, BarMap, AnalysisConfig)` → `[SegResult]`; `_active_blocks` finds silence-delimited sound blocks so loops never bridge a full-bar rest.
**Relationships**: Called by `indexer.process_song`; motif mining reuses `_active_blocks`.

### _finalize_bar_gap_loops <!-- ref:gms_ma/segment.py:430-549 -->
**Purpose**: Apply the bar-gap loop rule to a finished engine result: trim leading/trailing silence from cover windows, split windows containing internal silence >= `loop_gap_bars` bars, and edge-trim engine ladder variants.
**Why**: This is the post-processing step that ensures loops are silence-free and reconstruction is tick-exact. The `_active_blocks` function finds sound blocks split by long silences, and each cover window is re-based to its note span.
**Data Flow**: `SegResult, BarMap, Instrument, AnalysisConfig` → `SegResult` (mutated in place).
**Relationships**: Called at the end of every engine path in `segment_instrument`.

### _bar_ids / choose_grid_ticks <!-- ref:gms_ma/segment.py:200-300 -->
**Purpose**: Build a per-bar identity sequence using an adaptive grid, where each bar gets an integer id based on its pitch content (pitch lens) or onset rhythm (rhythm lens).
**Why**: The adaptive grid picks the coarsest subdivision that still lines up `grid_cov` of onsets, so swing and humanised material collapse cleanly. The `unique_empty` flag prevents empty bars from masquerading as musical repeats in the repeat engine.
**Data Flow**: `BarMap, Instrument, AnalysisConfig` → `[int]` bar ids.
**Relationships**: Called by `segment_instrument`; `choose_grid_ticks` is used by the motif miner for its tatum quantisation.

### tile_period / repeat_motif / _lrs <!-- ref:gms_ma/segment.py:330-430 -->
**Purpose**: `tile_period` finds the smallest bar-period that tiles from the first event; `repeat_motif` finds the largest exact repeating block anywhere (longest-first, then non-overlapping occurrences placed); `_lrs` implements the core longest-repeated-substring search using rolling hashes.
**Why**: The rolling hash (`_roll`) gives O(n) bucket candidates, then a binary search over window length finds the maximum feasible length; equal runs are hash-verified to avoid collisions. The period engine tiles from the start; the repeat engine can find repeats anywhere.
**Data Flow**: `[int]` bar ids → `int` period or `(L, anchor, [(start,end)])` repeat.
**Relationships**: Core engines used by `segment_instrument`; `_lrs` is also used by the motif miner's `_best_repeat_events`.

```python
def segment_song(song: Song, bm: BarMap, cfg: AnalysisConfig) -> list[SegResult]:
    return [segment_instrument(i, song, bm, cfg) for i in song.instruments]
```