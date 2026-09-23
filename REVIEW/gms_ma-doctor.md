# gms_ma/doctor.py

## song_counts / scan / fixable <!-- ref:gms_ma/doctor.py:14-100 -->
**Purpose**: Scan the DB for corrupted songs (missing tracks, dangling placements, missing assets, invalid manifests) and optionally delete them.
**Why**: Indexing can be interrupted or analysis can fail part-way, leaving orphaned rows; `scan` finds those so the CLI can report or delete them.
**Data Flow**: `repo` → `[{id, name, path, issues}]`; `findings` → fixable subset.
**Relationships**: Used by `cli.cmd_doctor`, `gui._check_db`, `indexer.classify_indexed_ex`.

### song_counts <!-- ref:gms_ma/doctor.py:14-30 -->
**Purpose**: Return row counts for one song across all dependent tables (tracks, patterns, placements, assets, stems, motif_hits).
**Why**: Used by the delete preview and tests to show the user how many rows will be removed before confirming deletion.
**Data Flow**: `repo, song_id` → `dict` of table counts.
**Relationships**: Called by `cli.cmd_delete` for the deletion preview.

### scan <!-- ref:gms_ma/doctor.py:32-85 -->
**Purpose**: Find suspicious songs by checking for zero tracks, dangling placements, missing assets, broken stem links, invalid manifest JSON, and missing source files.
**Why**: The `deep=False` option skips manifest JSON validation for cheap checks suitable for GUI refreshes; `deep=True` validates every manifest payload. Each check is a separate SQL query that aggregates by `song_id` so the final loop is O(songs) not O(rows).
**Data Flow**: `repo, deep` → `[{id, name, path, issues}]`.
**Relationships**: Called by `cli.cmd_doctor`, `gui._check_db`, `indexer.corrupt_songs`.

### fixable <!-- ref:gms_ma/doctor.py:87-90 -->
**Purpose**: Filter findings to only those whose source file still exists on disk.
**Why**: A song whose source file has been deleted cannot be re-indexed; it can only be removed from the DB. This distinction is important for the GUI's "Fix corrupt" flow.
**Data Flow**: `[findings]` → `[fixable findings]`.
**Relationships**: Called by `gui._fix_corrupt` and `cli.cmd_doctor --delete`.