# gms_ma/model.py

## Note / PitchBend / Instrument <!-- ref:gms_ma/model.py:16-90 -->
**Purpose**: The neutral in-memory MIDI model — one sounding `Note` (`start`/`end` ticks, `pitch`, `velocity`), a `PitchBend` event, and an `Instrument` that aggregates all notes/bends/programs for one `(track_index, channel)` pair.
**Why**: Keeping the model free of mido/sqlite/file concerns is the project's SOLID seam: every later stage talks through these dataclasses, so swapping the parser or the exporter is localised.
**Data Flow**: `parse` produces `Instrument.notes`; `__post_init__` sorts notes by `(start, pitch)`, bends by tick and programs by tick, so downstream code can assume ordering. `program_at(tick)` scans the program list (last change at or before `tick`); `is_drums` is `channel == 9`.
**Relationships**: Consumed by `segment`, `motif`, `features`, `indexer`. See `Song` below.

### Note <!-- ref:gms_ma/model.py:24-35 -->
**Purpose**: One sounding note with absolute tick positions, MIDI pitch, and velocity.
**Why**: `duration` is a computed property (`end - start`) rather than stored, avoiding redundancy and keeping the dataclass minimal.
**Data Flow**: `(start, end, pitch, velocity)` → `Note`; `duration` is derived.
**Relationships**: Sorted into `Instrument.notes`; consumed by `segment.extract_content`, `motif._events`, `features.analyze_notes`.

### Instrument <!-- ref:gms_ma/model.py:52-90 -->
**Purpose**: Aggregates everything MIDI emitted on one `(track, channel)` pair: notes, pitch bends, and program changes.
**Why**: Sorting in `__post_init__` guarantees that `notes_in_window` and `bends_in_window` can use binary-search-friendly ordering. `program_at(tick)` does a linear scan which is fine for the small number of program changes per track.
**Data Flow**: Built by `parse._song_from_mf`; consumed by `segment.segment_instrument`, `motif.mine_channel`, `features.analyze_notes`, `indexer.process_song`.
**Relationships**: See `Song` below; `is_drums` property is used throughout the pipeline to route drum channels differently.

### Song <!-- ref:gms_ma/model.py:107-141 -->
**Purpose**: Root aggregate: song identity plus tempo/time-signature maps and the list of `Instrument`s.
**Why**: Tempo/time-signature events are kept as sorted lists rather than a compiled map so the pure `BarMap` can do the heavy lifting; `initial_tempo` / `initial_time_signature` expose the common single-tempo case.
**Data Flow**: Built by `parse._song_from_mf`; `__post_init__` sorts tempos, time signatures and instruments. `total_note_ons()` and `drum_notes` are cheap aggregate counters used for the `songs` row.
**Relationships**: Input to `bars.BarMap`, `segment.segment_song`, `indexer.song_analysis`, `export`.

```python
@dataclass
class Note:
    """One sounding note.  ``start``/``end`` are absolute ticks."""
    start: int
    end: int
    pitch: int
    velocity: int

    @property
    def duration(self) -> int:
        return self.end - self.start
```