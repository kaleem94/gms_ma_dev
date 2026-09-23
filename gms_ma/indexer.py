"""High-level indexing: files -> neutral model -> segments -> .mid + DB.

This module is the composition root for ``index``: it wires the repository,
segmenter, exporter, feature/tag rules and manifest persistence together.
"""
from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from uuid import uuid4

from .bars import BarMap
from .features import analyze_notes, energy_score
from .model import Song
from .motif import MotifConfig, mine_channel
from .parse import parse_file
from .segment import AnalysisConfig, PatternContent, segment_song
from .store import Repository
from .tag import emotion_for, genre_for, gm_name, pattern_tags, tempo_class
from . import export as exporter
from . import features as feat


def pattern_metrics(content: PatternContent, pitched: bool, ppq: int | None = None):
    return analyze_notes(
        [(n[0], n[1], n[2], n[3]) for n in content.notes],
        content.length_ticks,
        content.length_bars if hasattr(content, "length_bars") else 1,
        pitched=pitched,
        ppq=ppq,
    )


def key_label_of(m) -> str:
    from .music_theory import key_name

    return key_name(m.key_tonic, m.key_mode)


def song_analysis(song: Song, bm: BarMap) -> dict:
    """Aggregate features/tags for a whole song (used for song-level tags)."""
    pitched = []
    all_notes = []
    has_drums = False
    prog_counts: Counter = Counter()
    for inst in song.instruments:
        has_drums = has_drums or inst.is_drums
        if not inst.is_drums and inst.program is not None:
            prog_counts[inst.program] += 1
        for n in inst.notes:
            all_notes.append((n.start, n.duration, n.pitch, n.velocity))
            if not inst.is_drums:
                pitched.append((n.start, n.duration, n.pitch, n.velocity))
    bars = bm.num_bars
    total = max(song.total_ticks, 1)
    pitched_m = analyze_notes(pitched, total, bars, pitched=True) if pitched else None
    overall_m = analyze_notes(all_notes, total, bars, pitched=False)
    bpm = 60_000_000.0 / song.initial_tempo
    genre, genre_conf = genre_for(prog_counts, has_drums, bpm)
    energy = energy_score(overall_m)
    is_major = pitched_m is None or pitched_m.key_mode == "major"
    centroid = pitched_m.pitch_centroid if pitched_m else 0.0
    valence, arousal, emotion = emotion_for(energy, bpm, is_major, centroid)
    return {
        "bpm": round(bpm, 1),
        "tempo_class": tempo_class(bpm),
        "genre": genre,
        "genre_conf": genre_conf,
        "energy": round(energy, 3),
        "mode": pitched_m.key_mode if pitched_m else "n/a",
        "key": key_label_of(pitched_m) if pitched_m else "n/a",
        "emotion": emotion,
        "valence": valence,
        "arousal": arousal,
        "pitched_m": pitched_m,
        "overall_m": overall_m,
    }


def process_song(
    repo: Repository,
    song: Song,
    bm: BarMap,
    out_dir: Path,
    cfg: AnalysisConfig,
    song_id: int,
) -> dict:
    """Segment, export, tag and store one already-parsed song. Returns stats."""
    stat = {"instruments": 0, "cover_segments": 0, "unique_cover": 0, "variants": 0,
            "motifs": 0, "patterns_tagged": 0}
    song_dir = out_dir / song.name
    song_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    by_inst = {id(r.instrument): r for r in segment_song(song, bm, cfg)}

    sa = song_analysis(song, bm)

    # --- song-level heuristic tags + features
    repo.clear_tags("song", song_id, source="heuristic")
    song_tags = [
        {"kind": "genre", "value": sa["genre"], "confidence": sa["genre_conf"]},
        {"kind": "mode", "value": sa["mode"], "confidence": 0.6},
        {"kind": "energy", "value": _energy_label(sa["energy"]),
         "confidence": round(0.5 + 0.4 * abs(sa["energy"] - 0.5) * 2, 2)},
        {"kind": "emotion", "value": sa["emotion"], "confidence": 0.55},
    ]
    if sa["mode"] != "n/a":
        song_tags.append({"kind": "key", "value": sa["key"], "confidence": 0.6})
    song_tags.append({"kind": "tempo-class", "value": sa["tempo_class"], "confidence": 0.8})
    for t in song_tags:
        repo.set_tag("song", song_id, t["kind"], t["value"], "heuristic", t["confidence"])
    repo.set_feature("song", song_id, {
        "bpm": sa["bpm"], "valence": sa["valence"], "arousal": sa["arousal"],
        "energy": sa["energy"],
    })

    for inst in song.instruments:
        track_id = repo.upsert_track(song_id, inst)
        stat["instruments"] += 1
        res = by_inst.get(id(inst))
        if res is None:
            continue
        repo.conn.execute("UPDATE tracks SET period_bars=?, engine=?, lens=? WHERE id=?",
                          (res.period_bars, res.engine, res.lens, track_id))
        for seg in res.cover:
            pid = _persist_pattern(repo, song_id, track_id, seg.content, "cover",
                                   seg.bar_index, seg.start_tick, seg.length_ticks,
                                   seg.length_bars, seg.confidence, inst, song,
                                   song_dir, written, stat, cfg)
            repo.add_placement(song_id, track_id, pid, seg.bar_index,
                               seg.start_tick, seg.length_ticks, seg.bar_index)
            stat["cover_segments"] += 1
        for v in res.variants:
            _persist_pattern(repo, song_id, track_id, v.content, "variant",
                             v.bar_index, v.start_tick, v.length_ticks, v.length_bars,
                             v.confidence, inst, song, song_dir, written, stat, cfg)
        if cfg.motifs:
            _persist_motifs(repo, song_id, track_id, inst, song, bm, cfg,
                            song_dir, written, stat)
    repo.conn.commit()
    return stat


def _energy_label(e: float) -> str:
    return "high" if e > 0.62 else ("mid" if e > 0.32 else "low")


def _fmt_dur(seconds: float) -> str:
    """Human-readable duration: seconds under a minute, else minutes+seconds."""
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes)}m {secs:04.1f}s"


def _persist_motifs(repo, song_id, track_id, inst, song, bm, cfg, song_dir,
                    written, stat) -> None:
    """Mine the grid-free motif catalog for one channel and store each family.

    Families are stored like ordinary patterns (kind ``motif``: .mid asset +
    stems + tags/features, so they are browsable, auditionable and exportable)
    plus a ``motif_hits`` row per occurrence.
    """
    mcfg = MotifConfig(max_families=cfg.motif_max_families)
    # Mixed pass, plus a separated melody / harmony pass when enabled.
    runs: list[tuple[str, object]] = [("", None)]
    if cfg.harmony_split != "off" and not inst.is_drums:
        from .harmony import split_layers

        melody, harmony = split_layers(inst.notes, cfg.harmony_split)
        if melody:
            runs.append(("melody", melody))
        if harmony:
            runs.append(("harmony", harmony))
    fams = []
    for layer, layer_notes in runs:
        try:
            fams.extend(mine_channel(inst, song.ticks_per_beat, mcfg, bm,
                                     notes=layer_notes, layer=layer))
        except Exception:
            continue  # motif mining is best-effort; never fail an index on it
    for fam in fams:
        if not fam.content:
            continue
        content = PatternContent(
            track_index=inst.track_index, channel=inst.channel,
            length_ticks=fam.length_ticks, notes=fam.content, pitch_bends=[],
            length_bars=bm.span_bars(fam.start_tick, fam.end_tick))
        pid = _persist_pattern(
            repo, song_id, track_id, content, "motif",
            bm.tick_to_bar(fam.start_tick), fam.start_tick,
            content.length_ticks, content.length_bars, fam.sim,
            inst, song, song_dir, written, stat, cfg)
        if fam.layer:
            repo.set_tag("pattern", pid, "layer", fam.layer, "heuristic", 0.8)
        for seq, h in enumerate(fam.hits):
            repo.add_motif_hit(pid, seq, h.start_tick, h.end_tick,
                               max(1, h.end_tick - h.start_tick),
                               bm.tick_to_bar(h.start_tick), h.sim, h.shift)


def _store_stem(repo, pid, song_dir, inst, cid, kind, bar_index, length_bars,
                render, data, write_files):
    """Persist one rendered stem (asset + stems row, optional .mid on disk)."""
    sa = repo.add_asset(str(uuid4()), "stem", pid, render, data)
    sfile = ""
    if write_files:
        stem_name = (f"{inst.track_index:02d}_ch{inst.channel:02d}_{cid}_{kind}"
                     f"_{bar_index:04d}_{length_bars}b_{render}.mid")
        spath = song_dir / stem_name
        spath.write_bytes(data)
        sfile = str(spath)
    repo.conn.execute(
        "INSERT OR REPLACE INTO stems(pattern_id, render, file, asset_id) "
        "VALUES(?,?,?,?)", (pid, render, sfile, sa))


def _persist_pattern(repo, song_id, track_id, content, kind, bar_index, start_tick,
                     length_ticks, length_bars, confidence, inst, song, song_dir,
                     written, stat, cfg) -> int:
    cid = content.content_id()
    pid = repo.add_pattern(song_id, track_id, cid, kind, length_ticks, length_bars,
                           bar_index, start_tick, confidence, len(content.notes), "")
    key = f"{kind}:{cid}"
    if key not in written:
        program = inst.program_at(start_tick)
        data = exporter.pattern_to_bytes(content, song, program,
                                         label=f"{inst.name or gm_name(program)} {kind}")
        # always persist into the DB (self-contained)
        pa = repo.add_asset(str(uuid4()), "pattern", pid, None, data)
        repo.link_asset("pattern", pid, None, pa)
        file_v = ""
        if cfg.write_files:
            fname = (f"{inst.track_index:02d}_ch{inst.channel:02d}_{cid}_{kind}"
                     f"_{bar_index:04d}_{length_bars}b.mid")
            fpath = song_dir / fname
            fpath.write_bytes(data)
            file_v = str(fpath)
        repo.conn.execute("UPDATE patterns SET file=? WHERE id=?", (file_v, pid))
        written.add(key)
        stat["unique_cover" if kind == "cover" else "variants"
              if kind == "variant" else "motifs"] += 1

        if cfg.stems:
            for mode in ("piano", "clap"):
                sk = exporter.render_rhythm_skeleton(
                    content, song, mode,
                    note=cfg.stem_note, drum=cfg.stem_drum,
                    label=f"{mode} skeleton {kind}")
                _store_stem(repo, pid, song_dir, inst, cid, kind, bar_index,
                            length_bars, mode, sk, cfg.write_files)

        # harmony / melody separation stems (pitched, non-drum patterns)
        if cfg.stems and cfg.harmony_split != "off" and not inst.is_drums:
            from .harmony import split_layers

            melody, harmony = split_layers(content.notes, cfg.harmony_split)
            for render, subset in (("melody", melody), ("harmony", harmony)):
                if not subset:
                    continue
                sdata = exporter.pattern_to_bytes(
                    exporter.subset_content(content, subset), song, program,
                    label=f"{inst.name or gm_name(program)} {render}")
                _store_stem(repo, pid, song_dir, inst, cid, kind, bar_index,
                            length_bars, render, sdata, cfg.write_files)

        # pattern-level heuristic tags + features
        pitched = not inst.is_drums
        m = pattern_metrics(content, pitched, song.ticks_per_beat)
        if content.length_bars > 0 and m.onsets > 0:
            repo.clear_tags("pattern", pid, source="heuristic")
            for t in pattern_tags(m, inst.is_drums):
                repo.set_tag("pattern", pid, t["kind"], t["value"], "heuristic",
                             t["confidence"])
            repo.set_feature("pattern", pid, m.to_dict())
            stat["patterns_tagged"] += 1
    return pid


def build_manifest(repo: Repository, name: str) -> dict:
    """Assemble the human/JSON view of a song straight from the DB tables."""
    sid = repo.song_id(name)
    if sid is None:
        raise KeyError(name)
    song_row = repo.song_row(name)
    from .model import Song, TempoEvent, TimeSigEvent

    geom_song = Song(name=name, path=song_row["path"], midi_type=song_row["midi_type"],
                     ticks_per_beat=song_row["ticks_per_beat"],
                     total_ticks=song_row["total_ticks"])
    geom_song.tempos = [TempoEvent(t, u) for (t, u) in repo.tempos_for(sid)]
    geom_song.time_signatures = [TimeSigEvent(t, n, d) for (t, n, d) in repo.timesigs_for(sid)]
    bm = BarMap(geom_song)
    bars = [[bm.bar_start_tick(i), round(bm.second_at_tick(bm.bar_start_tick(i)), 3)]
            for i in range(bm.num_bars)]

    tracks = []
    for t in repo.tracks_for(sid):
        pats = [dict(p) for p in repo.conn.execute(
            "SELECT * FROM patterns WHERE track_id=? ORDER BY kind, bar_index", (t["id"],))]
        for p in pats:
            p["tags"] = repo.tag_groups("pattern", p["id"])
            p["stems"] = repo.stems_for(p["id"])
        t2 = dict(t)
        t2["program_name"] = gm_name(t["program"])
        t2["patterns"] = pats
        t2["tags"] = repo.tag_groups("track", t["id"])
        motifs = []
        for p in pats:
            if p["kind"] == "motif":
                m = dict(p)
                m["hits"] = repo.motif_hits_for(p["id"])
                motifs.append(m)
        t2["motifs"] = motifs
        tracks.append(t2)

    placements = []
    for pl in repo.placements_for(sid):
        p = repo.pattern(pl["pattern_id"])
        placements.append({
            "track_id": pl["track_id"], "pattern_id": pl["pattern_id"],
            "content_id": p["content_id"], "bar_index": pl["bar_index"],
            "start_tick": pl["start_tick"], "length_ticks": pl["length_ticks"],
            "confidence": p["confidence"], "length_bars": p["length_bars"],
        })
    unique_cover = len({(pl["track_id"], pl["content_id"]) for pl in placements})
    motif_families = sum(len(t2["motifs"]) for t2 in tracks)
    motif_hits = sum(len(m["hits"]) for t2 in tracks for m in t2["motifs"])
    stats = {
        "placements": len(placements),
        "unique_cover": unique_cover,
        "compression_ratio": round(unique_cover / len(placements), 3) if placements else 0,
        "repetition_factor": round(len(placements) / unique_cover, 1) if unique_cover else 0,
        "motif_families": motif_families,
        "motif_hits": motif_hits,
    }
    return {
        "song": {k: song_row[k] for k in ("name", "path", "midi_type", "ticks_per_beat",
                                          "total_ticks", "seconds", "bpm", "notes",
                                          "drum_notes", "num_instruments")},
        "tempos": repo.tempos_for(sid),
        "timesigs": repo.timesigs_for(sid),
        "bars": bars,
        "tags": repo.tag_groups("song", sid),
        "stats": stats,
        "tracks": tracks,
        "placements": placements,
    }


def gather_midi(paths) -> list[Path]:
    """Recursively collect every .mid/.midi under the given files/folders."""
    files: list[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.mid")))
            files.extend(sorted(p.rglob("*.midi")))
        elif p.is_file():
            files.append(p)
    seen = set()
    out = []
    for f in files:
        key = str(f)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _norm_path(p) -> str:
    import os

    return os.path.normcase(os.path.abspath(str(p)))


def corrupt_songs(db_path) -> list[dict]:
    """Corrupt songs in the DB (fast, structural checks only); [] if no DB."""
    if not Path(db_path).exists():
        return []
    from . import doctor as doc

    try:
        repo = Repository(db_path)
    except Exception:
        return []
    try:
        return doc.scan(repo, deep=False)
    finally:
        repo.close()


def classify_indexed_ex(db_path, files, check_corrupt: bool = True):
    """Like :func:`classify_indexed` but also returns the corrupt files.

    Returns ``(pending, skipped, corrupt_files)``.  A file whose stored song is
    corrupt is moved from *skipped* to *pending* so it gets re-parsed.
    """
    import sqlite3

    indexed: set[str] = set()
    if Path(db_path).exists():
        try:
            con = sqlite3.connect(str(db_path))
            for (p,) in con.execute("SELECT path FROM songs"):
                if p:
                    indexed.add(_norm_path(p))
            con.close()
        except sqlite3.Error:
            indexed = set()

    corrupt_paths: set[str] = set()
    if check_corrupt and indexed:
        for f in corrupt_songs(db_path):
            if f.get("path"):
                corrupt_paths.add(_norm_path(f["path"]))

    pending: list[Path] = []
    skipped: list[Path] = []
    corrupt: list[Path] = []
    for f in files:
        key = _norm_path(f)
        if key in corrupt_paths:
            corrupt.append(f)
            pending.append(f)
        elif key in indexed:
            skipped.append(f)
        else:
            pending.append(f)
    return pending, skipped, corrupt


def classify_indexed(db_path, files, check_corrupt: bool = True) -> tuple[list[Path], list[Path]]:
    """Split files into (pending, already-indexed) by exact stored path.

    A file counts as already processed when the database's ``songs.path`` equals
    its absolute, case-normalised path — unless that song is corrupt, in which
    case it is treated as pending so it gets re-parsed.  Missing/unreadable DB
    -> all pending.
    """
    pending, skipped, _corrupt = classify_indexed_ex(db_path, files, check_corrupt)
    return pending, skipped


def run_index(
    files,
    db: str,
    out: str,
    cfg: AnalysisConfig | None = None,
    on_file=None,
    on_log=None,
    cancel=None,
) -> dict:
    """Index every MIDI file into loops + DB, reporting progress via callbacks.

    ``on_file(current, total, path, status)`` is called once per file;
    ``on_log(msg)`` receives human-readable skip/status lines.  ``cancel`` is an
    optional zero-arg callable that, when truthy, aborts between files.
    """
    import types

    from .parse import ParseError

    cfg = cfg or AnalysisConfig()
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    repo = Repository(db)
    run_id = repo.start_run()
    total = len(files)
    skipped: list[dict] = []
    indexed = placements = unique_patterns = motif_families = 0
    started = time.perf_counter()
    total_elapsed = 0.0
    try:
        for i, path in enumerate(files, start=1):
            if cancel is not None and cancel():
                raise RuntimeError("index cancelled by user")
            file_start = time.perf_counter()
            try:
                song = parse_file(path)
            except ParseError as exc:
                elapsed = time.perf_counter() - file_start
                skipped.append({"file": str(path), "reason": str(exc)})
                if on_log:
                    on_log(f"skip {path.name}: {exc} ({_fmt_dur(elapsed)})")
                if on_file:
                    on_file(i, total, path, "skipped")
                continue
            bm = BarMap(song)
            repo.delete_song(song.name)
            s = types.SimpleNamespace(
                name=song.name, path=song.path, midi_type=song.midi_type,
                ticks_per_beat=song.ticks_per_beat, total_ticks=song.total_ticks,
                seconds=round(bm.second_at_tick(song.total_ticks), 3),
                bpm=round(60_000_000.0 / song.initial_tempo, 1),
                notes=song.total_note_ons(), drum_notes=song.drum_notes,
                num_instruments=len(song.instruments),
            )
            sid = repo.upsert_song(s)
            for e in song.tempos:
                repo.add_tempo(sid, e.tick, e.us_per_beat)
            for e in song.time_signatures:
                repo.add_timesig(sid, e.tick, e.numerator, e.denominator)
            try:
                stats = process_song(repo, song, bm, out_dir, cfg, sid)
            except Exception as exc:
                # roll back the partial song so a failed analysis never leaves
                # a corrupted/incomplete row in the DB
                elapsed = time.perf_counter() - file_start
                repo.delete_song(song.name)
                skipped.append({"file": str(path), "reason": f"analysis failed: {exc}"})
                if on_log:
                    on_log(f"fail {path.name}: {exc} ({_fmt_dur(elapsed)})")
                if on_file:
                    on_file(i, total, path, "failed")
                continue
            payload = build_manifest(repo, song.name)
            repo.add_manifest(run_id, sid, payload)
            indexed += 1
            placements += stats["cover_segments"]
            unique_patterns += stats["unique_cover"]
            motif_families += stats.get("motifs", 0)
            elapsed = time.perf_counter() - file_start
            if on_log:
                on_log(f"ok {path.name} in {_fmt_dur(elapsed)}")
            if on_file:
                on_file(i, total, path, "ok")
    finally:
        total_elapsed = time.perf_counter() - started
        repo.finish_run(run_id, {"songs": indexed, "placements": placements,
                                 "unique_patterns": unique_patterns,
                                 "motif_families": motif_families,
                                 "skipped": len(skipped)})
        repo.close()
        if on_log:
            on_log(f"total time: {_fmt_dur(total_elapsed)}")
    return {
        "total": total,
        "indexed": indexed,
        "placements": placements,
        "unique_patterns": unique_patterns,
        "motif_families": motif_families,
        "elapsed": round(total_elapsed, 3),
        "skipped": skipped,
    }
