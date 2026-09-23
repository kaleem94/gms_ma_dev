# gms_ma/harmony.py

## merge_events <!-- ref:gms_ma/harmony.py:14-24 -->
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