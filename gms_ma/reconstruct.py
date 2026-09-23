"""Reconstruct a whole song from its stored loop placements (round-trip check).

Reads pattern .mid files + the DB placement table and re-places every cover
segment at its recorded tick, then compares the rebuilt note-on stream with the
original file.  Proves the placement metadata is complete and tick-accurate.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import mido

from . import parse as parse_mod
from .model import Song, TempoEvent, TimeSigEvent
from .store import Repository


def _load_pattern_notes(src):
    """Notes/bends of a pattern given a file path *or* raw bytes."""
    try:
        if isinstance(src, (bytes, bytearray)):
            sng = parse_mod.parse_bytes(bytes(src), "loop")
        else:
            sng = parse_mod.parse_file(src)
    except Exception:
        return [], []
    notes, bends = [], []
    for inst in sng.instruments:
        for n in inst.notes:
            notes.append((n.start, n.duration, n.pitch, n.velocity))
        for b in inst.pitch_bends:
            bends.append((b.tick, b.value))
    return notes, bends


def read_pattern_notes(repo: Repository, pattern_id: int,
                       render: str | None = None):
    """Notes/bends of a DB pattern/stem (asset BLOB, legacy file fallback)."""
    data = repo.pattern_bytes(pattern_id, render)
    return _load_pattern_notes(data) if data is not None else ([], [])


def _geom_song(repo: Repository, song_id: int, row) -> Song:
    song = Song(name=row["name"], path=row["path"], midi_type=row["midi_type"],
                ticks_per_beat=row["ticks_per_beat"], total_ticks=row["total_ticks"])
    song.tempos = [TempoEvent(t, u) for (t, u) in repo.tempos_for(song_id)]
    song.time_signatures = [TimeSigEvent(t, n, d) for (t, n, d) in repo.timesigs_for(song_id)]
    return song


def reconstruct(repo: Repository, name: str, out_path: str | Path | None = None):
    row = repo.conn.execute("SELECT * FROM songs WHERE name=?", (name,)).fetchone()
    if row is None:
        raise KeyError(name)
    song = _geom_song(repo, row["id"], row)
    out_path = Path(out_path) if out_path else Path("out/rebuilt") / f"{name}_rebuilt.mid"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    events_by_track: dict[int, list] = defaultdict(list)  # track_id -> msgs
    track_row: dict[int, dict] = {}
    for t in repo.tracks_for(row["id"]):
        track_row[t["id"]] = t
        if t["program"] is not None and not t["is_drums"]:
            events_by_track[t["id"]].append(
                (0, mido.Message("program_change", channel=t["channel"], program=t["program"], time=0))
            )
    for pl in repo.placements_for(row["id"]):
        pat = repo.pattern(pl["pattern_id"])
        notes, bends = read_pattern_notes(repo, pat["id"])
        off = pl["start_tick"]
        trow = track_row[pl["track_id"]]
        ch = trow["channel"]
        for (s, d, pitch, vel) in notes:
            events_by_track[pl["track_id"]].append(
                (off + s, mido.Message("note_on", channel=ch, note=pitch, velocity=max(1, vel), time=0)))
            events_by_track[pl["track_id"]].append(
                (off + s + d, mido.Message("note_off", channel=ch, note=pitch, velocity=0, time=0)))
        for (t, v) in bends:
            events_by_track[pl["track_id"]].append(
                (off + t, mido.Message("pitchwheel", channel=ch, pitch=v, time=0)))

    mf = mido.MidiFile(type=1, ticks_per_beat=song.ticks_per_beat)
    meta = mido.MidiTrack()
    cursor = 0
    for ev in song.tempos:
        meta.append(mido.MetaMessage("set_tempo", tempo=ev.us_per_beat, time=max(0, ev.tick - cursor)))
        cursor = ev.tick
    cursor = 0
    for ts in song.time_signatures:
        meta.append(mido.MetaMessage("time_signature", numerator=ts.numerator,
                                     denominator=ts.denominator, time=max(0, ts.tick - cursor)))
        cursor = ts.tick
    meta.append(mido.MetaMessage("track_name", name=f"{name} (reconstructed)", time=0))
    mf.tracks.append(meta)

    for tid, msgs in sorted(events_by_track.items()):
        msgs.sort(key=lambda m: (m[0], 0 if m[1].type == "note_off" else 1))
        track = mido.MidiTrack()
        cursor = 0
        for abs_t, msg in msgs:
            msg.time = max(0, abs_t - cursor)
            cursor = abs_t
            track.append(msg)
        if len(track) > 1:
            mf.tracks.append(track)
    mf.save(str(out_path))

    # ---- verification against the original -----------------------------
    orig = parse_mod.parse_file(song.path)
    expected = _onset_stream(orig)
    rebuilt_notes = _collect_onsets(events_by_track, track_row)
    ok = expected == rebuilt_notes
    return {
        "song": name,
        "out": str(out_path),
        "orig_notes": sum(len(v) for v in expected.values()),
        "rebuilt_notes": sum(len(v) for v in rebuilt_notes.values()),
        "match": ok,
        "mismatch_channels": sorted(set(expected) ^ set(rebuilt_notes)),
    }


def _onset_stream(song) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for inst in song.instruments:
        for n in inst.notes:
            out[inst.channel].append((n.start, n.pitch, n.velocity))
    for k in out:
        out[k].sort()
    return dict(out)


def _collect_onsets(events_by_track, track_row) -> dict[int, list]:
    out: dict[int, list] = defaultdict(list)
    for tid, msgs in events_by_track.items():
        ch = track_row[tid]["channel"]
        for abs_t, msg in msgs:
            if msg.type == "note_on":
                out[ch].append((abs_t, msg.note, msg.velocity))
    for k in out:
        out[k].sort()
    return dict(out)
