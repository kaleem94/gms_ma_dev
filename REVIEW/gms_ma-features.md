# gms_ma/features.py

## PatternMetrics / analyze_notes <!-- ref:gms_ma/features.py:17-151 -->
**Purpose**: Numeric descriptors for a note window or song — onsets, density, occupancy, legato/staccato, syncopation, polyphony, pitch stats, key, and (when `ppq` is given) harmony summary.
**Why**: Pure functions over `(start_rel, duration, pitch, velocity)` tuples keep this trivially unit-testable and free of mido/sqlite. Key detection runs first, then `analyze_harmony` is given that key so roman numerals are correct.
**Data Flow**: `notes, length_ticks, length_bars, pitched, ppq` → `PatternMetrics` (with `pcs`, key, progression, chords).
**Relationships**: `indexer.pattern_metrics` calls it with the song PPQ; `tag.pattern_tags` and `PatternMetrics.to_dict` (stored in `features`) consume it. Song-level calls omit `ppq`, so only patterns carry chords.

### PatternMetrics.to_dict <!-- ref:gms_ma/features.py:45-65 -->
**Purpose**: Serialise the metrics into a flat dict for storage in the `features` table.
**Why**: JSON-serialisable output makes it easy to store in SQLite and retrieve for the viewer/library UI without recomputing.
**Data Flow**: `PatternMetrics` → `dict` with rounded numeric values.
**Relationships**: Called by `indexer._persist_pattern` via `repo.set_feature`.

### energy_score <!-- ref:gms_ma/features.py:153-158 -->
**Purpose**: Compute a 0–1 rough intensity score from velocity, onset density, and occupancy.
**Why**: A simple weighted combination that provides a heuristic energy label used by both pattern-level and song-level tagging.
**Data Flow**: `PatternMetrics` → `float` in [0, 1].
**Relationships**: Called by `tag._energy_label` and `indexer.song_analysis`.

### analyze_notes (key detection path) <!-- ref:gms_ma/features.py:90-145 -->
**Purpose**: When `pitched=True`, compute pitch-class histogram, detect key/mode via Krumhansl–Schmuckler, and run chord analysis.
**Why**: The key detection feeds `analyze_harmony` so roman numerals are correct relative to the detected key; the chord analysis is only run when `ppq` is provided (i.e., for pattern-level analysis, not song-level).
**Data Flow**: `notes, pitched=True, ppq` → `PatternMetrics` with `pcs`, `key_tonic`, `key_mode`, `key_corr`, `chords`, `progression`, `top_chord`.
**Relationships**: Calls `music_theory.detect_key` and `harmony.analyze_harmony`.