"""Детекторы моментов на синтетических рядах с заранее вшитыми эпизодами."""

import numpy as np
import pytest

from src.emotion_labels import EMOTION_LABELS
from src.emotion_moments import EmotionSeries
from src.emotion_moments import detect_moments
from src.emotion_moments import jsd
from src.emotion_moments import load_profile
from src.emotion_moments import session_overview
from src.emotion_moments.detectors import detect_abrupt_changes
from src.emotion_moments.detectors import detect_face_voice_divergence
from src.emotion_moments.detectors import detect_flat_affect
from src.emotion_moments.detectors import detect_sustained

ANG, DIS, FEA, HAP, NEU, SAD = range(6)


def dist(main: int, p: float = 0.8, rng=None) -> list:
    v = np.full(6, (1 - p) / 5)
    v[main] = p
    if rng is not None:
        v = np.clip(v + rng.normal(0, 0.02, 6), 0.001, None)
        v /= v.sum()
    return [round(float(x), 4) for x in v]


def series(fused, face=None, voice=None, speech=None, face_present=None, predicted=None) -> EmotionSeries:
    n = len(fused)
    return EmotionSeries.from_document({
        "camera_id": "test", "labels": list(EMOTION_LABELS), "step_s": 1.0, "t": list(range(n)),
        "fused": fused, "face": face or fused, "voice": voice or [None] * n,
        "speech": speech or [0.0] * n, "face_present": face_present or [1.0] * n,
        "predicted": predicted or [0.0] * n, "others": [0.0] * n,
    })


def test_jsd_bounds():
    assert jsd(np.array(dist(HAP)), np.array(dist(HAP))) == pytest.approx(0)
    assert jsd(np.array([1, 0, 0, 0, 0, 0.0]), np.array([0, 1, 0, 0, 0, 0.0])) == pytest.approx(1)


def test_sustained_sadness_with_hysteresis_and_short_gap():
    rows = [dist(NEU)] * 20 + [dist(SAD, 0.7)] * 6 + [dist(SAD, 0.55)] * 4 + [None, None] + [dist(SAD, 0.7)] * 5 \
        + [dist(NEU)] * 20
    (c,) = detect_sustained(series(rows), "sad", enter_p=0.6, exit_delta=0.1, min_duration_s=8, max_gap_s=2)
    assert c.type == "sustained_sadness" and c.start_s == 20 and c.end_s == 37
    assert c.metrics["mean_p"] == pytest.approx(0.66, abs=0.005) and c.confidence < 1


def test_sustained_ignores_short_and_breaks_on_long_gap():
    rows = [dist(ANG, 0.8)] * 5 + [None] * 5 + [dist(ANG, 0.8)] * 5
    assert detect_sustained(series(rows), "anger", min_duration_s=8, max_gap_s=2) == []


def test_abrupt_change_found_once_with_direction():
    rng  = np.random.default_rng(0)
    rows = [dist(NEU, 0.7, rng) for _ in range(30)] + [dist(ANG, 0.75, rng) for _ in range(30)]
    (c,) = detect_abrupt_changes(series(rows), window_s=3, min_jsd=0.3, min_new_p=0.5, suppress_s=5)
    assert c.metrics["from"] == "neutral" and c.metrics["to"] == "anger" and c.metrics["at_s"] == 30
    assert detect_abrupt_changes(series([dist(NEU, 0.7, rng) for _ in range(60)])) == []


def test_face_voice_divergence_only_during_speech():
    n      = 40
    face   = [dist(HAP)] * n
    voice  = [dist(HAP)] * 10 + [dist(SAD)] * 12 + [dist(HAP)] * 18
    speech = [1.0] * 30 + [0.0] * 10
    (c,) = detect_face_voice_divergence(series(face, face=face, voice=voice, speech=speech), min_duration_s=4)
    assert (c.start_s, c.end_s) == (10, 22) and c.metrics["face"] == "happy" and c.metrics["voice"] == "sad"
    assert detect_face_voice_divergence(series(face, face=face, voice=voice, speech=[0.0] * n)) == []


def test_flat_affect_vs_lively_face():
    rng   = np.random.default_rng(1)
    flat  = [dist(NEU, 0.85, rng) for _ in range(120)]
    (c,) = detect_flat_affect(series(flat), window_s=60, max_variability=0.03)
    assert (c.start_s, c.end_s) == (0, 120) and c.metrics["dominant"] == "neutral"
    lively = [dist(i % 6, 0.8) for i in range(120)]
    assert detect_flat_affect(series(lively), window_s=60) == []


def test_profile_merges_overlapping_and_ranks():
    profile = load_profile("interview")
    rng  = np.random.default_rng(2)
    rows = [dist(HAP, 0.6, rng) for _ in range(40)] + [dist(SAD, 0.8, rng) for _ in range(30)] \
        + [dist(HAP, 0.6, rng) for _ in range(40)]
    moments = detect_moments(series(rows), profile)
    top = moments[0]
    assert "sustained_sadness" in top.types and "abrupt_change" in top.types
    assert top.start_s <= 40 <= top.end_s and top.level in ("high", "medium") and top.id.startswith("m")
    assert all(a.significance >= b.significance for a, b in zip(moments, moments[1:]))


def test_low_quality_lowers_confidence():
    profile = load_profile("interview")
    rows    = [dist(NEU)] * 10 + [dist(ANG, 0.8)] * 20 + [dist(NEU)] * 10
    good    = detect_moments(series(rows), profile)
    poor    = detect_moments(series(rows, predicted=[0.9] * 40), profile)
    assert poor[0].significance < good[0].significance


def test_overview_and_quality_flags():
    rows = [dist(HAP)] * 10 + [None] * 10
    ov   = session_overview(series(rows, face_present=[1.0] * 10 + [0.0] * 10))
    assert ov["duration_s"] == 20 and ov["face_present"] == 0.5
    assert "low_face" in ov["quality"] and "no_voice" in ov["quality"]
    assert ov["modalities"]["fused"]["dominant"] == {"happy": 1.0} and "voice" not in ov["modalities"]
