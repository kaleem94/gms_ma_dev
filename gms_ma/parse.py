"""Parse MIDI files into the neutral model (the only module that knows mido)."""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import mido

from .model import Instrument, Note, PitchBend, Song, TempoEvent, TimeSigEvent


class ParseError(Exception):
    """Raised when a file cannot be parsed.  Callers treat it as skippable."""


def parse_file(path: str | Path) -> Song:
    """Parse ``path`` into a :class:`Song`, raising :class:`ParseError` on failure."""
    path = Path(path)
    try:
        mf = mido.MidiFile(str(path))
    except Exception as exc:  # mido raises several ValueError/OSError flavours
        raise ParseError(f"unreadable MIDI: {exc}") from exc
    return _song_from_mf(mf, path.stem, str(path))


def parse_bytes(data: bytes, name: str = "loop") -> Song:
    """Parse an in-memory MIDI payload (e.g. a BLOB) into a :class:`Song`."""
    import io

    try:
        mf = mido.MidiFile(file=io.BytesIO(data))
    except Exception as exc:
        raise ParseError(f"unreadable MIDI bytes: {exc}") from exc
    return _song_from_mf(mf, name, "")


def _song_from_mf(mf: mido.MidiFile, name: str, path: str) -> Song:
    if mf.type not in (0, 1, 2):
        raise ParseError(f"unsupported MIDI type {mf.type}")

    ppq = mf.ticks_per_beat or 480
    song = Song(
        name=name,
        path=path,
        midi_type=mf.type,
        ticks_per_beat=ppq,
        total_ticks=1,
    )

    track_names: dict[int, str] = {}
    tempo_seen = False
    sig_seen = False
    absolute_end = 0

    # channel streams: (ti, ch) -> dict of "program" list, "bend" list, note stacks
    programs: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    bends: dict[tuple[int, int], list[PitchBend]] = defaultdict(list)
    stacks: dict[tuple[int, int], dict[int, list[tuple[int, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    raw_notes: dict[tuple[int, int], list[tuple[int, int, int, int, int]]] = defaultdict(
        list
    )  # (start, end, pitch, vel)

    for ti, track in enumerate(mf.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            absolute_end = max(absolute_end, tick)
            if msg.is_meta:
                if msg.type == "track_name" and msg.name:
                    track_names[ti] = msg.name
                elif msg.type == "set_tempo":
                    tempo_seen = True
                    song.tempos.append(TempoEvent(tick=tick, us_per_beat=msg.tempo))
                elif msg.type == "time_signature":
                    sig_seen = True
                    song.time_signatures.append(
                        TimeSigEvent(
                            tick=tick, numerator=msg.numerator, denominator=msg.denominator
                        )
                    )
                continue

            if msg.type == "program_change":
                programs[(ti, msg.channel)].append((tick, msg.program))
            elif msg.type == "pitchwheel":
                bends[(ti, msg.channel)].append(PitchBend(tick=tick, value=msg.pitch))
            elif msg.type == "note_on" and msg.velocity > 0:
                stacks[(ti, msg.channel)][msg.note].append((tick, msg.velocity))
            elif msg.type in ("note_off", "note_on"):  # note_on velocity 0 == note_off
                pile = stacks[(ti, msg.channel)].get(msg.note)
                if pile:
                    start, vel = pile.pop()
                    raw_notes[(ti, msg.channel)].append((start, tick, msg.note, vel))
            # control/aftertouch/sysex are intentionally dropped

    # close notes still ringing at the end of the file
    for key, per_pitch in stacks.items():
        for pitch, pile in per_pitch.items():
            for start, vel in pile:
                raw_notes[key].append((start, max(absolute_end, start + 1), pitch, vel))

    song.total_ticks = max(absolute_end + 1, 1)
    if not tempo_seen:
        song.tempos.append(TempoEvent(tick=0, us_per_beat=500000))
    if not sig_seen:
        song.time_signatures.append(TimeSigEvent(tick=0, numerator=4, denominator=4))
    song.tempos.sort(key=lambda e: e.tick)
    song.time_signatures.sort(key=lambda e: e.tick)
    # many files place meta slightly after tick 0; normalise the leading event to 0
    for tlist in (song.tempos, song.time_signatures):
        if tlist and tlist[0].tick > 0 and tlist[0].tick <= ppq * 2:
            tlist[0].tick = 0

    # assemble instruments
    for (ti, ch) in sorted(set(list(programs) + list(bends) + list(raw_notes))):
        notes = [
            Note(start=s, end=max(e, s + 1), pitch=p, velocity=v)
            for (s, e, p, v) in raw_notes[(ti, ch)]
        ]
        if not notes:
            continue
        inst = Instrument(
            track_index=ti,
            channel=ch,
            name=track_names.get(ti, ""),
            notes=notes,
            programs=sorted(programs[(ti, ch)]),
            pitch_bends=bends[(ti, ch)],
        )
        song.instruments.append(inst)

    if not song.instruments:
        raise ParseError("no playable notes found")
    return song
