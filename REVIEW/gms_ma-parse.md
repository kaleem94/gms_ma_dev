# gms_ma/parse.py

## parse_file / parse_bytes / _song_from_mf <!-- ref:gms_ma/parse.py:16-110 -->
**Purpose**: The only module that knows mido's file format on the read side. `parse_file` wraps `mido.MidiFile`, `parse_bytes` reads from memory, and `_song_from_mf` walks tracks/messages into the neutral model.
**Why**: mido raises on genuinely corrupt files; those are converted to a single `ParseError` so `run_index` can skip a bad file with a warning instead of aborting the whole run.
**Data Flow**: For each track it tracks a running `tick` from message deltas, records `note_on`/`note_off` pairs into `Instrument.notes`, tempo and time-signature meta into `Song`, and program changes into `Instrument.programs`. Defaults a `4/4` time signature when none is present.
**Relationships**: Feeds `model.Song`; errors surfaced by `indexer.run_index` (`ParseError` → skipped list) and `cli`.

### _song_from_mf internals <!-- ref:gms_ma/parse.py:40-110 -->
**Purpose**: The core parsing loop that processes every MIDI message and builds the neutral model.
**Why**: The function uses `defaultdict` for channel streams and processes messages in tick order, accumulating note-on pairs and closing them on note-off. Control/aftertouch/sysex are intentionally dropped as they are not needed for loop analysis. Notes still ringing at EOF are closed with `max(absolute_end, start + 1)` to avoid zero-length notes.
**Data Flow**: `mido.MidiFile` → `model.Song` with `Instrument.notes`, `TempoEvent`s, `TimeSigEvent`s.
**Relationships**: Called by `parse_file` and `parse_bytes`; the `ParseError` exception class is consumed by `indexer.run_index`.

```python
def parse_file(path: str | Path) -> Song:
    """Parse ``path`` into a :class:`Song`, raising :class:`ParseError` on failure."""
    path = Path(path)
    try:
        mf = mido.MidiFile(str(path))
    except Exception as exc:  # mido raises several ValueError/OSError flavours
        raise ParseError(f"unreadable MIDI: {exc}") from exc
    return _song_from_mf(mf, path.stem, str(path))
```