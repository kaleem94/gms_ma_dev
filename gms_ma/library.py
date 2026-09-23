"""Whole-library browsing: pattern catalog, multi-select facet filters, DAW export.

The timeline viewer is single-song; this module provides the cross-database
"library" view.  A :func:`catalog_rows` row is one **unique pattern** anywhere
in the DB with everything the UI needs to describe and audition it (source
song, instrument/track, tags, stems, occurrences).  Filtering is done in pure
Python over the (small) catalog so OR-within-facet / AND-across-facet semantics
and facet counts that exclude their own selections stay simple and testable.

DAW helpers (:func:`sanitize_midi`, :func:`resolve_pattern_file`,
:func:`pattern_events`) let a loop be auditioned straight from the DB and be
re-exported to a single channel for dropping onto one instrument track.
"""
from __future__ import annotations

import io
import json
import os
import threading
from collections import Counter
from pathlib import Path

import mido

from .reconstruct import _load_pattern_notes
from .store import Repository
from .tag import FAMILY_RANGES, family, gm_name

# Facet groups exposed as multi-select filters.  ``kind`` is a tag category for
# pattern/song tags; ``value`` reads a plain column/derived field.
# Selection semantics: values inside one facet OR together, facets AND.
_FACETS = (
    "style", "key", "mode", "energy",          # pattern tags
    "genre", "emotion", "tempo_class",          # song tags
    "family", "kind",                            # instrument / loop class
)
_PATTERN_TAG_FACETS = ("style", "key", "mode", "energy")
_SONG_TAG_FACETS = ("genre", "emotion", "tempo_class")
_SONG_TAG_KINDS = {"genre": "genre", "emotion": "emotion",
                   "tempo_class": "tempo-class"}

# valid stem render modes (rhythm skeletons + harmony/melody separation)
_STEM_RENDERS = ("piano", "clap", "harmony", "melody")

_CHANNELED_TYPES = {
    "note_off", "note_on", "polytouch", "control_change",
    "program_change", "channel_aftertouch", "pitchwheel",
}

# Channel to remap DAW-sanitized loops onto (0 == the "1" a DAW shows as ch.1).
DAW_CHANNEL = 0


def _tag_value(tags: dict, kind: str):
    vals = tags.get(kind)
    return vals[0]["value"] if vals else None


def _tags_map(tags: dict) -> dict:
    """tags -> {kind: value} used for internal filtering."""
    out = {}
    for kind in tags:
        v = _tag_value(tags, kind)
        if v is not None:
            out[kind] = v
    return out


def _tags_meta(tags: dict) -> dict:
    """tags -> {kind: {value, source}} used for UI chips (manual override hint)."""
    out = {}
    for kind, vals in tags.items():
        if vals:
            out[kind] = {"value": vals[0]["value"], "source": vals[0]["source"]}
    return out


_ROW_COLS = """p.id AS pattern_id, p.song_id, p.track_id, p.content_id,
          p.kind, p.length_ticks, p.length_bars, p.bar_index,
          p.start_tick, p.confidence, p.notes_count, p.file,
          t.track_index, t.channel, t.program, t.name AS track_name,
          t.is_drums, t.engine, t.lens, t.period_bars,
          t.start_tick AS t_start, t.end_tick AS t_end,
          s.name AS song_name, s.bpm, s.ticks_per_beat"""

_BASE_FROM = ("FROM patterns p "
              "JOIN tracks t ON t.id = p.track_id "
              "JOIN songs s ON s.id = p.song_id")


def _family_case(tcol: str = "t") -> str:
    """SQL CASE reproducing :func:`tag.family` (+ drums / unknown)."""
    branches = " ".join(
        f"WHEN {tcol}.program BETWEEN {lo} AND {hi} THEN '{name}'"
        for lo, hi, name in FAMILY_RANGES)
    return (f"CASE WHEN {tcol}.is_drums THEN 'drums' "
            f"WHEN {tcol}.program IS NULL THEN 'unknown' "
            f"{branches} ELSE 'sfx' END")


def _eff_tag_expr(target_type: str, id_col: str) -> str:
    """Correlated subquery for the effective (manual>model>heuristic) tag value."""
    return (f"(SELECT tg.value FROM tags tg WHERE tg.target_type='{target_type}' "
            f"AND tg.target_id = {id_col} AND tg.kind = ? "
            "ORDER BY CASE tg.source WHEN 'manual' THEN 0 "
            "WHEN 'model' THEN 1 ELSE 2 END, tg.rowid LIMIT 1)")


def _facet_value_sql(facet: str):
    """(value-expression, kind-param-or-None) for a facet's effective value."""
    if facet in _PATTERN_TAG_FACETS:
        return _eff_tag_expr("pattern", "p.id"), facet
    if facet in _SONG_TAG_FACETS:
        return _eff_tag_expr("song", "s.id"), _SONG_TAG_KINDS[facet]
    if facet == "family":
        return _family_case("t"), None
    if facet == "kind":
        return "p.kind", None
    return "NULL", None


def _where(filters: dict | None, exclude: str | None = None):
    """(WHERE sql, params) for a filter set, optionally excluding one facet."""
    f = filters or {}
    conds: list[str] = []
    params: list = []
    if f.get("song"):
        conds.append("s.name = ?")
        params.append(f["song"])
    q = str(f.get("q") or "").strip().lower()
    if q:
        like = f"%{q}%"
        conds.append("(LOWER(s.name) LIKE ? OR LOWER(IFNULL(t.name,'')) LIKE ? "
                     "OR LOWER(IFNULL(p.content_id,'')) LIKE ?)")
        params += [like, like, like]
    for facet in _FACETS:
        if facet == exclude:
            continue
        vals = f.get(facet)
        if not vals:
            continue
        vals = sorted(vals)
        expr, kind = _facet_value_sql(facet)
        ph = ",".join("?" * len(vals))
        conds.append(f"{expr} IN ({ph})")
        if kind:
            params.append(kind)          # subquery's kind binds before its values
        params += vals
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return where, params


def _effective_tags(conn, target_type: str, ids=None) -> dict:
    """``{target_id: {kind: [recs]}}`` with manual>model>heuristic precedence.

    ``ids`` optionally restricts to a set (page-sized); None scans all rows.
    """
    sql = ("SELECT target_id, kind, value, source, confidence FROM tags "
           "WHERE target_type = ?")
    args: list = [target_type]
    if ids is not None:
        ids = tuple(ids)
        if not ids:
            return {}
        sql += f" AND target_id IN ({','.join('?' * len(ids))})"
        args += list(ids)
    sql += " ORDER BY target_id, kind, rowid"
    grouped: dict = {}
    for r in conn.execute(sql, args):
        grouped.setdefault(r["target_id"], {}).setdefault(r["kind"], {}).setdefault(
            r["source"], []).append({"value": r["value"], "source": r["source"],
                                     "confidence": r["confidence"]})
    out: dict = {}
    for tid, kinds in grouped.items():
        eff = {}
        for k, srcs in kinds.items():
            for src in ("manual", "model", "heuristic"):
                if src in srcs:
                    eff[k] = srcs[src]
                    break
        out[tid] = eff
    return out


def _build_row(r, init_us, occ, motif_occ, stems, p_vals, p_meta, s_vals, s_meta):
    """Assemble one client-facing catalog row from a big-join row + aux maps."""
    pid = r["pattern_id"]
    prog = r["program"]
    fam = "drums" if r["is_drums"] else (family(prog) if prog is not None else "unknown")
    ppq = r["ticks_per_beat"] or 480
    us = init_us.get(r["song_id"], 500000)
    spt = us / (ppq * 1_000_000.0)
    dur_s = r["length_ticks"] * spt
    span_s = (r["t_end"] - r["t_start"]) * spt if r["t_end"] >= r["t_start"] else 0.0
    ocs = motif_occ.get(pid, []) if r["kind"] == "motif" else occ.get(pid, [])
    return {
        "pattern_id": pid,
        "content_id": r["content_id"],
        "kind": r["kind"],
        "length_bars": r["length_bars"],
        "length_ticks": r["length_ticks"],
        "bar_index": r["bar_index"],
        "start_tick": r["start_tick"],
        "confidence": r["confidence"],
        "notes_count": r["notes_count"],
        "duration_s": round(dur_s, 3),
        "stems": sorted(stems.get(pid, [])),
        "pvals": p_vals.get(pid, {}),    # internal filter values
        "svals": s_vals.get(r["song_id"], {}),
        "ptags": p_meta.get(pid, {}),    # UI tag chips
        "stags": s_meta.get(r["song_id"], {}),
        "song": {"name": r["song_name"], "bpm": r["bpm"]},
        "track": {
            "id": r["track_id"], "track_index": r["track_index"],
            "name": r["track_name"], "channel": r["channel"],
            "program": prog, "program_name": gm_name(prog) if prog is not None else None,
            "family": fam, "is_drums": bool(r["is_drums"]),
            "engine": r["engine"], "lens": r["lens"],
            "period_bars": r["period_bars"],
            "span_s": round(span_s, 3),
        },
        "occurrences": ocs,
        "occ_count": len(ocs),
        "file": r["file"],
    }


def query_page(repo: Repository, filters: dict | None = None,
               offset: int = 0, limit: int | None = None) -> list[dict]:
    """Client rows for one page (SQL-filtered, page-only assembly).

    Scales with the page size, not the whole library: only the requested rows
    are read from SQL and only their tags/stems/occurrences are fetched.
    """
    conn = repo.conn
    where, params = _where(filters)
    order = "ORDER BY s.name, t.track_index, p.bar_index, p.id"
    sql = f"SELECT {_ROW_COLS} {_BASE_FROM}{where} {order}"
    if limit is not None and limit > 0:
        sql += " LIMIT ? OFFSET ?"
        params = params + [int(limit), int(offset)]
    raw = [dict(r) for r in conn.execute(sql, params)]
    if not raw:
        return []
    pids = tuple({r["pattern_id"] for r in raw})
    sids = tuple({r["song_id"] for r in raw})
    qm_p = ",".join("?" * len(pids))
    qm_s = ",".join("?" * len(sids))
    init_us: dict = {}
    for r in conn.execute(
            f"SELECT song_id, us_per_beat FROM (SELECT song_id, us_per_beat,"
            f" ROW_NUMBER() OVER (PARTITION BY song_id ORDER BY tick, rowid) rn"
            f" FROM tempos WHERE song_id IN ({qm_s})) WHERE rn = 1", sids):
        init_us[r["song_id"]] = r["us_per_beat"]
    occ: dict = {}
    for r in conn.execute(
            f"SELECT pattern_id, bar_index, start_tick, length_ticks FROM placements"
            f" WHERE pattern_id IN ({qm_p}) ORDER BY pattern_id, bar_index", pids):
        occ.setdefault(r["pattern_id"], []).append(
            {"bar_index": r["bar_index"], "start_tick": r["start_tick"],
             "length_ticks": r["length_ticks"]})
    motif_occ: dict = {}
    for r in conn.execute(
            f"SELECT pattern_id, bar_index, start_tick, end_tick, length_ticks,"
            f" sim, shift FROM motif_hits WHERE pattern_id IN ({qm_p})"
            f" ORDER BY pattern_id, seq", pids):
        motif_occ.setdefault(r["pattern_id"], []).append(
            {"bar_index": r["bar_index"], "start_tick": r["start_tick"],
             "end_tick": r["end_tick"], "length_ticks": r["length_ticks"],
             "sim": r["sim"], "shift": r["shift"]})
    stems: dict = {}
    for r in conn.execute(
            f"SELECT pattern_id, render FROM stems WHERE pattern_id IN ({qm_p})",
            pids):
        stems.setdefault(r["pattern_id"], []).append(r["render"])
    p_eff = _effective_tags(conn, "pattern", pids)
    s_eff = _effective_tags(conn, "song", sids)
    p_vals = {k: _tags_map(v) for k, v in p_eff.items()}
    p_meta = {k: _tags_meta(v) for k, v in p_eff.items()}
    s_vals = {k: _tags_map(v) for k, v in s_eff.items()}
    s_meta = {k: _tags_meta(v) for k, v in s_eff.items()}
    return [_build_row(r, init_us, occ, motif_occ, stems, p_vals, p_meta, s_vals, s_meta)
            for r in raw]


def _query_songs(conn, where: str, params: list) -> list[dict]:
    occ = ("SUM(CASE WHEN p.kind='motif' "
           "THEN (SELECT COUNT(*) FROM motif_hits mh WHERE mh.pattern_id = p.id) "
           "ELSE (SELECT COUNT(*) FROM placements pl WHERE pl.pattern_id = p.id) END)")
    rows = conn.execute(
        f"SELECT s.id AS song_id, s.name AS name, s.bpm AS bpm, s.seconds AS seconds,"
        f" COUNT(*) AS n_patterns, {occ} AS n_placements {_BASE_FROM}{where}"
        f" GROUP BY s.id ORDER BY LOWER(s.name)", params)
    s_meta = {k: _tags_meta(v) for k, v in _effective_tags(conn, "song").items()}
    return [{"name": r["name"], "bpm": r["bpm"], "stags": s_meta.get(r["song_id"], {}),
             "n_patterns": r["n_patterns"], "n_placements": r["n_placements"],
             "seconds": round(r["seconds"] or 0.0, 3)} for r in rows]


def _facets_unfiltered(conn) -> dict:
    """All facet counts with no active filters, in a few window-function passes.

    Much faster than the per-facet correlated queries: the effective tag values
    (manual>model>heuristic) are computed once per tag group, then counted.
    """
    facets: dict = {}
    for kind, value, n in conn.execute(
            "WITH eff AS (SELECT target_id, kind, value FROM ("
            "  SELECT target_id, kind, value, ROW_NUMBER() OVER ("
            "    PARTITION BY target_id, kind ORDER BY CASE source"
            "    WHEN 'manual' THEN 0 WHEN 'model' THEN 1 ELSE 2 END, rowid) rn"
            "  FROM tags WHERE target_type='pattern'"
            "    AND kind IN ('style','key','mode','energy')) WHERE rn=1) "
            "SELECT e.kind, e.value, COUNT(*) FROM patterns p "
            "JOIN eff e ON e.target_id = p.id GROUP BY e.kind, e.value"):
        facets.setdefault(kind, []).append({"value": value, "n": n})
    for kind, value, n in conn.execute(
            "WITH eff AS (SELECT target_id, kind, value FROM ("
            "  SELECT target_id, kind, value, ROW_NUMBER() OVER ("
            "    PARTITION BY target_id, kind ORDER BY CASE source"
            "    WHEN 'manual' THEN 0 WHEN 'model' THEN 1 ELSE 2 END, rowid) rn"
            "  FROM tags WHERE target_type='song'"
            "    AND kind IN ('genre','emotion','tempo-class')) WHERE rn=1) "
            "SELECT e.kind, e.value, COUNT(*) FROM patterns p "
            "JOIN songs s ON s.id = p.song_id JOIN eff e ON e.target_id = s.id "
            "GROUP BY e.kind, e.value"):
        facet = {"tempo-class": "tempo_class"}.get(kind, kind)
        facets.setdefault(facet, []).append({"value": value, "n": n})
    facets["family"] = [{"value": v, "n": n} for v, n in conn.execute(
        f"SELECT v, COUNT(*) AS n FROM (SELECT {_family_case('t')} AS v "
        f"FROM patterns p JOIN tracks t ON t.id = p.track_id) "
        f"WHERE v IS NOT NULL GROUP BY v")]
    facets["kind"] = [{"value": v, "n": n} for v, n in conn.execute(
        "SELECT kind AS v, COUNT(*) AS n FROM patterns GROUP BY kind")]
    for facet in _FACETS:
        facets.setdefault(facet, [])
        facets[facet].sort(key=lambda c: (-c["n"], c["value"]))
    return facets


def read_library_facets(conn):
    """Stored unfiltered facet counts, or None when the cache is empty."""
    rows = conn.execute("SELECT facet, value, n FROM library_facets").fetchall()
    if not rows:
        return None
    out: dict = {}
    for r in rows:
        out.setdefault(r["facet"], []).append({"value": r["value"], "n": r["n"]})
    for facet in _FACETS:
        out.setdefault(facet, [])
        out[facet].sort(key=lambda c: (-c["n"], c["value"]))
    return out


def read_library_songs(conn):
    """Stored unfiltered song summaries, or None when the cache is empty."""
    rows = conn.execute(
        "SELECT name, bpm, seconds, n_patterns, n_placements, stags "
        "FROM library_songs ORDER BY LOWER(name)").fetchall()
    if not rows:
        return None
    return [{"name": r["name"], "bpm": r["bpm"], "seconds": r["seconds"],
             "n_patterns": r["n_patterns"], "n_placements": r["n_placements"],
             "stags": json.loads(r["stags"] or "{}")} for r in rows]


def _store_library_cache(conn, facets: dict, songs: list) -> None:
    conn.execute("DELETE FROM library_facets")
    conn.executemany(
        "INSERT INTO library_facets(facet, value, n) VALUES(?,?,?)",
        [(f, str(c["value"]), int(c["n"]))
         for f, cs in facets.items() for c in cs])
    conn.execute("DELETE FROM library_songs")
    conn.executemany(
        "INSERT INTO library_songs(name, bpm, seconds, n_patterns, n_placements, stags) "
        "VALUES(?,?,?,?,?,?)",
        [(s["name"], s["bpm"], s["seconds"], s["n_patterns"], s["n_placements"],
          json.dumps(s["stags"])) for s in songs])
    conn.commit()


def refresh_library_cache(repo: Repository) -> dict:
    """Precompute + store the unfiltered facet counts and song summaries.

    Run by ``index``/``tag``/``delete``/``doctor``/``optimize``; the viewer
    self-heals if the tables are empty.  Returns row counts for logging.
    """
    conn = repo.conn
    facets = _facets_unfiltered(conn)
    songs = _query_songs(conn, "", [])
    _store_library_cache(conn, facets, songs)
    return {"facets": sum(len(cs) for cs in facets.values()), "songs": len(songs)}


def query_meta(repo: Repository, filters: dict | None = None) -> dict:
    """total + facet counts + song summaries for a filter set, all in SQL.

    With no active filters this reads the precomputed tables (fast); if they
    are missing it computes once and stores them (viewer self-heal).
    """
    conn = repo.conn
    where, params = _where(filters)
    total = conn.execute(f"SELECT COUNT(*) {_BASE_FROM}{where}", params).fetchone()[0]
    active = {k for k, v in (filters or {}).items() if v}
    if not active:
        facets = read_library_facets(conn)
        songs = read_library_songs(conn)
        if facets is None or songs is None:
            facets = facets if facets is not None else _facets_unfiltered(conn)
            songs = songs if songs is not None else _query_songs(conn, "", [])
            _store_library_cache(conn, facets, songs)
    else:
        facets = {}
        for facet in _FACETS:
            expr, kind = _facet_value_sql(facet)
            w, wp = _where(filters, exclude=facet)
            p = ([kind] if kind else []) + wp
            sql = (f"SELECT v, COUNT(*) AS n FROM (SELECT {expr} AS v {_BASE_FROM}{w}) "
                   f"WHERE v IS NOT NULL GROUP BY v ORDER BY n DESC, v")
            facets[facet] = [{"value": v, "n": n} for v, n in conn.execute(sql, p)]
        songs = _query_songs(conn, where, params)
    return {"total": total, "facets": facets, "songs": songs}


def catalog_rows(repo: Repository) -> list[dict]:
    """Every unique pattern in the DB, denormalized for browsing/filtering.

    ``duration_s`` is the loop's **own** span: ``length_ticks`` played at the
    song's initial tempo — the same tempo basis the live audition scheduler uses
    (and what a DAW hears when it imports the exported loop .mid, which carries
    that tempo).  Scaling from the whole-song average (``seconds / total_ticks``)
    drifts on songs with tempo changes, which is why a loop's listed time used to
    disagree with how long it actually plays.
    """
    conn = repo.conn
    rows = [dict(r) for r in conn.execute(
        f"SELECT {_ROW_COLS} {_BASE_FROM} "
        "ORDER BY s.name, t.track_index, p.bar_index, p.id")]
    if not rows:
        return []

    # first tempo event per song (what playback + the exported .mid use);
    # ORDER BY tick,rowid reproduces the exact first row audition sees.  A
    # window function keeps this O(n) instead of the old correlated subquery
    # (which scanned the whole tempos table per song when song_id was unindexed).
    init_us: dict[int, int] = {}
    for r in conn.execute(
            "SELECT song_id, us_per_beat FROM ("
            "  SELECT song_id, us_per_beat,"
            "         ROW_NUMBER() OVER (PARTITION BY song_id"
            "                            ORDER BY tick ASC, rowid ASC) AS rn"
            "  FROM tempos) WHERE rn = 1"):
        init_us[r["song_id"]] = r["us_per_beat"]

    occ: dict[int, list[dict]] = {}
    for r in conn.execute(
            "SELECT pattern_id, bar_index, start_tick, length_ticks "
            "FROM placements ORDER BY pattern_id, bar_index"):
        occ.setdefault(r["pattern_id"], []).append(
            {"bar_index": r["bar_index"], "start_tick": r["start_tick"],
             "length_ticks": r["length_ticks"]})

    motif_occ: dict[int, list[dict]] = {}
    for r in conn.execute(
            "SELECT pattern_id, bar_index, start_tick, end_tick, "
            "length_ticks, sim, shift FROM motif_hits "
            "ORDER BY pattern_id, seq"):
        motif_occ.setdefault(r["pattern_id"], []).append(
            {"bar_index": r["bar_index"], "start_tick": r["start_tick"],
             "end_tick": r["end_tick"], "length_ticks": r["length_ticks"],
             "sim": r["sim"], "shift": r["shift"]})

    stems: dict[int, list[str]] = {}
    for r in conn.execute("SELECT pattern_id, render FROM stems"):
        stems.setdefault(r["pattern_id"], []).append(r["render"])

    # pre-flatten tag records once per target (a song's tags are shared by all
    # of its patterns; patterns usually repeat across placements' rows)
    pattern_tags = _effective_tags(conn, "pattern")
    song_tags = _effective_tags(conn, "song")
    p_vals = {pid: _tags_map(recs) for pid, recs in pattern_tags.items()}
    p_meta = {pid: _tags_meta(recs) for pid, recs in pattern_tags.items()}
    s_vals = {sid: _tags_map(recs) for sid, recs in song_tags.items()}
    s_meta = {sid: _tags_meta(recs) for sid, recs in song_tags.items()}

    return [_build_row(r, init_us, occ, motif_occ, stems, p_vals, p_meta, s_vals, s_meta)
            for r in rows]


_CACHE_LOCK = threading.Lock()
_BUILD_LOCK = threading.Lock()          # one builder at a time (catalog + meta)
_CATALOG_CACHE: dict[str, tuple[int, list[dict]]] = {}


def _db_stamp(repo: Repository) -> int:
    """DB file mtime (ns) used as the cache-invalidation key; 0 if unreadable."""
    try:
        return os.stat(repo.db_path).st_mtime_ns
    except OSError:
        return 0


def cached_catalog(repo: Repository) -> list[dict]:
    """Like :func:`catalog_rows` but cached per DB file until its mtime changes.

    Browsing fires a catalog rebuild on every filter change; for large
    libraries that is wasteful, so we key a cache on the DB file's mtime (which
    changes whenever an indexer writes).  Concurrent readers are served from
    the cache; ``_BUILD_LOCK`` ensures only one thread rebuilds (the startup
    warm-up and a request racing it share the result instead of building twice).
    """
    db = repo.db_path
    stamp = _db_stamp(repo)
    with _CACHE_LOCK:
        hit = _CATALOG_CACHE.get(db)
        if hit is not None and hit[0] == stamp:
            return hit[1]
    with _BUILD_LOCK:
        with _CACHE_LOCK:                   # re-check: another thread may have built
            hit = _CATALOG_CACHE.get(db)
            if hit is not None and hit[0] == stamp:
                return hit[1]
        rows = catalog_rows(repo)
        with _CACHE_LOCK:
            _CATALOG_CACHE[db] = (stamp, rows)
    return rows


def _row_value(row: dict, facet: str):
    if facet in _PATTERN_TAG_FACETS:
        return row["pvals"].get(facet)
    if facet in _SONG_TAG_FACETS:
        return row["svals"].get(_SONG_TAG_KINDS[facet])
    if facet == "family":
        return row["track"]["family"]
    if facet == "kind":
        return row["kind"]
    return None


def apply_filters(rows: list[dict], filters: dict | None = None) -> list[dict]:
    """Filter catalog rows.  Each non-empty facet value-collection is OR-ed;
    facets (and the optional ``song`` drill-down / ``q`` text) are AND-ed."""
    f = {k: set(v) if isinstance(v, (list, set, tuple)) else v
         for k, v in (filters or {}).items() if v}
    if not f:
        return rows                          # no active filters -> all rows (no copy)
    q = str(f.get("q") or "").strip().lower()
    song = f.get("song")
    out = []
    for row in rows:
        if song and row["song"]["name"] != song:
            continue
        if q:
            hay = " ".join((row["song"]["name"], row["track"]["name"] or "",
                            row["content_id"] or "")).lower()
            if q not in hay:
                continue
        ok = True
        for facet in _FACETS:
            sel = f.get(facet)
            if not sel:
                continue
            val = _row_value(row, facet)
            if val is None or val not in sel:
                ok = False
                break
        if ok:
            out.append(row)
    return out


def facet_counts(rows: list[dict], filters: dict | None = None) -> dict:
    """Per-facet value counts over rows matching every applied filter *except*
    the facet's own selections (so ticked values never collapse to zero while
    the page is being narrowed)."""
    base = {k: (set(v) if isinstance(v, (list, set, tuple)) else v)
            for k, v in (filters or {}).items() if v}
    out: dict[str, list[dict]] = {}
    if not base:
        # default (no filters): count every facet in a single traversal
        counters = {facet: Counter() for facet in _FACETS}
        for row in rows:
            for facet in _FACETS:
                val = _row_value(row, facet)
                if val is not None:
                    counters[facet][val] += 1
        for facet in _FACETS:
            out[facet] = [{"value": v, "n": n}
                          for v, n in counters[facet].most_common()]
        return out
    for facet in _FACETS:
        fb = dict(base)
        fb.pop(facet, None)
        seen: Counter = Counter()
        for row in apply_filters(rows, fb):
            val = _row_value(row, facet)
            if val is not None:
                seen[val] += 1
        out[facet] = [{"value": v, "n": n}
                      for v, n in seen.most_common()]
    return out


def filter_signature(filters: dict | None = None) -> str:
    """Canonical string for a filter set (cache key for :func:`cached_meta`)."""
    parts = []
    for k in sorted((filters or {}).keys()):
        v = (filters or {}).get(k)
        if not v:
            continue
        if isinstance(v, (set, frozenset, list, tuple)):
            parts.append(f"{k}=" + ",".join(sorted(str(x) for x in v)))
        else:
            parts.append(f"{k}={v}")
    return "|".join(parts)


def song_summaries(matched: list[dict]) -> list[dict]:
    """One header per song in the matched set (patterns + placements)."""
    groups: dict[str, dict] = {}
    for r in matched:
        name = r["song"]["name"]
        g = groups.get(name)
        if g is None:
            g = groups[name] = {"name": name, "bpm": r["song"]["bpm"],
                                "stags": r["stags"], "n_patterns": 0,
                                "n_placements": 0}
        g["n_patterns"] += 1
        g["n_placements"] += r["occ_count"]
    return sorted(groups.values(), key=lambda s: s["name"].lower())


_META_CACHE: dict = {}
_META_LOCK = threading.Lock()


def cached_meta(repo: Repository, filters: dict | None = None) -> dict:
    """total + facet counts + song summaries, cached per filter set.

    Keyed on the DB mtime and the canonical filter signature, so page/window
    navigation (same filters) skips the SQL aggregation.  Must be called with
    the repository lock held.
    """
    sig = filter_signature(filters)
    key = (repo.db_path, _db_stamp(repo), sig)
    with _META_LOCK:
        hit = _META_CACHE.get(key)
        if hit is not None:
            return hit
    with _BUILD_LOCK:
        with _META_LOCK:                    # re-check: another thread may have built
            hit = _META_CACHE.get(key)
            if hit is not None:
                return hit
        result = query_meta(repo, filters)   # may self-heal (writes -> new mtime)
        key2 = (repo.db_path, _db_stamp(repo), sig)
        with _META_LOCK:
            if len(_META_CACHE) > 64:        # bounded; pages re-warm quickly
                _META_CACHE.clear()
            _META_CACHE[key2] = result
    return result


def _find_pattern(repo: Repository, pattern_id: int) -> dict | None:
    row = repo.conn.execute("SELECT * FROM patterns WHERE id=?",
                            (pattern_id,)).fetchone()
    return dict(row) if row else None


def resolve_pattern_file(repo: Repository, pattern_id: int,
                         render: str | None = None) -> tuple[dict, str]:
    """Return (pattern_row, path) for a pattern and optional render stem.

    Prefers the stored on-disk ``file``; when the DB is the only store the asset
    BLOB is materialised to a temp cache file so DAW/download flows still get a
    real path.
    """
    pat = _find_pattern(repo, pattern_id)
    if not pat:
        raise KeyError(pattern_id)
    path = pat["file"] or ""
    if render in _STEM_RENDERS:
        for st in repo.stems_for(pattern_id):
            if st["render"] == render:
                path = st["file"] or ""
                break
    if path and Path(path).exists():
        return pat, path
    data = repo.pattern_bytes(pattern_id, render)
    if data is None:
        raise FileNotFoundError(pattern_id)
    return pat, str(_cache_asset(data))


def _cache_asset(data: bytes) -> Path:
    import hashlib
    import tempfile

    d = Path(tempfile.gettempdir()) / "midi_assets"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{hashlib.sha256(data).hexdigest()[:24]}.mid"
    if not p.exists():
        p.write_bytes(data)
    return p


def ch1_path_for(path: str) -> Path:
    p = Path(path)
    return p.with_name(f"{p.stem}_ch1.mid")


def sanitize_midi(src, dst, channel: int = DAW_CHANNEL) -> None:
    """Rewrite a MIDI so every channelized event sits on one DAW channel.

    Program changes and pitch bend are preserved (timbre intact); note numbers
    are untouched.  ``dst`` is written/overwritten; meta + markers are kept.
    """
    mf = mido.MidiFile(str(src))
    for track in mf.tracks:
        rebuilt = []
        for msg in track:
            if not msg.is_meta and msg.type in _CHANNELED_TYPES:
                rebuilt.append(msg.copy(channel=channel))
            else:
                rebuilt.append(msg)
        track[:] = rebuilt
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    mf.save(str(dst))


def sanitize_for_daw(repo: Repository, pattern_id: int,
                     render: str | None = None) -> Path:
    """Write the DAW-ready (single-channel) sibling of a loop and return it."""
    _pat, src = resolve_pattern_file(repo, pattern_id, render)
    dst = ch1_path_for(src)
    if not Path(src).exists():
        raise FileNotFoundError(src)
    sanitize_midi(src, dst)
    return dst


def retempo_midi(data: bytes, bpm: float) -> bytes:
    """Return ``data`` (a .mid) with its tempo set so it plays at ``bpm``.

    Note ticks are untouched, so a DAW set to ``bpm`` hears the same speed the
    viewer auditioned (the export-BPM feature).
    """
    us = int(round(60_000_000.0 / max(1.0, float(bpm))))
    mf = mido.MidiFile(file=io.BytesIO(data))
    found = False
    for track in mf.tracks:
        for msg in track:
            if msg.type == "set_tempo":
                msg.tempo = us
                found = True
                break
        if found:
            break
    if not found and mf.tracks:
        mf.tracks[0].insert(0, mido.MetaMessage("set_tempo", tempo=us, time=0))
    buf = io.BytesIO()
    mf.save(file=buf)
    return buf.getvalue()


def retempo_file(src, bpm: float) -> Path:
    """Write a retempoed copy of ``src`` (name keeps the source stem + bpm)."""
    import tempfile

    data = Path(src).read_bytes()
    d = Path(tempfile.gettempdir()) / "midi_assets"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{Path(src).stem}_{int(round(float(bpm)))}bpm.mid"
    p.write_bytes(retempo_midi(data, bpm))
    return p


def pattern_time_base(repo: Repository, song_id: int) -> tuple[int, int]:
    """(ppq, us_per_beat) a standalone loop from ``song_id`` plays at.

    This mirrors what the exported loop .mid embeds (the song's initial tempo),
    so tick -> seconds conversions here and in :func:`pattern_events` /
    :func:`pattern_span_seconds` stay identical to the audition and the DAW.
    """
    song = repo.conn.execute("SELECT ticks_per_beat FROM songs WHERE id=?",
                             (song_id,)).fetchone()
    ppq = song["ticks_per_beat"] if song and song["ticks_per_beat"] else 480
    row = repo.conn.execute(
        "SELECT us_per_beat FROM tempos WHERE song_id=? "
        "ORDER BY tick ASC, rowid ASC LIMIT 1", (song_id,)).fetchone()
    us = row["us_per_beat"] if row else 500000
    return ppq, us


def pattern_span_seconds(repo: Repository, pattern_id: int) -> float:
    """Length (seconds) of a pattern's own loop period at its own tempo."""
    pat = _find_pattern(repo, pattern_id)
    if not pat:
        return 0.0
    ppq, us = pattern_time_base(repo, pat["song_id"])
    return pat["length_ticks"] * us / (ppq * 1_000_000.0)


def pattern_events(repo: Repository, pattern_id: int,
                   render: str | None = None) -> list:
    """Audition events [(sec, kind, ch, a, b)] for a DB pattern, no manifest.

    Mirrors the timeline viewer's event building but sources everything from
    the repository (song PPQ/tempo, track channel/program, stem file).
    """
    pat = _find_pattern(repo, pattern_id)
    if not pat:
        return []
    song_id = pat["song_id"]
    track = dict(repo.conn.execute(
        "SELECT * FROM tracks WHERE id=?", (pat["track_id"],)).fetchone())
    ppq, us = pattern_time_base(repo, song_id)
    spt = us / (ppq * 1_000_000.0)

    fpath = pat["file"]
    if render in _STEM_RENDERS:
        for st in repo.stems_for(pattern_id):
            if st["render"] == render:
                fpath = st["file"]
                break
    data = repo.pattern_bytes(pattern_id, render)
    if data is None and fpath and Path(fpath).exists():
        try:
            data = Path(fpath).read_bytes()
        except OSError:
            data = None
    notes, bends = _load_pattern_notes(data) if data is not None else ([], [])

    if render == "clap":
        ch, prog = 9, None
    elif render == "piano":
        ch = track["channel"] if track["channel"] != 9 else 0
        prog = 0
    else:
        ch = track["channel"]
        prog = track["program"] if not track["is_drums"] else None

    out = [(0.0, "prog", ch, prog, 0)] if prog is not None else []
    for (s, d, pitch, vel) in notes:
        out.append((s * spt, "on", ch, pitch, vel))
        out.append(((s + d) * spt, "off", ch, pitch, 0))
    for (t, v) in bends:
        out.append((t * spt, "bend", ch, v, 0))
    out.sort(key=lambda e: (e[0], 1 if e[1] == "off" else 0))
    return out
