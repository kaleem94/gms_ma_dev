"""feature & tag rule tests (deterministic heuristics)."""
from gms_ma.features import analyze_notes, energy_score
from gms_ma.tag import emotion_for, genre_for, gm_name, pattern_tags, tempo_class
from tests.conftest import BAR


def test_energy_and_mode_on_fixture():
    # bright, loud, dense: high energy
    notes = [(i * (BAR // 4), BAR // 4, 60, 127) for i in range(16)]
    m = analyze_notes(notes, BAR * 2, 2, pitched=True)
    assert energy_score(m) > 0.6
    # key detection on a pure C-major triad held across four bars
    notes2 = []
    for b in range(4):
        for pc in (60, 64, 67):
            notes2.append((b * BAR, BAR - 10, pc, 100))
    m2 = analyze_notes(notes2, BAR * 4, 4, pitched=True)
    assert m2.key_tonic == 0 and m2.key_mode == "major"  # C major


def test_style_tags_present_for_drums_and_melody():
    drum_notes = [(b * BAR, 20, 36, 110) for b in range(4)] + \
                 [(b * BAR + BAR // 2, 20, 38, 100) for b in range(4)]
    m = analyze_notes(drum_notes, BAR * 4, 4, pitched=False)
    tags = pattern_tags(m, is_drums=True)
    kinds = {t["kind"] for t in tags}
    assert "energy" in kinds and "style" in kinds
    assert all(t["confidence"] <= 1.0 for t in tags)


def test_genre_dance_fixture():
    # synth bass + saw lead + drums at 128 bpm -> dance/electronic
    from collections import Counter

    progs = Counter({81: 1, 38: 1})
    g, conf = genre_for(progs, has_drums=True, bpm=128)
    assert g == "dance-electronic"
    assert 0.3 <= conf <= 0.85


def test_emotion_mapping():
    v, a, label = emotion_for(energy=0.8, bpm=130, is_major=True, pitch_centroid=72)
    assert v > 0.5 and a > 0.5 and label == "upbeat-energetic"
    v2, a2, label2 = emotion_for(energy=0.2, bpm=70, is_major=False, pitch_centroid=52)
    assert label2 == "melancholic"


def test_tempo_class_and_gm_names():
    assert tempo_class(30) == "very slow"
    assert tempo_class(60) == "slow"
    assert tempo_class(90) == "moderate"
    assert tempo_class(128) == "fast"
    assert tempo_class(200) == "very fast"
    assert gm_name(0) == "Acoustic Grand"
    assert gm_name(81) == "Saw Lead"
