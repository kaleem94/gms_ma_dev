"""CLI `doctor`: flag and (with --delete) purge corrupted songs."""
import types

from gms_ma import doctor as doc
from gms_ma.cli import main
from gms_ma.store import Repository
from tests.conftest import index_one, repetitive_song


def _corrupt(tmp_path):
    db = tmp_path / "db.sqlite"
    out = tmp_path / "out"
    good = tmp_path / "good.mid"
    repetitive_song(good, bar_count=2)
    index_one(db, good, out)
    repo = Repository(db)
    s = types.SimpleNamespace(name="corrupt", path=str(tmp_path / "corrupt.mid"),
                              midi_type=1, ticks_per_beat=480, total_ticks=10,
                              seconds=0.1, bpm=120.0, notes=1, drum_notes=0,
                              num_instruments=1)
    repo.upsert_song(s)
    repo.close()
    return db, out


def test_scan_flags_incomplete_song(tmp_path):
    db, _out = _corrupt(tmp_path)
    repo = Repository(db)
    findings = doc.scan(repo)
    names = {f["name"] for f in findings}
    assert "corrupt" in names
    assert "good" not in names
    issues = next(f["issues"] for f in findings if f["name"] == "corrupt")
    assert any("no tracks" in i for i in issues)
    repo.close()


def test_doctor_delete_removes_corrupt_song(tmp_path):
    db, _out = _corrupt(tmp_path)
    rc = main(["doctor", "--db", str(db), "--delete", "--yes"])
    assert rc == 0
    repo = Repository(db)
    assert repo.song_id("corrupt") is None
    assert repo.song_id("good") is not None
    repo.close()


def test_doctor_without_delete_keeps_song(tmp_path):
    db, _out = _corrupt(tmp_path)
    rc = main(["doctor", "--db", str(db)])
    assert rc == 0
    repo = Repository(db)
    assert repo.song_id("corrupt") is not None
    repo.close()


def test_scan_deep_only_parses_manifests_when_asked(tmp_path):
    db = tmp_path / "db.sqlite"
    out = tmp_path / "out"
    good = tmp_path / "good.mid"
    repetitive_song(good, bar_count=2)
    index_one(db, good, out)

    repo = Repository(db)
    sid = repo.song_id("good")
    run = repo.start_run()
    repo.conn.execute(
        "INSERT OR REPLACE INTO manifests(run_id, song_id, payload) VALUES(?,?,?)",
        (run, sid, "{not valid json"))
    repo.conn.commit()
    repo.close()

    repo = Repository(db)
    shallow = doc.scan(repo, deep=False)
    deep = doc.scan(repo, deep=True)
    repo.close()
    # a structurally healthy song is not flagged by the shallow scan ...
    assert not any(f["name"] == "good" for f in shallow)
    # ... but the deep scan catches the broken manifest payload
    issues = next(f["issues"] for f in deep if f["name"] == "good")
    assert any("manifest" in i for i in issues)


def test_fixable_filters_missing_sources(tmp_path):
    present = tmp_path / "present.mid"
    present.write_bytes(b"MThd")
    findings = [
        {"name": "a", "path": str(present)},
        {"name": "b", "path": str(tmp_path / "missing.mid")},
        {"name": "c", "path": None},
    ]
    assert [f["name"] for f in doc.fixable(findings)] == ["a"]
