"""Numeric descriptors for a note window or a whole song.

Pure functions over note tuples ``(start_rel, duration, pitch, velocity)`` —
no mido / sqlite / file knowledge, so trivially unit-testable.
"""
from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field

from .music_theory import detect_key, pitch_class_histogram


@dataclass
class PatternMetrics:
    onsets: int = 0
    length_ticks: int = 1
    length_bars: int = 1
    velocity_mean: float = 0.0
    velocity_std: float = 0.0
    onsets_per_bar: float = 0.0
    occupancy: float = 0.0            # fraction of time at least one note sounds
    legato_ratio: float = 0.0         # notes >= a 16th in length
    staccato_ratio: float = 0.0       # notes <  a 16th in length
    syncopation: float = 0.0          # onsets off the 16th grid gridlines
    mean_polyphony: float = 0.0
    pitch_lo: int = 0
    pitch_hi: int = 0
    pitch_range: int = 0
    pitch_centroid: float = 0.0
    pitch_spread: float = 0.0
    key_tonic: int = 0
    key_mode: str = "major"
    key_corr: float = 0.0
    progression: str = ""
    top_chord: str = ""
    chord_changes_per_bar: float = 0.0
    chordal_ratio: float = 0.0
    chords: list = field(default_factory=list)
    pcs: Counter = field(default_factory=Counter)

    @property
    def is_pitched(self) -> bool:
        return bool(self.pcs)

    def to_dict(self) -> dict:
        return {
            "onsets": self.onsets,
            "onsets_per_bar": round(self.onsets_per_bar, 3),
            "velocity_mean": round(self.velocity_mean, 2),
            "velocity_std": round(self.velocity_std, 2),
            "occupancy": round(self.occupancy, 3),
            "legato_ratio": round(self.legato_ratio, 3),
            "staccato_ratio": round(self.staccato_ratio, 3),
            "syncopation": round(self.syncopation, 3),
            "mean_polyphony": round(self.mean_polyphony, 2),
            "pitch_range": self.pitch_range,
            "pitch_centroid": round(self.pitch_centroid, 1),
            "pitch_spread": round(self.pitch_spread, 1),
            "key": key_label(self),
            "top_chord": self.top_chord,
            "progression": self.progression,
            "chord_changes_per_bar": round(self.chord_changes_per_bar, 3),
            "chordal_ratio": round(self.chordal_ratio, 3),
        }


def key_label(m: PatternMetrics) -> str:
    from .music_theory import key_name

    return key_name(m.key_tonic, m.key_mode)


def analyze_notes(notes, length_ticks: int, length_bars: int, pitched: bool = True,
                  ppq: int | None = None) -> PatternMetrics:
    """``notes``: list of (start_rel, dur, pitch, vel) tuples in one window."""
    m = PatternMetrics(length_ticks=max(length_ticks, 1), length_bars=max(length_bars, 1))
    if not notes:
        return m
    m.onsets = len(notes)
    bar = m.length_ticks / m.length_bars
    sixteenth = max(1.0, bar / 4.0)
    velocities = [n[3] for n in notes]
    m.velocity_mean = statistics.fmean(velocities)
    m.velocity_std = statistics.pstdev(velocities) if len(velocities) > 1 else 0.0
    m.onsets_per_bar = m.onsets / m.length_bars

    # occupancy over the window
    covered = 0
    events: list[tuple[int, int]] = []
    for (s, d, p, v) in notes:
        events.append((s, +1))
        events.append((s + max(d, 1), -1))
    events.sort()
    cur = 0
    prev = events[0][0]
    for t, delta in events:
        if cur > 0 and t > prev:
            covered += t - prev
        cur += delta
        prev = t
    m.occupancy = min(1.0, covered / m.length_ticks)

    dur_16ths = [max(0.0, n[1]) / sixteenth for n in notes]
    m.legato_ratio = sum(1 for d in dur_16ths if d >= 1) / len(notes)
    m.staccato_ratio = sum(1 for d in dur_16ths if d < 1) / len(notes)

    # syncopation: onsets that do not land on a 16th gridline of the bar
    off = 0
    for (s, d, p, v) in notes:
        pos_in_bar = s % bar if bar > 0 else 0.0
        grid = round(pos_in_bar / sixteenth)
        if abs(pos_in_bar - grid * sixteenth) > sixteenth * 0.12:
            off += 1
    m.syncopation = off / len(notes)

    # polyphony: active notes at each onset tick
    starts = [n[0] for n in notes]
    active: list[float] = []
    for t in starts:
        cnt = sum(1 for (s, d, p, v) in notes if s <= t < s + max(d, 1))
        active.append(cnt)
    m.mean_polyphony = statistics.fmean(active) if active else 0.0

    pitches = [n[2] for n in notes]
    m.pitch_lo = min(pitches)
    m.pitch_hi = max(pitches)
    m.pitch_range = m.pitch_hi - m.pitch_lo
    m.pitch_centroid = statistics.fmean(pitches)
    m.pitch_spread = statistics.pstdev(pitches) if len(pitches) > 1 else 0.0

    if pitched:
        pc = Counter()
        for (s, d, p, v) in notes:
            pc[int(p) % 12] += max(0.0, v / 127.0) * max(1.0, d / 10.0)
        m.pcs = pc
        tonic, mode, corr = detect_key(pc)
        m.key_tonic, m.key_mode, m.key_corr = tonic, mode, corr
        if ppq:
            from .harmony import analyze_harmony

            h = analyze_harmony(notes, ppq, tonic, mode, m.length_bars)
            m.chords = h["chords"]
            m.progression = h["progression"]
            m.top_chord = h["top_chord"]
            m.chord_changes_per_bar = h["changes_per_bar"]
            m.chordal_ratio = h["chordal_ratio"]
    return m


def energy_score(m: PatternMetrics) -> float:
    """0..1 rough intensity: velocity, onset density and occupancy."""
    vel = m.velocity_mean / 127.0
    density = min(1.0, m.onsets_per_bar / 12.0)
    occ = m.occupancy
    return min(1.0, max(0.0, 0.35 * vel + 0.4 * density + 0.25 * occ))


def mean_pitch(notes) -> float:
    return statistics.fmean(n[2] for n in notes) if notes else 0.0


def pc_histogram(notes) -> Counter:
    c: Counter = Counter()
    for (s, d, p, v) in notes:
        c[int(p) % 12] += max(0.0, v / 127.0) * max(1.0, d / 10.0)
    return c
