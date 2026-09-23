"""Chord/harmony detection + harmony/melody layer separation."""
import json

from gms_ma import library as lib
from gms_ma.harmony import analyze_harmony, split_layers
from gms_ma.music_theory import detect_chord, roman_numeral
from gms_ma.store import Repository
from tests.conftest import index_one, repetitive_song


def _n(start, dur, pitch, vel=100):
    return (start, dur, pitch, vel)


# ----------------------------------------------------------------- pure
def test_detect_chord_triads_and_inversions():
    root, quality, label, _bass, conf = detect_chord([60, 64, 67])       # C E G
    assert (root, quality, label) == (0, "maj", "C")
    assert conf > 0.9

    root, quality, label, _bass, _c = detect_chord([57, 60, 64])         # A C E
    assert (root, quality, label) == (9, "min", "Am")

    # first inversion: E in the bass under C major
    root, quality, label, bass, _c = detect_chord([52, 60, 64, 67])
    assert (root, quality, label, bass) == (0, "maj", "C/E", 4)


def test_detect_chord_sevenths_power_and_nc():
    assert detect_chord([60, 64, 67, 71])[2] == "Cmaj7"
    assert detect_chord([55, 59, 62, 65])[2] == "G7"          # G B D F
    assert detect_chord([60, 67])[2] == "C5"                  # power chord
    assert detect_chord([60])[2] == "N.C."
    assert detect_chord([])[2] == "N.C."


def test_roman_numerals_in_c_major():
    assert roman_numeral(0, "maj", 0, "major") == "I"
    assert roman_numeral(9, "min", 0, "major") == "vi"
    assert roman_numeral(7, "7", 0, "major") == "V7"
    assert roman_numeral(2, "min7", 0, "major") == "ii7"


def test_analyze_harmony_progression():
    notes = []
    prog = [(60, 64, 67), (57, 60, 64), (53, 57, 60), (55, 59, 62)]
    for i, pitches in enumerate(prog):                       # one bar, 4 triads
        for p in pitches:
            notes.append(_n(i * 480, 480, p))
    h = analyze_harmony(notes, ppq=480, key_tonic=0, key_mode="major", length_bars=1)
    assert h["top_chord"] == "C"
    assert h["progression"] == "C - Am - F - G"
    assert h["chordal_ratio"] == 1.0
    assert h["changes_per_bar"] == 4.0


def test_split_layers_top_and_onset_and_off():
    notes = [_n(0, 480, 60), _n(0, 480, 64), _n(0, 480, 67), _n(480, 480, 62)]
    melody, harmony = split_layers(notes, "top")
    assert [n[2] for n in melody] == [67, 62]                # top note per onset
    assert sorted(n[2] for n in harmony) == [60, 64]

    melody, harmony = split_layers(notes, "onset")
    assert [n[2] for n in melody] == [62]                    # single-note onset
    assert sorted(n[2] for n in harmony) == [60, 64, 67]     # the chord onset

    melody, harmony = split_layers(notes, "off")
    assert len(melody) == 4 and harmony == []


# ------------------------------------------------------------- integration
def test_index_emits_chord_tags_layers_and_stems(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=8)
    db = tmp_path / "db.sqlite"
    index_one(db, midi, tmp_path / "out")
    repo = Repository(db)
    try:
        row = repo.conn.execute(
            "SELECT p.id FROM patterns p JOIN tracks t ON t.id=p.track_id "
            "WHERE t.is_drums=0 AND p.kind='cover' LIMIT 1").fetchone()
        assert row is not None
        pid = row["id"]

        tags = repo.tag_groups("pattern", pid)
        assert "chord" in tags and "harmony" in tags

        payload = json.loads(repo.conn.execute(
            "SELECT payload FROM features WHERE target_type='pattern' AND target_id=?",
            (pid,)).fetchone()["payload"])
        assert payload["top_chord"] and payload["progression"]

        stems = {s["render"] for s in repo.stems_for(pid)}
        assert {"harmony", "melody"} <= stems
        assert lib.pattern_events(repo, pid, render="melody")   # stem is playable

        n_layer = repo.conn.execute(
            "SELECT COUNT(*) c FROM tags WHERE target_type='pattern' AND kind='layer'"
        ).fetchone()["c"]
        assert n_layer > 0
    finally:
        repo.close()
