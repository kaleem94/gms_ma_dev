"""Regression tests for the folder-index pipeline + GUI module wiring."""
from pathlib import Path

from gms_ma import gui as gui_mod
from gms_ma.indexer import classify_indexed, gather_midi, run_index
from gms_ma.store import Repository
from tests.conftest import write_midi


def _two_files(tmp_path):
    a = tmp_path / "songs" / "a.mid"
    b = tmp_path / "songs" / "sub" / "b.MID"
    a.parent.mkdir(parents=True, exist_ok=True)
    b.parent.mkdir(parents=True, exist_ok=True)
    write_midi(a, parts=[{"name": "x", "channel": 0, "notes": [(0, 480, 60, 100)]}])
    write_midi(b, parts=[{"name": "y", "channel": 9, "notes": [(0, 60, 36, 100)]}])
    return a, b


def test_gather_midi_recursive(tmp_path):
    a, b = _two_files(tmp_path)
    files = gather_midi([tmp_path])
    assert {str(f) for f in files} == {str(a), str(b)}


def test_run_index_rolls_back_failed_analysis(tmp_path, monkeypatch):
    from gms_ma import indexer

    midi = tmp_path / "x.mid"
    write_midi(midi, parts=[{"name": "x", "channel": 0, "notes": [(0, 480, 60, 100)]}])
    db = str(tmp_path / "db.sqlite")

    def boom(*_args, **_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(indexer, "process_song", boom)
    summary = indexer.run_index([midi], db=db, out=str(tmp_path / "out"))
    assert summary["indexed"] == 0 and len(summary["skipped"]) == 1
    repo = Repository(db)
    assert repo.all_songs() == []          # no partial/corrupt song left behind
    repo.close()


def test_classify_reparses_corrupt_song(tmp_path):
    from gms_ma.indexer import classify_indexed_ex, run_index

    midi = tmp_path / "a.mid"
    write_midi(midi, parts=[{"name": "x", "channel": 0, "notes": [(0, 480, 60, 100)]}])
    db = str(tmp_path / "db.sqlite")
    out = str(tmp_path / "out")
    run_index([midi], db=db, out=out)

    # corrupt it: drop its tracks (a partial index)
    repo = Repository(db)
    sid = repo.song_id("a")
    repo.conn.execute("DELETE FROM tracks WHERE song_id=?", (sid,))
    repo.conn.commit()
    repo.close()

    pending, skipped, corrupt = classify_indexed_ex(db, [midi])
    assert [p.name for p in pending] == ["a.mid"]   # moved out of the skip set
    assert skipped == []
    assert [c.name for c in corrupt] == ["a.mid"]

    # re-parsing repairs the song
    run_index([midi], db=db, out=out)
    repo = Repository(db)
    sid = repo.song_id("a")
    tracks = repo.conn.execute(
        "SELECT COUNT(*) c FROM tracks WHERE song_id=?", (sid,)).fetchone()["c"]
    assert tracks > 0
    repo.close()


def test_run_index_progress(tmp_path):
    a, b = _two_files(tmp_path)
    calls = []
    summary = run_index(
        [a, b],
        db=str(tmp_path / "db.sqlite"),
        out=str(tmp_path / "out"),
        on_file=lambda cur, total, path, status: calls.append((cur, total, status)),
    )
    assert summary["total"] == 2 and summary["indexed"] == 2
    assert len(calls) == 2 and calls[-1][0] == 2 and calls[-1][1] == 2
    assert all(c[2] == "ok" for c in calls)
    assert (tmp_path / "out").is_dir()
    assert (tmp_path / "db.sqlite").exists()


def test_run_index_logs_per_file_and_total_time(tmp_path):
    a, b = _two_files(tmp_path)
    logs = []
    summary = run_index(
        [a, b],
        db=str(tmp_path / "db.sqlite"),
        out=str(tmp_path / "out"),
        on_log=logs.append,
    )
    assert summary["elapsed"] >= 0
    assert sum(1 for m in logs if m.startswith("ok ")) == 2   # one line per file
    assert any(m.startswith("total time:") for m in logs)     # and a final total
    assert any("a.mid" in m for m in logs)


def test_gui_module_importable():
    assert hasattr(gui_mod, "IndexerGUI")
    assert hasattr(gui_mod, "main")


def test_gui_log_tags_prefix():
    assert set(gui_mod.LOG_LEVELS) == {"info", "warning", "error"}

    class FakeLog:
        def __init__(self):
            self.parts = []

        def config(self, **_k):
            pass

        def insert(self, _where, text, tag=None):
            self.parts.append((text, tag))

        def see(self, *_a):
            pass

    class FakeSelf:
        log = FakeLog()

    gui_mod.IndexerGUI._log(FakeSelf(), "boom", "error")
    assert FakeSelf.log.parts[0] == ("[error] ", "error")
    assert FakeSelf.log.parts[1] == ("boom\n", None)
    # unknown levels fall back to info
    FakeSelf.log.parts.clear()
    gui_mod.IndexerGUI._log(FakeSelf(), "hello", "nonsense")
    assert FakeSelf.log.parts[0] == ("[info] ", "info")


def test_classify_indexed_skips_exact_path(tmp_path):
    a, b = _two_files(tmp_path)
    db = str(tmp_path / "db.sqlite")
    run_index([a], db=db, out=str(tmp_path / "out"))
    pending, skipped = classify_indexed(db, [a, b])
    assert pending == [b]
    assert skipped == [a]
    # re-index flag semantics are decided by the caller, not the classifier
    pending_all, skipped_all = classify_indexed(db, [a])
    assert pending_all == [] and skipped_all == [a]


def test_classify_indexed_missing_db_is_all_pending(tmp_path):
    a, b = _two_files(tmp_path)
    pending, skipped = classify_indexed(str(tmp_path / "nope.db"), [a, b])
    assert len(pending) == 2 and skipped == []
    assert (tmp_path / "nope.db").exists() is False  # classifier never creates it
