# gms_ma/cli.py

## cmd_index <!-- ref:gms_ma/cli.py:30-60 -->
**Purpose**: The `index` subcommand: gather MIDI files, construct an `AnalysisConfig`, run `run_index`, and print a JSON summary.
**Why**: The CLI is the primary non-GUI entry point; it mirrors the GUI's configuration surface so both paths produce identical analyses.
**Data Flow**: `args.paths, args.db, args.out, args.*` → `{total, indexed, placements, unique_patterns, motif_families, elapsed, skipped}`.
**Relationships**: Calls `indexer.gather_midi`, `indexer.run_index`, `library.refresh_library_cache`.

### cmd_list / cmd_manifest / cmd_reconstruct / cmd_export / cmd_delete / cmd_doctor / cmd_optimize <!-- ref:gms_ma/cli.py:62-280 -->
**Purpose**: The remaining subcommands for listing patterns, printing manifests, reconstructing songs, exporting MIDI, deleting songs, scanning DB integrity, and post-processing optimisation.
**Why**: Each subcommand is a thin wrapper around the corresponding module function, keeping the CLI surface flat and testable.
**Data Flow**: `args` → module function → stdout JSON / exit code.
**Relationships**: Each delegates to `store.Repository`, `indexer`, `library`, `reconstruct`, or `doctor`.

### _build_parser <!-- ref:gms_ma/cli.py:282-400 -->
**Purpose**: Construct the `argparse` parser with all subcommands and their arguments.
**Why**: Centralising the parser definition makes it easy to see the full CLI surface and add new subcommands. Each subcommand has its own `set_defaults(func=cmd_*)` so the dispatch is a simple `args.func(args)` call.
**Data Flow**: `argv` → `argparse.Namespace` → `args.func(args)`.
**Relationships**: Called by `main()`; each subcommand handler is defined above.

### main <!-- ref:gms_ma/cli.py:402-408 -->
**Purpose**: Entry point: parse arguments and dispatch to the appropriate subcommand handler.
**Why**: `KeyboardInterrupt` is caught and returns exit code 130, matching the convention for user-cancelled operations.
**Data Flow**: `argv` → exit code.
**Relationships**: Called by `__main__.py`.