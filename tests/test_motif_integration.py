"""Motif feature integration: index storage, manifest, library, reconstruct."""
from gms_ma import library as lib
from gms_ma import reconstruct as rc
from gms_ma.indexer import build_manifest
from gms_ma.segment import AnalysisConfig
from gms_ma.store import Repository
from tests.conftest import index_one, repetitive_song


def _counts(repo):
    con = repo.conn
    fam = con.execute("SELECT COUNT(*) c FROM patterns WHERE kind='motif'").fetchone()["c"]
    hits = con.execute("SELECT COUNT(*) c FROM motif_hits").fetchone()["c"]
    return fam, hits


def test_index_default_stores_motifs(tmp_path):
    midi = tmp_path / "x.mid"
    repetitive_song(midi, bar_count=8)
    db = tmp_path / "db.sqlite"
    index_one(db, midi, tmp_path / "out")          # AnalysisConfig() -> motifs on
    repo = Repository(db)
    fam, hits = _counts(repo)
    assert fam > 0 and hits >= fam

    name = repo.all_songs()[0]["name"]
    m = build_manifest(repo, name)
    assert m["stats"]["motif_families"] == fam
    assert m["stats"]["motif_hits"] == hits
    with_m = [t for t in m["tracks"] if t["motifs"]]
    assert with_m
    assert all(any(p["kind"] == "motif" for p in t["patterns"])
               for t in m["tracks"] if t["motifs"])
    # families carry hits + are ordinary (auditionable) patterns
    fam0 = with_m[0]["motifs"][0]
    assert fam0["hits"]
    assert repo.pattern_bytes(fam0["id"]) is not None

    # library catalog surfaces motif rows with their occurrence counts
    motif_rows = [r for r in lib.catalog_rows(repo) if r["kind"] == "motif"]
    assert motif_rows and all(r["occ_count"] > 0 for r in motif_rows)
    assert len(lib.apply_filters(lib.catalog_rows(repo), {"kind": ["motif"]})) == fam

    # reconstruction is untouched by motif rows
    report = rc.reconstruct(repo, name, tmp_path / "rebuilt.mid")
    assert report["match"] is True
    repo.close()


def test_reindex_replaces_motifs(tmp_path):
    midi = tmp_path / "x.mid"
    repetitive_song(midi, bar_count=4)
    db = tmp_path / "db.sqlite"
    index_one(db, midi, tmp_path / "out")
    repo = Repository(db)
    fam1, hits1 = _counts(repo)
    repo.close()
    index_one(db, midi, tmp_path / "out")          # delete_song + re-add
    repo = Repository(db)
    assert _counts(repo) == (fam1, hits1)
    repo.close()


def test_no_motifs_opt_out(tmp_path):
    midi = tmp_path / "x.mid"
    repetitive_song(midi, bar_count=4)
    db = tmp_path / "db.sqlite"
    index_one(db, midi, tmp_path / "out", cfg=AnalysisConfig(motifs=False))
    repo = Repository(db)
    assert _counts(repo) == (0, 0)
    name = repo.all_songs()[0]["name"]
    m = build_manifest(repo, name)
    assert m["stats"]["motif_families"] == 0
    assert all(not t["motifs"] for t in m["tracks"])
    report = rc.reconstruct(repo, name, tmp_path / "rebuilt2.mid")
    assert report["match"] is True
    repo.close()
