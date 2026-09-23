# gms_ma/export.py

## subset_content / pattern_to_bytes / render_rhythm_skeleton <!-- ref:gms_ma/export.py:55-163 -->
**Purpose**: Render a `PatternContent` back to MIDI bytes, either the full loop, a note subset (harmony/melody), or a rhythm-only skeleton.
**Why**: This is the only place (besides `parse`/`reconstruct`) that knows mido's write side, keeping the model DB-agnostic. `pattern_to_bytes` asserts the program once at the top and adds `LOOP_START`/`LOOP_END` markers for DAW looping; `render_rhythm_skeleton` collapses chords to one attack per onset and clips clap hits to ~a 16th.
**Data Flow**: `PatternContent (+ Song, program, subset)` → `bytes`.
**Relationships**: Called by `indexer._persist_pattern`/`_store_stem` and `reconstruct`.

### pattern_to_bytes <!-- ref:gms_ma/export.py:75-115 -->
**Purpose**: Serialise one pattern window to a complete MIDI file with tempo, time signature, and track name metadata.
**Why**: The `LOOP_START`/`LOOP_END` markers embedded in the meta track let DAWs recognise loop boundaries. The program change is asserted once at the top (drums keep channel semantics). Notes are sorted by tick then by off-before-on to ensure correct scheduling.
**Data Flow**: `PatternContent, Song, program` → `bytes` (MIDI file).
**Relationships**: Called by `indexer._persist_pattern`; used by `reconstruct` and `library.resolve_pattern_file`.

### render_rhythm_skeleton <!-- ref:gms_ma/export.py:117-163 -->
**Purpose**: Render a loop's rhythm (onsets/durations) without its pitches, as either a piano skeleton (melodic channel, Acoustic Grand) or a clap skeleton (channel 9, GM Hand Clap).
**Why**: Rhythm skeletons let users hear the groove/pattern independent of the original instrumentation. Chords collapse onto a single hit per onset, preserving the longest duration so overlapping attacks read naturally.
**Data Flow**: `PatternContent, Song, mode, note, drum, clip_ticks` → `bytes` (MIDI file).
**Relationships**: Called by `indexer._persist_pattern` for stem export; used by the viewer for stem audition.

### bytes_to_midi <!-- ref:gms_ma/export.py:165-168 -->
**Purpose**: Parse raw MIDI bytes back into a `mido.MidiFile` object.
**Why**: Used by `reconstruct` and `library` for reading pattern data without a file path.
**Data Flow**: `bytes` → `mido.MidiFile`.
**Relationships**: Used by `reconstruct._load_pattern_notes` and `library.pattern_events`.