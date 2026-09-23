"""Viewer About panel: /api/about metadata and the local /license text."""
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import gms_ma
from gms_ma.store import Repository
from gms_ma.viewer import _Handler


def _serve(repo):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.repo = repo
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


def _fetch(port, path):
    """Return (status, content_type, body_bytes) without raising on 4xx."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()


def test_api_about_reports_package_metadata(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        st, ct, body = _fetch(port, "/api/about")
        assert st == 200 and "application/json" in ct
        data = json.loads(body)
        assert data["name"] == "gms-ma"
        assert data["version"] == gms_ma.__version__
        assert data["license"] == "Apache-2.0"
        assert data["license_url"] == "/license"
        assert data["repo"].startswith("https://github.com/")
    finally:
        server.shutdown()
        repo.close()


def test_license_route_serves_apache_text(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        st, ct, body = _fetch(port, "/license")
        assert st == 200 and "text/plain" in ct
        text = body.decode("utf-8")
        assert "Apache License" in text
        assert "Version 2.0" in text
    finally:
        server.shutdown()
        repo.close()
