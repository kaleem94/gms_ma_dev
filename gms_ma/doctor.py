"""Database integrity checks: report (and optionally purge) corrupt songs.

A song can end up "corrupted" in the DB when indexing is interrupted or an
analysis fails part-way: it may have no tracks, patterns without MIDI assets,
placements pointing at deleted patterns, broken stem links or an unparsable
stored manifest.  :func:`scan` finds those so the CLI can report or delete them.
"""
from __future__ import annotations

import json
from pathlib import Path

from .store import Repository


def song_counts(repo: Repository, song_id: int) -> dict:
    """Row counts for one song (used by the delete preview and tests)."""
    con = repo.conn

    def one(sql: str, *args) -> int:
        return con.execute(sql, args).fetchone()[0]

    pats = "SELECT id FROM patterns WHERE song_id=?"
    return {
        "tracks": one("SELECT COUNT(*) FROM tracks WHERE song_id=?", song_id),
        "patterns": one("SELECT COUNT(*) FROM patterns WHERE song_id=?", song_id),
        "placements": one("SELECT COUNT(*) FROM placements WHERE song_id=?", song_id),
        "assets": one(
            f"SELECT COUNT(*) FROM midi_assets WHERE pattern_id IN ({pats})", song_id),
        "stems": one(
            f"SELECT COUNT(*) FROM stems WHERE pattern_id IN ({pats})", song_id),
        "motif_hits": one(
            f"SELECT COUNT(*) FROM motif_hits WHERE pattern_id IN ({pats})", song_id),
    }


def scan(repo: Repository, deep: bool = True) -> list[dict]:
    """Return ``[{id, name, path, issues: [...]}]`` for every suspicious song.

    ``deep=False`` skips manifest-JSON validation (the expensive part) so the
    check is cheap enough to run on every GUI refresh / classify.
    """
    con = repo.conn

    tracks = {r["song_id"]: r["c"] for r in con.execute(
        "SELECT song_id, COUNT(*) c FROM tracks GROUP BY song_id")}
    dangling = {r["song_id"]: r["c"] for r in con.execute(
        "SELECT pl.song_id, COUNT(*) c FROM placements pl "
        "LEFT JOIN patterns p ON p.id = pl.pattern_id "
        "WHERE p.id IS NULL GROUP BY pl.song_id")}
    no_asset = {r["song_id"]: r["c"] for r in con.execute(
        "SELECT p.song_id, COUNT(*) c FROM patterns p WHERE "
        "  p.asset_id IS NULL OR NOT EXISTS ("
        "    SELECT 1 FROM midi_assets a WHERE a.asset_id = p.asset_id) "
        "GROUP BY p.song_id")}
    bad_stems = {r["song_id"]: r["c"] for r in con.execute(
        "SELECT p.song_id, COUNT(*) c FROM stems s "
        "JOIN patterns p ON p.id = s.pattern_id WHERE "
        "  s.asset_id IS NULL OR NOT EXISTS ("
        "    SELECT 1 FROM midi_assets a WHERE a.asset_id = s.asset_id) "
        "GROUP BY p.song_id")}

    bad_man: dict[int, int] = {}
    if deep:
        for m in con.execute("SELECT song_id, payload FROM manifests"):
            try:
                json.loads(m["payload"])
            except (TypeError, ValueError):
                bad_man[m["song_id"]] = bad_man.get(m["song_id"], 0) + 1

    findings: list[dict] = []
    for row in con.execute("SELECT id, name, path FROM songs ORDER BY name"):
        sid, name, path = row["id"], row["name"], row["path"]
        issues: list[str] = []
        if tracks.get(sid, 0) == 0:
            issues.append("no tracks (incomplete index)")
        if dangling.get(sid):
            issues.append(f"{dangling[sid]} placement(s) reference missing patterns")
        if no_asset.get(sid):
            issues.append(f"{no_asset[sid]} pattern(s) have no MIDI asset")
        if bad_stems.get(sid):
            issues.append(f"{bad_stems[sid]} stem(s) reference missing assets")
        if bad_man.get(sid):
            issues.append(f"{bad_man[sid]} invalid manifest payload(s)")
        if path and not Path(path).exists():
            issues.append("source file missing (informational)")
        if issues:
            findings.append({"id": sid, "name": name, "path": path, "issues": issues})
    return findings


def fixable(findings: list[dict]) -> list[dict]:
    """Corrupt songs whose source file still exists (so they can be re-indexed)."""
    return [f for f in findings if f.get("path") and Path(f["path"]).exists()]
