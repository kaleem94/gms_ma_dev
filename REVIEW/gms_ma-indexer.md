# gms_ma/indexer.py

## run_index <!-- ref:gms_ma/indexer.py:446-546 -->
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

### song_analysis <!-- ref:gms_ma/indexer.py:55-80 -->
**Purpose**: Aggregate features/tags for a whole song (used for song-level tags).
**Why**: Computes genre, energy, emotion, key/mode from the aggregated note data across all instruments; the pitched metrics are computed separately from the overall metrics so drum notes don't skew key detection.
**Data Flow**: `Song, BarMap` → `dict` with bpm, tempo_class, genre, energy, mode, key, emotion, valence, arousal.
**Relationships**: Called by `process_song`; uses `features.analyze_notes`, `tag.genre_for`, `tag.emotion_for`.

### gather_midi / _norm_path <!-- ref:gms_ma/indexer.py:352-392 -->
**Purpose**: Recursively collect every `.mid`/`.midi` under the given files/folders, deduplicating by absolute normalised path.
**Why**: `_norm_path` uses `os.path.normcase` for case-insensitive comparison on Windows, preventing the same file from being indexed twice when it appears under different path casings.
**Data Flow**: `paths: list` → `list[Path]` (deduplicated).
**Relationships**: Called by `cli.cmd_index` and `gui._start`.