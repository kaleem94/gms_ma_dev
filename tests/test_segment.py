"""segment tests: repetition detection, cover, ladder variants, content ids."""
from gms_ma.bars import BarMap
from gms_ma.parse import parse_file
from gms_ma.segment import AnalysisConfig, extract_content, segment_instrument
from tests.conftest import BAR, drum_pattern, repetitive_song, write_midi


def _segments(path, cfg=None):
    cfg = cfg or AnalysisConfig()
    song = parse_file(path)
    bm = BarMap(song)
    return song, bm, cfg


def test_detect_one_bar_drums(tmp_path):
    path = tmp_path / "d.mid"
    write_midi(path, parts=[{"name": "d", "channel": 9, "notes": drum_pattern(8)}])
    song, bm, cfg = _segments(path)
    drums = song.instruments[0]
    res = segment_instrument(drums, song, bm, cfg)
    assert res.period_bars == 1
    assert len(res.cover) == 8
    # identical 1-bar loops collapse to a single content id
    cids = {seg.content.content_id() for seg in res.cover}
    assert len(cids) == 1
    # ladder provides longer loop options
    assert res.variants and all(v.length_bars > 1 for v in res.variants)
    # note coverage complete: every note assigned to exactly one cover segment
    assigned = sum(len(s.content.notes) for s in res.cover)
    assert assigned == len(drums.notes)


def test_detect_four_bar_progression(tmp_path):
    path = tmp_path / "p.mid"
    pad = []
    chords = [(60, 64, 67), (62, 65, 69), (57, 60, 64), (55, 59, 62)]
    for b in range(8):
        for pc in chords[b % 4]:
            pad.append((b * BAR, BAR - 10, pc, 90))
    write_midi(path, parts=[{"name": "pad", "channel": 0, "programs": [(0, 48)],
                             "notes": pad}])
    song, bm, cfg = _segments(path)
    inst = song.instruments[0]
    res = segment_instrument(inst, song, bm, cfg)
    assert res.period_bars == 4
    assert len(res.cover) == 2          # bars 0-3 and 4-7
    assert len({s.content.content_id() for s in res.cover}) == 1  # same chord loop
    assert res.cover[0].length_bars == 4


def test_non_repeating_continuous_keeps_single_phrase(tmp_path):
    # unique notes every bar, no real rests -> no place to split
    path = tmp_path / "lead.mid"
    notes = [(b * BAR, BAR // 4, 60 + b, 90) for b in range(12)]
    write_midi(path, parts=[{"name": "lead", "channel": 2, "programs": [(0, 81)],
                             "notes": notes}])
    song, bm, cfg = _segments(path)
    inst = song.instruments[0]
    res = segment_instrument(inst, song, bm, cfg)
    assert res.period_bars is None
    assert len(res.cover) == 1                     # no gaps => one phrase
    assert res.cover[0].length_bars == 12
    assert res.cover[0].bar_index == 0


def test_gap_splitting_phrase_boundaries(tmp_path):
    # two 4-bar phrases separated by a 4-bar rest -> split in the middle of the gap
    path = tmp_path / "phr.mid"
    notes = []
    for b in range(4):                             # phrase 1: bars 0-3
        notes.append((b * BAR, BAR // 4, 60 + b, 90))
    for b in range(8, 12):                         # phrase 2: bars 8-11
        notes.append((b * BAR, BAR // 4, 70 + b, 90))
    write_midi(path, parts=[{"name": "mel", "channel": 2, "programs": [(0, 81)],
                             "notes": notes}])
    song, bm, cfg = _segments(path)
    inst = song.instruments[0]
    res = segment_instrument(inst, song, bm, cfg)
    assert res.period_bars is None
    assert len(res.cover) == 2                     # one split inside the rest
    seg1, seg2 = res.cover
    assert seg1.bar_index == 0 and seg2.bar_index == 6  # boundary mid-gap
    assert len(seg1.content.notes) == 4 and len(seg2.content.notes) == 4
    # coverage is still complete
    assert sum(len(s.content.notes) for s in res.cover) == len(inst.notes)


def test_gap_threshold_respects_small_rests(tmp_path):
    # a single 2/4 bar of rest is only 2 beats -> below the default 4-beat gap
    path = tmp_path / "small.mid"
    from tests.conftest import BAR as _B  # BAR is 4/4; craft 2/4 via shorter notes

    notes = [(0, 100, 60, 90), (2 * 240, 100, 62, 90)]  # two notes, one bar rest
    write_midi(path, parts=[{"name": "x", "channel": 2, "notes": notes}],
               sig=(2, 4), ppq=120)
    song, bm, cfg = _segments(path)
    cfg.loop_gap_bars = 0  # isolate the phrase-gap rule from bar-gap splitting
    inst = song.instruments[0]
    res = segment_instrument(inst, song, bm, cfg)
    assert len(res.cover) == 1


def test_static_pitch_bend_filtered(tmp_path):
    path = tmp_path / "b.mid"
    # neutral 0 held, then a run of constant -200, then one real change to 1000
    write_midi(path, parts=[{"name": "b", "channel": 0, "programs": [(0, 81)],
                             "notes": [(0, BAR * 2, 60, 100)],
                             "bends": [(0, 0), (120, 0), (240, -200),
                                       (480, -200), (600, 1000)]}])
    song, bm, cfg = _segments(path)
    inst = song.instruments[0]
    res = segment_instrument(inst, song, bm, cfg)
    assert len(inst.pitch_bends) == 5
    values = [v for (_t, v) in res.cover[0].content.pitch_bends]
    assert values == [0, -200, 1000]  # static runs collapsed


def _notes_bars(spec, bars):
    """spec: list per bar of (offset, dur, pitch, vel)."""
    notes = []
    for b in range(bars):
        for (off, d, p, v) in spec[b % len(spec)]:
            notes.append((b * BAR + off, d, p, v))
    return notes


def test_repeat_engine_finds_motif_anywhere(tmp_path):
    # bars 0-1 == bars 4-5 (motif R), bars 2-3 are a distinct middle (S)
    R0 = [(0, 120, 60, 100), (480, 240, 62, 90)]
    R1 = [(0, 240, 55, 80)]
    S0 = [(0, 120, 48, 100)]
    S1 = [(480, 120, 50, 90)]
    spec = [R0, R1, S0, S1]
    path = tmp_path / "motif.mid"
    write_midi(path, parts=[{"name": "m", "channel": 2, "programs": [(0, 81)],
                             "notes": _notes_bars(spec, 6)}])
    song, bm, cfg = _segments(path)
    cfg.engine = "repeat"
    cfg.loop_gap_bars = 0  # the S phrase has an internal >=1-bar rest; isolate it
    res = segment_instrument(song.instruments[0], song, bm, cfg)
    assert res.engine == "repeat"
    assert len(res.cover) == 3
    cids = {seg.content.content_id() for seg in res.cover}
    assert len(cids) == 2                      # motif reused once + S middle
    bars_used = sorted(seg.bar_index for seg in res.cover)
    assert bars_used == [0, 2, 4]              # R, S, R windows
    first = next(seg for seg in res.cover if seg.bar_index == 0)
    last = next(seg for seg in res.cover if seg.bar_index == 4)
    assert first.content.content_id() == last.content.content_id()


def test_rhythm_lens_diverges_from_pitch(tmp_path):
    # four bars, same rhythm but each bar a different single note
    path = tmp_path / "rhythm.mid"
    notes = [(b * BAR, 200, 60 + b, 100) for b in range(4)]
    write_midi(path, parts=[{"name": "m", "channel": 2, "programs": [(0, 81)],
                             "notes": notes}])
    song, bm, _ = _segments(path)
    inst = song.instruments[0]

    pitch_res = segment_instrument(inst, song, bm, AnalysisConfig(lens="pitch",
                                                                  engine="period"))
    assert pitch_res.engine == "gap"           # pitches never repeat

    rhythm_res = segment_instrument(inst, song, bm, AnalysisConfig(lens="rhythm",
                                                                   engine="period"))
    assert rhythm_res.engine == "period"
    assert rhythm_res.period_bars == 1
    assert len(rhythm_res.cover) == 4


def test_adaptive_grid_chooses_coarse_dominant_unit(tmp_path):
    # onsets on an 8th-note grid (240 ticks at ppq 480) -> grid unit 240
    from gms_ma.segment import choose_grid_ticks

    path = tmp_path / "grid.mid"
    notes = [(b * BAR + 240 * g, 100, 50 + g, 90)
             for b in range(2) for g in range(4)]
    write_midi(path, parts=[{"name": "g", "channel": 0, "notes": notes}])
    song, bm, cfg = _segments(path)
    assert choose_grid_ticks(bm, song.instruments[0], cfg) == 240


# ------------------------------------------------------------ bar-gap loops
def test_trim_removes_leading_silence(tmp_path):
    # first note sits half a bar into its window; loop must start at the note
    from gms_ma.segment import AnalysisConfig

    notes = [(BAR // 2, 100, 60, 90), (BAR + BAR // 2, 100, 64, 90)]
    path = tmp_path / "trim.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    song, bm, cfg = _segments(path)
    res = segment_instrument(song.instruments[0], song, bm, cfg)
    assert res.cover
    seg = res.cover[0]
    # trimmed: placement origin == first onset, content starts at note 0
    assert seg.start_tick == BAR // 2
    assert seg.content.notes[0][0] == 0
    expected = notes[1][0] + notes[1][1] - notes[0][0]
    assert seg.length_ticks == expected


def test_internal_bar_gap_splits_into_blocks_plus_span(tmp_path):
    # two 2-bar phrases (different notes) separated by a ~2-bar silence, forced
    # into one window by a huge gap_beats so the split is done by the bar-gap
    # rule itself (repeat/period engines see no similarity across the gap).
    from gms_ma.segment import AnalysisConfig

    def phrase(base, root):
        out = []
        for b in range(2):
            out.append((base + b * BAR, BAR - 10, root + b, 90))
        return out

    notes = phrase(0, 60) + phrase(4 * BAR, 72)      # bars 2-3 empty
    path = tmp_path / "blocks.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    song, bm, cfg = _segments(path)
    cfg = AnalysisConfig(engine="gap", gap_beats=64.0)  # loop_gap_bars stays 1
    res = segment_instrument(song.instruments[0], song, bm, cfg)
    assert res.engine == "gap"
    # silence-free blocks: phrase1 (bars0-1) and phrase2 (bars4-5)
    assert len(res.cover) == 2
    counts = sorted(len(s.content.notes) for s in res.cover)
    assert counts == [2, 2]
    for seg in res.cover:
        assert seg.content.notes[0][0] == 0            # no leading silence
        assert seg.start_tick in (0, 4 * BAR)
    # the full span (silence included) is kept as a longer variant
    spans = [v for v in res.variants if len(v.content.notes) == 4]
    assert spans
    assert spans[0].length_ticks > 3 * BAR             # includes the rest
