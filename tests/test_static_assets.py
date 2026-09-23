"""Static asset serving: shells, ES modules/CSS, MIME types, traversal guard."""
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

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


def test_shells_reference_modules(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        st, ct, body = _fetch(port, "/")
        assert st == 200 and "text/html" in ct
        assert b"/assets/js/timeline/app.js" in body
        assert b"/assets/css/tokens.css" in body

        st, ct, body = _fetch(port, "/library")
        assert st == 200 and "text/html" in ct
        assert b"/assets/js/library/app.js" in body
        assert b"/assets/css/tokens.css" in body
    finally:
        server.shutdown()
        repo.close()


def test_static_modules_and_mime(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        for path, mime in (
            ("/assets/css/tokens.css", "text/css"),
            ("/assets/css/components.css", "text/css"),
            ("/assets/js/common/settings.js", "text/javascript"),
            ("/assets/js/common/theme.js", "text/javascript"),
            ("/assets/js/common/components/aboutPanel.js", "text/javascript"),
            ("/assets/js/common/components/miniPlayer.js", "text/javascript"),
            ("/assets/js/common/exportActions.js", "text/javascript"),
            ("/assets/js/timeline/app.js", "text/javascript"),
            ("/assets/js/library/app.js", "text/javascript"),
        ):
            st, ct, body = _fetch(port, path)
            assert st == 200, path
            assert mime in ct, (path, ct)
            assert body, path
    finally:
        server.shutdown()
        repo.close()


def test_static_traversal_and_missing(tmp_path):
    repo = Repository(tmp_path / "db.sqlite")
    server = _serve(repo)
    port = server.server_address[1]
    try:
        st, _ct, _body = _fetch(port, "/assets/../gms_ma/viewer.py")
        assert st in (403, 404)
        st, _ct, _body = _fetch(port, "/assets/does-not-exist.js")
        assert st == 404
        st, _ct, _body = _fetch(port, "/assets/")
        assert st == 404
    finally:
        server.shutdown()
        repo.close()


def test_write_body_swallows_client_disconnect():
    """A browser aborting mid-write must not raise out of the handler."""
    class AbortingWFile:
        def write(self, _data):
            raise ConnectionAbortedError(10053, "software caused connection abort")

    class FakeHandler:
        headers = {"Accept-Encoding": "identity"}
        close_connection = False
        wfile = AbortingWFile()

        def send_response(self, _code):
            pass

        def send_header(self, _k, _v):
            pass

        def end_headers(self):
            pass

    handler = FakeHandler()
    _Handler._write_body(handler, 200, {"Content-Type": "text/plain"}, b"hello")
    assert handler.close_connection is True


def test_bind_server_returns_bound_socket():
    import socket

    from gms_ma.viewer import _bind_server

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    srv, bound = _bind_server(port)
    try:
        assert bound >= port
    finally:
        srv.server_close()
