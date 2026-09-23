# gms_ma/bars.py

## _bar_ticks <!-- ref:gms_ma/bars.py:10-20 -->
**Purpose**: Compute the tick length of one bar from the song's ticks-per-beat and the time signature.
**Why**: Encodes the `4/4`-style `ticks_per_beat * 4 / denominator * numerator` relationship in one place. A previous bug where non-4/4 meters produced zero-length bars (and hung the segmenter) lives at this boundary. The `max(1, ...)` guards prevent zero-length bars for unusual denominators.
**Data Flow**: `(ticks_per_beat, TimeSigEvent)` → `int` ticks per bar.
**Relationships**: Used by `BarMap._build`.

### BarMap <!-- ref:gms_ma/bars.py:21-143 -->
**Purpose**: Piecewise tick→bar→second conversion across the song's tempo and time-signature maps.
**Why**: Tempo can change mid-song, so seconds are not linear in ticks; `_tempo_segments` caches a piecewise `(tick_from, tick_to, seconds_per_tick)` table and `second_at_tick` / `tick_at_second` binary-search it. The `bars` list is memoised so repeated layout calls stay cheap.
**Data Flow**: `Song` → `bars list`, and `tick` → `bar`, `tick` → `seconds`, `seconds` → `tick`, `(start,end)` → `span_bars`.
**Relationships**: Used by `segment`, `motif`, `indexer`, `viewer.TickClock` (which reimplements the same idea for playback), and the timeline JS via the manifest's `bars` array.

### _build <!-- ref:gms_ma/bars.py:45-90 -->
**Purpose**: Build the complete bar list by walking the song tick-by-tick, adopting time signatures in force and splitting bars when a signature change falls mid-bar.
**Why**: A guard of 200,000 iterations prevents infinite loops on malformed files. The `_bar_ticks` function handles the denominator conversion correctly (the old integer `4 // denominator` was a bug for denominators > 4).
**Data Flow**: `Song.time_signatures, Song.ticks_per_beat` → `list[(start_tick, ticks_in_bar)]`.
**Relationships**: Called by `BarMap.bars` property; uses `_bar_ticks`.

### second_at_tick / tick_at_second <!-- ref:gms_ma/bars.py:92-143 -->
**Purpose**: Convert between ticks and wall-clock seconds using the tempo map.
**Why**: Tempo changes mean seconds are not linear in ticks; the piecewise `_tempo_segments` table makes these conversions O(log n) via binary search in `_build` and O(n) in `second_at_tick`.
**Data Flow**: `tick` → `float seconds`; `seconds` → `int tick`.
**Relationships**: Used by `viewer.TickClock` for playback scheduling.

```python
def _bar_ticks(ticks_per_beat: int, ts: TimeSigEvent) -> int:
    """Ticks in one bar of the given signature (quarter = ticks_per_beat).

    A ``1/denominator`` note is ``ticks_per_beat * 4 / denominator`` ticks; the
    old integer ``4 // denominator`` yielded 0 for denominators > 4 (e.g. 12/16),
    which produced zero-length bars and an effectively hung viewer.
    """
    beat_ticks = max(1, round(ticks_per_beat * 4 / max(1, ts.denominator)))
    return max(1, ts.numerator * beat_ticks)
```