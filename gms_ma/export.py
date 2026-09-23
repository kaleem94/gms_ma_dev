"""Render :class:`PatternContent` back to playable MIDI bytes (via mido).

The only mido knowledge here beyond :mod:`parse` lives in this module and
:mod:`reconstruct`, keeping the model and DB agnostic of the file format.
"""
from __future__ import annotations

import io

import mido

from .model import Song
from .segment import PatternContent


def _meta_track(content: PatternContent, song: Song, label: str) -> mido.MidiTrack:
    meta = mido.MidiTrack()
    meta.append(mido.MetaMessage("set_tempo", tempo=song.initial_tempo, time=0))
    sig = song.initial_time_signature
    meta.append(
        mido.MetaMessage(
            "time_signature",
            numerator=sig.numerator,
            denominator=sig.denominator,
            time=0,
        )
    )
    meta.append(mido.MetaMessage("track_name", name=(label or "loop")[:60], time=0))
    meta.append(mido.MetaMessage("marker", text="LOOP_START", time=0))
    meta.append(mido.MetaMessage("marker", text="LOOP_END", time=content.length_ticks))
    return meta


def _note_track(events, channel: int, program: int | None) -> mido.MidiTrack:
    """events: list of (tick, 'on'/'off', note, vel)."""
    data = mido.MidiTrack()
    if program is not None:
        data.append(mido.Message("program_change", channel=channel, program=program, time=0))
    ordered: list[tuple[int, int, tuple]] = []  # (tick, order, payload)
    for (tick, kind, note, vel) in events:
        ordered.append((tick, 0 if kind == "off" else 1, (kind, note, vel)))
    ordered.sort()
    cursor = 0
    for tick, _o, (kind, note, vel) in ordered:
        delta = max(0, tick - cursor)
        cursor = tick
        if kind == "off":
            data.append(mido.Message("note_off", channel=channel, note=note, velocity=0, time=delta))
        else:
            data.append(mido.Message("note_on", channel=channel, note=note,
                                     velocity=max(1, vel), time=delta))
    return data


def subset_content(content: PatternContent, notes) -> PatternContent:
    """A copy of ``content`` keeping only the given ``(start,dur,pitch,vel)`` notes."""
    wanted = set(notes)
    return PatternContent(
        track_index=content.track_index,
        channel=content.channel,
        length_ticks=content.length_ticks,
        notes=[n for n in content.notes if n in wanted],
        pitch_bends=list(content.pitch_bends),
        length_bars=content.length_bars,
    )


def pattern_to_bytes(
    content: PatternContent,
    song: Song,
    program: int | None,
    include_markers: bool = True,
    label: str = "",
) -> bytes:
    """Serialise one pattern window.  ``program`` is the GM patch to assert."""
    mf = mido.MidiFile(type=1, ticks_per_beat=song.ticks_per_beat)
    mf.tracks.append(_meta_track(content, song, label))

    events: list[tuple[int, str, int, int]] = []
    for (start, dur, pitch, vel) in content.notes:
        events.append((start, "on", pitch, vel))
        events.append((start + dur, "off", pitch, 0))
    for (tdelta, value) in content.pitch_bends:
        events.append((tdelta, "bend", value, 0))
    events.sort(key=lambda e: (e[0], 0 if e[1] == "off" else 1))

    data = mido.MidiTrack()
    # a program is asserted once at the top (drums keep channel semantics)
    if program is not None and content.channel != 9:
        data.append(mido.Message("program_change", channel=content.channel, program=program, time=0))
    cursor = 0
    for tick, kind, a, b in events:
        delta = max(0, tick - cursor)
        cursor = tick
        if kind == "on":
            data.append(mido.Message("note_on", channel=content.channel, note=a,
                                     velocity=max(1, b), time=delta))
        elif kind == "off":
            data.append(mido.Message("note_off", channel=content.channel, note=a, velocity=0, time=delta))
        else:  # pitch bend (value in mido's signed convention)
            data.append(mido.Message("pitchwheel", channel=content.channel, pitch=a, time=delta))
    mf.tracks.append(data)

    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def render_rhythm_skeleton(
    content: PatternContent,
    song: Song,
    mode: str,
    note: int = 60,
    drum: int = 39,
    clip_ticks: int | None = None,
    label: str = "",
) -> bytes:
    """Render a loop's rhythm (onsets/durations) without its pitches.

    ``mode='piano'`` plays every attack as a single ``note`` (default C4) on a
    melodic channel with Acoustic Grand; ``mode='clap'`` plays every attack as
    a short percussive hit (default GM Hand Clap key 39) on channel 9.
    """
    if mode not in ("piano", "clap"):
        raise ValueError(f"unknown skeleton mode: {mode}")
    if mode == "piano":
        channel = content.channel if content.channel != 9 else 0
        program = 0
        pnote = note
        clip = None
    else:
        channel = 9
        program = None
        pnote = drum
        ppq = max(1, song.ticks_per_beat)
        clip = clip_ticks if clip_ticks is not None else max(1, ppq // 4)  # ~16th

    # one attack per onset (chords collapse onto a single hit), preserving the
    # longest duration so overlapping attacks read naturally
    onsets: dict[int, list[tuple[int, int]]] = {}  # start -> [(dur, vel)]
    for (start, dur, _pitch, vel) in content.notes:
        onsets.setdefault(start, []).append((dur, vel))
    strikes: list[tuple[int, int, int]] = []
    for start, entries in onsets.items():
        dur = max(d for d, _ in entries)
        vel = max(v for _, v in entries)
        if clip is not None:
            dur = min(dur, clip)
        strikes.append((start, dur, vel))

    mf = mido.MidiFile(type=1, ticks_per_beat=song.ticks_per_beat)
    mf.tracks.append(_meta_track(content, song, label or mode))

    events: list[tuple[int, str, int, int]] = []
    for (start, dur, vel) in strikes:
        events.append((start, "on", pnote, vel))
        events.append((start + max(1, dur), "off", pnote, 0))
    mf.tracks.append(_note_track(events, channel, program))

    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def bytes_to_midi(data: bytes) -> mido.MidiFile:
    buf = io.BytesIO(data)
    buf.seek(0)
    return mido.MidiFile(file=buf)
