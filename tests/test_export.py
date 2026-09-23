"""export tests: pattern -> MIDI bytes with correct channel/program/markers."""
import mido

from gms_ma import export as ex
from gms_ma.model import Song, TempoEvent, TimeSigEvent
from gms_ma.segment import PatternContent


def _song(ppq=480, tempo=500000):
    s = Song(name="x", total_ticks=ppq * 4, ticks_per_beat=ppq)
    s.tempos = [TempoEvent(0, tempo)]
    s.time_signatures = [TimeSigEvent(0, 4, 4)]
    return s


def test_export_round_trip_channel_program_ppq():
    content = PatternContent(
        track_index=0, channel=2, length_ticks=1920,
        notes=[(0, 480, 60, 100), (480, 480, 64, 90)],
        pitch_bends=[(240, 6000)],
    )
    data = ex.pattern_to_bytes(content, _song(), program=81, label="test")
    mf = mido.MidiFile(file=__import__("io").BytesIO(data))
    assert mf.ticks_per_beat == 480
    msgs = [m for t in mf.tracks for m in t]
    programs = [m for m in msgs if m.type == "program_change"]
    assert programs and programs[0].channel == 2 and programs[0].program == 81
    ons = sorted((m.note, m.channel) for m in msgs if m.type == "note_on")
    assert ons == [(60, 2), (64, 2)]
    bends = [m for m in msgs if m.type == "pitchwheel"]
    assert bends and bends[0].pitch == 6000
    markers = [m for t in mf.tracks for m in t
               if m.is_meta and m.type == "marker"]
    assert {m.text for m in markers} == {"LOOP_START", "LOOP_END"}


def test_export_drums_no_program():
    content = PatternContent(
        track_index=0, channel=9, length_ticks=480,
        notes=[(0, 20, 36, 110)], pitch_bends=[],
    )
    data = ex.pattern_to_bytes(content, _song(), program=None)
    mf = mido.MidiFile(file=__import__("io").BytesIO(data))
    msgs = [m for t in mf.tracks for m in t]
    assert not [m for m in msgs if m.type == "program_change"]
