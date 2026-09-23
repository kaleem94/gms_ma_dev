"""Rhythm-skeleton stems: exporter behaviour + index registration + manifest."""
from pathlib import Path

import mido

from gms_ma import export as ex
from gms_ma.model import Song, TempoEvent, TimeSigEvent
from gms_ma.segment import AnalysisConfig, PatternContent
from tests.conftest import index_one, repetitive_song


def _song(ppq=480, tempo=500000):
    s = Song(name="x", total_ticks=ppq * 4, ticks_per_beat=ppq)
    s.tempos = [TempoEvent(0, tempo)]
    s.time_signatures = [TimeSigEvent(0, 4, 4)]
    return s


def _msgs(data):
    mf = mido.MidiFile(file=__import__("io").BytesIO(data))
    return [m for t in mf.tracks for m in t]


def _abs_msgs(data):
    mf = mido.MidiFile(file=__import__("io").BytesIO(data))
    out = []
    for t in mf.tracks:
        tick = 0
        for m in t:
            tick += m.time
            out.append((tick, m))
    return out


def test_piano_skeleton_single_note_melodic():
    content = PatternContent(track_index=0, channel=2, length_ticks=1920,
                             notes=[(0, 480, 60, 100), (480, 240, 64, 90)],
                             pitch_bends=[])
    data = ex.render_rhythm_skeleton(content, _song(), "piano", note=60)
    msgs = _msgs(data)
    progs = [m for m in msgs if m.type == "program_change"]
    assert progs and progs[0].channel == 2 and progs[0].program == 0
    ons = [m for m in msgs if m.type == "note_on"]
    assert len(ons) == 2
    assert {m.note for m in ons} == {60}                 # every attack C4
    assert all(m.channel == 2 for m in ons)
    # durations preserved (offsets at 480 and 720 in absolute ticks)
    off_ticks = sorted(t for t, m in _abs_msgs(data) if m.type == "note_off")
    assert off_ticks == [480, 720]


def test_clap_skeleton_drum_channel_short():
    content = PatternContent(track_index=0, channel=2, length_ticks=1920,
                             notes=[(0, 960, 60, 100)], pitch_bends=[])
    data = ex.render_rhythm_skeleton(content, _song(), "clap", drum=39)
    msgs = _msgs(data)
    assert not [m for m in msgs if m.type == "program_change"]
    ons = [m for m in msgs if m.type == "note_on"]
    assert len(ons) == 1
    assert ons[0].channel == 9 and ons[0].note == 39     # GM Hand Clap
    offs = [m for m in msgs if m.type == "note_off"]
    # clipped to ~a 16th (120 ticks at ppq 480), not the original 960
    assert offs[0].time <= 120


def test_index_registers_stems_and_manifest(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    db = tmp_path / "db.sqlite"
    out = tmp_path / "out"
    index_one(db, midi, out)
    from gms_ma.indexer import build_manifest
    from gms_ma.store import Repository

    repo = Repository(db)
    name = repo.all_songs()[0]["name"]
    m = build_manifest(repo, name)
    stems = []
    for tr in m["tracks"]:
        for p in tr["patterns"]:
            stems += p["stems"]
    assert len(stems) > 0
    for st in stems:
        assert st["render"] in ("piano", "clap", "harmony", "melody")
        assert Path(st["file"]).exists()
    # every stem row has a sibling file on disk
    rows = repo.conn.execute("SELECT COUNT(*) c FROM stems").fetchone()["c"]
    assert rows == len(stems)
    repo.close()


def test_no_stems_skips_files(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    db = tmp_path / "db2.sqlite"
    out = tmp_path / "out2"
    index_one(db, midi, out, cfg=AnalysisConfig(stems=False))
    from gms_ma.store import Repository

    repo = Repository(db)
    rows = repo.conn.execute("SELECT COUNT(*) c FROM stems").fetchone()["c"]
    assert rows == 0
    piano_files = list(Path(out).rglob("*_piano.mid"))
    assert not piano_files
    repo.close()
