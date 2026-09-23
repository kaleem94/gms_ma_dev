"""motif mining tests: data-driven grid, off-bar & sub-bar motifs, tolerance.

The miner is intentionally grid-free / bar-free: windows may start anywhere
(pickups, off-beats) and have any length (sub-bar riffs up to whole phrases).
Matching is tolerant of small timing jitter and of transposition/ornamentation.
A motif's ``length_ticks`` ends at the last note, so tests judge period from
the *spacing between hits* (start_tick deltas).
"""
from gms_ma.motif import MotifConfig, mine_channel
from gms_ma.parse import parse_file
from tests.conftest import BAR, write_midi


def _mine(path, cfg=None):
    song = parse_file(path)
    return mine_channel(song.instruments[0], song.ticks_per_beat,
                        cfg or MotifConfig())


def _spacing(m):
    return m.hits[1].start_tick - m.hits[0].start_tick if m.reps > 1 else 0


def _motifs_by(fams, **pred):
    return [m for m in fams
            if all(getattr(m, k) == v for k, v in pred.items())]


# ------------------------------------------------------------------ basics
def test_finds_one_bar_loop(tmp_path):
    # 8 identical bars of a kick/snare/hat groove -> 1-bar motif x8
    notes = []
    for b in range(8):
        t0 = b * BAR
        notes += [(t0, 20, 36, 110), (t0 + BAR // 2, 20, 38, 100),
                  (t0 + BAR // 4, 15, 42, 80), (t0 + 3 * BAR // 4, 15, 42, 80)]
    path = tmp_path / "loop.mid"
    write_midi(path, parts=[{"name": "d", "channel": 9, "notes": notes}])
    fams = _mine(path)
    bar = [m for m in fams if m.events == 4]
    assert any(m.reps >= 8 and abs(_spacing(m) - BAR) < 10 for m in bar)


def test_sub_bar_motif_beats_bar_grid(tmp_path):
    # two 2-note riffs alternate every 2 beats: sub-bar cells recur x8 though
    # the 1-bar surface also repeats (A/B). The 2-event cell must be found with
    # its ~2-beat period (BAR/2) - something bar-grid engines never produce.
    def riff(base, lo):
        return [(base, 100, lo, 90), (base + 400, 100, lo + 2, 90)]
    notes = []
    for bar in range(8):
        for half in range(2):
            lo = 60 if (bar + half) % 2 == 0 else 50
            off = half * (BAR // 2)
            notes += riff(bar * BAR + off, lo)
    path = tmp_path / "subbar.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    fams = _mine(path)
    cells = [m for m in fams if m.events == 2 and m.reps >= 8]
    assert cells
    assert any(abs(_spacing(m) - BAR // 2) < 10 for m in cells)


def test_off_bar_pickup_start(tmp_path):
    # motif always begins half a beat past the bar line (off-grid phase)
    notes = []
    for b in range(6):
        t0 = b * BAR + BAR // 2
        notes += [(t0, 120, 60, 90), (t0 + 480, 120, 64, 90),
                  (t0 + 240, 120, 62, 90)]
    path = tmp_path / "pickup.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    fams = _mine(path)
    assert any(abs((m.start_tick % BAR) - BAR // 2) < 10 and m.reps >= 2
               for m in fams)


def test_transposed_repeat_is_one_family(tmp_path):
    # the same 1-bar figure played in bars 0-3, transposed +7 in bars 4-7
    spec = [(0, 120, 60, 100), (240, 120, 62, 90), (480, 240, 65, 80)]
    notes = []
    for k in range(2):
        shift = 0 if k == 0 else 7
        for b in range(4):
            t0 = (k * 4 + b) * BAR
            for (off, d, p, v) in spec:
                notes.append((t0 + off, d, p + shift, v))
    path = tmp_path / "trans.mid"
    write_midi(path, parts=[{"name": "m", "channel": 2, "notes": notes}])
    fams = _mine(path)
    cell = [m for m in fams if m.events == 3 and m.reps >= 8]
    assert cell
    assert any(abs(h.shift) == 7 for h in cell[0].hits)


def test_ornament_does_not_kill_repeat(tmp_path):
    # identical 4-note phrase at bars 0-3 and 8-11 (with a differing tag after)
    def phrase(base):
        return [(base + b * BAR, BAR - 10, 60 + b, 90) for b in range(4)]

    notes = phrase(0) + phrase(8 * BAR)
    path = tmp_path / "orn.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    fams = _mine(path)
    phr = [m for m in fams if m.events == 4 and m.reps >= 2]
    assert phr
    # the second occurrence sits 8 bars later, so its period is 8 bars
    assert any(abs(_spacing(m) - 8 * BAR) < 10 for m in phr)


def test_timing_jitter_tolerated(tmp_path):
    # same riff twice, the second humanised by 5 ticks (~1/24th beat)
    def riff(base, jitter):
        return [(base + off + jitter, d, p, v)
                for (off, d, p, v) in [(0, 120, 60, 90), (240, 120, 64, 90),
                                       (480, 240, 67, 80), (960, 120, 55, 90)]]

    notes = riff(0, 0) + riff(4 * BAR, 5)
    path = tmp_path / "jitter.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    fams = _mine(path)
    riff_m = [m for m in fams if m.events == 4 and m.reps == 2]
    assert riff_m
    assert any(abs(_spacing(m) - 4 * BAR) < 20 for m in riff_m)


def test_low_ppq_quantization(tmp_path):
    # ppq = 96 (very coarse); a bar = 384 ticks with 8 hits per bar
    notes = []
    for b in range(8):
        t0 = b * 4 * 96
        for g in range(8):
            notes.append((t0 + g * 48, 10, 36 + (g % 3), 100))
    path = tmp_path / "low.mid"
    write_midi(path, parts=[{"name": "d", "channel": 9, "notes": notes}],
               ppq=96)
    fams = _mine(path)
    bar = [m for m in fams if m.events == 8]
    assert any(m.reps >= 8 and abs(_spacing(m) - 384) < 5 for m in bar)


def test_deterministic_and_capped(tmp_path):
    notes = []
    for b in range(12):
        t0 = b * BAR
        for g in range(8):
            notes.append((t0 + g * (BAR // 8), 15, 60 + (g * 3) % 12, 90))
    path = tmp_path / "det.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    a = _mine(path)
    b = _mine(path)
    assert [(m.start_tick, m.end_tick, m.events, m.reps) for m in a] == \
           [(m.start_tick, m.end_tick, m.events, m.reps) for m in b]
    assert len(a) <= MotifConfig().max_families


def test_bar_gap_loops_and_full_span(tmp_path):
    # "X rest(2 bars) X" twice: X is a silence-free loop (x4), while the
    # span that bridges the 2-bar rests is still kept as its own longer motif.
    from gms_ma.bars import BarMap
    from gms_ma.parse import parse_file

    def figure(bar):
        t0 = bar * BAR
        return [(t0, 100, 60, 90), (t0 + BAR // 4, 100, 64, 90),
                (t0 + BAR // 2, 100, 67, 90)]

    notes = []
    for b in (0, 3, 6, 9):
        notes += figure(b)                    # bars 1-2, 4-5, 7-8, 10-11 rest
    path = tmp_path / "gap.mid"
    write_midi(path, parts=[{"name": "m", "channel": 0, "notes": notes}])
    song = parse_file(path)
    bm = BarMap(song)
    fams = mine_channel(song.instruments[0], song.ticks_per_beat,
                        MotifConfig(), bm)
    # silence-free loops: the 3-event figure, repeated 4x (never bridging)
    x = [m for m in fams if m.events == 3]
    assert any(m.reps >= 4 for m in x), [(m.events, m.reps) for m in fams]
    # full span preserved: X + rest + X as a longer motif (2 occurrences)
    assert any(m.events == 6 and m.reps >= 2 for m in fams), \
        [(m.events, m.reps) for m in fams]
