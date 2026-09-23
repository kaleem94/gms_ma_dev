"""Whole-library browsing + DAW export helpers (no browser automation)."""
import gzip
import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import mido
import pytest

from gms_ma import clipboard, library as lib
from gms_ma.store import Repository
from tests.conftest import BAR, chord_bars, drum_pattern, index_one, repetitive_song, write_midi


@pytest.fixture
def repo(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=8)
    db = tmp_path / "db.sqlite"
    index_one(db, midi, tmp_path / "out")
    r = Repository(db)
    yield r
    r.close()


def _serve(repo):
    from gms_ma.viewer import _Handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.repo = repo
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.loads(r.read())


def _post(port, path, body):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


# ------------------------------------------------------------- library data
def test_copy_ch1_guards(tmp_path, monkeypatch):
    """copy-ch1 must 501 without a clipboard backend and 404 for unknown ids
    (no clipboard write happens for the 404 path)."""
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        monkeypatch.setattr(clipboard, "supported", lambda: False)
        status, body = _post(port, "/api/library/copy-ch1", {"pattern_id": 1})
        assert status == 501

        monkeypatch.setattr(clipboard, "supported", lambda: True)
        status, body = _post(port, "/api/library/copy-ch1", {"pattern_id": 999999})
        assert status == 404
    finally:
        server.shutdown()
        repo.close()
def test_catalog_rows(repo):
    rows = lib.catalog_rows(repo)
    assert rows
    names = {r["song"]["name"] for r in rows}
    assert len(names) == 1
    cover = [r for r in rows if r["kind"] == "cover"]
    assert cover, "expected some cover patterns"
    assert any(r["track"]["is_drums"] for r in rows)
    for r in rows:
        assert "file" in r
        assert r["duration_s"] >= 0


def test_apply_filters(repo):
    rows = lib.catalog_rows(repo)
    name = rows[0]["song"]["name"]
    # song drill-down
    assert {r["song"]["name"] for r in lib.apply_filters(rows, {"song": name})} == {name}
    # kind filter
    assert all(r["kind"] == "cover"
               for r in lib.apply_filters(rows, {"kind": ["cover"]}))
    # instrument family filter (AND across facets, OR inside one)
    drums = lib.apply_filters(rows, {"family": ["drums"]})
    assert drums and all(r["track"]["family"] == "drums" for r in drums)
    fams = {r["track"]["family"] for r in rows}
    mixed = lib.apply_filters(rows, {"family": list(fams)})
    assert {r["track"]["family"] for r in mixed} == fams
    # text search on instrument name
    hit = lib.apply_filters(rows, {"q": "drums"})
    assert hit and all("drums" in (r["track"]["name"] or "").lower()
                       for r in hit)
    # no filters -> the same list is returned (fast path, no copy)
    assert lib.apply_filters(rows, {}) is rows
    assert lib.apply_filters(rows, None) is rows


def test_cached_meta_is_cached(repo):
    m1 = lib.cached_meta(repo, {})
    m2 = lib.cached_meta(repo, {})
    assert m1 is m2                         # second call served from cache
    assert m1["total"] > 0
    assert m1["songs"] and m1["facets"]


def test_library_cache_refresh_and_read(repo):
    counts = lib.refresh_library_cache(repo)
    assert counts["facets"] > 0 and counts["songs"] > 0
    facets = lib.read_library_facets(repo.conn)
    songs = lib.read_library_songs(repo.conn)
    assert facets is not None and songs is not None
    rows = lib.catalog_rows(repo)
    for facet, want in lib.facet_counts(rows, {}).items():
        got = {c["value"]: c["n"] for c in facets[facet]}
        assert got == {c["value"]: c["n"] for c in want}, facet
    got_songs = {s["name"]: s for s in songs}
    for s in lib.song_summaries(rows):
        assert got_songs[s["name"]]["n_patterns"] == s["n_patterns"]
        assert got_songs[s["name"]]["n_placements"] == s["n_placements"]


def test_query_meta_uses_precomputed(repo, monkeypatch):
    lib.refresh_library_cache(repo)

    def boom(*_a, **_k):
        raise AssertionError("should have used the precomputed cache")

    monkeypatch.setattr(lib, "_facets_unfiltered", boom)
    monkeypatch.setattr(lib, "_query_songs", boom)
    meta = lib.query_meta(repo, {})
    assert meta["total"] > 0 and meta["facets"] and meta["songs"]


def test_query_meta_self_heals(repo):
    assert lib.read_library_facets(repo.conn) is None
    meta = lib.query_meta(repo, {})
    assert meta["total"] > 0
    assert lib.read_library_facets(repo.conn) is not None
    assert lib.read_library_songs(repo.conn) is not None


def test_cli_optimize(tmp_path):
    from argparse import Namespace

    from gms_ma import cli

    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    db = str(tmp_path / "db.sqlite")
    index_one(db, midi, tmp_path / "out")
    assert cli.cmd_optimize(Namespace(db=db)) == 0
    repo = Repository(db)
    try:
        assert lib.read_library_facets(repo.conn) is not None
        assert lib.read_library_songs(repo.conn) is not None
    finally:
        repo.close()


def test_sql_query_matches_pure_catalog(repo):
    """The SQL page/meta path must agree with the pure catalog implementation."""
    rows = lib.catalog_rows(repo)
    meta = lib.query_meta(repo, {})
    assert meta["total"] == len(rows)
    pure_facets = lib.facet_counts(rows, {})
    for facet, counts in pure_facets.items():
        got = {c["value"]: c["n"] for c in meta["facets"][facet]}
        want = {c["value"]: c["n"] for c in counts}
        assert got == want, facet
    page = lib.query_page(repo, {}, offset=0, limit=5)
    for got, want in zip(page, rows[:5]):
        assert got["pattern_id"] == want["pattern_id"]
        assert got["duration_s"] == want["duration_s"]
        assert got["ptags"] == want["ptags"]
        assert got["stags"] == want["stags"]
        assert got["stems"] == want["stems"]
        assert got["occ_count"] == want["occ_count"]
        assert got["track"] == want["track"]
    name = rows[0]["song"]["name"]
    pure = lib.apply_filters(rows, {"song": name})
    got = lib.query_page(repo, {"song": name}, 0, None)
    assert [r["pattern_id"] for r in got] == [r["pattern_id"] for r in pure]


def test_facet_counts_structure(repo):
    rows = lib.catalog_rows(repo)
    counts = lib.facet_counts(rows, {})
    assert counts["kind"]
    assert counts["family"]
    # pattern style facet: totals match rows that actually carry a style tag
    styled = sum(1 for r in rows if r["pvals"].get("style"))
    assert sum(c["n"] for c in counts["style"]) == styled
    # song genre facet totals match rows whose song carries a genre tag
    genre_rows = sum(1 for r in rows if r["svals"].get("genre"))
    assert sum(c["n"] for c in counts["genre"]) == genre_rows


def test_events_build(repo):
    rows = lib.catalog_rows(repo)
    cover = next(r for r in rows if r["kind"] == "cover")
    events = lib.pattern_events(repo, cover["pattern_id"])
    ons = [e for e in events if e[1] == "on"]
    offs = [e for e in events if e[1] == "off"]
    assert ons and len(ons) == len(offs)
    assert all(0 <= e[2] <= 15 for e in events)


def test_duration_matches_own_tempo_span(tmp_path):
    """Listed seconds must equal how long the loop actually plays: its tick
    length at the song's *initial* tempo, not the whole-song average (which
    drifts on songs that contain tempo changes)."""
    midi = tmp_path / "tempo.mid"
    write_midi(midi, [
        {"name": "drums", "channel": 9, "notes": drum_pattern(8)},
        {"name": "strings", "channel": 0, "notes": chord_bars(8),
         "programs": [(0, 48)]},
    ], tempo_changes=[(4 * BAR, 250000)])   # first tempo stays 500000 us
    db = tmp_path / "db.sqlite"
    index_one(db, midi, tmp_path / "out")
    repo = Repository(db)
    rows = lib.catalog_rows(repo)
    assert rows
    for r in rows:
        span = lib.pattern_span_seconds(repo, r["pattern_id"])
        # duration_s is rounded for display (3 dp), so allow that much slack
        assert abs(r["duration_s"] - span) <= 5e-4, r["content_id"]
        # span is length_ticks @ 500000 us/beat on a 480-ppq clock
        expect = r["length_ticks"] * 500000 / (480 * 1_000_000.0)
        assert abs(span - expect) < 1e-6, r["content_id"]
    repo.close()


def test_offset_events():
    from gms_ma.viewer import _offset_events

    ev = [(0.0, "prog", 0, 81, 0), (0.5, "on", 0, 60, 90), (0.8, "off", 0, 60, 0),
          (2.0, "on", 0, 64, 90), (2.3, "off", 0, 64, 0)]
    out = _offset_events(ev, 1.0)
    # program change is kept (re-scheduled at t0) so a mid-loop seek still
    # re-arms the patch; everything already sounded is dropped.
    assert out[0][:2] == (1.0, "prog")
    assert all(e[0] >= 1.0 - 1e-6 for e in out)
    assert {e[1] for e in out} == {"prog", "on", "off"}


# ------------------------------------------------------------- DAW sanitize
def test_sanitize_midi_remaps_channel(tmp_path):
    src = tmp_path / "loop.mid"
    write_midi(src, [{"name": "lead", "channel": 2, "notes": [
        (0, 240, 60, 90), (480, 240, 62, 90)],
        "programs": [(0, 81)], "bends": [(120, 1000)]}])
    dst = tmp_path / "loop_ch1.mid"
    lib.sanitize_midi(src, dst, channel=0)
    assert dst.exists()
    mf = mido.MidiFile(str(dst))
    channeled = {"note_on", "note_off", "program_change", "pitchwheel",
                 "control_change", "polytouch", "channel_aftertouch"}
    saw_note = saw_prog = False
    for tr in mf.tracks:
        for msg in tr:
            if msg.is_meta:
                continue
            assert msg.type not in channeled or msg.channel == 0
            if msg.type == "note_on":
                saw_note = True
            if msg.type == "program_change":
                saw_prog = True
    assert saw_note and saw_prog


def test_ch1_path_for():
    p = Path("out") / "loops" / "S" / "01_ch00_x_4b.mid"
    assert lib.ch1_path_for(str(p)).name == "01_ch00_x_4b_ch1.mid"


# ------------------------------------------------------------- endpoints
def test_library_endpoints(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/library") as r:
            html = r.read().decode("utf-8")
        assert "loop library" in html
        assert "/assets/js/library/app.js" in html
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/assets/js/library/app.js") as r:
            lib_js = r.read().decode("utf-8")
        assert "exportActions" in lib_js          # download/copy moved to the shared module
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/assets/js/common/exportActions.js") as r:
            exp_js = r.read().decode("utf-8")
        assert "library/download" in exp_js
        payload = _get(port, "/api/library")
        assert payload["patterns"] and payload["facets"]["kind"]
        assert payload["songs"] and payload["total"] > 0
        assert all("seconds" in s and s["seconds"] > 0 for s in payload["songs"])
        one = payload["patterns"][0]
        pid = one["pattern_id"]
        assert "id" in one["track"] and "track_index" in one["track"]
        assert "span_s" in one["track"]
        # original + DAW ch1 downloads return real MIDI bytes
        for mode in ("original", "ch1"):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/library/download"
                    f"?pattern_id={pid}&mode={mode}") as r:
                head = r.read(4)
            assert head == b"MThd", mode
        # drill-down filter restricts to the requested song
        drill = _get(port, "/api/library?song=" + one["song"]["name"])
        assert {p["song"]["name"] for p in drill["patterns"]} == {one["song"]["name"]}
    finally:
        server.shutdown()
        repo.close()


def test_library_jit_pagination(tmp_path):
    """The catalog is paged server-side; facets/songs/total stay global."""
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        full = _get(port, "/api/library?page_size=0")
        total = full["total"]
        assert total > 2

        p1 = _get(port, "/api/library?page=1&page_size=2")
        assert len(p1["patterns"]) == 2
        assert p1["page"] == 1 and p1["page_size"] == 2
        assert p1["pages"] == (total + 1) // 2
        assert p1["total"] == total

        # tree mode: song headers only, no loop rows shipped
        songs_only = _get(port, "/api/library?include_patterns=0&page_size=0")
        assert songs_only["patterns"] == []
        assert songs_only["total"] == total
        assert songs_only["songs"]

        # gzip is used for compressible JSON when the client asks for it
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/library?page=1&page_size=2")
        req.add_header("Accept-Encoding", "gzip")
        with urllib.request.urlopen(req) as r:
            assert r.headers.get("Content-Encoding") == "gzip"
            assert json.loads(gzip.decompress(r.read()))["page"] == 1
    finally:
        server.shutdown()
        repo.close()


def test_library_count_matches_total(tmp_path):
    """The cheap /count endpoint (loading indicator) agrees with /api/library."""
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        full = _get(port, "/api/library?page_size=0")
        count = _get(port, "/api/library/count")
        assert count["total"] == full["total"] > 0

        song = urllib.parse.quote(full["songs"][0]["name"])
        filtered = _get(port, "/api/library/count?song=" + song)
        assert 0 < filtered["total"] <= full["total"]
    finally:
        server.shutdown()
        repo.close()


def test_library_page_window(tmp_path):
    """`window=K` returns a contiguous block of pages around `page`, and the
    default page size is bounded (never "all") when omitted."""
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        total = _get(port, "/api/library?page_size=0")["total"]
        assert total > 4

        w = _get(port, "/api/library?page=2&page_size=2&window=1")
        assert w["window_start"] == 1 and w["window_end"] == 3
        assert len(w["patterns"]) == min(6, total)
        assert w["page"] == 2 and w["pages"] == (total + 1) // 2

        one = _get(port, "/api/library?page=2&page_size=2&window=0")
        assert one["window_start"] == one["window_end"] == 2
        assert len(one["patterns"]) == 2

        default = _get(port, "/api/library")
        assert default["page_size"] == 50          # bounded default, not "all"
    finally:
        server.shutdown()
        repo.close()


def test_clipboard_missing_file_raises():
    if not clipboard.supported():
        pytest.skip("windows only")
    with pytest.raises(FileNotFoundError):
        clipboard.copy_file_to_clipboard("Z:/definitely/not/a/real.mid")
