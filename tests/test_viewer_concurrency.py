"""Regression: the threaded viewer must serialise the shared SQLite connection."""
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from gms_ma.store import Repository
from gms_ma.viewer import _Handler
from tests.conftest import index_one, repetitive_song


def test_concurrent_library_requests(tmp_path):
    midi = tmp_path / "arr.mid"
    repetitive_song(midi, bar_count=4)
    index_one(tmp_path / "db.sqlite", midi, tmp_path / "out")
    repo = Repository(tmp_path / "db.sqlite")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.repo = repo
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]

    errors: list[str] = []

    def hit(path):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                json.loads(r.read())
        except Exception as exc:  # noqa: BLE001 - collect for assertion
            errors.append(f"{path}: {exc!r}")

    paths = ["/api/library", "/api/songs", "/api/midi-ports"] * 6
    threads = [threading.Thread(target=hit, args=(p,)) for p in paths]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors
    finally:
        server.shutdown()
        repo.close()
