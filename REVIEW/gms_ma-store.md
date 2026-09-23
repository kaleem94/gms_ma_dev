# gms_ma/store.py

## Repository / schema <!-- ref:gms_ma/store.py:15-89 -->
**Purpose**: SQLite repository owning the schema (`songs`, `tracks`, `patterns`, `placements`, `tempos`, `timesigs`, `tags`, `features`, `stems`, `motif_hits`, `midi_assets`, `index_runs`, `manifests`) and all CRUD.
**Why**: A single file is the source of truth; `check_same_thread=False` plus an explicit `lock` lets the threaded viewer share one connection safely. `_ensure_column` gives cheap forward-compatible migrations.
**Data Flow**: Dataclasses → rows; rows → dicts for the manifest/library.
**Relationships**: Used by `indexer`, `library`, `viewer`, `doctor`, `cli`.

### delete_song <!-- ref:gms_ma/store.py:108-162 -->
**Purpose**: Remove a song and every dependent row (tracks, patterns, placements, assets, stems, motif hits, tags, features, manifests), returning per-table counts.
**Why**: Re-indexing and `delete`/`doctor` must never leave orphans; centralising the cascade avoids drift. Optional `--files` removes the generated `out/<song>/` folder.
**Data Flow**: `name` → `counts dict`.
**Relationships**: Used by `run_index` (delete-before-reindex), `cli.cmd_delete`, `doctor`.

### upsert_song / upsert_track / add_pattern / add_placement <!-- ref:gms_ma/store.py:164-260 -->
**Purpose**: Upsert (insert-or-update) operations for songs, tracks, patterns, and placements.
**Why**: `ON CONFLICT` clauses make these idempotent so re-indexing a song replaces its data without creating duplicates. `add_placement` uses `INSERT OR REPLACE` so the latest placement for a `(song, track, bar)` wins.
**Data Flow**: Dataclass/row data → SQLite rows; returns the row id.
**Relationships**: Called by `indexer.process_song` and `indexer.run_index`.

### pattern_bytes / tag_groups <!-- ref:gms_ma/store.py:304-399 -->
**Purpose**: `pattern_bytes` returns a pattern's or stem's MIDI BLOB (falling back to the legacy on-disk file); `tag_groups` returns effective tags per kind with `manual > model > heuristic` precedence.
**Why**: Keeping the BLOB in `midi_assets` makes the DB self-contained; the precedence rule lets manual overrides win everywhere without special-casing readers.
**Data Flow**: `(pattern_id, render)` → `bytes|None`; `(target_type, target_id)` → `{kind: [tag…]}`.
**Relationships**: Used by `library`, `viewer`, `reconstruct`, and the manifest builder.

### set_feature / add_asset / link_asset <!-- ref:gms_ma/store.py:262-302 -->
**Purpose**: Store JSON feature payloads and MIDI asset BLOBs with SHA-256 hashing.
**Why**: The `features` table uses a JSON text column for flexibility; `midi_assets` stores the raw MIDI bytes with a SHA-256 hash for integrity checking. `link_asset` updates the `patterns` or `stems` table to point at the stored asset.
**Data Flow**: `target_type, target_id, payload` → `features` row; `asset_id, kind, pattern_id, render, data` → `midi_assets` row.
**Relationships**: Called by `indexer._persist_pattern` and `indexer._store_stem`.