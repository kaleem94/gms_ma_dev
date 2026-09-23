# gms_ma/reconstruct.py

## reconstruct <!-- ref:gms_ma/reconstruct.py:52-123 -->
**Purpose**: Rebuild a song note-for-note from its stored placements and diff it against the original, printing a match report.
**Why**: This is the validation harness for the whole segmentation idea — if reconstruction matches, the placement model is sound. It reads pattern notes from the DB (BLOB first, legacy file fallback).
**Data Flow**: `repo, song name, out_path` → `report dict (match, mismatches…)`.
**Relationships**: Uses `store.pattern_bytes` / `read_pattern_notes`; exposed via `cli.cmd_reconstruct`.

### _load_pattern_notes / read_pattern_notes <!-- ref:gms_ma/reconstruct.py:14-40 -->
**Purpose**: Extract notes and pitch bends from a pattern given a file path or raw bytes.
**Why**: The BLOB-first approach (via `repo.pattern_bytes`) keeps the DB as the primary store; the legacy file fallback ensures backward compatibility with databases that stored files on disk before the asset BLOB feature.
**Data Flow**: `repo/pattern_id/render` → `([notes], [bends])`.
**Relationships**: Called by `reconstruct` and `library.pattern_events`.

### _geom_song <!-- ref:gms_ma/reconstruct.py:42-50 -->
**Purpose**: Reconstruct a minimal `Song` object from the DB for tempo/time-signature context.
**Why**: The reconstruction needs the song's PPQ and tempo map to place notes at the correct absolute ticks; the `Song` object provides this context.
**Data Flow**: `repo, song_id, row` → `Song`.
**Relationships**: Called by `reconstruct`.

```python
def reconstruct(repo: Repository, name: str, out_path: str | Path | None = None):
    row = repo.conn.execute("SELECT * FROM songs WHERE name=?", (name,)).fetchone()
    if row is None:
        raise KeyError(name)
    song = _geom_song(repo, row["id"], row)
    out_path = Path(out_path) if out_path else Path("out/rebuilt") / f"{name}_rebuilt.mid"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    events_by_track: dict[int, list] = defaultdict(list)
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
```