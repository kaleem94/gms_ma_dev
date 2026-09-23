"""Viewer API & audio-event scheduling tests (no browser automation)."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from gms_ma.store import Repository
from gms_ma.viewer import _Handler, pattern_play_events, song_play_events
from tests.conftest import index_one, repetitive_song


def _serve(repo):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.repo = repo
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
        return json.loads(r.read())


def test_api_endpoints(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    try:
        songs = _get(server.server_address[1], "/api/songs")
        assert songs and songs[0]["name"]
        name = songs[0]["name"]
        m = _get(server.server_address[1], "/api/song?name=" + name)
        assert m["placements"] and m["bars"]
        assert "genre" in m["tags"] or "energy" in m["tags"]
    finally:
        server.shutdown()
        repo.close()


def test_song_play_events_respect_enabled_tracks(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    from gms_ma.indexer import build_manifest

    m = build_manifest(repo, repo.all_songs()[0]["name"])
    all_events = song_play_events(m)
    all_ons = [e for e in all_events if e[1] == "on"]
    # pick the bass track (channel 1) from the synthetic arrangement
    bass = next(tr for tr in m["tracks"] if tr["channel"] == 1)
    ev = song_play_events(m, enabled_tracks={bass["id"]})
    ons = [e for e in ev if e[1] == "on"]
    assert ons and len(ons) < len(all_ons)
    assert all(e[2] == 1 for e in ons)               # only channel 1 sounds
    assert not any(e[1] == "prog" and e[2] == 0 for e in ev)  # muted prog dropped
    # absent filter keeps everything
    assert len(song_play_events(m)) == len(all_events)
    repo.close()


def test_play_events_build(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    from gms_ma.indexer import build_manifest

    m = build_manifest(repo, repo.all_songs()[0]["name"])
    events = song_play_events(m)
    ons = [e for e in events if e[1] == "on"]
    assert ons, "expected playable note events"
    # every note has a matching off after it in time
    ons_done = 0
    for (t, kind, ch, a, b) in events:
        if kind == "off":
            ons_done += 1
    assert ons_done == len(ons)
    # pattern audition builds too
    pid = m["placements"][0]["pattern_id"]
    pat_events = pattern_play_events(m, pid)
    assert pat_events
    repo.close()
