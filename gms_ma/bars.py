"""Bar / beat / second arithmetic for a parsed Song.

Pure functions of the neutral model (no I/O), so this is easily unit tested.
"""
from __future__ import annotations

from .model import Song, TimeSigEvent


def _bar_ticks(ticks_per_beat: int, ts: TimeSigEvent) -> int:
    """Ticks in one bar of the given signature (quarter = ticks_per_beat).

    A ``1/denominator`` note is ``ticks_per_beat * 4 / denominator`` ticks; the
    old integer ``4 // denominator`` yielded 0 for denominators > 4 (e.g. 12/16),
    which produced zero-length bars and an effectively hung viewer.
    """
    beat_ticks = max(1, round(ticks_per_beat * 4 / max(1, ts.denominator)))
    return max(1, ts.numerator * beat_ticks)


class BarMap:
    """A lazily-evaluated list of bars covering the whole song."""

    def __init__(self, song: Song):
        self.song = song
        self._bars: list[tuple[int, int]] | None = None  # (start_tick, ticks_in_bar)
        self._segs: list[tuple[int, int, float]] | None = None

    # -- bar geometry -------------------------------------------------------
    @property
    def bars(self) -> list[tuple[int, int]]:
        if self._bars is None:
            self._bars = self._build()
        return self._bars

    def _build(self) -> list[tuple[int, int]]:
        song = self.song
        ppq = song.ticks_per_beat
        events = sorted(song.time_signatures, key=lambda e: e.tick)
        pointer = 0
        ts = song.initial_time_signature
        result: list[tuple[int, int]] = []
        cursor = 0
        total = max(song.total_ticks, 1)
        guard = 0
        while cursor < total and guard < 200000:
            guard += 1
            # adopt the signature in force from this tick onward
            while pointer < len(events) and events[pointer].tick <= cursor:
                ts = events[pointer]
                pointer += 1
            bar_len = _bar_ticks(ppq, ts)
            next_change = events[pointer].tick if pointer < len(events) else None
            if next_change is not None and next_change > cursor and cursor + bar_len > next_change:
                # signature changes mid-bar: close the bar at the change
                result.append((cursor, next_change - cursor))
                cursor = next_change
                continue
            result.append((cursor, bar_len))
            cursor += bar_len
        return result

    @property
    def num_bars(self) -> int:
        return len(self.bars)

    def bar_start_tick(self, index: int) -> int:
        return self.bars[index][0]

    def bar_length_ticks(self, index: int) -> int:
        return self.bars[index][1]

    def tick_to_bar(self, tick: int) -> int:
        """Bar index containing ``tick`` (clamped to the final bar)."""
        lo, hi = 0, max(0, len(self.bars) - 1)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.bars[mid][0] <= tick:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def span_bars(self, start_tick: int, end_tick: int) -> int:
        """Number of bars touched by [start_tick, end_tick)."""
        return self.tick_to_bar(max(end_tick - 1, 0)) - self.tick_to_bar(start_tick) + 1

    # -- tempo / seconds ------------------------------------------------------
    def _tempo_segments(self):
        """[(tick_from, tick_to, seconds_per_tick)] over the whole song (cached)."""
        if self._segs is not None:
            return self._segs
        song = self.song
        ppq = song.ticks_per_beat
        points = sorted(song.tempos, key=lambda e: e.tick)
        segs = []
        cursor = 0
        for i, ev in enumerate(points):
            if ev.tick < cursor:
                continue
            upto = points[i + 1].tick if i + 1 < len(points) else song.total_ticks
            if upto <= cursor:
                continue
            spt = ev.us_per_beat / (ppq * 1_000_000.0)
            segs.append((cursor, upto, spt))
            cursor = upto
        if not segs:
            spt = song.initial_tempo / (ppq * 1_000_000.0)
            segs = [(0, song.total_ticks, spt)]
        self._segs = segs
        return segs

    def tempo_at(self, tick: int) -> int:
        song = self.song
        result = song.initial_tempo
        for ev in song.tempos:
            if ev.tick <= tick:
                result = ev.us_per_beat
            else:
                break
        return result

    def second_at_tick(self, tick: int) -> float:
        total = 0.0
        for frm, upto, spt in self._tempo_segments():
            if tick <= frm:
                break
            if tick >= upto:
                total += (upto - frm) * spt
            else:
                total += (tick - frm) * spt
                break
        return total

    def tick_at_second(self, sec: float) -> int:
        acc = 0.0
        for frm, upto, spt in self._tempo_segments():
            seg_len = (upto - frm) * spt
            if acc + seg_len >= sec or seg_len <= 0:
                return frm + int((sec - acc) / spt) if spt > 0 else frm
            acc += seg_len
        return self.song.total_ticks


def grid_unit_ticks(ppq: int, divisions: int = 4) -> int:
    """Ticks per grid unit (default: a 16th note when divisions == 4)."""
    return max(1, round(ppq / divisions))
