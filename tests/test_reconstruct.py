"""End-to-end tests: index -> placements -> reconstruct round trip."""
import json
from pathlib import Path

from gms_ma.bars import BarMap
from gms_ma import reconstruct as rc
from gms_ma.store import Repository
from tests.conftest import index_one, repetitive_song

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


def test_synthetic_round_trip(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=8)
    db = tmp_path / "db.sqlite"
    out = tmp_path / "out"
    song = index_one(db, midi, out)
    name = song.name
    repo = Repository(db)
    sid = repo.song_id(name)
    # structure sanity: cover placements present and tagged patterns exist
    placements = repo.placements_for(sid)
    assert placements, "expected placements in DB"
    tags = repo.conn.execute(
        "SELECT count(*) c FROM tags WHERE target_type='pattern' AND source='heuristic'"
    ).fetchone()["c"]
    assert tags > 0
    # every placement must reference an existing pattern file
    for pl in placements:
        pat = repo.pattern(pl["pattern_id"])
        assert Path(pat["file"]).exists(), pat["file"]
    report = rc.reconstruct(repo, name, tmp_path / "rebuilt.mid")
    assert report["match"] is True
    assert report["orig_notes"] == report["rebuilt_notes"]
    # reconstructed file parses and has the same channel count
    from gms_ma.parse import parse_file

    rebuilt = parse_file(tmp_path / "rebuilt.mid")
    assert rebuilt.total_note_ons() == song.total_note_ons()
    repo.close()


def test_synthetic_manifest_json(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    db = tmp_path / "db.sqlite"
    out = tmp_path / "out"
    index_one(db, midi, out)
    repo = Repository(db)
    from gms_ma.indexer import build_manifest

    m = build_manifest(repo, repo.all_songs()[0]["name"])
    assert m["song"]["notes"] == m["song"]["notes"] > 0
    assert m["bars"]
    assert m["tracks"] and m["placements"]
    # json-serialisable
    json.dumps(m)
    repo.close()


def test_real_file_round_trip_eb_star():
    f = SAMPLE / "eb-Star_Dragon_Tower.mid"
    if not f.exists():
        return
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "db.sqlite"
        index_one(db, f, Path(td) / "out")
        repo = Repository(db)
        report = rc.reconstruct(repo, f.stem, Path(td) / "rebuilt.mid")
        repo.close()
        assert report["match"] is True


def test_corrupt_file_skipped(tmp_path):
    import pytest
    from gms_ma.parse import ParseError, parse_file

    bad = tmp_path / "bad.mid"
    bad.write_bytes(b"MThd\x00\x00\x00\x06\x01\x00nope\x00")
    with pytest.raises(ParseError):
        parse_file(bad)
