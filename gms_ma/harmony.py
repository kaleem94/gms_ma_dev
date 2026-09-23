"""Chord/harmony analysis and harmony/melody layer separation.

Pure functions over note tuples ``(start, dur, pitch, velocity)`` — no mido /
sqlite / file knowledge.  Chords come from onset-merged pitch sets (the same
representation :mod:`motif` uses), labelled with :func:`music_theory.detect_chord`.
"""
from __future__ import annotations

from collections import Counter

from .music_theory import detect_chord, roman_numeral


def merge_events(notes, eps: int):
    """Chord-merge onsets -> ``[(tick, [pitch, ...], end_tick)]``."""
    evs: list[list] = []
    for (start, dur, pitch, _vel) in sorted(notes, key=lambda n: (n[0], n[2])):
        if evs and start - evs[-1][0] <= eps:
            evs[-1][1].append(pitch)
            evs[-1][2] = max(evs[-1][2], start + max(dur, 1))
        else:
            evs.append([start, [pitch], start + max(dur, 1)])
    return [(t, ps, end) for (t, ps, end) in evs]


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


def _onset(n):
    return n.start if hasattr(n, "start") else n[0]


def _pitch(n):
    return n.pitch if hasattr(n, "pitch") else n[2]


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
