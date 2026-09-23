# gms_ma/viewer.py

## _clamp_speed <!-- ref:gms_ma/viewer.py:52-59 -->
**Purpose**: Clamp a playback speed multiplier to the UI range 0.25–3.
**Why**: The client can send anything; the server must be defensive.
**Data Flow**: `any` → `float in [0.25, 3]` (default 1.0 on parse failure).
**Relationships**: Used by all three play endpoints.

### TickClock <!-- ref:gms_ma/viewer.py:61-88 -->
**Purpose**: Piecewise tempo-map-aware tick→seconds conversion for playback event scheduling.
**Why**: Mirrors `bars.BarMap` timing but is self-contained for the viewer, so it does not depend on the analysis objects.
**Data Flow**: `(ppq, tempos, total_ticks)` → `sec(tick)`.
**Relationships**: Used by `song_play_events`.

### _Player <!-- ref:gms_ma/viewer.py:89-171 -->
**Purpose**: Background thread that schedules MIDI events on a winmm port, honouring a start offset, an optional end hold, and a speed multiplier.
**Why**: The Windows Multimedia API is event-based, so timing is a `sleep` loop against `wall0 + (t - t0)/speed`. A `threading.Event` gives prompt cancellation; `all_notes_off` on exit avoids stuck notes. The port is opened per `play()` so device selection is per-request.
**Data Flow**: `events, t0, end_s, speed, device` → live MIDI; `stop()` cancels.
**Relationships**: Used by `_do_POST` play endpoints; `midi_out` does the raw wire calls.

```python
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
```

### _Handler (HTTP request handler) <!-- ref:gms_ma/viewer.py:173-600 -->
**Purpose**: A threaded stdlib HTTP server that serves the timeline viewer, the loop library, JSON APIs for playback/clipboard, and static assets.
**Why**: No external web framework or build step is needed; the entire UI is served from a single `assets/` directory. SQLite access is serialised through `repo.lock` to avoid corruption from concurrent requests.
**Data Flow**: HTTP request → `do_GET`/`do_POST` → DB query or file serve → JSON/HTML response.
**Relationships**: Uses `store.Repository`, `indexer.build_manifest`, `library.*`, `midi_out`, `clipboard`; serves `assets/timeline.html` and `assets/library.html`.

### _serve_static / _serve_asset <!-- ref:gms_ma/viewer.py:340-400 -->
**Purpose**: Serve static files from the assets directory with path traversal protection and MIME type detection.
**Why**: `_serve_static` guards against path traversal by resolving the target path and checking it is within the assets root. `_serve_asset` serves the main HTML pages. Both use `_write_body` which handles gzip compression for compressible content types and gracefully handles broken client connections.
**Data Flow**: HTTP request path → file bytes → HTTP response with correct Content-Type.
**Relationships**: Called by `do_GET` for `/assets/*`, `/`, `/library`, `/license` endpoints.

### _json / _write_body <!-- ref:gms_ma/viewer.py:280-340 -->
**Purpose**: Helper methods for sending JSON responses and writing HTTP response bodies with optional gzip compression.
**Why**: `_write_body` handles gzip compression for compressible content types (JSON, text, JS, SVG) when the client advertises `gzip` in `Accept-Encoding`. Broken pipe and connection errors are caught and silently handled since they indicate the client cancelled the request.
**Data Flow**: `data, code` → HTTP response body.
**Relationships**: Used by all API endpoints in `_Handler`.