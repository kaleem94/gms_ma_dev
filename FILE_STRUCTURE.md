# Project Structure — gms-ma

A map of the repository: every tracked file, grouped by directory, with a short
description. Paths are relative to the repo root.

> Generated / ignored directories are **not** listed: `.venv/`, `.git/`,
> `__pycache__/`, `.pytest_cache/`, `out/` (exported loops), `db/` (the SQLite
> analysis database) and local `data/` (your own MIDI collections).

---

## Repository root

| File | Description |
|---|---|
| `README.md` | Main project documentation: features, architecture, quickstart, commands, design notes. |
| `REVIEW.md` | Concatenated code review of every module (overview + data-flow diagram + per-module notes). |
| `FILE_STRUCTURE.md` | This file — annotated repository map. |
| `LICENSE` | Apache License 2.0 full text. |
| `pyproject.toml` | Packaging metadata (setuptools), `mido>=1.3` dependency, `gms-ma` console entry point and package data. |
| `.gitattributes` | Normalises line endings to LF and marks binary assets. |
| `.gitignore` | Excludes venv, caches, generated `out/`, `db/*.db` and local `data/`/`db/`. |
| `pytest.ini` | Pytest config (`pythonpath = .`, `testpaths = tests`, quiet output). |
| `requirements.txt` | Runtime dependency: `mido>=1.3` (kept for convenience; authoritative deps live in `pyproject.toml`). |
| `requirements-dev.txt` | Dev dependencies: runtime deps plus `pytest>=8`. |
| `.github/workflows/ci.yml` | CI: pytest on Windows + Linux and a wheel/sdist build check. |

---

## `gms_ma/` — Python package

The analysis engine, storage layer, CLI, HTTP server and desktop GUI. Runtime is
the standard library plus `mido` only. Also contains `assets/`, the bundled web
UI (described in its own section below).

| File | Description |
|---|---|
| `__init__.py` | Package docstring and `__version__`. |
| `__main__.py` | Enables `python -m gms_ma`; dispatches to `cli.main`. |
| `model.py` | Neutral in-memory MIDI model (`Note`, `PitchBend`, `Instrument`, `Song`, tempo/timesig events). |
| `parse.py` | The only mido read-side module; parses files/bytes into `model.Song`, converting corrupt files to `ParseError`. |
| `bars.py` | Pure tick ↔ bar ↔ second math (`BarMap`) across tempo and time-signature maps. |
| `segment.py` | Bar-aligned loop/cover detection: adaptive grid, pitch/rhythm lenses, `period`/`repeat`/`hybrid` engines, `AnalysisConfig`. |
| `motif.py` | Grid-free motif catalogue: mines repeated note windows of any length, with transposition-aware similarity. |
| `harmony.py` | Chord-sequence analysis for a note window and melody/harmony layer separation (`split_layers`). |
| `music_theory.py` | Krumhansl–Schmuckler key/mode detection, chord naming (`detect_chord`) and roman-numeral labelling. |
| `features.py` | Pure numeric descriptors for a note window/song (density, syncopation, pitch stats, key, harmony summary). |
| `tag.py` | Heuristic tags (`key`, `mode`, `chord`, `harmony`, `energy`, `style`) and song-level `genre`/`emotion`. |
| `export.py` | Renders `PatternContent` back to MIDI bytes (full loop, note subset, or rhythm skeleton). |
| `store.py` | SQLite repository: schema, CRUD, cascading `delete_song`, MIDI BLOBs, tag precedence. |
| `indexer.py` | Composition root for `index`: parse → segment → mine → persist → manifest, with progress/cancel callbacks. |
| `reconstruct.py` | Rebuilds a song note-for-note from stored placements and diffs it against the original. |
| `library.py` | Cross-corpus catalogue: denormalised rows, filtering/facet counts, pattern playback events, retempo/DAW export helpers. |
| `viewer.py` | Threaded stdlib HTTP server: JSON APIs, static assets, and live MIDI playback scheduling. |
| `doctor.py` | Scans the DB for corrupted songs and reports which are fixable. |
| `gui.py` | tkinter desktop indexer (folder/file picker, progress, engine/options, viewer launcher). |
| `clipboard.py` | Windows-only `CF_HDROP` file-to-clipboard helper via `ctypes` (degrades gracefully). |
| `midi_out.py` | Minimal live MIDI output via the Windows Multimedia API (`ctypes`), no third-party backend. |
| `cli.py` | Argument parser and dispatch for every subcommand (`index`, `list`, `manifest`, `view`, `gui`, `tag`, …). |

---

## `gms_ma/assets/` — Web UI (package data; native ES modules, no build step)

Shipped inside the Python package so `pip install gms-ma` serves the UI from the
installed wheel. Paths below are relative to this directory.

| File | Description |
|---|---|
| `timeline.html` | HTML shell for the song-structure timeline viewer. |
| `library.html` | HTML shell for the corpus-wide loop library browser. |

### `gms_ma/assets/css/`

| File | Description |
|---|---|
| `tokens.css` | Theme design tokens: default dark palette plus light/night overrides. |
| `components.css` | Shared component styles (settings popover, about panel, transport, toasts). |
| `timeline.css` | Styles for the timeline viewer page. |
| `library.css` | Styles for the loop-library page. |

### `gms_ma/assets/js/common/` — shared modules

| File | Description |
|---|---|
| `api.js` | Thin `ApiClient` wrapping every viewer JSON endpoint in one place. |
| `dom.js` | Tiny DOM/string helpers (`$`, `esc`, `on`, `make`). |
| `format.js` | Pure formatting/colour helpers (time, hash hue, clamping). |
| `gm.js` | General-MIDI instrument names and display-name resolution (render only). |
| `settings.js` | Observable, localStorage-backed settings store shared by both pages. |
| `storage.js` | Safe localStorage JSON wrapper that never throws. |
| `theme.js` | Resolves auto/light/dark/night and exposes the canvas palette from CSS vars. |
| `exportActions.js` | Shared pattern download/clipboard/DAW-export actions (optional export BPM). |

### `gms_ma/assets/js/common/components/` — shared components

| File | Description |
|---|---|
| `miniPlayer.js` | Bottom transport (play/pause, seek, 0.25–3× speed) used by both pages. |
| `settingsPanel.js` | Settings popover (gear button) that edits the settings store. |
| `aboutPanel.js` | About popover fetching version/license/repo from `/api/about`. |
| `toast.js` | Transient toast notifications for action feedback. |

### `gms_ma/assets/js/timeline/`

| File | Description |
|---|---|
| `app.js` | Timeline composition root: state, canvas renderers (Arrangement/Motifs/Harmony/Repetition), zoom, selection, playback. |
| `layout.js` | Pure layout math: motif lane packing, largest-per-region, layer grouping, adaptive time axis. |

### `gms_ma/assets/js/library/`

| File | Description |
|---|---|
| `app.js` | Library composition root: paged catalogue fetch, filters/search, flat and tree rendering. |
| `forest.js` | Pure building-block containment: which shorter loops sit bar-aligned inside longer ones. |
| `pager.js` | Pure pagination math (page sizes, sliding window, range labels). |

---

## `tests/` — pytest suite (plus Node-run JS tests)

| File | Description |
|---|---|
| `conftest.py` | Shared fixtures and synthetic MIDI builders plus a one-file index helper. |
| `test_assets.py` | DB-embedded MIDI assets: storage, DB-only reconstruction, export. |
| `test_bars.py` | Bar/beat/second arithmetic. |
| `test_cli_delete.py` | CLI `delete`: purge a song and its rows, optionally files too. |
| `test_cli_doctor.py` | CLI `doctor`: flag and (`--delete`) purge corrupted songs. |
| `test_export.py` | Pattern → MIDI bytes with correct channel/program/markers. |
| `test_export_bpm.py` | Export-BPM (retempo) and playback-speed clamping. |
| `test_gui.py` | Folder-index pipeline and GUI module wiring regression tests. |
| `test_harmony.py` | Chord/harmony detection and harmony/melody layer separation. |
| `test_js_forest.py` | Node ESM behavioural test for the building-block forest module. |
| `test_js_gm.py` | Node ESM behavioural test for the General-MIDI name module. |
| `test_js_layout.py` | Node ESM behavioural tests for the Motifs-view layout module. |
| `test_js_pager.py` | Node ESM behavioural test for the library pagination module. |
| `test_js_syntax.py` | Syntax gate for all browser ES modules (skipped without Node). |
| `test_library.py` | Whole-library browsing and DAW export helpers. |
| `test_motif_integration.py` | Motif feature integration: index storage, manifest, library, reconstruct. |
| `test_motifs.py` | Motif mining: data-driven grid, off-bar/sub-bar motifs, tolerance. |
| `test_parse.py` | Parsing: channel splitting, note normalisation, corrupt files. |
| `test_reconstruct.py` | End-to-end index → placements → reconstruct round trip. |
| `test_segment.py` | Repetition detection, covers, ladder variants, content ids. |
| `test_static_assets.py` | Static asset serving: shells, modules/CSS, MIME types, traversal guard. |
| `test_stems.py` | Rhythm-skeleton stems: exporter behaviour, index registration, manifest. |
| `test_store.py` | Store schema, placement round trip, tag precedence, manifests. |
| `test_tag.py` | Feature and tag rule tests (deterministic heuristics). |
| `test_viewer.py` | Viewer API and audio-event scheduling tests. |
| `test_viewer_about.py` | Viewer About panel: `/api/about` metadata and local `/license` text. |
| `test_viewer_concurrency.py` | Regression: the threaded viewer serialises the shared SQLite connection. |

---

## `REVIEW/` — per-module code-review notes

Generated review documentation: an overview plus one markdown file per module.

| File | Description |
|---|---|
| `overview.md` | Overall architecture, data-flow diagram and cross-module review. |
| `gms_ma-<module>.md` | Per-module review (`model`, `parse`, `bars`, `segment`, `motif`, `music_theory`, `harmony`, `features`, `tag`, `export`, `store`, `indexer`, `reconstruct`, `library`, `viewer`, `cli`, `gui`, `doctor`, `clipboard`, `midi_out`). |
| `assets-frontend.md` | Review of the web UI (`gms_ma/assets/` HTML/CSS/JS). |
