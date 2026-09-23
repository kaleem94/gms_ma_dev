"""CLI `delete`: purge a song (and its rows) from the DB, optionally files too."""
from gms_ma.cli import main
from gms_ma.store import Repository
from tests.conftest import index_one, repetitive_song


def _index_two(tmp_path):
    db = tmp_path / "db.sqlite"
    out = tmp_path / "out"
    a = tmp_path / "a.mid"
    b = tmp_path / "b.mid"
    repetitive_song(a, bar_count=2)
    repetitive_song(b, bar_count=2)
    index_one(db, a, out)
    index_one(db, b, out)
    repo = Repository(db)
    names = sorted(s["name"] for s in repo.all_songs())
    repo.close()
    assert names == ["a", "b"]
    return db, out, names


def test_delete_requires_yes(tmp_path):
    db, out, names = _index_two(tmp_path)
    rc = main(["delete", "--db", str(db), "--song", names[0]])
    assert rc == 2
    repo = Repository(db)
    assert sorted(s["name"] for s in repo.all_songs()) == names
    repo.close()


def test_delete_song_and_files(tmp_path):
    db, out, names = _index_two(tmp_path)
    target = names[0]
    assert (out / target).is_dir()
    rc = main(["delete", "--db", str(db), "--song", target,
               "--yes", "--files", "--out", str(out)])
    assert rc == 0
    repo = Repository(db)
    assert [s["name"] for s in repo.all_songs()] == [names[1]]
    repo.close()
    assert not (out / target).exists()
    assert (out / names[1]).is_dir()


def test_delete_unknown_song(tmp_path):
    db, _out, _names = _index_two(tmp_path)
    rc = main(["delete", "--db", str(db), "--song", "nope", "--yes"])
    assert rc == 1
