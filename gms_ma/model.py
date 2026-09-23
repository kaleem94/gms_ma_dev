"""Neutral in-memory MIDI model used across the pipeline.

Kept deliberately free of mido, sqlite and file concerns so every stage
(parse -> segment -> export -> store -> tag -> view) talks through plain
dataclasses.  This is the practical SOLID seam of the project.
"""
from __future__ import annotations

from dataclasses import dataclass, field


DRUM_CHANNEL = 9  # GM channel index for percussion (mido uses 0-based)


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


@dataclass
class PitchBend:
    """A pitch wheel change at an absolute tick (mido convention: -8192..8191)."""

    tick: int
    value: int


@dataclass
class Instrument:
    """Everything MIDI emitted on one (track, channel) pair."""

    track_index: int
    channel: int
    name: str = ""
    notes: list[Note] = field(default_factory=list)
    pitch_bends: list[PitchBend] = field(default_factory=list)
    programs: list[tuple[int, int]] = field(default_factory=list)  # (tick, program)

    def __post_init__(self) -> None:
        self.notes.sort(key=lambda n: (n.start, n.pitch))
        self.pitch_bends.sort(key=lambda p: p.tick)
        self.programs.sort(key=lambda p: p[0])

    @property
    def is_drums(self) -> bool:
        return self.channel == DRUM_CHANNEL

    def program_at(self, tick: int) -> int | None:
        chosen: int | None = None
        for t, prog in self.programs:
            if t <= tick:
                chosen = prog
            else:
                break
        return chosen

    @property
    def program(self) -> int | None:
        """First program seen (convention for library metadata)."""
        return self.programs[0][1] if self.programs else None

    @property
    def start_tick(self) -> int:
        return self.notes[0].start if self.notes else 0

    @property
    def end_tick(self) -> int:
        if not self.notes:
            return 0
        return max(n.end for n in self.notes)

    @property
    def uses_pitch_bend(self) -> bool:
        return bool(self.pitch_bends)

    def notes_in_window(self, start: int, end: int) -> list[Note]:
        """Notes whose *attack* falls inside [start, end)."""
        return [n for n in self.notes if start <= n.start < end]

    def bends_in_window(self, start: int, end: int) -> list[PitchBend]:
        return [b for b in self.pitch_bends if start <= b.tick < end]


@dataclass
class TempoEvent:
    tick: int
    us_per_beat: int


@dataclass
class TimeSigEvent:
    tick: int
    numerator: int
    denominator: int  # stored as power value (4 = quarter note)


@dataclass
class Song:
    name: str
    path: str = ""
    midi_type: int = 1
    ticks_per_beat: int = 480
    total_ticks: int = 0
    tempos: list[TempoEvent] = field(default_factory=list)
    time_signatures: list[TimeSigEvent] = field(default_factory=list)
    instruments: list[Instrument] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tempos.sort(key=lambda e: e.tick)
        self.time_signatures.sort(key=lambda e: e.tick)
        self.instruments.sort(key=lambda i: (i.track_index, i.channel))

    @property
    def initial_tempo(self) -> int:
        return self.tempos[0].us_per_beat if self.tempos else 500000

    @property
    def initial_time_signature(self) -> TimeSigEvent:
        return self.time_signatures[0] if self.time_signatures else TimeSigEvent(0, 4, 4)

    @property
    def drum_notes(self) -> int:
        return sum(len(i.notes) for i in self.instruments if i.is_drums)

    def instrument(self, track_index: int, channel: int) -> Instrument | None:
        for i in self.instruments:
            if i.track_index == track_index and i.channel == channel:
                return i
        return None

    def total_note_ons(self) -> int:
        return sum(len(i.notes) for i in self.instruments)
