"""Shared fixtures/helpers: synthetic MIDI builders + a one-file index helper."""
from __future__ import annotations

import types
from pathlib import Path

import pytest
import mido

from gms_ma.bars import BarMap
from gms_ma.indexer import process_song
from gms_ma.model import Instrument, Note, PitchBend, Song, TempoEvent, TimeSigEvent
from gms_ma.parse import parse_file
from gms_ma.segment import AnalysisConfig, extract_content, segment_song
from gms_ma.store import Repository

PPQ = 480
BAR = PPQ * 4  # one 4/4 bar


# ---------------------------------------------------------------- builders
def _delta_track(events):
    """events: list of (abs_tick, message); returns a MidiTrack with deltas."""
    track = mido.MidiTrack()
    events.sort(key=lambda e: (e[0], 0 if e[1].type in ("note_off",) else 1))
    cursor = 0
    for tick, msg in events:
        msg.time = max(0, tick - cursor)
        cursor = tick
        track.append(msg)
    return track


def part_events(channel, notes, programs=(), bends=()):
    """Absolute-tick events for one instrument part.

    notes: (start_tick, duration, pitch, velocity)
    programs: (tick, program); bends: (tick, value)
    """
    ev = []
    for tick, prog in programs:
        ev.append((tick, mido.Message("program_change", channel=channel, program=prog)))
    for tick, val in bends:
        ev.append((tick, mido.Message("pitchwheel", channel=channel, pitch=val)))
    for (s, d, p, v) in notes:
        ev.append((s, mido.Message("note_on", channel=channel, note=p, velocity=max(1, v))))
        ev.append((s + d, mido.Message("note_off", channel=channel, note=p, velocity=0)))
    return ev


def write_midi(path, parts, *, ppq=PPQ, tempo_us=500000, sig=(4, 4),
               midi_type=1, tempo_changes=()):
    """Build a MIDI file.

    parts: list of dicts {name, channel, notes, programs, bends}
    tempo_changes: list of (tick, us_per_beat) additional tempo events
    """
    mf = mido.MidiFile(type=midi_type, ticks_per_beat=ppq)

    def deltaize(items):
        out = []
        cursor = 0
        for tick, msg in items:
            msg.time = max(0, tick - cursor)
            cursor = tick
            out.append(msg)
        return out

    all_tempo = dict(sorted([(0, tempo_us)] + list(tempo_changes)))
    meta_items = []
    meta_items.append((0, mido.MetaMessage("time_signature", numerator=sig[0],
                                           denominator=sig[1])))
    meta_items.append((0, mido.MetaMessage("track_name", name="test")))
    for tick, us in all_tempo.items():
        meta_items.append((tick, mido.MetaMessage("set_tempo", tempo=us)))

    if midi_type == 0:
        track = mido.MidiTrack()
        parts_events = []
        for part in parts:
            parts_events.extend(
                part_events(part["channel"], part.get("notes", []),
                            part.get("programs", []), part.get("bends", [])))
        for msg in deltaize(meta_items):
            track.append(msg)
        for msg in _delta_track(parts_events):
            track.append(msg)
        mf.tracks.append(track)
    else:
        meta = mido.MidiTrack()
        for msg in deltaize(meta_items):
            meta.append(msg)
        mf.tracks.append(meta)
        for part in parts:
            tr = mido.MidiTrack()
            tr.append(mido.MetaMessage("track_name", name=part.get("name", ""), time=0))
            events = part_events(part["channel"], part.get("notes", []),
                                 part.get("programs", []), part.get("bends", []))
            for msg in _delta_track(events):
                tr.append(msg)
            mf.tracks.append(tr)
    mf.save(str(path))
    return path


def drum_pattern(bar_count=8, start_bar=0):
    """1-bar drum loop: kick on 1, snare on beat 3, hihat 16ths."""
    notes = []
    for b in range(bar_count):
        t0 = (start_bar + b) * BAR
        notes.append((t0, 20, 36, 110))                      # kick
        notes.append((t0 + BAR // 2, 20, 38, 100))           # snare
        for g in range(4):                                   # hats on 8ths
            notes.append((t0 + (g * BAR // 4), 15, 42, 80))
    return notes


def chord_bars(bar_count=8, start_bar=0, prog=48, base_pitch=60):
    """4-bar chord progression repeating after 4 bars (whole-note triads)."""
    chords = [(60, 64, 67), (62, 65, 69), (57, 60, 64), (55, 59, 62)]
    notes = []
    for b in range(bar_count):
        t0 = (start_bar + b) * BAR
        for pc in chords[b % 4]:
            notes.append((t0, BAR - 10, base_pitch + (pc - 60), 90))
    return notes


def repetitive_song(path, bar_count=8, extra_unique_part=False):
    """A classic 8-bar pop arrangement: 1-bar drums + 4-bar pad chords."""
    pad_notes = []
    chords = [(60, 64, 67), (62, 65, 69), (57, 60, 64), (55, 59, 62)]
    for b in range(bar_count):
        t0 = b * BAR
        for pc in chords[b % 4]:
            pad_notes.append((t0, BAR - 10, pc, 90))
    parts = [
        {"name": "drums", "channel": 9, "notes": drum_pattern(bar_count)},
        {"name": "strings", "channel": 0, "notes": pad_notes,
         "programs": [(0, 48)]},
        {"name": "bass", "channel": 1, "notes": [(b * BAR, BAR // 2, 36, 100)
                                                 for b in range(bar_count)],
         "programs": [(0, 33)]},
    ]
    if extra_unique_part:
        # a non-repeating lead so nothing repeats: each bar gets a new note
        parts.append({"name": "lead", "channel": 2,
                      "notes": [(b * BAR, BAR // 4, 60 + b, 90) for b in range(bar_count)],
                      "programs": [(0, 81)]})
    return write_midi(path, parts)


# ----------------------------------------------------------------- index
def index_one(db_path, midi_path, out_dir, cfg=None) -> Song:
    """Run the whole index pipeline for a single file; returns parsed Song."""
    cfg = cfg or AnalysisConfig()
    repo = Repository(db_path)
    song = parse_file(midi_path)
    bm = BarMap(song)
    repo.delete_song(song.name)
    s = types.SimpleNamespace(
        name=song.name, path=song.path, midi_type=song.midi_type,
        ticks_per_beat=song.ticks_per_beat, total_ticks=song.total_ticks,
        seconds=round(bm.second_at_tick(song.total_ticks), 3),
        bpm=round(60_000_000.0 / song.initial_tempo, 1),
        notes=song.total_note_ons(), drum_notes=song.drum_notes,
        num_instruments=len(song.instruments),
    )
    sid = repo.upsert_song(s)
    for e in song.tempos:
        repo.add_tempo(sid, e.tick, e.us_per_beat)
    for e in song.time_signatures:
        repo.add_timesig(sid, e.tick, e.numerator, e.denominator)
    out_dir.mkdir(parents=True, exist_ok=True)
    process_song(repo, song, bm, Path(out_dir), cfg, sid)
    repo.close()
    return song


@pytest.fixture
def tmp_midi(tmp_path):
    return tmp_path / "test.mid"


@pytest.fixture
def song_path(tmp_path):
    p = tmp_path / "arrangement.mid"
    repetitive_song(p, bar_count=8)
    return p
