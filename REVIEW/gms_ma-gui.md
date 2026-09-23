# gms_ma/gui.py

## IndexerGUI <!-- ref:gms_ma/gui.py:30-300 -->
**Purpose**: A tkinter desktop GUI for batch-indexing a MIDI folder with a progress bar, log pane, and configuration controls.
**Why**: Provides a visual alternative to the CLI for users who prefer a graphical interface; the worker thread runs `run_index` in the background so the UI stays responsive.
**Data Flow**: UI events → `_start` → `_worker` thread → `_poll` → UI updates; `_open_viewer` launches the HTTP server as a detached process.
**Relationships**: Uses `indexer.gather_midi`, `indexer.classify_indexed_ex`, `indexer.run_index`, `store.Repository`, `library.refresh_library_cache`.

### _start / _worker / _poll <!-- ref:gms_ma/gui.py:180-280 -->
**Purpose**: `_start` validates input and launches the worker thread; `_worker` runs `run_index` in the background and pushes progress/log/done events to a queue; `_poll` drains the queue on the Tkinter main loop every 80ms.
**Why**: The queue-based communication pattern avoids thread-safety issues with Tkinter widgets (which are not thread-safe). The `_busy` flag prevents re-entrant starts.
**Data Flow**: `files, db, out, cfg, total` → worker thread → queue → `_poll` → UI updates.
**Relationships**: `_worker` calls `run_index` and `library.refresh_library_cache`.

### _browse / _pick_files / _refresh_processed <!-- ref:gms_ma/gui.py:100-180 -->
**Purpose**: File selection dialogs and DB classification of chosen files.
**Why**: `_refresh_processed` calls `classify_indexed_ex` to determine which files are pending, already indexed, or corrupt, and updates the UI label accordingly. The "Re-index already processed" checkbox overrides the skip logic.
**Data Flow**: `folder/path selection` → `gather_midi` → `classify_indexed_ex` → UI label update.
**Relationships**: Calls `indexer.gather_midi`, `indexer.classify_indexed_ex`.

### _check_db / _fix_corrupt <!-- ref:gms_ma/gui.py:220-260 -->
**Purpose**: Scan the DB for corrupted songs and offer to re-index fixable ones.
**Why**: The "Fix corrupt" button is only enabled when there are fixable findings (source files still exist). This provides a GUI alternative to the `doctor --delete` CLI command.
**Data Flow**: `db` → `doctor.scan` → findings list → UI log + button enable/disable.
**Relationships**: Uses `doctor.scan`, `doctor.fixable`; calls `_start` with `force_reindex=True`.