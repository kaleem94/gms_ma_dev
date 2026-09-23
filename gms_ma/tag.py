"""Rule-based tagging: style-of-play (patterns) and genre/emotion (songs).

The rules implement a single ``tag_for_*`` seam so a future pretrained-model
scorer can be swapped in without touching DB / CLI / viewer (DIP / OCP).
Tags are *heuristic* — coarse on purpose, deterministic, and overridable by
manual tags stored with ``source='manual'``.
"""
from __future__ import annotations

from collections import Counter

GM_NAMES = [
    "Acoustic Grand", "Bright Acoustic", "Electric Grand", "Honky-Tonk", "Electric Piano 1",
    "Electric Piano 2", "Harpsichord", "Clavi", "Celesta", "Glockenspiel", "Music Box",
    "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer", "Drawbar Organ",
    "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion", "Harmonica",
    "Tango Accordion", "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Guitar",
    "Muted Guitar", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics",
    "Acoustic Bass", "Fingered Bass", "Picked Bass", "Fretless Bass", "Slap Bass 1",
    "Slap Bass 2", "Synth Bass 1", "Synth Bass 2", "Violin", "Viola", "Cello",
    "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2",
    "Choir Aahs", "Voice Oohs", "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone",
    "Tuba", "Muted Trumpet", "French Horn", "Brass Section", "Synth Brass 1",
    "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe",
    "English Horn", "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute",
    "Blown Bottle", "Shakuhachi", "Whistle", "Ocarina", "Square Lead", "Saw Lead",
    "Calliope Lead", "Chiff Lead", "Charang", "Voice Lead", "Fifths Lead", "Bass Lead",
    "New Age Pad", "Warm Pad", "Polysynth Pad", "Choir Pad", "Bowed Pad", "Metallic Pad",
    "Halo Pad", "Sweep Pad", "Rain", "Soundtrack", "Crystal", "Atmosphere",
    "Brightness", "Goblins", "Echoes", "Sci-Fi", "Sitar", "Banjo", "Shamisen", "Koto",
    "Kalimba", "Bagpipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo", "Steel Drums",
    "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal",
    "Guitar Fret Noise", "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring",
    "Helicopter", "Applause", "Gunshot",
]


def gm_name(program: int | None) -> str:
    if program is None:
        return "n/a"
    return GM_NAMES[program % 128]


_FAMILY: dict[tuple[int, int], str] = {
    (0, 7): "keys", (8, 15): "chromatic", (16, 23): "organ", (24, 31): "guitar",
    (32, 39): "bass", (40, 47): "strings", (48, 55): "ensemble", (56, 63): "brass",
    (64, 71): "reed", (72, 79): "pipe", (80, 87): "synthlead", (88, 95): "synthpad",
    (96, 103): "synthfx", (104, 111): "ethnic", (112, 119): "percussive", (120, 127): "sfx",
}


def family(program: int) -> str:
    for (lo, hi), name in _FAMILY.items():
        if lo <= program <= hi:
            return name
    return "sfx"


# Same ranges as :func:`family`, as an ordered tuple for SQL CASE generation.
FAMILY_RANGES = tuple((lo, hi, name) for (lo, hi), name in _FAMILY.items())


# --------------------------------------------------------------------------
# pattern (style of play) tags
# --------------------------------------------------------------------------
def pattern_tags(metrics, is_drums: bool) -> list[dict]:
    """Return a list of {kind, value, confidence} heuristic tags for a window."""
    out: list[dict] = []
    if metrics.is_pitched and not is_drums:
        from .music_theory import key_name

        out.append({"kind": "key", "value": key_name(metrics.key_tonic, metrics.key_mode),
                    "confidence": round(0.5 + 0.5 * abs(metrics.key_corr), 2)})
        out.append({"kind": "mode", "value": metrics.key_mode,
                    "confidence": round(0.5 + 0.5 * abs(metrics.key_corr), 2)})
        if getattr(metrics, "top_chord", ""):
            out.append({"kind": "chord", "value": metrics.top_chord, "confidence": 0.6})
        if getattr(metrics, "progression", ""):
            out.append({"kind": "harmony", "value": metrics.progression,
                        "confidence": 0.6})

    e = _energy_label(metrics)
    out.append({"kind": "energy", "value": e, "confidence": _energy_conf(metrics)})

    styles = _style(metrics, is_drums)
    for st, conf in styles:
        out.append({"kind": "style", "value": st, "confidence": conf})
    return out


def _energy_label(metrics) -> str:
    from .features import energy_score

    s = energy_score(metrics)
    return "high" if s > 0.62 else ("mid" if s > 0.32 else "low")


def _energy_conf(metrics) -> float:
    from .features import energy_score

    s = energy_score(metrics)
    return round(0.5 + 0.5 * abs(s - 0.5) * 2, 2) if s else 0.4


def _style(metrics, is_drums: bool) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    opb = metrics.onsets_per_bar
    sync = metrics.syncopation
    poly = metrics.mean_polyphony
    leg = metrics.legato_ratio
    stac = metrics.staccato_ratio
    occ = metrics.occupancy

    if is_drums or metrics.pitch_range <= 12:
        if opb >= 6 and sync < 0.3:
            out.append(("steady-pulse", 0.7))
        elif sync >= 0.35:
            out.append(("syncopated", 0.7))
        elif opb >= 3:
            out.append(("groove", 0.6))
        if stac >= 0.5 and opb >= 4:
            out.append(("staccato", 0.6))
        if opb < 2:
            out.append(("sparse", 0.6))
    else:
        if leg >= 0.65 and occ >= 0.6 and poly >= 2:
            out.append(("legato-pad", 0.7))
        elif leg >= 0.6:
            out.append(("legato", 0.6))
        if stac >= 0.45 and opb >= 3:
            out.append(("staccato", 0.6))
        if sync >= 0.35:
            out.append(("syncopated", 0.6))
        if poly >= 2.5:
            out.append(("chordal", 0.6))
        if opb >= 6 and metrics.pitch_spread <= 7 and 0.4 <= leg < 0.8:
            out.append(("arpeggiated", 0.6))
        if opb < 2:
            out.append(("sparse", 0.6))
    if not out:
        out.append(("textural", 0.4))
    return out[:3]


# --------------------------------------------------------------------------
# song level: tempo class, genre, emotion
# --------------------------------------------------------------------------
def tempo_class(bpm: float) -> str:
    if bpm < 60:
        return "very slow"
    if bpm < 90:
        return "slow"
    if bpm <= 125:
        return "moderate"
    if bpm <= 150:
        return "fast"
    return "very fast"


def genre_scores(program_counts: Counter, has_drums: bool, bpm: float) -> dict:
    fam = Counter()
    for prog, cnt in program_counts.items():
        fam[family(prog)] += cnt
    synth_bass = sum(c for p, c in program_counts.items() if p in (38, 39))
    s: dict[str, float] = {k: 0.0 for k in
                           ("dance-electronic", "epic-orchestral", "pop-rock",
                            "ballad", "jazz-swing", "classical", "world")}
    w = {
        "keys":      {"ballad": 0.6, "classical": 0.4, "jazz-swing": 0.4, "pop-rock": 0.2},
        "chromatic": {"ballad": 0.2, "classical": 0.2, "pop-rock": 0.4},
        "organ":     {"jazz-swing": 0.7, "pop-rock": 0.4},
        "guitar":    {"pop-rock": 1.0, "ballad": 0.4, "jazz-swing": 0.3},
        "bass":      {"pop-rock": 0.4, "ballad": 0.3, "jazz-swing": 0.3, "dance-electronic": 0.3},
        "strings":   {"epic-orchestral": 1.1, "ballad": 0.6, "classical": 0.5},
        "ensemble":  {"epic-orchestral": 1.2, "classical": 0.3, "ballad": 0.3},
        "choir":     {"epic-orchestral": 1.6, "ballad": 0.4},
        "brass":     {"epic-orchestral": 0.8, "jazz-swing": 0.5, "classical": 0.3, "pop-rock": 0.2},
        "reed":      {"jazz-swing": 0.7, "classical": 0.4, "ballad": 0.3},
        "pipe":      {"epic-orchestral": 1.0, "classical": 0.4},
        "synthlead": {"dance-electronic": 1.6, "pop-rock": 0.4},
        "synthpad":  {"dance-electronic": 1.3, "epic-orchestral": 0.4},
        "synthfx":   {"dance-electronic": 0.8},
        "ethnic":    {"world": 1.8, "epic-orchestral": 0.4},
        "percussive": {"world": 0.4, "jazz-swing": 0.2},
        "sfx":       {},
    }
    for f, cnt in fam.items():
        for cat, score in w.get(f, {}).items():
            s[cat] += score * cnt
    # instrument-agnostic / tempo modifiers
    if synth_bass:
        s["dance-electronic"] += 1.2 * synth_bass
    if has_drums:
        s["dance-electronic"] += 0.6
        s["pop-rock"] += 0.5
    if bpm >= 120:
        s["dance-electronic"] += 0.7
    if bpm >= 100 and bpm <= 140:
        s["pop-rock"] += 0.4
    if bpm <= 92:
        s["ballad"] += 0.7
        s["jazz-swing"] += 0.3
    if not has_drums and sum(program_counts.values()) > 0:
        s["classical"] += 0.7
    return s


def genre_for(program_counts: Counter, has_drums: bool, bpm: float) -> tuple[str, float]:
    if not program_counts and not has_drums:
        return "other", 0.3
    s = genre_scores(program_counts, has_drums, bpm)
    ranked = sorted(s.items(), key=lambda kv: -kv[1])
    best, second = ranked[0], ranked[1]
    total = max(1e-6, sum(v for _, v in ranked))
    margin = (best[1] - second[1]) / total
    conf = min(0.85, max(0.3, 0.4 + margin * 1.2))
    return best[0], round(conf, 2)


def emotion_for(energy: float, bpm: float, is_major: bool, pitch_centroid: float) -> tuple[float, float, str]:
    """Return (valence 0..1, arousal 0..1, discrete label).  A heuristic 2-D model."""
    arousal = max(0.0, min(1.0, 0.3 + (bpm - 90) / 140.0 * 0.4 + energy * 0.3))
    valence = 0.5
    valence += 0.22 if is_major else -0.22
    valence += (pitch_centroid - 64) / 128.0 * 0.15
    valence += (energy - 0.5) * 0.1
    valence = max(0.0, min(1.0, valence))
    if valence >= 0.5 and arousal >= 0.5:
        label = "upbeat-energetic"
    elif valence >= 0.5:
        label = "calm-positive"
    elif arousal >= 0.5:
        label = "tense-dramatic"
    else:
        label = "melancholic"
    return round(valence, 3), round(arousal, 3), label
