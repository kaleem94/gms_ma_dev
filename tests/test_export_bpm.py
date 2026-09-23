"""Export-BPM (retempo) + playback-speed clamp."""
import io
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import mido

from gms_ma import library as lib
from gms_ma.store import Repository
from gms_ma.viewer import _Handler, _clamp_speed
from tests.conftest import index_one, repetitive_song, write_midi


def _serve(repo):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.repo = repo
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _tempos(data: bytes):
    mf = mido.MidiFile(file=io.BytesIO(data))
    return [m.tempo for tr in mf.tracks for m in tr if m.type == "set_tempo"]


def test_retempo_midi_sets_tempo(tmp_path):
    midi = tmp_path / "a.mid"
    write_midi(midi, parts=[{"name": "x", "channel": 0, "notes": [(0, 480, 60, 100)]}])
    out = lib.retempo_midi(midi.read_bytes(), 150)
    tempos = _tempos(out)
    assert tempos and abs(tempos[0] - 400000) < 1     # 60e6 / 150


def test_download_applies_bpm(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/library?page_size=1") as r:
            pid = json.loads(r.read())["patterns"][0]["pattern_id"]
        url = f"http://127.0.0.1:{port}/api/library/download?pattern_id={pid}&bpm=150"
        with urllib.request.urlopen(url) as r:
            body = r.read()
            assert "150bpm" in r.headers.get("Content-Disposition", "")
        tempos = _tempos(body)
        assert tempos and abs(tempos[0] - 400000) < 1
    finally:
        server.shutdown()
        repo.close()


def test_clamp_speed():
    assert _clamp_speed(2) == 2.0
    assert _clamp_speed("1.5") == 1.5
    assert _clamp_speed(0.1) == 0.25
    assert _clamp_speed(9) == 3.0
    assert _clamp_speed("bad") == 1.0
    assert _clamp_speed(None) == 1.0
