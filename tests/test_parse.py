"""parse tests: channel splitting, note normalisation, corrupt files."""
import mido
import pytest

from gms_ma.parse import ParseError, parse_file
from tests.conftest import write_midi


def test_type1_split_and_notes(tmp_path):
    path = tmp_path / "a.mid"
    write_midi(
        path,
        parts=[
            {"name": "keys", "channel": 0, "programs": [(0, 5)],
             "notes": [(0, 120, 60, 100), (480, 240, 64, 90)]},
            {"name": "drums", "channel": 9,
             "notes": [(0, 60, 36, 110), (240, 60, 42, 90)]},
        ],
    )
    song = parse_file(path)
    assert len(song.instruments) == 2
    keys = next(i for i in song.instruments if not i.is_drums)
    drums = next(i for i in song.instruments if i.is_drums)
    assert keys.channel == 0 and drums.channel == 9
    assert keys.program == 5
    assert keys.program_at(0) == 5
    assert len(keys.notes) == 2 and len(drums.notes) == 2
    n = keys.notes[0]
    assert (n.start, n.pitch, n.velocity) == (0, 60, 100)
    assert keys.notes[1].duration == 240


def test_type0_multi_channel(tmp_path):
    path = tmp_path / "type0.mid"
    write_midi(
        path,
        parts=[
            {"name": "", "channel": 0, "notes": [(0, 120, 60, 100)]},
            {"name": "", "channel": 2, "notes": [(0, 120, 67, 100)]},
        ],
        midi_type=0,
    )
    song = parse_file(path)
    assert {i.channel for i in song.instruments} == {0, 2}
    assert song.total_note_ons() == 2


def test_pitch_bend_captured(tmp_path):
    path = tmp_path / "bend.mid"
    write_midi(path, parts=[{"name": "lead", "channel": 3, "programs": [(0, 81)],
                             "notes": [(0, 480, 72, 100)],
                             "bends": [(0, 0), (240, 1000)]}])
    song = parse_file(path)
    lead = song.instruments[0]
    assert lead.uses_pitch_bend
    assert [(b.tick, b.value) for b in lead.pitch_bends] == [(0, 0), (240, 1000)]


def test_velocity_zero_is_off(tmp_path):
    path = tmp_path / "v0.mid"
    mf = mido.MidiFile(type=1, ticks_per_beat=480)
    mf.tracks.append(mido.MidiTrack())
    tr = mido.MidiTrack()
    tr.append(mido.Message("note_on", channel=0, note=60, velocity=100, time=0))
    tr.append(mido.Message("note_on", channel=0, note=60, velocity=0, time=240))
    mf.tracks.append(tr)
    mf.save(str(path))
    song = parse_file(path)
    assert len(song.instruments) == 1
    n = song.instruments[0].notes[0]
    assert n.duration == 240 and n.velocity == 100


def test_corrupt_file_raises(tmp_path):
    bad = tmp_path / "bad.mid"
    bad.write_bytes(b"MThd\x00\x00\x00\x06not really")
    with pytest.raises(ParseError):
        parse_file(bad)
