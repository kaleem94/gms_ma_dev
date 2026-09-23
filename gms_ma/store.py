"""SQLite persistence (repository) for songs, patterns, placements, tags.

The DB is the single source of truth.  Human-readable JSON manifests live in
the ``manifests`` table (not on the filesystem) and can be printed on demand.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS songs(
  id INTEGER PRIMARY KEY,
  name TEXT UNIQUE NOT NULL,
  path TEXT NOT NULL,
  midi_type INTEGER, ticks_per_beat INTEGER, total_ticks INTEGER,
  seconds REAL, bpm REAL, notes INTEGER, drum_notes INTEGER, num_instruments INTEGER,
  created_at TEXT DEFAULT (datetime('now')));
CREATE TABLE IF NOT EXISTS tracks(
  id INTEGER PRIMARY KEY,
  song_id INTEGER NOT NULL, track_index INTEGER NOT NULL, channel INTEGER NOT NULL,
  name TEXT, program INTEGER, is_drums INTEGER, notes_count INTEGER,
  uses_pitch_bend INTEGER, period_bars INTEGER,
  engine TEXT, lens TEXT,
  start_tick INTEGER, end_tick INTEGER,
  UNIQUE(song_id, track_index, channel));
CREATE TABLE IF NOT EXISTS patterns(
  id INTEGER PRIMARY KEY,
  song_id INTEGER NOT NULL, track_id INTEGER NOT NULL, content_id TEXT NOT NULL,
  kind TEXT NOT NULL, length_ticks INTEGER, length_bars INTEGER,
  bar_index INTEGER, start_tick INTEGER, confidence REAL, notes_count INTEGER,
  file TEXT, asset_id TEXT, UNIQUE(song_id, content_id, kind));
CREATE TABLE IF NOT EXISTS placements(
  id INTEGER PRIMARY KEY, song_id INTEGER NOT NULL, track_id INTEGER NOT NULL,
  pattern_id INTEGER NOT NULL, bar_index INTEGER NOT NULL,
  start_tick INTEGER NOT NULL, length_ticks INTEGER NOT NULL, seq INTEGER NOT NULL,
  UNIQUE(song_id, track_id, bar_index));
CREATE TABLE IF NOT EXISTS tempos(
  song_id INTEGER NOT NULL, tick INTEGER NOT NULL, us_per_beat INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS timesigs(
  song_id INTEGER NOT NULL, tick INTEGER NOT NULL, numerator INTEGER NOT NULL,
  denominator INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_tempos_song ON tempos(song_id, tick);
CREATE INDEX IF NOT EXISTS idx_timesigs_song ON timesigs(song_id, tick);
CREATE INDEX IF NOT EXISTS idx_placements_pattern ON placements(pattern_id);
CREATE INDEX IF NOT EXISTS idx_patterns_track ON patterns(track_id);
CREATE TABLE IF NOT EXISTS tags(
  id INTEGER PRIMARY KEY,
  target_type TEXT NOT NULL, target_id INTEGER NOT NULL,
  kind TEXT NOT NULL, value TEXT NOT NULL,
  source TEXT NOT NULL, confidence REAL NOT NULL DEFAULT 1.0,
  created_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_tags_target ON tags(target_type, target_id);
CREATE TABLE IF NOT EXISTS features(
  target_type TEXT NOT NULL, target_id INTEGER NOT NULL, payload TEXT NOT NULL,
  PRIMARY KEY(target_type, target_id));
CREATE TABLE IF NOT EXISTS stems(
  pattern_id INTEGER NOT NULL, render TEXT NOT NULL, file TEXT,
  asset_id TEXT, PRIMARY KEY(pattern_id, render));
CREATE TABLE IF NOT EXISTS motif_hits(
  pattern_id INTEGER NOT NULL, seq INTEGER NOT NULL,
  start_tick INTEGER NOT NULL, end_tick INTEGER NOT NULL,
  length_ticks INTEGER NOT NULL, bar_index INTEGER,
  sim REAL, shift INTEGER, PRIMARY KEY(pattern_id, seq));
CREATE TABLE IF NOT EXISTS midi_assets(
  asset_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL, pattern_id INTEGER NOT NULL, render TEXT,
  sha256 TEXT NOT NULL, data BLOB NOT NULL,
  created_at TEXT DEFAULT (datetime('now')));
CREATE INDEX IF NOT EXISTS idx_assets_pattern ON midi_assets(pattern_id);
CREATE TABLE IF NOT EXISTS index_runs(
  id INTEGER PRIMARY KEY, created_at TEXT DEFAULT (datetime('now')), stats TEXT);
CREATE TABLE IF NOT EXISTS manifests(
  id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, song_id INTEGER NOT NULL,
  payload TEXT NOT NULL, created_at TEXT DEFAULT (datetime('now')),
  UNIQUE(run_id, song_id));
-- Precomputed library metadata (post-processed by `optimize`/index; the viewer
-- self-heals if empty).  Small tables so browsing never scans all tags.
CREATE TABLE IF NOT EXISTS library_facets(
  facet TEXT NOT NULL, value TEXT NOT NULL, n INTEGER NOT NULL,
  PRIMARY KEY(facet, value));
CREATE TABLE IF NOT EXISTS library_songs(
  name TEXT PRIMARY KEY, bpm REAL, seconds REAL,
  n_patterns INTEGER, n_placements INTEGER, stags TEXT);
"""


def _ensure_column(conn, table: str, column: str, decl: str) -> None:
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
    except sqlite3.OperationalError:
        pass  # column already present


class Repository:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        # The connection is shared (check_same_thread=False) with the threaded
        # viewer, so all access must be serialised through this lock.
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        # migrations for databases created before these columns existed
        _ensure_column(self.conn, "tracks", "engine", "TEXT")
        _ensure_column(self.conn, "tracks", "lens", "TEXT")
        _ensure_column(self.conn, "patterns", "asset_id", "TEXT")
        _ensure_column(self.conn, "stems", "asset_id", "TEXT")
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------- writing
    def delete_song(self, name: str) -> dict:
        """Remove a song and every row that depends on it.

        Also used before re-indexing, so a partially indexed ("corrupted") song
        never leaks orphaned tags/features/stems/assets/manifests.  Returns a
        dict of the number of rows removed per table.
        """
        zero = {"songs": 0, "tracks": 0, "patterns": 0, "placements": 0,
                "assets": 0, "stems": 0, "motif_hits": 0, "tags": 0,
                "features": 0, "manifests": 0}
        row = self.conn.execute("SELECT id FROM songs WHERE name=?", (name,)).fetchone()
        if row is None:
            return zero
        sid = row["id"]
        # gather child ids *before* deleting their parents
        track_ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM tracks WHERE song_id=?", (sid,))]
        pat_ids = [r["id"] for r in self.conn.execute(
            "SELECT id FROM patterns WHERE song_id=?", (sid,))]

        counts = dict(zero)
        counts["songs"] = 1
        counts["tracks"] = len(track_ids)
        counts["patterns"] = len(pat_ids)

        for pid in pat_ids:
            for tbl, key in (("motif_hits", "motif_hits"),
                             ("stems", "stems"),
                             ("midi_assets", "assets")):
                counts[key] += self.conn.execute(
                    f"DELETE FROM {tbl} WHERE pattern_id=?", (pid,)).rowcount
            counts["tags"] += self.conn.execute(
                "DELETE FROM tags WHERE target_type='pattern' AND target_id=?",
                (pid,)).rowcount
            counts["features"] += self.conn.execute(
                "DELETE FROM features WHERE target_type='pattern' AND target_id=?",
                (pid,)).rowcount
        for tid in track_ids:
            counts["tags"] += self.conn.execute(
                "DELETE FROM tags WHERE target_type='track' AND target_id=?",
                (tid,)).rowcount
        counts["tags"] += self.conn.execute(
            "DELETE FROM tags WHERE target_type='song' AND target_id=?", (sid,)).rowcount
        counts["features"] += self.conn.execute(
            "DELETE FROM features WHERE target_type='song' AND target_id=?", (sid,)).rowcount
        counts["manifests"] = self.conn.execute(
            "DELETE FROM manifests WHERE song_id=?", (sid,)).rowcount
        counts["placements"] = self.conn.execute(
            "DELETE FROM placements WHERE song_id=?", (sid,)).rowcount
        for tbl in ("patterns", "tracks", "tempos", "timesigs"):
            self.conn.execute(f"DELETE FROM {tbl} WHERE song_id=?", (sid,))
        self.conn.execute("DELETE FROM songs WHERE id=?", (sid,))
        self.conn.commit()
        return counts

    def upsert_song(self, s) -> int:
        cur = self.conn.execute(
            """INSERT INTO songs(name, path, midi_type, ticks_per_beat, total_ticks,
                 seconds, bpm, notes, drum_notes, num_instruments)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET path=excluded.path,
                 midi_type=excluded.midi_type, ticks_per_beat=excluded.ticks_per_beat,
                 total_ticks=excluded.total_ticks, seconds=excluded.seconds,
                 bpm=excluded.bpm, notes=excluded.notes, drum_notes=excluded.drum_notes,
                 num_instruments=excluded.num_instruments""",
            (s.name, s.path, s.midi_type, s.ticks_per_beat, s.total_ticks,
             s.seconds, s.bpm, s.notes, s.drum_notes, s.num_instruments),
        )
        self.conn.commit()
        return self.conn.execute("SELECT id FROM songs WHERE name=?", (s.name,)).fetchone()["id"]

    def add_tempo(self, song_id, tick, us_per_beat): self.conn.execute("INSERT INTO tempos VALUES(?,?,?)", (song_id, tick, us_per_beat))
    def add_timesig(self, song_id, tick, num, denom): self.conn.execute("INSERT INTO timesigs VALUES(?,?,?,?)", (song_id, tick, num, denom))

    def upsert_track(self, song_id, inst, period_bars=None) -> int:
        cur = self.conn.execute(
            """INSERT INTO tracks(song_id, track_index, channel, name, program,
                 is_drums, notes_count, uses_pitch_bend, period_bars, start_tick, end_tick)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(song_id, track_index, channel) DO UPDATE SET
                 name=excluded.name, program=excluded.program, is_drums=excluded.is_drums,
                 notes_count=excluded.notes_count, uses_pitch_bend=excluded.uses_pitch_bend,
                 period_bars=excluded.period_bars, start_tick=excluded.start_tick,
                 end_tick=excluded.end_tick""",
            (song_id, inst.track_index, inst.channel, inst.name,
             inst.program, int(inst.is_drums), len(inst.notes), int(inst.uses_pitch_bend),
             period_bars, inst.start_tick, inst.end_tick),
        )
        self.conn.commit()
        return self.conn.execute(
            "SELECT id FROM tracks WHERE song_id=? AND track_index=? AND channel=?",
            (song_id, inst.track_index, inst.channel),
        ).fetchone()["id"]

    def add_pattern(self, song_id, track_id, content_id, kind, length_ticks,
                    length_bars, bar_index, start_tick, confidence, notes_count, file) -> int:
        cur = self.conn.execute(
            """INSERT OR IGNORE INTO patterns(song_id, track_id, content_id, kind,
                 length_ticks, length_bars, bar_index, start_tick, confidence,
                 notes_count, file)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (song_id, track_id, content_id, kind, length_ticks, length_bars,
             bar_index, start_tick, confidence, notes_count, file),
        )
        self.conn.commit()
        row = self.conn.execute(
            "SELECT id FROM patterns WHERE song_id=? AND content_id=? AND kind=?",
            (song_id, content_id, kind),
        ).fetchone()
        return row["id"]

    def add_placement(self, song_id, track_id, pattern_id, bar_index, start_tick, length_ticks, seq):
        self.conn.execute(
            "INSERT OR REPLACE INTO placements(song_id, track_id, pattern_id, bar_index, start_tick, length_ticks, seq) VALUES(?,?,?,?,?,?,?)",
            (song_id, track_id, pattern_id, bar_index, start_tick, length_ticks, seq),
        )

    def add_motif_hit(self, pattern_id, seq, start_tick, end_tick, length_ticks,
                      bar_index, sim=None, shift=None):
        self.conn.execute(
            "INSERT OR REPLACE INTO motif_hits(pattern_id, seq, start_tick, end_tick, "
            "length_ticks, bar_index, sim, shift) VALUES(?,?,?,?,?,?,?,?)",
            (pattern_id, seq, start_tick, end_tick, length_ticks, bar_index, sim, shift),
        )

    def motif_hits_for(self, pattern_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT seq, start_tick, end_tick, length_ticks, bar_index, sim, shift "
            "FROM motif_hits WHERE pattern_id=? ORDER BY seq", (pattern_id,))]

    def set_tag(self, target_type, target_id, kind, value, source, confidence=1.0):
        self.conn.execute(
            "DELETE FROM tags WHERE target_type=? AND target_id=? AND kind=? AND source=? AND value=?",
            (target_type, target_id, kind, source, value),
        )
        self.conn.execute(
            "INSERT INTO tags(target_type, target_id, kind, value, source, confidence) VALUES(?,?,?,?,?,?)",
            (target_type, target_id, kind, value, source, confidence),
        )
        self.conn.commit()

    def clear_tags(self, target_type, target_id, kind=None, source=None):
        q = "DELETE FROM tags WHERE target_type=? AND target_id=?"
        args: list = [target_type, target_id]
        if kind:
            q += " AND kind=?"; args.append(kind)
        if source:
            q += " AND source=?"; args.append(source)
        self.conn.execute(q, args)
        self.conn.commit()

    def set_feature(self, target_type, target_id, payload: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO features(target_type, target_id, payload) VALUES(?,?,?)",
            (target_type, target_id, json.dumps(payload)),
        )

    def add_stem(self, pattern_id: int, render: str, file: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO stems(pattern_id, render, file) VALUES(?,?,?)",
            (pattern_id, render, file),
        )

    def set_stem_file(self, pattern_id: int, render: str, file: str):
        self.conn.execute(
            "UPDATE stems SET file=? WHERE pattern_id=? AND render=?",
            (file, pattern_id, render),
        )

    def stems_for(self, pattern_id: int) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT render, file, asset_id FROM stems WHERE pattern_id=? ORDER BY render",
            (pattern_id,))]

    # ------------------------------------------------------------ midi assets
    def add_asset(self, asset_id: str, kind: str, pattern_id: int,
                  render: str | None, data: bytes) -> str:
        import hashlib

        sha = hashlib.sha256(data).hexdigest()
        self.conn.execute(
            "INSERT OR REPLACE INTO midi_assets(asset_id, kind, pattern_id, render, sha256, data) "
            "VALUES(?,?,?,?,?,?)",
            (asset_id, kind, pattern_id, render, sha, data),
        )
        return asset_id

    def link_asset(self, kind: str, pattern_id: int, render: str | None,
                   asset_id: str) -> None:
        if kind == "pattern":
            self.conn.execute("UPDATE patterns SET asset_id=? WHERE id=?",
                              (asset_id, pattern_id))
        else:
            self.conn.execute("UPDATE stems SET asset_id=? WHERE pattern_id=? AND render=?",
                              (asset_id, pattern_id, render))

    def pattern_bytes(self, pattern_id: int, render: str | None = None) -> bytes | None:
        """MIDI payload for a pattern (or one of its stems), DB-first.

        Falls back to the legacy on-disk ``file`` path when no asset exists.
        """
        kind = "pattern" if render is None else "stem"
        row = self.conn.execute(
            "SELECT data FROM midi_assets WHERE pattern_id=? AND kind=? AND render IS ?",
            (pattern_id, kind, render)).fetchone()
        if row is not None and row["data"] is not None:
            return bytes(row["data"])
        # legacy fallback: on-disk file
        f = None
        if render is None:
            r = self.conn.execute("SELECT file FROM patterns WHERE id=?", (pattern_id,)).fetchone()
            f = r["file"] if r else None
        else:
            r = self.conn.execute("SELECT file FROM stems WHERE pattern_id=? AND render=?",
                                  (pattern_id, render)).fetchone()
            f = r["file"] if r else None
        if f and Path(f).exists():
            try:
                return Path(f).read_bytes()
            except OSError:
                return None
        return None

    def start_run(self) -> int:
        cur = self.conn.execute("INSERT INTO index_runs DEFAULT VALUES")
        self.conn.commit()
        return cur.lastrowid

    def add_manifest(self, run_id, song_id, payload: dict):
        self.conn.execute(
            "INSERT OR REPLACE INTO manifests(run_id, song_id, payload) VALUES(?,?,?)",
            (run_id, song_id, json.dumps(payload)),
        )

    def finish_run(self, run_id, stats: dict):
        self.conn.execute("UPDATE index_runs SET stats=? WHERE id=?", (json.dumps(stats), run_id))
        self.conn.commit()

    # ------------------------------------------------------------- reading
    def song_id(self, name) -> int | None:
        row = self.conn.execute("SELECT id FROM songs WHERE name=?", (name,)).fetchone()
        return row["id"] if row else None

    def all_songs(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT id,name,notes,drum_notes,num_instruments,bpm,seconds,midi_type,created_at FROM songs ORDER BY name")]

    def song_row(self, name):
        return dict(self.conn.execute("SELECT * FROM songs WHERE name=?", (name,)).fetchone())

    def tracks_for(self, song_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM tracks WHERE song_id=? ORDER BY track_index, channel", (song_id,))]

    def patterns_for(self, song_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM patterns WHERE song_id=? ORDER BY track_id, bar_index", (song_id,))]

    def placements_for(self, song_id):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM placements WHERE song_id=? ORDER BY track_id, seq", (song_id,))]

    def pattern(self, pattern_id):
        return dict(self.conn.execute("SELECT * FROM patterns WHERE id=?", (pattern_id,)).fetchone())

    def tempos_for(self, song_id):
        return [tuple(r) for r in self.conn.execute(
            "SELECT tick, us_per_beat FROM tempos WHERE song_id=? ORDER BY tick", (song_id,))]

    def timesigs_for(self, song_id):
        return [tuple(r) for r in self.conn.execute(
            "SELECT tick, numerator, denominator FROM timesigs WHERE song_id=? ORDER BY tick", (song_id,))]

    def tag_groups(self, target_type, target_id):
        """Tags with manual override precedence: manual > model > heuristic."""
        rows = [dict(r) for r in self.conn.execute(
            "SELECT kind, value, source, confidence FROM tags WHERE target_type=? AND target_id=? ORDER BY kind",
            (target_type, target_id))]
        out: dict[str, list] = {}
        by_kind: dict[str, dict[str, list]] = {}
        order = {"manual": 0, "model": 1, "heuristic": 2}
        for r in rows:
            by_kind.setdefault(r["kind"], {}).setdefault(r["source"], []).append(r)
        for kind, sources in by_kind.items():
            chosen = None
            for src in ("manual", "model", "heuristic"):
                if src in sources:
                    chosen = sources[src]
                    break
            if chosen:
                out[kind] = chosen
        return out

    def manifest_latest(self, name) -> dict | None:
        row = self.conn.execute(
            """SELECT m.payload FROM manifests m JOIN songs s ON s.id=m.song_id
               WHERE s.name=? ORDER BY m.run_id DESC LIMIT 1""", (name,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def latest_run(self):
        row = self.conn.execute("SELECT * FROM index_runs ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None
