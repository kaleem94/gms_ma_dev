"""Command-line entry point: index / list / manifest / reconstruct / view / tag."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .indexer import build_manifest
from .store import Repository
from . import reconstruct as rc


def _refresh_library_cache(db: str) -> None:
    """Recompute the precomputed library facet/song cache (best-effort)."""
    from . import library as lib

    try:
        repo = Repository(db)
    except Exception:
        return
    try:
        lib.refresh_library_cache(repo)
    except Exception:
        pass
    finally:
        repo.close()


def cmd_index(args):
    from .indexer import AnalysisConfig as _Cfg
    from .indexer import gather_midi, run_index

    files = gather_midi(args.paths)
    if not files:
        print("no MIDI files found", file=sys.stderr)
        return 1
    cfg = _Cfg(engine=args.detect, lens=args.lens,
               grid_cov=args.grid_cov, grid_cap=args.grid_cap,
               fallback_grid_bars=args.fallback_grid,
               min_period_conf=args.min_conf,
               max_period_bars=args.max_period,
               gap_beats=args.gap_beats,
               loop_gap_bars=args.loop_gap_bars,
               stems=not args.no_stems,
               stem_note=args.stem_note, stem_drum=args.stem_drum,
               write_files=not args.no_files,
               motifs=not args.no_motifs,
               motif_max_families=args.motif_max,
               harmony_split=args.harmony_split)
    def _log(msg):
        # per-file timing / ok lines are informational; skip/fail are warnings
        prefix = "  ! " if msg.startswith(("skip", "fail")) else "  "
        print(f"{prefix}{msg}", file=sys.stderr)

    summary = run_index(files, args.db, args.out, cfg, on_log=_log)
    if summary["indexed"]:
        _refresh_library_cache(args.db)      # keep the precomputed cache fresh
    print(json.dumps({"total": summary["total"],
                      "indexed": summary["indexed"],
                      "placements": summary["placements"],
                      "unique_patterns": summary["unique_patterns"],
                      "motif_families": summary.get("motif_families", 0),
                      "elapsed": summary.get("elapsed", 0),
                      "skipped": len(summary["skipped"])}, indent=2))
    if summary["skipped"]:
        print(f"\nskipped files ({len(summary['skipped'])}):", file=sys.stderr)
        for s in summary["skipped"]:
            print(f"  - {s['file']}: {s['reason']}", file=sys.stderr)
    return 0


def _row_matches_tag(repo: Repository, row, tag_filter: str):
    kind, _, want = tag_filter.partition("=")
    if not kind:
        return True
    for k, vals in repo.tag_groups("pattern", row["id"]).items():
        if kind and k != kind:
            continue
        for v in vals:
            if (not want) or (v["value"] == want):
                return True
    return False


def cmd_list(args):
    from .tag import gm_name

    repo = Repository(args.db)
    q = ("SELECT p.*, t.track_index, t.channel, t.program, s.name AS song "
         "FROM patterns p JOIN tracks t ON t.id=p.track_id "
         "JOIN songs s ON s.id=p.song_id ")
    conds, params = [], []
    if args.song:
        conds.append("s.name=?"); params.append(args.song)
    if args.kind:
        conds.append("p.kind=?"); params.append(args.kind)
    if args.channel is not None:
        conds.append("t.channel=?"); params.append(args.channel)
    if args.minconf is not None:
        conds.append("p.confidence>=?"); params.append(args.minconf)
    if conds:
        q += "WHERE " + " AND ".join(conds)
    q += " ORDER BY s.name, t.track_index, p.bar_index"
    rows = [dict(r) for r in repo.conn.execute(q, params)]
    shown = 0
    for r in rows:
        if args.tag and not _row_matches_tag(repo, r, args.tag):
            continue
        shown += 1
        prog = gm_name(r["program"]) if r["program"] is not None else "-"
        if args.json:
            print(json.dumps(r, default=str))
        else:
            print(f"{r['song'][:22]:<22} ch{r['channel']:>2} {prog:<18} "
                  f"{r['kind']:<7} len={r['length_bars']:>2}b conf={r['confidence']:.2f} "
                  f"{r['content_id']}")
    print(f"\n{shown} pattern(s)", file=sys.stderr)
    repo.close()
    return 0


def cmd_manifest(args):
    repo = Repository(args.db)
    if args.song:
        try:
            payload = build_manifest(repo, args.song)
        except KeyError:
            print(f"no such song: {args.song}", file=sys.stderr)
            repo.close()
            return 1
        print(json.dumps(payload, indent=2, default=str))
    else:
        run = repo.latest_run()
        songs = repo.all_songs()
        info = {"songs": songs,
                "latest_run": run and {k: (json.loads(v) if k == "stats" else v)
                                       for k, v in run.items()}}
        print(json.dumps(info, indent=2, default=str))
    repo.close()
    return 0


def cmd_reconstruct(args):
    repo = Repository(args.db)
    try:
        report = rc.reconstruct(repo, args.song, args.out)
    except KeyError:
        print(f"no such song: {args.song}", file=sys.stderr)
        repo.close()
        return 1
    print(json.dumps(report, indent=2, default=str))
    ok = report.get("match", False)
    repo.close()
    return 0 if ok else 2


def cmd_motifs(args):
    """Probe: mine recurring note windows per channel, straight from .mid files."""
    from .bars import BarMap
    from .indexer import gather_midi
    from .motif import MotifConfig, mine_channel
    from .parse import parse_file

    def bar_pos(bm, tick):
        b = bm.tick_to_bar(tick)
        start = bm.bar_start_tick(b)
        ln = bm.bar_length_ticks(b)
        return b + ((tick - start) / ln if ln else 0.0)

    cfg = MotifConfig()
    total = 0
    for path in gather_midi(args.paths):
        try:
            song = parse_file(path)
        except Exception as exc:
            print(f"{path.name}: skip ({exc})", file=sys.stderr)
            continue
        bm = BarMap(song)
        for inst in song.instruments:
            if args.channel is not None and inst.channel != args.channel:
                continue
            fams = mine_channel(inst, song.ticks_per_beat, cfg, bm)
            if not fams:
                continue
            drum = " (drums)" if inst.is_drums else ""
            print(f"\n== {path.name}  ch{inst.channel:02d}{drum}  "
                  f"{len(fams)} motifs / {len(inst.notes)} notes")
            for m in fams:
                b0 = bar_pos(bm, m.start_tick)
                b1 = bar_pos(bm, m.end_tick)
                hits = " ".join(
                    f"{bar_pos(bm, h.start_tick):.2f}"
                    + (f"({'+' if h.shift > 0 else ''}{h.shift})" if h.shift else "")
                    for h in m.hits[:args.top or len(m.hits)])
                print(f"  #{m.id:<3} bars {b0:6.2f}-{b1:6.2f}  "
                      f"len {m.length_ticks:>6}t {m.events:>3}e {m.notes:>3}n "
                      f"sim {m.sim:.2f} x{m.reps} @ {hits}")
            total += len(fams)
    print(f"\n{total} motif families across files", file=sys.stderr)
    return 0


def cmd_view(args):
    repo = Repository(args.db)
    from .viewer import serve

    serve(repo, port=args.port, open_browser=not args.no_browser)


def cmd_tag(args):
    repo = Repository(args.db)
    if args.action == "set":
        target_id = int(args.target_id)
        repo.set_tag(args.target, target_id, args.kind, args.value, "manual", args.confidence)
        print(f"manual tag set: {args.target}#{target_id} {args.kind}={args.value}")
    elif args.action == "clear":
        repo.clear_tags(args.target, int(args.target_id), args.kind, args.source)
        print("cleared")
    elif args.action == "list":
        if args.target == "song" and args.song:
            sid = repo.song_id(args.song)
            if sid is None:
                print(f"no such song: {args.song}", file=sys.stderr)
                return 1
            print(json.dumps({k: v for k, v in repo.tag_groups("song", sid).items()},
                             indent=2, default=str))
        else:
            print("pass --song (and optionally --target) to inspect tags")
    if args.action in ("set", "clear"):
        try:
            from . import library as lib

            lib.refresh_library_cache(repo)   # manual tags change facet values
        except Exception:
            pass
    repo.close()
    return 0


def cmd_gui(args):
    from .gui import main

    main()
    return 0


def cmd_export(args):
    """Write the MIDI stored in the DB back out to real .mid files."""
    from .store import Repository as _Repo

    repo = _Repo(args.db)
    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)
    conds, params = [], []
    if args.song:
        conds.append("s.name=?"); params.append(args.song)
    q = ("SELECT p.id AS pid, p.content_id, p.kind, p.bar_index, p.length_bars, "
         "t.track_index, t.channel, s.name AS song "
         "FROM patterns p JOIN tracks t ON t.id=p.track_id "
         "JOIN songs s ON s.id=p.song_id ")
    if conds:
        q += "WHERE " + " AND ".join(conds)
    q += " ORDER BY s.name, p.id"
    files = 0
    missing = 0
    with_stems = not args.no_stems
    for r in repo.conn.execute(q, params):
        song_dir = outdir / r["song"]
        song_dir.mkdir(parents=True, exist_ok=True)
        base = f"{r['track_index']:02d}_ch{r['channel']:02d}_{r['content_id']}_{r['kind']}" \
               f"_{r['bar_index']:04d}_{r['length_bars']}b"
        data = repo.pattern_bytes(r["pid"])
        if data is None:
            missing += 1
        else:
            (song_dir / f"{base}.mid").write_bytes(data)
            files += 1
        if with_stems:
            for st in repo.stems_for(r["pid"]):
                sdata = repo.pattern_bytes(r["pid"], st["render"])
                if sdata is None:
                    missing += 1
                else:
                    (song_dir / f"{base}_{st['render']}.mid").write_bytes(sdata)
                    files += 1
    print(json.dumps({"written": files, "missing": missing, "out": str(outdir)},
                     indent=2))
    repo.close()
    return 0


def cmd_delete(args):
    """Delete one or more songs (and every row that depends on them) from the DB."""
    from . import doctor as doc
    from .store import Repository

    repo = Repository(args.db)
    names = list(args.song or [])
    if not names:
        print("delete: pass --song NAME (repeatable)", file=sys.stderr)
        repo.close()
        return 2
    targets: list[str] = []
    for name in names:
        sid = repo.song_id(name)
        if sid is None:
            print(f"[warning] no such song: {name}", file=sys.stderr)
            continue
        c = doc.song_counts(repo, sid)
        targets.append(name)
        print(f"[info] {name}: {c['tracks']} track(s), {c['patterns']} pattern(s), "
              f"{c['placements']} placement(s), {c['assets']} asset(s), "
              f"{c['stems']} stem(s)")
    if not targets:
        repo.close()
        return 1
    if not args.yes:
        print("[warning] refusing to delete without --yes", file=sys.stderr)
        repo.close()
        return 2
    import shutil

    for name in targets:
        repo.delete_song(name)
        print(f"[info] deleted song '{name}' from the DB")
        if args.files:
            folder = Path(args.out) / name
            if folder.exists():
                shutil.rmtree(folder)
                print(f"[info] removed {folder}")
    try:
        from . import library as lib

        lib.refresh_library_cache(repo)       # deleted songs change the counts
    except Exception:
        pass
    repo.close()
    return 0


def cmd_doctor(args):
    """Scan the DB for corrupted songs and optionally delete them."""
    from . import doctor as doc
    from .store import Repository

    repo = Repository(args.db)
    findings = doc.scan(repo)
    if not findings:
        print("[info] no corrupted songs found")
        repo.close()
        return 0
    for f in findings:
        print(f"[warning] {f['name']}: " + "; ".join(f["issues"]))
    print(f"[info] {len(findings)} song(s) flagged")
    if not args.delete:
        repo.close()
        return 0
    if not args.yes:
        print("[warning] refusing to delete without --yes", file=sys.stderr)
        repo.close()
        return 2
    import shutil

    for f in findings:
        repo.delete_song(f["name"])
        print(f"[info] deleted song '{f['name']}'")
        if args.files:
            folder = Path(args.out) / f["name"]
            if folder.exists():
                shutil.rmtree(folder)
                print(f"[info] removed {folder}")
    try:
        from . import library as lib

        lib.refresh_library_cache(repo)       # deleted songs change the counts
    except Exception:
        pass
    repo.close()
    return 0


def cmd_optimize(args):
    """Post-process: precompute the library cache and refresh SQLite stats."""
    from . import library as lib

    repo = Repository(args.db)
    try:
        counts = lib.refresh_library_cache(repo)
        try:
            # Analyze only the query-relevant tables (skips the huge BLOB table,
            # which `PRAGMA optimize` would otherwise analyze — ~60 s wasted).
            for tbl in ("patterns", "tracks", "songs", "tags", "placements",
                        "motif_hits", "tempos", "stems", "features"):
                repo.conn.execute(f"ANALYZE {tbl}")
        except Exception:
            pass
        repo.conn.commit()
    finally:
        repo.close()
    print(json.dumps({"db": args.db, "facet_rows": counts["facets"],
                      "song_rows": counts["songs"], "optimized": True}, indent=2))
    return 0


def _build_parser():
    p = argparse.ArgumentParser(prog="gms-ma",
                                description="GMS MIDI analyzer: loop decomposition & library")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    ix = sub.add_parser("index", help="index MIDI files into loops + DB")
    ix.add_argument("paths", nargs="+", help="files or directories")
    ix.add_argument("--db", default="db/midi_loops.db")
    ix.add_argument("--out", default="out/loops")
    ix.add_argument("--fallback-grid", type=int, default=4, help="bars per grid chunk")
    ix.add_argument("--min-conf", type=float, default=0.55)
    ix.add_argument("--max-period", type=int, default=64)
    ix.add_argument("--gap-beats", type=float, default=4.0,
                    help="min silent run (quarter-note beats) that counts as a "
                         "phrase gap when splitting non-repeating material")
    ix.add_argument("--loop-gap-bars", type=float, default=1.0,
                    help="a silence of >= this many bars splits a loop window "
                         "into silence-free block loops (0 disables)")
    ix.add_argument("--detect", choices=("hybrid", "period", "repeat"),
                    default="hybrid",
                    help="period=tile from start, repeat=largest repeat anywhere, "
                         "hybrid=period then repeat then gap")
    ix.add_argument("--lens", choices=("pitch", "rhythm"), default="pitch",
                    help="what a bar must match on: pitch-sets or onset rhythm")
    ix.add_argument("--grid-cov", type=float, default=0.9,
                    help="fraction of onsets the chosen grid must line up")
    ix.add_argument("--grid-cap", type=int, default=32,
                    help="finest beat subdivision to consider")
    ix.add_argument("--no-stems", action="store_true",
                    help="skip exporting piano + clap rhythm skeletons")
    ix.add_argument("--stem-note", type=int, default=60,
                    help="piano skeleton note (MIDI pitch, default C4=60)")
    ix.add_argument("--stem-drum", type=int, default=39,
                    help="clap skeleton GM drum key (default 39 = Hand Clap)")
    ix.add_argument("--no-files", action="store_true",
                    help="do not write .mid to disk (MIDI is always stored in the DB)")
    ix.add_argument("--no-motifs", action="store_true",
                    help="skip mining + storing the grid-free motif catalog")
    ix.add_argument("--motif-max", type=int, default=80,
                    help="max motif families stored per channel (default 80)")
    ix.add_argument("--harmony-split", choices=("off", "top", "onset"), default="top",
                    help="separate harmony/melody layers: top=top voice is melody, "
                         "onset=chord onsets are harmony (off disables)")
    ix.set_defaults(func=cmd_index)

    ls = sub.add_parser("list", help="list extracted patterns")
    ls.add_argument("--db", default="db/midi_loops.db")
    ls.add_argument("--song")
    ls.add_argument("--kind", choices=("cover", "variant", "motif"))
    ls.add_argument("--channel", type=int)
    ls.add_argument("--minconf", type=float)
    ls.add_argument("--tag", help="filter e.g. style=syncopated or mode")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_list)

    mf = sub.add_parser("manifest", help="print a song's manifest JSON (from DB)")
    mf.add_argument("--db", default="db/midi_loops.db")
    mf.add_argument("--song")
    mf.set_defaults(func=cmd_manifest)

    rc_ = sub.add_parser("reconstruct", help="rebuild a song from its placements")
    rc_.add_argument("--db", default="db/midi_loops.db")
    rc_.add_argument("--song", required=True)
    rc_.add_argument("--out")
    rc_.set_defaults(func=cmd_reconstruct)

    ex = sub.add_parser("export", help="write DB-stored MIDI assets to files")
    ex.add_argument("--db", default="db/midi_loops.db")
    ex.add_argument("--song", default=None)
    ex.add_argument("--out", default="out/loops")
    ex.add_argument("--no-stems", action="store_true", help="skip piano/clap stems")
    ex.set_defaults(func=cmd_export)

    vw = sub.add_parser("view", help="open the song-structure timeline viewer")
    vw.add_argument("--db", default="db/midi_loops.db")
    vw.add_argument("--port", type=int, default=8123)
    vw.add_argument("--no-browser", action="store_true")
    vw.set_defaults(func=cmd_view)

    gu = sub.add_parser("gui", help="desktop folder-indexer with progress bar")
    gu.set_defaults(func=cmd_gui)

    tg = sub.add_parser("tag", help="manual tag overrides (list/set/clear)")
    tg.add_argument("--db", default="db/midi_loops.db")
    tg.add_argument("action", choices=("list", "set", "clear"))
    tg.add_argument("--target", choices=("pattern", "song"), default="pattern")
    tg.add_argument("--target-id")
    tg.add_argument("--song", help="for song tags or list")
    tg.add_argument("--kind")
    tg.add_argument("--value")
    tg.add_argument("--confidence", type=float, default=1.0)
    tg.add_argument("--source", default=None)
    tg.set_defaults(func=cmd_tag)

    mo = sub.add_parser("motifs", help="probe: mine recurring windows per channel "
                                       "(prototype, no DB)")
    mo.add_argument("paths", nargs="+", help="files or directories")
    mo.add_argument("--channel", type=int, default=None)
    mo.add_argument("--top", type=int, default=0, help="show only first N hits per motif")
    mo.set_defaults(func=cmd_motifs)

    dl = sub.add_parser("delete", help="delete a song from the DB")
    dl.add_argument("--db", default="db/midi_loops.db")
    dl.add_argument("--song", action="append", default=None,
                    help="song name (repeatable)")
    dl.add_argument("--files", action="store_true",
                    help="also remove the generated out/<song>/ folder")
    dl.add_argument("--out", default="out/loops")
    dl.add_argument("--yes", action="store_true", help="confirm the deletion")
    dl.set_defaults(func=cmd_delete)

    dr = sub.add_parser("doctor", help="scan the DB for corrupted songs")
    dr.add_argument("--db", default="db/midi_loops.db")
    dr.add_argument("--delete", action="store_true", help="delete flagged songs")
    dr.add_argument("--files", action="store_true",
                    help="also remove their generated out/<song>/ folders")
    dr.add_argument("--out", default="out/loops")
    dr.add_argument("--yes", action="store_true", help="confirm deletion")
    dr.set_defaults(func=cmd_doctor)

    op = sub.add_parser("optimize",
                        help="post-process: precompute library facets/songs + "
                             "refresh SQLite stats")
    op.add_argument("--db", default="db/midi_loops.db")
    op.set_defaults(func=cmd_optimize)
    return p


def main(argv=None):
    args = _build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
