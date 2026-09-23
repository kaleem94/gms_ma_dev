"""Local web viewer: song-structure timeline with seek + optional live MIDI.

Serves a single self-contained HTML page (no external deps) and JSON APIs over
the SQLite repository.  Live audition is best-effort via :mod:`midi_out`; when
no MIDI device is present the UI simply disables the audio controls.
"""
from __future__ import annotations

import bisect
import gzip
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__
from . import clipboard
from . import library as lib
from . import midi_out
from .indexer import build_manifest
from .store import Repository

_DISCLAIMER = ("gms-ma is a computational analysis tool intended for "
               "musicological research, educational purposes, and structural "
               "analysis. Users are responsible for ensuring that their "
               "ingestion, storage, and commercial use of third-party MIDI "
               "files comply with local copyright laws and licensing agreements.")

_ABOUT = {"name": "gms-ma", "tagline": "GMS MIDI analyzer",
          "version": __version__,
          "license": "Apache-2.0", "license_url": "/license",
          "repo": "https://github.com/kaleem94/gms-ma",
          "disclaimer": _DISCLAIMER}

_PKG_DIR = Path(__file__).resolve().parent
_ASSETS_DIR = _PKG_DIR / "assets"
_ASSET = _ASSETS_DIR / "timeline.html"
_LIB_ASSET = _ASSETS_DIR / "library.html"


def _license_bytes() -> bytes | None:
    """Locate the Apache-2.0 text for the ``/license`` route.

    Checks a bundled ``gms_ma/LICENSE`` (wheel/sdist), the source-checkout repo
    root, then the installed distribution's metadata.
    """
    for candidate in (_PKG_DIR / "LICENSE", _PKG_DIR.parent / "LICENSE"):
        if candidate.is_file():
            try:
                return candidate.read_bytes()
            except OSError:
                pass
    try:
        from importlib.metadata import distribution

        dist = distribution("gms-ma")
        for name in ("LICENSE", "LICENSE.txt", "COPYING",
                     "licenses/LICENSE", "LICENSES/LICENSE"):
            try:
                text = dist.read_text(name)
            except Exception:
                text = None
            if text:
                return text.encode("utf-8")
        for f in dist.files or []:
            if f.name.upper().startswith(("LICENSE", "COPYING")):
                try:
                    return dist.locate_file(f).read_bytes()
                except OSError:
                    continue
    except Exception:
        pass
    return None

# MIME types for the static asset tree (JS/CSS are ES modules/stylesheets).
_MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".map": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
}

# valid stem render modes (rhythm skeletons + harmony/melody separation)
_STEM_RENDERS = ("piano", "clap", "harmony", "melody")


def _clamp_speed(v, default: float = 1.0) -> float:
    """Playback speed multiplier clamped to the UI range 0.25..3."""
    try:
        s = float(v)
    except (TypeError, ValueError):
        return default
    return min(3.0, max(0.25, s))


class TickClock:
    """Piecewise (tempo-map aware) tick -> seconds conversion."""

    def __init__(self, ppq: int, tempos: list, total_ticks: int):
        self._breaks: list[tuple[int, int, int]] = []  # (tick_from, tick_to, spt_sec)
        pts = sorted(tempos)
        if not pts:
            pts = [(0, 500000)]
        for i, (tick, us) in enumerate(pts):
            frm = tick
            upto = pts[i + 1][0] if i + 1 < len(pts) else total_ticks
            if upto > frm:
                self._breaks.append((frm, upto, us / (ppq * 1_000_000.0)))
        if not self._breaks:
            self._breaks = [(0, max(total_ticks, 1), 0.5 / (ppq * 1_000_000.0))]

    def sec(self, tick: int) -> float:
        acc = 0.0
        for frm, upto, spt in self._breaks:
            if tick <= frm:
                return acc
            if tick >= upto:
                acc += (upto - frm) * spt
            else:
                return acc + (tick - frm) * spt
        return acc


class _Player:
    """Background thread playing scheduled MIDI events on a winmm port."""

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._out: midi_out.WinMidiOut | None = None

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(self, events: list, t0: float = 0.0, device_id: int = 0,
             end_s: float | None = None, speed: float = 1.0):
        """Schedule ``events`` from ``t0``; keep the port open until ``end_s``.

        ``end_s`` is the transport time (seconds, same basis as ``events`` /
        ``t0``) at which playback should finish — used so a loop audition runs
        for its full loop span even when its last note ends early.  ``speed``
        (0.25..3) scales wall-clock time: 2.0 plays twice as fast.
        """
        self.stop()
        self._stop.clear()
        out = midi_out.WinMidiOut(device_id)
        self._out = out
        self._thread = threading.Thread(
            target=self._run, args=(events, t0, end_s, out, max(0.01, speed)),
            daemon=True,
        )
        self._thread.start()

    def _run(self, events, t0, end_s, out, speed=1.0):
        wall0 = time.perf_counter()
        for (t, kind, ch, a, b) in events:
            if t < t0 - 1e-6:
                continue
            if self._stop.is_set():
                break
            target = wall0 + (t - t0) / speed
            while True:
                remain = target - time.perf_counter()
                if remain <= 0:
                    break
                time.sleep(min(remain, 0.01))
                if self._stop.is_set():
                    return
            try:
                if kind == "on":
                    out.note_on(ch, a, b)
                elif kind == "off":
                    out.note_off(ch, a)
                elif kind == "prog":
                    out.program_change(ch, a)
                elif kind == "bend":
                    out.pitch_bend(ch, a + 8192)  # mido signed -> 14-bit wire
            except Exception:
                return
        # hold any trailing silence so the transport reaches the loop end
        if end_s is not None:
            target = wall0 + (end_s - t0) / speed
            while not self._stop.is_set():
                remain = target - time.perf_counter()
                if remain <= 0:
                    break
                time.sleep(min(remain, 0.01))
        try:
            out.all_notes_off()
        finally:
            out.close()
            self._out = None

    def stop(self):
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        if self._out is not None:
            try:
                self._out.all_notes_off()
                self._out.close()
            except Exception:
                pass
            self._out = None


def _pattern_read(data):
    from . import reconstruct as rc

    return rc._load_pattern_notes(data)


def _pat_bytes(manifest, pat, render, repo):
    """DB asset first; legacy on-disk file fallback (repo-less callers)."""
    if repo is not None:
        data = repo.pattern_bytes(pat["id"], render)
        if data is not None:
            return data
    # legacy fallback resolved from the manifest
    if render in _STEM_RENDERS:
        for st in pat.get("stems", []):
            if st["render"] == render and st.get("file"):
                try:
                    from pathlib import Path as _P
                    return _P(st["file"]).read_bytes()
                except OSError:
                    return None
    f = pat.get("file")
    if f:
        try:
            from pathlib import Path as _P
            return _P(f).read_bytes()
        except OSError:
            return None
    return None


def song_play_events(manifest: dict, enabled_tracks: set[int] | None = None,
                     repo=None) -> list:
    """Absolute-tick events [(sec, kind, ch, a, b)] reconstructing a whole song.

    ``enabled_tracks`` optionally restricts the mix to those track ids (mute /
    solo); None means everything.
    """
    song = manifest["song"]
    clock = TickClock(song["ticks_per_beat"], manifest["tempos"], song["total_ticks"])
    raw: list = []  # (tick, order, kind, ch, a, b)
    prog_at: dict[int, int] = {}
    track_rows = {t["id"]: t for t in manifest["tracks"]}
    enabled_ch = set()
    cache: dict[int, tuple] = {}
    for pl in manifest["placements"]:
        tr = track_rows[pl["track_id"]]
        if enabled_tracks is not None and tr["id"] not in enabled_tracks:
            continue
        pat = next((p for p in tr["patterns"] if p["id"] == pl["pattern_id"]), None)
        if not pat:
            continue
        if pat["id"] not in cache:
            data = _pat_bytes(manifest, pat, None, repo)
            cache[pat["id"]] = _pattern_read(data) if data is not None else ([], [])
        notes, bends = cache[pat["id"]]
        ch = tr["channel"]
        enabled_ch.add(ch)
        off = pl["start_tick"]
        for (s, d, pitch, vel) in notes:
            raw.append((off + s, 1, "on", ch, pitch, vel))
            raw.append((off + s + d, 0, "off", ch, pitch, 0))
        for (t, v) in bends:
            raw.append((off + t, 2, "bend", ch, v, 0))
        if tr["program"] is not None and not tr["is_drums"]:
            if ch not in prog_at:
                prog_at[ch] = tr["program"]
    if enabled_tracks is not None:
        # drop program changes that belong to now-muted channels
        prog_at = {c: p for c, p in prog_at.items() if c in enabled_ch}
    for ch, prog in prog_at.items():
        raw.append((0, -1, "prog", ch, prog, 0))
    raw.sort(key=lambda r: (r[0], r[1]))
    out = []
    for (tick, _o, kind, ch, a, b) in raw:
        out.append((clock.sec(tick), kind, ch, a, b))
    return out


def _offset_events(events: list, t0: float) -> list:
    """Restart a pattern mid-way (seek/resume).

    Events that already sounded before ``t0`` are dropped, but program changes
    are re-scheduled at ``t0`` so the instrument patch is still set after a
    seek into the middle of the loop.
    """
    out = []
    for (t, kind, ch, a, b) in events:
        if kind == "prog":
            out.append((t0, kind, ch, a, b))
        elif t >= t0 - 1e-6:
            out.append((t, kind, ch, a, b))
    out.sort(key=lambda e: (e[0], 1 if e[1] == "off" else 0))
    return out


def pattern_play_events(manifest: dict, pattern_id: int, render: str | None = None,
                        repo=None) -> list:
    song = manifest["song"]
    ppq = song["ticks_per_beat"]
    for tr in manifest["tracks"]:
        for pat in tr["patterns"]:
            if pat["id"] != pattern_id:
                continue
            data = _pat_bytes(manifest, pat, render, repo)
            notes, bends = _pattern_read(data) if data is not None else ([], [])
            us = manifest["tempos"][0][1] if manifest["tempos"] else 500000
            spt = us / (ppq * 1_000_000.0)
            if render == "clap":
                ch, prog = 9, None
            elif render == "piano":
                ch = tr["channel"] if tr["channel"] != 9 else 0
                prog = 0
            else:
                ch, prog = tr["channel"], tr["program"]
            out = [(0.0, "prog", ch, prog, 0)] if prog is not None else []
            for (s, d, pitch, vel) in notes:
                out.append((s * spt, "on", ch, pitch, vel))
                out.append(((s + d) * spt, "off", ch, pitch, 0))
            for (t, v) in bends:
                out.append((t * spt, "bend", ch, v, 0))
            out.sort(key=lambda e: (e[0], 1 if e[1] == "off" else 0))
            return out
    return []


def _json(data, code=200):
    body = json.dumps(data).encode()
    return code, {"Content-Type": "application/json; charset=utf-8",
                  "Content-Length": str(len(body))}, body


class _Handler(BaseHTTPRequestHandler):
    player: _Player = _Player()
    repo: Repository | None = None

    def log_message(self, *a):
        pass

    def _write_body(self, code, hdr, body: bytes):
        """Send a response, gzip-compressing compressible bodies when allowed.

        A browser routinely cancels an in-flight request (refresh, navigate,
        abort) which aborts the socket mid-write; that is not a server error, so
        connection teardown is swallowed instead of surfacing a traceback.
        """
        hdr = dict(hdr)
        ctype = hdr.get("Content-Type", "")
        compressible = (ctype.startswith("application/json")
                        or ctype.startswith("text/")
                        or ctype.startswith("application/javascript")
                        or ctype.startswith("image/svg"))
        if (len(body) > 1024 and compressible
                and "gzip" in (self.headers.get("Accept-Encoding", "") or "").lower()):
            body = gzip.compress(body, 5)
            hdr["Content-Encoding"] = "gzip"
        hdr["Content-Length"] = str(len(body))
        try:
            self.send_response(code)
            for k, v in hdr.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionError, OSError):
            self.close_connection = True

    def _serve_asset(self, asset: Path, ctype: str = "text/html; charset=utf-8"):
        html = asset.read_text(encoding="utf-8") if asset.exists() else None
        if html is None:
            return self._error(f"{asset.name} asset not found", 500)
        return self._write_body(200, {"Content-Type": ctype}, html.encode("utf-8"))

    def _serve_static(self, rel: str):
        """Serve a file from the assets tree, guarding against path traversal."""
        rel = rel.replace("\\", "/").lstrip("/")
        if not rel:
            return self._error("not found", 404)
        root = _ASSETS_DIR.resolve()
        try:
            target = (root / rel).resolve()
        except (OSError, ValueError):
            return self._error("not found", 404)
        if target != root and root not in target.parents:
            return self._error("not found", 404)
        if not target.is_file():
            return self._error("not found", 404)
        try:
            body = target.read_bytes()
        except OSError:
            return self._error("not found", 404)
        ctype = _MIME.get(target.suffix.lower(), "application/octet-stream")
        return self._write_body(200, {"Content-Type": ctype,
                                      "Cache-Control": "no-cache"}, body)

    def _serve_license(self):
        """Serve the project's Apache 2.0 text so the About panel works offline."""
        body = _license_bytes()
        if body is None:
            return self._error("license not found", 404)
        return self._write_body(200, {"Content-Type": "text/plain; charset=utf-8",
                                      "Cache-Control": "no-cache"}, body)

    def _error(self, msg, code=400):
        body = json.dumps({"error": msg}).encode()
        return self._write_body(code, {"Content-Type": "application/json"}, body)

    def do_GET(self):
        path, _, qs = self.path.partition("?")
        if path.startswith("/assets/"):
            return self._serve_static(path[len("/assets/"):])
        if path in ("/", "/index.html"):
            return self._serve_asset(_ASSET)
        if path in ("/library", "/library.html"):
            return self._serve_asset(_LIB_ASSET)
        if path == "/license":
            return self._serve_license()
        if path == "/api/about":
            return self._write_body(200, {"Content-Type": "application/json",
                                          "Cache-Control": "no-cache"},
                                    json.dumps(_ABOUT).encode("utf-8"))
        # These endpoints touch no DB state, so serve them before acquiring the
        # shared repository lock (a heavy /api/library build must not stall them).
        if path == "/api/midi-ports":
            code, hdr, body = _json({
                "available": midi_out.available(),
                "outputs": midi_out.list_outputs(),
                "clipboard": clipboard.supported(),
            })
            return self._write_body(code, hdr, body)
        if path == "/api/status":
            code, hdr, body = _json({"playing": self.player.playing})
            return self._write_body(code, hdr, body)
        # SQLite connection is shared across request threads -> serialise DB use
        with self.repo.lock:
            if path == "/api/songs":
                code, hdr, body = _json(self.repo.all_songs())
            elif path == "/api/song":
                name = _query_param(qs, "name")
                if not name:
                    return self._error("missing name")
                try:
                    code, hdr, body = _json(build_manifest(self.repo, name))
                except KeyError:
                    return self._error(f"no such song: {name}")
            elif path == "/api/library":
                return self._library_get(qs)
            elif path == "/api/library/count":
                return self._library_count(qs)
            elif path == "/api/library/download":
                return self._library_download(qs)
            else:
                return self._error("not found", 404)
            self._write_body(code, hdr, body)

    def _library_get(self, qs: str):
        filters: dict = {"song": _query_param(qs, "song") or None,
                         "q": _query_param(qs, "q") or ""}
        for facet in _LIB_FACETS:
            vals = _query_list(qs, facet)
            if vals:
                filters[facet] = set(vals)
        meta = lib.cached_meta(self.repo, filters)       # SQL aggregates (cached)
        total = meta["total"]

        # JIT: only the requested slice (or page window) is read from SQL and
        # assembled; facets/songs/total still describe the whole match set.  A
        # bounded default page size keeps callers from pulling everything.
        page = max(1, _query_int(qs, "page", 1))
        page_size = _query_int(qs, "page_size", 50)      # 0 == all
        window = max(0, _query_int(qs, "window", 0))
        pages = 1 if page_size <= 0 else max(1, (total + page_size - 1) // page_size)
        page = min(page, pages)
        include = _query_param(qs, "include_patterns")
        if include in ("0", "false", "no"):
            slice_rows = []
            wstart = wend = page
        elif page_size > 0:
            wstart = max(1, page - window)               # +/- window pages
            wend = min(pages, page + window)
            slice_rows = lib.query_page(self.repo, filters,
                                        offset=(wstart - 1) * page_size,
                                        limit=(wend - wstart + 1) * page_size)
        else:
            slice_rows = lib.query_page(self.repo, filters, offset=0, limit=None)
            wstart = wend = page
        code, hdr, body = _json({
            "total": total,
            "page": page,
            "page_size": page_size,
            "pages": pages,
            "window_start": wstart,
            "window_end": wend,
            "patterns": [_client_pattern_row(r) for r in slice_rows],
            "songs": meta["songs"],
            "facets": meta["facets"],
            "active_song": filters.get("song") or None,
        })
        self._write_body(code, hdr, body)

    def _library_count(self, qs: str):
        """Cheap match count (no pattern rows/facets) for the loading indicator."""
        filters: dict = {"song": _query_param(qs, "song") or None,
                         "q": _query_param(qs, "q") or ""}
        for facet in _LIB_FACETS:
            vals = _query_list(qs, facet)
            if vals:
                filters[facet] = set(vals)
        meta = lib.cached_meta(self.repo, filters)
        code, hdr, body = _json({"total": meta["total"]})
        self._write_body(code, hdr, body)

    def _library_download(self, qs: str):
        pid_s = _query_param(qs, "pattern_id")
        render = _query_param(qs, "render")
        mode = _query_param(qs, "mode") or "original"
        bpm = _query_param(qs, "bpm")
        if not pid_s:
            return self._error("missing pattern_id")
        render = render if render in _STEM_RENDERS else None
        try:
            _pat, src = lib.resolve_pattern_file(self.repo, int(pid_s), render)
        except (KeyError, TypeError, ValueError):
            return self._error("pattern not found", 404)
        path = src
        if mode == "ch1":
            dst = lib.ch1_path_for(src)
            if not Path(dst).exists():
                try:
                    lib.sanitize_midi(src, dst)
                except Exception as exc:
                    return self._error(f"sanitize failed: {exc}", 500)
            path = str(dst)
        if not Path(path).exists():
            return self._error("file not found", 404)
        try:
            data = Path(path).read_bytes()
        except OSError as exc:
            return self._error(str(exc), 500)
        name = Path(path).name
        if bpm:
            try:
                data = lib.retempo_midi(data, float(bpm))
                name = f"{Path(path).stem}_{int(round(float(bpm)))}bpm.mid"
            except Exception as exc:
                return self._error(f"retempo failed: {exc}", 500)
        self._write_body(200, {
            "Content-Type": "audio/midi",
            "Content-Disposition": f'attachment; filename="{name}"',
        }, data)

    def do_POST(self):
        # shared SQLite connection -> serialise with other request threads
        with self.repo.lock:
            self._do_POST()

    def _do_POST(self):
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length", 0) or 0)
        payload = json.loads(self.rfile.read(length)) if length else {}
        if path == "/api/play":
            if not midi_out.available():
                return self._error("no MIDI backend available", 501)
            name = payload.get("song")
            if not name:
                return self._error("missing song")
            manifest = build_manifest(self.repo, name)
            tracks = payload.get("tracks")
            enabled = set(int(t) for t in tracks) if isinstance(tracks, list) else None
            try:
                self.player.play(
                    song_play_events(manifest, enabled_tracks=enabled, repo=self.repo),
                    t0=float(payload.get("t", 0.0)),
                    device_id=int(payload.get("device", 0)),
                    speed=_clamp_speed(payload.get("speed", 1.0)),
                )
            except Exception as exc:
                return self._error(str(exc), 500)
            code, hdr, body = _json({"ok": True,
                                     "total_s": manifest["song"]["seconds"]})
        elif path == "/api/play-pattern":
            if not midi_out.available():
                return self._error("no MIDI backend available", 501)
            name = payload.get("song")
            pid = payload.get("pattern_id")
            render = payload.get("render")
            if not name or not pid:
                return self._error("missing song/pattern_id")
            manifest = build_manifest(self.repo, name)
            ev = pattern_play_events(manifest, int(pid), repo=self.repo,
                                     render=render if render in _STEM_RENDERS else None)
            if not ev:
                return self._error("pattern not found", 404)
            span = lib.pattern_span_seconds(self.repo, int(pid))
            try:
                self.player.play(ev, t0=0.0, end_s=span if span > 0 else None,
                                 device_id=int(payload.get("device", 0)),
                                 speed=_clamp_speed(payload.get("speed", 1.0)))
            except Exception as exc:
                return self._error(str(exc), 500)
            code, hdr, body = _json({"ok": True, "total_s": round(span, 3)})
        elif path == "/api/library/play":
            if not midi_out.available():
                return self._error("no MIDI backend available", 501)
            pid = payload.get("pattern_id")
            if not pid:
                return self._error("missing pattern_id")
            render = payload.get("render")
            ev = lib.pattern_events(self.repo, int(pid),
                                    render=render if render in _STEM_RENDERS else None)
            if not ev:
                return self._error("pattern not found", 404)
            span = lib.pattern_span_seconds(self.repo, int(pid))
            try:
                t0 = float(payload.get("t", 0.0) or 0.0)
                if t0 > 0:
                    ev = _offset_events(ev, t0)
                self.player.play(ev, t0=t0, end_s=span,
                                 device_id=int(payload.get("device", 0)),
                                 speed=_clamp_speed(payload.get("speed", 1.0)))
            except Exception as exc:
                return self._error(str(exc), 500)
            code, hdr, body = _json({"ok": True, "total_s": round(span, 3)})
        elif path == "/api/library/clipboard":
            if not clipboard.supported():
                return self._error("clipboard file copy is unavailable", 501)
            pid = payload.get("pattern_id")
            if not pid:
                return self._error("missing pattern_id")
            render = payload.get("render")
            try:
                _pat, src = lib.resolve_pattern_file(
                    self.repo, int(pid),
                    render=render if render in _STEM_RENDERS else None)
                if payload.get("bpm"):
                    src = str(lib.retempo_file(src, float(payload["bpm"])))
                clipboard.copy_file_to_clipboard(src)
            except KeyError:
                return self._error("pattern not found", 404)
            except Exception as exc:
                return self._error(str(exc), 500)
            code, hdr, body = _json({"ok": True})
        elif path == "/api/library/copy-ch1":
            if not clipboard.supported():
                return self._error("clipboard file copy is unavailable", 501)
            pid = payload.get("pattern_id")
            if not pid:
                return self._error("missing pattern_id")
            render = payload.get("render")
            try:
                dst = lib.sanitize_for_daw(
                    self.repo, int(pid),
                    render=render if render in _STEM_RENDERS else None)
                if payload.get("bpm"):
                    dst = lib.retempo_file(dst, float(payload["bpm"]))
                clipboard.copy_file_to_clipboard(str(dst))
            except (KeyError, FileNotFoundError) as exc:
                return self._error(str(exc), 404)
            except Exception as exc:
                return self._error(str(exc), 500)
            code, hdr, body = _json({"ok": True, "filename": Path(dst).name})
        elif path == "/api/library/sanitize":
            pid = payload.get("pattern_id")
            if not pid:
                return self._error("missing pattern_id")
            render = payload.get("render")
            try:
                dst = lib.sanitize_for_daw(
                    self.repo, int(pid),
                    render=render if render in _STEM_RENDERS else None)
                if payload.get("bpm"):
                    dst = lib.retempo_file(dst, float(payload["bpm"]))
            except (KeyError, FileNotFoundError) as exc:
                return self._error(str(exc), 404)
            except Exception as exc:
                return self._error(str(exc), 500)
            clipped = False
            if clipboard.supported():
                try:
                    clipboard.copy_file_to_clipboard(str(dst))
                    clipped = True
                except Exception:
                    clipped = False
            code, hdr, body = _json({"ok": True, "filename": Path(dst).name,
                                     "clipped": clipped})
        elif path == "/api/stop":
            self.player.stop()
            code, hdr, body = _json({"ok": True})
        else:
            return self._error("not found", 404)
        self._write_body(code, hdr, body)


def _query_param(qs: str, key: str) -> str | None:
    from urllib.parse import parse_qs

    return parse_qs(qs).get(key, [None])[0]


def _query_int(qs: str, key: str, default: int = 0) -> int:
    raw = _query_param(qs, key)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


_LIB_FACETS = ("style", "key", "mode", "energy",
               "genre", "emotion", "tempo_class", "family", "kind")


def _query_list(qs: str, key: str) -> list[str]:
    """Collect a multi-select facet param (repeatable and/or comma-separated)."""
    from urllib.parse import parse_qs

    out: list[str] = []
    for raw in parse_qs(qs).get(key, []):
        for part in raw.split(","):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _client_pattern_row(r: dict) -> dict:
    d = dict(r)
    d.pop("file", None)
    d.pop("pvals", None)
    d.pop("svals", None)
    return d


class _QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Threaded server that swallows client-disconnect errors.

    Browsers abort in-flight requests all the time (refresh, navigate, cancel);
    socketserver's default ``handle_error`` would dump a traceback for each.
    """

    daemon_threads = True

    def handle_error(self, request, client_address):
        import sys

        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionError, OSError)):
            return
        super().handle_error(request, client_address)


def _bind_server(port: int, attempts: int = 20):
    """Bind the viewer to the first free port at/after ``port``.

    A stale viewer or another app holding the default port used to make
    ``serve`` raise and exit, leaving the browser with ERR_CONNECTION_REFUSED.
    """
    last: OSError | None = None
    for candidate in range(port, port + max(1, attempts)):
        try:
            return _QuietThreadingHTTPServer(("127.0.0.1", candidate), _Handler), candidate
        except OSError as exc:
            last = exc
    raise last if last else OSError(f"could not bind a port from {port}")


def serve(repo: Repository, port: int = 8123, open_browser: bool = True):
    _Handler.repo = repo
    if not _ASSET.exists():
        raise FileNotFoundError(f"viewer asset missing at {_ASSET}")
    if not _ASSETS_DIR.is_dir():
        raise FileNotFoundError(f"assets directory missing at {_ASSETS_DIR}")

    def _warm_catalog():
        # Use a dedicated connection so the (possibly large) SQL aggregation and
        # default meta never hold the viewer's shared repo lock and stall
        # /api/song.  Warming the no-filter meta makes the first /library load a
        # cache hit instead of aggregating on the request thread.
        try:
            warm = Repository(repo.db_path)
            try:
                lib.cached_meta(warm, {})
            finally:
                warm.close()
        except Exception:
            pass

    threading.Thread(target=_warm_catalog, daemon=True).start()
    srv, port = _bind_server(port)
    print(f"gms-ma viewer: http://127.0.0.1:{port}")
    if open_browser:
        try:
            import webbrowser

            webbrowser.open(f"http://127.0.0.1:{port}")
        except Exception:
            pass
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _Handler.player.stop()
        srv.server_close()
