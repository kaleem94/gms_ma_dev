# gms-ma

**gms-ma** (GMS MIDI analyzer) is a computational-musicology workbench for General-MIDI
collections. Point it at a folder of songs and it produces a structured,
queryable analysis of how each piece is built: where material repeats, how
phrases and riffs recur, which motifs are reused (and where they transpose), how
instruments layer over time, and what key, tempo, meter and stylistic traits
each track carries — all derived from the raw note data, with no audio or ML
stack.

Every channel is decomposed into its repeating units: bar-aligned **cover loops**
(grooves, riffs, chord progressions) and a grid-free **motif catalogue**
(sub-bar figures, off-beat pickups, ornamented and transposed phrases). Each unit
is stored together with its occurrences (*placements* / *hits*), so the analysis
is more than a picture — a song can be rebuilt note-for-note from its repeated
material, and any unit can be isolated, auditioned, compared across the corpus
and exported as MIDI.

A local **web timeline viewer** renders the analysis as per-instrument rows you
can scrub, hover and click (form, repetition, motif lanes, and a compression view
of every extracted level), with best-effort **live MIDI audition** for hearing a
pattern in context. The companion **loop library** turns the whole corpus into a
filterable, comparable catalogue of musical units.

Everything is stdlib + [`mido`](https://mido.readthedocs.io/) only — no numpy /
ML stack (safe on Python 3.14).

> **Disclaimer:** gms-ma is a computational analysis tool intended for
> musicological research, educational purposes, and structural analysis. Users
> are responsible for ensuring that their ingestion, storage, and commercial use
> of third-party MIDI files comply with local copyright laws and licensing
> agreements. See the full [Disclaimer](#disclaimer).

---

## Platform support

> **This release targets Windows only.** macOS and Linux support is planned and
> will be added in a future release.

Running on Windows, the indexing/analysis engine, CLI, web viewer and loop
library all work out of the box (standard library + `mido`, no third-party
audio stack). Two subsystems are currently Windows-specific:

- **Live MIDI audition** — uses the Windows Multimedia API (`winmm`) via
  `ctypes` (no third-party MIDI backend). With no MIDI output present the audio
  controls disable automatically; the visuals always work.
- **Copy MIDI** (file-to-clipboard) — uses the Windows `CF_HDROP` clipboard
  format via `ctypes`.

On non-Windows platforms these two features are unavailable and their controls
degrade gracefully (no crash); macOS/Linux support for the rest of the toolkit
is not available in this release.

---

## Architecture

```
Raw MIDI  ->  Mido parser  ->  SQLite engine  ->  HTML5 canvas timeline
.mid/.midi    gms_ma.parse     db/midi_loops.db    gms_ma.viewer (/ + /library)
                               one self-contained DB: loops, placements, motifs, tags, features, manifests
```

Parse with `mido`, persist every artefact in a single SQLite file, and render it
in the browser from native ES modules — no server framework and no build step.

---

## Quickstart

```bash
# 1. install (Python 3.10+, Windows; pulls in mido)
pip install gms-ma

# 2. analyse a folder of MIDI (repeatable / idempotent)
gms-ma index ./midi_folder

# 3. open the timeline + loop library (Ctrl+C to stop)
gms-ma view
```

The installed `gms-ma` launcher is equivalent to `python -m gms_ma` if you
prefer the module form. Then open <http://127.0.0.1:8123> for the
song-structure timeline and <http://127.0.0.1:8123/library> for the corpus-wide
loop browser.

---

## What you can study

- **Form & repetition** — how much of a track is repetition, which blocks recur
  (and how many times), where the one-off material sits, and a compression ratio
  per instrument.
- **Phrase & motif structure** — repeated windows of *any* length at *any*
  offset, including nested motifs, with full occurrence lists and transposition.
- **Texture & orchestration** — one row per (track, channel) with the GM patch
  resolved (`Ch 0: Acoustic Grand Piano`, `Ch 9: Standard Drum Kit`), per-track
  mute/solo, and layer density over time.
- **Harmony & key** — Krumhansl–Schmuckler key/mode per song and per pattern.
- **Chords & harmony** — per-pattern chord labels (triads, 7ths, sus/dim/aug/
  power, slash inversions) and roman-numeral progressions relative to the key,
  with separated **harmony/melody** layers.
- **Rhythm & meter** — bar/beat grid, tempo map, time signatures, onset rhythm
  through *pitch* vs *rhythm* lenses, plus syncopation / legato / staccato style
  tags.
- **Style & emotion (heuristic)** — genre, tempo class, energy and a 2-D
  valence/arousal emotion label, overridable by manual tags.
- **Cross-corpus comparison** — the loop library filters by song/pattern/
  instrument tags and lays out **building-block trees** (longest loop → nested
  phrases) per track.

---

## Development setup (from source)

```powershell
# 1. create & activate the venv, install the package (editable) + dev deps
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

# 2. run the unit tests (100 with Node installed; JS tests auto-skip without it)
.\.venv\Scripts\python.exe -m pytest

# 3. analyse your MIDI collection (files or folders; repeatable/idempotent)
.\.venv\Scripts\python.exe -m gms_ma index .\midi_folder --db db/midi_loops.db --out out/loops

# 4. inspect the extracted units (add e.g. --song "EINS-Z~6 (3)" / --kind cover / --minconf 0.8 / --tag style=syncopated)
.\.venv\Scripts\python.exe -m gms_ma list --db db/midi_loops.db

# 5. print a song's full analysis JSON straight from the DB (nothing written to disk)
.\.venv\Scripts\python.exe -m gms_ma manifest --db db/midi_loops.db --song "eb-Star_Dragon_Tower"

# 6. validate the analysis: rebuild a song from its repeated units and diff vs the original
.\.venv\Scripts\python.exe -m gms_ma reconstruct --db db/midi_loops.db --song "eb-Star_Dragon_Tower"

# 7. open the timeline viewer (Ctrl+C to stop)
.\.venv\Scripts\python.exe -m gms_ma view --db db/midi_loops.db --port 8123

#    …then compare the whole corpus at http://127.0.0.1:8123/library

# 8. no-typing option: desktop folder indexer (pick a folder, watch progress)
.\.venv\Scripts\python.exe -m gms_ma gui
```

A corrupt MIDI file is **skipped with a warning** — the index never aborts on a
bad file.

---

## What you get (analysis artefacts)

```
out/loops/<Song>/…            unique loop .mid files (channel + program preserved)
db/midi_loops.db              all metadata (patterns, placements, tempos, tags, manifests)
```

The database is the single source of truth: `db/midi_loops.db` is a plain SQLite
file, directly queryable with standard SQL (`sqlite3`, pandas, DuckDB, …) for
external data pipelines or ML training — no export step required.

Every generated MIDI (each unique loop **and** its piano/clap skeletons) is also
stored as a BLOB in the `midi_assets` table keyed by a unique `asset_id` — the
database is self-contained, so reconstruction, playback, audition and the loop
library all read from the DB (falling back to legacy on-disk files when a row
predates assets). Writing `.mid` files to disk is optional: `--no-files` during
index (DB-only), the GUI's **Save loops to disk** checkbox, or write them back
at any time with `export`.

Each pattern `.mid` keeps: the song's PPQ/tempo/time-signature, the original GM
**program change + channel** (drums stay on channel 9/10 with GM drum keys), only
that instrument's notes, and pitch-bend **only where the value actually changes**
(static pitch-bend runs are dropped). `LOOP_START`/`LOOP_END` markers are added
for DAW-friendly looping. Identical repeated content is exported **once** and
reused — repeats are recorded as *placements*.

### Rhythm-skeleton stems (default on)
Besides the real note loop, every unique pattern also gets two rhythm-only
renderings so you can hear the groove without pitch: **`_piano.mid`** (Acoustic
Grand, every attack on a single C4 note, `--stem-note`) and **`_clap.mid`**
(GM Hand Clap key 39 on channel 9, each hit clipped to ~a 16th, `--stem-drum`).
Disable with `--no-stems`; stems are registered in the DB and offered as
**Original / Piano / Clap** audition buttons in the viewer's pattern panel.

### How repetition is detected
Each instrument is analysed through an **adaptive grid** (the coarsest regular
beat-subdivision that still lines up with ≥90% of onsets; rarer faster notes
are handled per-bar at their own smaller unit) and two **match lenses**:
*pitch* (a bar matches on the pitch-sets attacked in each grid cell) or
*rhythm* (matching only onset rhythm). Three engines then segment the track:

- `period` — the smallest bar-period that **tiles from the start** (plus a
  1×/2×/4×/8× ladder so longer progressions are captured).
- `repeat` — the **largest exact repeating block anywhere** (longest-first,
  occurrences placed as shared loops; leftover bars are phrase-split).
- `hybrid` (default) — tries `period`, then `repeat`, then **gap-splitting**:
  phrases are cut only at the middle of silences that are genuinely long
  enough (`--gap-beats 4`), never on an arbitrary fixed grid.

Choose via `--detect hybrid|period|repeat`, `--lens pitch|rhythm`, and tune the
grid with `--grid-cov`/`--grid-cap`. The engine/lens actually chosen is stored
per instrument and shown in the viewer header.

### Motifs (grid-free catalogue)
Besides the bar-aligned cover loops, every channel is mined for **repeated
windows of any length at any start position** — sub-bar riffs, off-beat pickups,
ornamented/transposed phrases — using a data-driven tatum instead of a fixed
grid. Motif families are stored like ordinary patterns (their own `.mid`, stems,
tags/features) plus a per-occurrence **hit list**. Mining is on by default;
disable with `--no-motifs`, cap families per channel with `--motif-max 80`.

Every pitched pattern is also **harmony-analysed**: simultaneous onsets are
labelled with a chord (`C`, `Am`, `G7`, `C/E`, …) and a roman numeral relative to
the detected key, stored as `chord`/`harmony` tags and features. With
`--harmony-split top` (default) or `onset`, each pattern additionally gets
separated **harmony** and **melody** stems (auditionable in the viewer), and
motifs are mined **per layer** (tagged `layer=melody|harmony`) as well as mixed.
Use `--harmony-split off` to disable separation.

### Understanding the viewer
One row per instrument, labelled `Ch N: <General-MIDI patch>` (e.g.
`Ch 0: Acoustic Grand Piano`, `Ch 9: Standard Drum Kit`; a real MIDI track name
is kept as a secondary line). Every **block = one loop placement**; identical
colours = the *same* underlying pattern reappearing. The header shows
`placements / unique` and the repetition factor — a tight drum groove collapsing
to a few colours tells you the detection worked. Hovering a block shows a popup
and a one-line breakdown in the **bottom bar** (`4-bar melody loop · 12 notes ·
repeats 3× in track`); **clicking locks** the block — the right panel keeps its
details (Clear button, click empty space, or click the same block again to
unlock) while hover only previews. Click/drag the ruler to **seek**; **Play**
streams the whole reconstructed song and **Audition loop** plays the clicked
pattern.

The shared bottom transport has a **0.25–3× speed slider**; the whole-song
playhead and every loop audition follow it. The pattern panel also offers an
**Export BPM** field (defaults to `song BPM × speed`) and **Download MIDI /
Copy MIDI / Copy DAW ch1 / DAW ch1 export** for the selected loop or motif, with
a render picker (Original / Piano / Clap / Harmony / Melody). Downloads carry the
export BPM (the tempo meta is rewritten), so a DAW set to that BPM hears the same
speed you auditioned.

A collapsible **Legend** (the `?` button, bottom-right of the canvas) explains
the axes — **X = time (bar numbers)**, **Y = tracks/instruments** — and what the
colours mean, adapting its text to the active view.

The default **View: Arrangement** shows one block per loop placement per
instrument. Switch **View: Arrangement / Motifs / Harmony / Repetition** in the
toolbar.
The **Repetition** view is a compression view that stacks, per instrument,
**every extracted level from smallest to largest**: each level's window length
(1×, 2×, 4×, 8× … of the detected loop — dynamic per instrument) gets a
colour-coded tile row where repeating windows keep their colour and one-off
cells stay grey, with one thin colour-coded line underneath per repeating group
showing exactly where that pattern recurs.

In **Motifs** each
instrument is a clean group: overlapping motif hits are stacked into
non-overlapping **lanes** (or reduced to the largest motif per region — see
Settings), and a thin **arrangement strip** below shows that instrument's cover
placements for context. Click a motif block to **select and audition** it (no
hover selection); the arrangement strip is not selectable — clicking it seeks.
Every lane carries a detail label (`layer · hits · top chord · progression`).

**View: Harmony** groups, per instrument, the older *mixed* motif families and
the new *melody* / *harmony* layer families into separate sections, each with
packed lanes, per-lane detail and chord labels on wide blocks. Selecting a motif
shows its **layer**, **top chord** and **chord progression** in the hover popup
and the sidebar.

The **⚙ Settings** popover (top-right of both pages) sets the **theme**
(Auto / Light / Dark / Night — Auto follows the browser), **Arrangement block**
and **Motif hit** opacity, how much **others dim** while a selection is active,
the **Motif overlap** mode (**Stack lanes** / **Largest only**), and a **Show
chords & harmony** toggle that hides every chord/harmony name on the timeline.
Preferences persist in `localStorage` and are shared between the timeline and the
library.

The side-panel **Playback mix** list mutes or **soloes** individual instruments
(whole-song playback only): untick a checkbox to mute, press `S` to solo, and use
**All on / Clear solo / Reset**. Disabled instruments are dimmed on the timeline,
and toggling while playing restarts from the current position with the new mix.

- Live MIDI uses the Windows Multimedia API via `ctypes` (no third-party MIDI
  backend). If no MIDI output exists the audio buttons disable automatically —
  visuals always work. No MIDI device needed to use the tool.

### Loop library (`/library`) — compare the corpus
The same server also hosts a database visualizer at **`/library`**. Left panel =
multi-select **category filters** (Song genre/emotion/tempo, Pattern
style/key/mode/energy, Instrument GM family and loop kind — values inside one
category combine as OR, categories as AND, with live counts) plus a text search.
Click a **song** to drill into all of its sub-patterns; click a **pattern** to
see *where it comes from* (source song + timeline link, instrument / channel /
GM program, detector engine+lens, bar position, how many times it recurs, tags)
and to audition it (Play **Original / Piano / Clap**). A slim transport bar at
the bottom shows current / total time with a draggable seek bar, a **0.25–3×
speed slider**, and play-pause
(seeking jumps straight into the loop). When a song list gets large, hit
**View: Tree** to organise it as an expandable hierarchy — *song → instrument
group → track → loops* — in both the whole-library list and the song drill-down.
Every level is collapsed by default: click a group/track header (or its ▸ button)
to expand it, and playing a loop auto-expands its ancestors. Each channel header
also has a **▶** button that plays just that channel's full line across the song.
Instrument grouping is configurable (**Group: Off / Family / Program**, Family
by default). Under each track the loops form a **building-block tree**: the
longest covering loop is shown on top, and expanding it reveals the shorter
loops/phrases (same song + same instrument only) that sit, bar-aligned, inside
the bars it covers. DAW export per selection:

Lists load **on demand**: the server pages the catalogue (**Rows per page**
10/25/50/100/200/All with **Prev/Next** and a *Go to page* box) and only ships the
requested loops, while the filters, live counts and song list describe the whole
match set. The default page size is **25** (never “All” unless you pick it), and
the client keeps a **sliding window of ±5 pages** around the current page, so
nearby navigation is instant and only distant jumps re-request. In **Tree** view
only song summaries are fetched up front — a song's loops load lazily when it is
expanded — and every response is gzip-compressed, so large libraries open quickly.
The unfiltered facet counts and song list are **precomputed** by `optimize`
(automatically after `index`) into small tables, so browsing never scans the whole
tag table — a large library opens in ~0.1 s.

- **Download MIDI** — fetch the active loop `.mid`.
- **Copy MIDI** — put that `.mid` file on the Windows clipboard (CF_HDROP via
  `ctypes`), so you can paste it straight into Explorer or a DAW file-drop.
- **DAW ch1 export** — rewrite the loop onto a single MIDI channel (0 = the "1"
  a DAW shows), keeping program change + pitch bend; writes `<loop>_ch1.mid`
  next to the source, copies it to the clipboard and downloads it.

---

## Commands

| Command | Purpose |
|---|---|
| `index <paths…> [--out out/loops] [--db …] [--detect hybrid\|period\|repeat] [--lens pitch\|rhythm] [--no-stems] [--stem-note 60] [--stem-drum 39] [--no-files] [--no-motifs] [--motif-max 80] [--loop-gap-bars 1] [--min-conf 0.55] [--max-period 64] [--gap-beats 4] [--grid-cov 0.9] [--grid-cap 32] [--harmony-split off\|top\|onset]` | Parse, decompose, export and tag a collection (MIDI always stored in DB; `--no-files` skips disk copies; `--no-motifs` skips the grid-free motif catalogue) |
| `export [--song NAME] [--out out/loops] [--no-stems]` | Write the DB-stored MIDI assets back to `.mid` files |
| `list [--song …] [--kind cover\|variant\|motif] [--channel N] [--minconf X] [--tag style=syncopated] [--json]` | Browse loops |
| `manifest [--song …]` | Print stored JSON (song-level) or the index summary, from the DB |
| `reconstruct --song NAME [--out …]` | Rebuild the song from placements; prints a match report vs the original |
| `view [--port 8123]` | Song-structure timeline **and** `/library` loop browser (seek + live MIDI audition + DAW export) |
| `delete --song NAME [--song …] [--files] [--yes]` | Delete a song and **every** row that depends on it (tracks, patterns, placements, assets, stems, motif hits, tags, features, manifests). `--files` also removes `out/<song>/`; requires `--yes` |
| `doctor [--delete] [--files] [--yes]` | Scan the DB for **corrupted songs** (no tracks, dangling placements, patterns/stems without assets, invalid manifest JSON, missing source file) and optionally purge them |
| `gui` | Desktop (tkinter) indexer — choose a folder (recurses subfolders) **or multi-select specific `.mid` files**, pick **Engine** (hybrid/period/repeat), **Match lens** (pitch/rhythm), **Rhythm stems**, **Extract motifs (grid-free)** and **Save loops to disk**. Files whose exact path is already in the DB are **skipped automatically**; tick **"Re-index already processed"** to force reprocessing. Already-indexed files whose DB song is **corrupt are re-parsed automatically**, and **Check DB** / **Fix corrupt** scan the whole DB and re-index the repairable songs. Progress counts only the pending files |
| `tag list/set/clear [--target pattern\|song] [--target-id N] [--kind] [--value]` | **Manual tag overrides** — these beat heuristic tags everywhere |
| `optimize [--db …]` | **Post-process the DB**: precompute the library facet counts + song summaries (so `/library` never scans the whole tag table) and refresh SQLite planner stats (`ANALYZE`). Run once on an existing DB; `index`/`tag`/`delete` refresh it automatically, and the viewer self-heals if it is missing |

---

## Analytical annotations (heuristics — deliberately coarse, overridable)

- **Per pattern**: `key`, `mode`, `energy` (high/mid/low) and `style` of play
  (`legato`, `staccato`, `syncopated`, `chordal`, `arpeggiated`, `steady-pulse`,
  `groove`, `sparse` …).
- **Per song**: `genre` (`dance-electronic`, `epic-orchestral`, `pop-rock`,
  `ballad`, `jazz-swing`, `classical`, `world`, …), `emotion` (2-D
  valence/arousal → `upbeat-energetic`, `calm-positive`, `tense-dramatic`,
  `melancholic`), `key`/`mode` (Krumhansl-Schmuckler), `energy`, `tempo-class`.

Set your own: `tag set --target pattern --target-id 42 --kind style --value "acid"`,
then any read path (list / manifest / viewer) shows manual tags **instead of**
heuristic ones for that kind. Manual tags double as future training labels.

---

## Future: learned analysis (optional upgrade)

Heuristics were chosen for Phase 1; nothing in the DB blocks a stronger analyser
later. Feature vectors are already stored per pattern/song in the `features`
table as a bridge:

- **Audio** models (Essentia, VGGish, …) can't classify raw `.mid` — only usable
  after synthesis (out of scope).
- **Symbolic/MIDI** transformers (MusicBERT, MMT/M3-style models trained on
  Lakh/MAESTRO-style corpora) can embed note sequences for style/retrieval and
  fine-tune for genre/emotion, but need PyTorch and are not plug-and-play on
  Python 3.14 yet. Practical route: export `features` → fine-tune/serve a model
  → write results into the existing `tags` rows with `source='model'` (which
  ranks between manual and heuristic). The scorer seam in `tag.py` makes the swap
  localised.

---

## Design notes

- Single-responsibility modules connected through plain dataclasses:
  `parse` (mido-only) → `bars` (pure tick/bar/sec math) → `segment`
  (detector strategy seam) / `motif` (grid-free catalogue) → `export` →
  `store` (repository) → `features` / `music_theory` / `tag` (rules seam) →
  `reconstruct` / `viewer` / `library` (whole-DB browse + DAW export) /
  `clipboard` (CF_HDROP file copy) / `cli`.
- JSON manifests live **inside the DB** (`manifests` table) — never on disk.
- The web UI is a thin HTML shell plus native ES modules and CSS under
  `gms_ma/assets/` (`assets/css/*`, `assets/js/{common,timeline,library}/*`),
  served by the viewer's `/assets/*` static route (traversal-guarded,
  MIME-typed, gzip for text). Shared `common/` modules hold the settings store, theme/palette, API
  client and components; the library list is paged/JIT server-side.
- SQLite connection is `check_same_thread=False`; the viewer is threaded, so all
  repository access is serialised through `Repository.lock`.

## Troubleshooting

- **`E_bis_g.mid` skipped** — that file is genuinely corrupt (mido: *"data byte
  must be in range 0..127"*); expected behaviour.
- **Play button disabled** — no MIDI output port was found; the viewer still
  works visually.
- **Re-index to refresh** — `index` deletes and re-creates each song's rows, so
  re-running after tuning parameters is safe.
- **Corrupted / partial song in the DB** — a failed analysis is rolled back
  automatically, but if an older DB has a bad song, run `doctor` to list it and
  `doctor --delete --yes` (or `delete --song NAME --yes`) to remove it. Add
  `--files` to also delete its generated `out/<song>/` folder. The GUI does a
  cheap corruption check when you process a folder again and **auto-reparses**
  the affected files; its **Check DB** button lists corrupt songs and **Fix
  corrupt** re-indexes the repairable ones (missing-source songs must be removed
  with `doctor --delete`).
- All generated artifacts (`out/`, `db/*.db`) are git-ignored.

---

## Disclaimer

gms-ma is a computational analysis tool intended for musicological research,
educational purposes, and structural analysis. Users are responsible for
ensuring that their ingestion, storage, and commercial use of third-party MIDI
files comply with local copyright laws and licensing agreements.

The analysis output is heuristic and provided for research and informational
purposes only; it is not legal advice, and it does not grant any rights to the
analysed MIDI content.

---

## License

Copyright 2026 the gms-ma authors.

Licensed under the **Apache License, Version 2.0** (SPDX: `Apache-2.0`). You may
not use this project except in compliance with the License. You may obtain a copy
of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software distributed
under the License is distributed on an "AS IS" BASIS, **WITHOUT WARRANTIES OR
CONDITIONS OF ANY KIND**, either express or implied. See the License for the
specific language governing permissions and limitations under the License.

See [`LICENSE`](LICENSE) for the full text.
