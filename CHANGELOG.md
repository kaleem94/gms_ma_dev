# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-09-23

### Added

- Initial public release of **gms-ma** (GMS MIDI analyzer), a Windows-first
  computational-musicology workbench for General-MIDI collections.
- Bar-aligned **cover-loop** detection with `period` / `repeat` / `hybrid`
  engines, adaptive grid and pitch/rhythm match lenses.
- Grid-free **motif catalogue** with transposition-aware similarity and per-hit
  occurrence lists.
- Key/mode, chord/roman-numeral and harmony/melody layer analysis.
- Heuristic per-pattern and per-song tags, features and emotion/genre labelling.
- SQLite repository with per-song manifests, MIDI BLOBs, rhythm-skeleton stems
  and whole-song reconstruction.
- Threaded stdlib **web timeline viewer** and corpus-wide **loop library**
  browser with live MIDI audition and DAW export helpers.
- `tkinter` desktop indexer and a full `gms-ma` CLI (`index`, `export`, `list`,
  `manifest`, `reconstruct`, `view`, `delete`, `doctor`, `gui`, `tag`,
  `optimize`).

[Unreleased]: https://github.com/kaleem94/gms-ma/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/kaleem94/gms-ma/releases/tag/v0.1.0
