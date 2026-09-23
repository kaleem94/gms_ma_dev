# gms_ma/music_theory.py

## detect_key <!-- ref:gms_ma/music_theory.py:17-30 -->
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

### _corr <!-- ref:gms_ma/music_theory.py:35-45 -->
**Purpose**: Pearson correlation coefficient between two pitch-class distributions.
**Why**: Used by `detect_key` to score how well a key profile matches the observed pitch-class histogram; the correlation is the confidence metric returned to callers.
**Data Flow**: `[float]` (normalised histograms) → `float` in [-1, 1].
**Relationships**: Called by `detect_key`.