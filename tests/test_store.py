"""store tests: schema, placement round trip, tag precedence, manifests."""
from gms_ma.store import Repository


def _repo(tmp_path):
    return Repository(tmp_path / "t.db")


def test_song_lifecycle(tmp_path):
    repo = _repo(tmp_path)
    import types

    s = types.SimpleNamespace(name="song1", path="a.mid", midi_type=1,
                              ticks_per_beat=480, total_ticks=100,
                              seconds=1.0, bpm=120.0, notes=5, drum_notes=0,
                              num_instruments=1)
    sid = repo.upsert_song(s)
    assert sid == repo.song_id("song1")
    repo.add_tempo(sid, 0, 500000)
    repo.add_timesig(sid, 0, 4, 4)
    assert repo.tempos_for(sid) == [(0, 500000)]
    repo.delete_song("song1")
    assert repo.song_id("song1") is None
    repo.close()


def test_tag_manual_override_precedence(tmp_path):
    repo = _repo(tmp_path)
    repo.set_tag("pattern", 1, "style", "syncopated", "heuristic", 0.6)
    repo.set_tag("pattern", 1, "style", "legato", "model", 0.8)
    repo.set_tag("pattern", 1, "style", "driving", "manual", 1.0)
    groups = repo.tag_groups("pattern", 1)
    # manual source wins entirely for this kind
    assert groups["style"][0]["source"] == "manual"
    assert groups["style"][0]["value"] == "driving"
    assert len(groups["style"]) == 1
    # a kind without manual override still exposes heuristic/model rows
    repo.set_tag("pattern", 1, "energy", "high", "heuristic", 0.7)
    groups = repo.tag_groups("pattern", 1)
    assert groups["energy"][0]["value"] == "high"
    repo.close()


def test_manifest_table_round_trip(tmp_path):
    repo = _repo(tmp_path)
    run_id = repo.start_run()
    import types

    s = types.SimpleNamespace(name="songX", path="x.mid", midi_type=1,
                              ticks_per_beat=480, total_ticks=10, seconds=0.1,
                              bpm=120.0, notes=1, drum_notes=0, num_instruments=1)
    sid = repo.upsert_song(s)
    repo.add_manifest(run_id, sid, {"hello": "world", "list": [1, 2]})
    got = repo.manifest_latest("songX")
    assert got == {"hello": "world", "list": [1, 2]}
    repo.close()


def test_feature_store(tmp_path):
    repo = _repo(tmp_path)
    repo.set_feature("pattern", 7, {"onsets": 4, "key": "C major"})
    row = repo.conn.execute(
        "SELECT payload FROM features WHERE target_type='pattern' AND target_id=7"
    ).fetchone()
    assert "C major" in row["payload"]
    repo.close()


def test_delete_song_purges_related_rows(tmp_path):
    import types

    repo = _repo(tmp_path)
    a = types.SimpleNamespace(name="a", path="a.mid", midi_type=1, ticks_per_beat=480,
                              total_ticks=10, seconds=0.1, bpm=120.0, notes=1,
                              drum_notes=0, num_instruments=1)
    b = types.SimpleNamespace(name="b", path="b.mid", midi_type=1, ticks_per_beat=480,
                              total_ticks=10, seconds=0.1, bpm=120.0, notes=1,
                              drum_notes=0, num_instruments=1)
    sid, sid2 = repo.upsert_song(a), repo.upsert_song(b)
    repo.add_tempo(sid, 0, 500000)
    repo.add_timesig(sid, 0, 4, 4)
    inst = types.SimpleNamespace(track_index=0, channel=0, name="t", program=0,
                                 is_drums=False, notes=[], uses_pitch_bend=False,
                                 start_tick=0, end_tick=1)
    tid = repo.upsert_track(sid, inst)
    pid = repo.add_pattern(sid, tid, "cid", "cover", 480, 1, 0, 0, 1.0, 1, "")
    repo.add_placement(sid, tid, pid, 0, 0, 480, 0)
    repo.add_asset("asset1", "pattern", pid, None, b"data")
    repo.link_asset("pattern", pid, None, "asset1")
    repo.add_motif_hit(pid, 0, 0, 240, 240, 0, 1.0, 0)
    repo.conn.execute(
        "INSERT OR REPLACE INTO stems(pattern_id, render, file, asset_id) "
        "VALUES(?,?,?,?)", (pid, "piano", "", "asset1"))
    repo.set_tag("pattern", pid, "style", "x", "heuristic", 0.5)
    repo.set_tag("track", tid, "style", "y", "heuristic", 0.5)
    repo.set_tag("song", sid, "genre", "z", "heuristic", 0.5)
    repo.set_feature("pattern", pid, {"a": 1})
    repo.set_feature("song", sid, {"b": 2})
    run = repo.start_run()
    repo.add_manifest(run, sid, {"x": 1})

    counts = repo.delete_song("a")
    assert counts["songs"] == 1 and counts["tracks"] == 1 and counts["patterns"] == 1
    assert counts["placements"] == 1 and counts["assets"] == 1 and counts["stems"] == 1
    assert counts["motif_hits"] == 1 and counts["tags"] == 3 and counts["features"] == 2
    assert counts["manifests"] == 1

    for tbl, col, val in (("tracks", "song_id", sid), ("patterns", "song_id", sid),
                          ("placements", "song_id", sid), ("tempos", "song_id", sid),
                          ("timesigs", "song_id", sid), ("manifests", "song_id", sid),
                          ("midi_assets", "pattern_id", pid),
                          ("motif_hits", "pattern_id", pid),
                          ("stems", "pattern_id", pid)):
        got = repo.conn.execute(
            f"SELECT COUNT(*) c FROM {tbl} WHERE {col}=?", (val,)).fetchone()["c"]
        assert got == 0, tbl
    left_tags = repo.conn.execute(
        "SELECT COUNT(*) c FROM tags WHERE "
        "(target_type='pattern' AND target_id=?) OR "
        "(target_type='track' AND target_id=?) OR "
        "(target_type='song' AND target_id=?)", (pid, tid, sid)).fetchone()["c"]
    assert left_tags == 0
    # the second song is untouched
    assert repo.song_id("b") == sid2
    repo.close()
