"""Pitch-class statistics: key / mode detection (Krumhansl-Schmuckler)."""
from __future__ import annotations

from collections import Counter

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

# Krumhansl-Kessler probe tone profiles
_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]


def key_name(tonic: int, mode: str) -> str:
    return f"{NOTE_NAMES[tonic % 12]} {mode}"


def detect_key(pc_weights: Counter) -> tuple[int, str, float]:
    """Return (tonic, 'major'|'minor', correlation) from weighted pitch-class counts."""
    hist = [float(pc_weights.get(pc, 0.0)) for pc in range(12)]
    total = sum(hist)
    if total <= 0:
        return 0, "major", 0.0
    hist = [h / total for h in hist]
    best = (0, "major", -1.0)
    for tonic in range(12):
        for mode, prof in (("major", _MAJOR), ("minor", _MINOR)):
            corr = _corr([hist[(tonic + i) % 12] for i in range(12)], prof)
            if corr > best[2]:
                best = (tonic, mode, corr)
    return best


def pitch_class_histogram(pitches_durations) -> Counter:
    """``pitches_durations`` = iterable of (pitch, weight)."""
    c: Counter = Counter()
    for pitch, weight in pitches_durations:
        c[int(pitch) % 12] += max(0.0, weight)
    return c


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _corr(a, b) -> float:
    n = len(a)
    ma, mb = _mean(a), _mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    denom = (va * vb) ** 0.5
    return cov / denom if denom else 0.0


# --------------------------------------------------------------------------
# chord detection
# --------------------------------------------------------------------------

# (quality, intervals-from-root, label suffix).  Ordered so common qualities
# win ties over their enharmonic/colour variants.
_CHORD_TEMPLATES = [
    ("maj", (0, 4, 7), ""),
    ("min", (0, 3, 7), "m"),
    ("dim", (0, 3, 6), "dim"),
    ("aug", (0, 4, 8), "aug"),
    ("sus2", (0, 2, 7), "sus2"),
    ("sus4", (0, 5, 7), "sus4"),
    ("5", (0, 7), "5"),
    ("6", (0, 4, 7, 9), "6"),
    ("m6", (0, 3, 7, 9), "m6"),
    ("add9", (0, 2, 4, 7), "add9"),
    ("maj7", (0, 4, 7, 11), "maj7"),
    ("min7", (0, 3, 7, 10), "m7"),
    ("7", (0, 4, 7, 10), "7"),
    ("m7b5", (0, 3, 6, 10), "m7b5"),
    ("dim7", (0, 3, 6, 9), "dim7"),
    ("9", (0, 2, 4, 7, 10), "9"),
    ("maj9", (0, 2, 4, 7, 11), "maj9"),
    ("min9", (0, 2, 3, 7, 10), "m9"),
]

_MINOR_QUALITIES = {"min", "min7", "m7b5", "dim", "dim7", "m6", "min9"}
_ROMAN_SUFFIX = {"maj7": "maj7", "min7": "7", "7": "7", "m7b5": "ø7",
                 "dim7": "°7", "dim": "°", "aug": "+"}


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


_MAJOR_SCALE = (0, 2, 4, 5, 7, 9, 11)
_MINOR_SCALE = (0, 2, 3, 5, 7, 8, 10)
_ROMAN = ("I", "II", "III", "IV", "V", "VI", "VII")


def roman_numeral(root: int, quality: str, key_tonic: int, key_mode: str) -> str:
    """Functional roman numeral of a chord relative to a key (e.g. ``V7``, ``vi``)."""
    degree = (root - key_tonic) % 12
    scale = _MINOR_SCALE if key_mode == "minor" else _MAJOR_SCALE
    idx, diff = 0, 99
    for i, s in enumerate(scale):
        d = min((degree - s) % 12, (s - degree) % 12)
        if d < diff:
            idx, diff = i, d
    accidental = ""
    if diff == 1:
        accidental = "b" if (scale[idx] - degree) % 12 == 1 else "#"
    base = _ROMAN[idx]
    if quality in _MINOR_QUALITIES:
        base = base.lower()
    return accidental + base + _ROMAN_SUFFIX.get(quality, "")
