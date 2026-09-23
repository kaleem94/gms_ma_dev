"""bar/beat/second arithmetic tests."""
from gms_ma.bars import BarMap
from gms_ma.model import Song, TempoEvent, TimeSigEvent
from tests.conftest import write_midi


def test_tempo_change_seconds(tmp_path):
    # bar 1 at 120bpm then speed doubles to 240bpm for the 2nd bar
    path = tmp_path / "tempo.mid"
    write_midi(path, parts=[{"name": "x", "channel": 0,
                             "notes": [(0, 1920, 60, 100), (1920, 1920, 62, 100)]}],
               ppq=480, tempo_us=500000,
               tempo_changes=[(1920, 250000)])
    from gms_ma.parse import parse_file

    song = parse_file(path)
    bm = BarMap(song)
    assert bm.num_bars >= 2
    assert bm.bar_start_tick(1) == 1920
    # bar 1 @ 120bpm (0.5s/beat * 4 beats) = 2s
    assert abs(bm.second_at_tick(1920) - 2.0) < 1e-6
    # bar 2 @ 240bpm => +1s
    assert abs(bm.second_at_tick(3840) - 3.0) < 1e-6
    # inverse mapping
    assert abs(bm.tick_at_second(2.5) - 2880) < 4
    assert bm.tempo_at(0) == 500000
    assert bm.tempo_at(2000) == 250000


def test_signature_change_bar_length():
    song = Song(name="sig", total_ticks=1920 + 1440, ticks_per_beat=480)
    song.tempos = [TempoEvent(0, 500000)]
    song.time_signatures = [TimeSigEvent(0, 4, 4), TimeSigEvent(tick=1920,
                                                               numerator=3,
                                                               denominator=4)]
    bm = BarMap(song)
    assert bm.num_bars == 2
    assert bm.bar_start_tick(1) == 1920
    assert bm.bar_length_ticks(1) == 1440  # 3/4 bar = 3 * 480


def test_tick_to_bar_clamps(tmp_path):
    song = Song(name="s", total_ticks=1920 * 2, ticks_per_beat=480)
    song.tempos = [TempoEvent(0, 500000)]
    song.time_signatures = [TimeSigEvent(0, 4, 4)]
    bm = BarMap(song)
    assert bm.num_bars == 2
    assert bm.tick_to_bar(0) == 0
    assert bm.tick_to_bar(1919) == 0
    assert bm.tick_to_bar(1920) == 1
    assert bm.tick_to_bar(1_000_000) == 1  # clamped


def test_small_denominator_signature_has_positive_bar_length():
    # 12/16 at ppq 384: a 16th = 96 ticks, so one bar = 12 * 96 = 1152 ticks.
    # (The old `4 // denominator` math made this 0 -> the bar builder spun to its
    # 200k guard and build_manifest hung for ~40s.)
    song = Song(name="bach", total_ticks=1152 * 3, ticks_per_beat=384)
    song.tempos = [TempoEvent(0, 689655)]
    song.time_signatures = [TimeSigEvent(0, 12, 16)]
    bm = BarMap(song)
    assert bm.bar_length_ticks(0) == 1152
    assert bm.num_bars == 3
    assert bm.bar_start_tick(1) == 1152
    # second_at_tick stays fast/correct (tempo map cached)
    assert abs(bm.second_at_tick(1152) - 3 * 689655 / 1_000_000) < 1e-6
