"""DB-embedded MIDI assets: storage, DB-only reconstruction, export."""
from argparse import Namespace
from pathlib import Path

from gms_ma import cli
from gms_ma.segment import AnalysisConfig
from gms_ma.store import Repository
from tests.conftest import index_one, repetitive_song


def _indexed(tmp_path, write_files=True):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    db = str(tmp_path / "db.sqlite")
    out = tmp_path / "out"
    cfg = AnalysisConfig(write_files=write_files)
    index_one(db, midi, out, cfg=cfg)
    return db, out


def test_index_stores_assets_and_links(tmp_path):
    db, out = _indexed(tmp_path)
    repo = Repository(db)
    npat = repo.conn.execute("SELECT COUNT(*) c FROM patterns").fetchone()["c"]
    nstem = repo.conn.execute("SELECT COUNT(*) c FROM stems").fetchone()["c"]
    nass = repo.conn.execute("SELECT COUNT(*) c FROM midi_assets").fetchone()["c"]
    assert nass == npat + nstem                # original loop + piano + clap each
    assert nass > 0
    # each pattern row is asset-linked and its bytes are non-empty
    for p in repo.conn.execute("SELECT id, asset_id, file FROM patterns"):
        assert p["asset_id"]
        data = repo.pattern_bytes(p["id"])
        assert data and data.startswith(b"MThd")
    # files on disk also present (write_files default True)
    assert len(list(Path(out).rglob("*.mid"))) == nass
    repo.close()


def test_db_only_reconstruct_and_export(tmp_path):
    db, out = _indexed(tmp_path, write_files=False)
    # no files were written
    assert not list(Path(out).rglob("*.mid"))
    repo = Repository(db)
    name = repo.all_songs()[0]["name"]
    from gms_ma import reconstruct as rc

    report = rc.reconstruct(repo, name, str(tmp_path / "rebuilt.mid"))
    assert report["match"] is True            # blob-only pipeline works
    repo.close()

    # export writes everything back from the DB
    res = cli.cmd_export(Namespace(db=db, song=None, out=str(tmp_path / "exported"),
                                   no_stems=False))
    assert res == 0
    written = list(Path(tmp_path / "exported").rglob("*.mid"))
    assert written
    repo2 = Repository(db)
    nass = repo2.conn.execute("SELECT COUNT(*) c FROM midi_assets").fetchone()["c"]
    repo2.close()
    assert len(written) == nass


def test_pattern_bytes_legacy_file_fallback(tmp_path):
    db, _out = _indexed(tmp_path)
    repo = Repository(db)
    pid = repo.conn.execute("SELECT id FROM patterns LIMIT 1").fetchone()["id"]
    data_db = repo.pattern_bytes(pid)
    assert data_db
    # wipe the asset -> falls back to the legacy on-disk file
    repo.conn.execute("DELETE FROM midi_assets WHERE pattern_id=?", (pid,))
    repo.conn.commit()
    assert repo.pattern_bytes(pid) == data_db
    repo.close()
