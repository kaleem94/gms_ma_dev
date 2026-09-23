# gms_ma/library.py

## catalog_rows / cached_catalog <!-- ref:gms_ma/library.py:77-231 -->
**Purpose**: Denormalise every unique pattern into a browsable row (song, track, tags, stems, occurrences, duration) and cache the list per DB mtime.
**Why**: Filtering/facet counts are pure-Python over a small catalog, which keeps OR-within-facet / AND-across-facet semantics simple and testable; the mtime cache avoids rebuilding on every request while staying correct after an index.
**Data Flow**: `repo` → `[row]`; `cached_catalog` memoises by `st_mtime_ns`.
**Relationships**: Used by `viewer._library_get`/`_library_count` and the library JS.

### apply_filters / facet_counts <!-- ref:gms_ma/library.py:244-293 -->
**Purpose**: Filter the catalogue and compute live facet counts (excluding a facet's own selection).
**Why**: Keeps the JIT server paging cheap: the full match set drives counts/songs, but only the requested page of patterns is serialised.
**Data Flow**: `rows, filters` → `matched rows`; `rows, filters` → `{facet: {value: count}}`.
**Relationships**: Used by `_library_get`/`_library_count`.

### resolve_pattern_file / sanitize_midi / retempo_midi <!-- ref:gms_ma/library.py:300-406 -->
**Purpose**: Materialise a pattern/stem to a real path (on-disk file or DB BLOB → temp cache), rewrite it to a single DAW channel, and rewrite its tempo.
**Why**: `resolve_pattern_file` lets download/clipboard flows work even for DB-only stores. `retempo_midi` sets the `set_tempo` meta (inserting one if absent) while leaving note ticks untouched, so an exported file at the export BPM plays at the speed the viewer auditioned.
**Data Flow**: `(repo, pattern_id, render)` → `(row, path)`; `data, bpm` → `bytes`; `src, bpm` → `Path`.
**Relationships**: Used by `viewer._library_download`, the clipboard/copy-ch1/sanitize endpoints, and the DAW export.

```python
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
```

### pattern_events / pattern_span_seconds / pattern_time_base <!-- ref:gms_ma/library.py:409-481 -->
**Purpose**: Build absolute-time play events for one DB pattern and its loop span.
**Why**: Mirrors the timeline viewer's event building but sources everything from the repository (song PPQ/initial tempo, track channel/program, stem file/BLOB), so library audition and DAW export agree on timing.
**Data Flow**: `(repo, pattern_id, render)` → `[(sec, kind, ch, a, b)]`; `(repo, pattern_id)` → `seconds`.
**Relationships**: Used by `viewer` `/api/library/play` and `/api/play-pattern`.

### refresh_library_cache / query_meta / cached_meta <!-- ref:gms_ma/library.py:500-600 -->
**Purpose**: Precompute and cache the unfiltered facet counts and song summaries; provide SQL-filtered metadata queries with caching.
**Why**: The `cached_meta` function uses a two-layer cache (DB mtime + filter signature) so repeated requests with the same filters skip the SQL aggregation. `refresh_library_cache` is called by `index`/`tag`/`delete`/`doctor`/`optimize` to keep the precomputed tables fresh.
**Data Flow**: `repo, filters` → `{total, facets, songs}`; `repo` → `{facets: int, songs: int}`.
**Relationships**: Used by `viewer._library_get`/`_library_count` and `cli.cmd_optimize`.