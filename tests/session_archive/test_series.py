"""Секундный ряд субъекта сессии."""

import pytest

from src.session_archive import build_series
from src.session_archive import build_series_from_document
from src.session_archive.series import choose_subject

SAD   = [0, 0, 0, 0, 0.2, 0.8]
HAPPY = [0, 0, 0, 0.9, 0.1, 0]


def face(track, scores, face_id=None, voice=None, predicted=False):
    return {"track_id": track, "face_id": face_id, "emotion_scores": scores, "face_scores": scores,
            "voice_scores": voice, "predicted": predicted}


def test_subject_merges_reentry_track_and_ignores_overlapping_people():
    frames = [(t, [face(1, SAD)]) for t in range(0, 1000, 100)]
    frames += [(t, [face(2, SAD), face(3, HAPPY)]) for t in range(2000, 3000, 100)]
    frames += [(t, [face(3, HAPPY)]) for t in range(3000, 3500, 100)]
    subject = choose_subject(frames)
    assert subject[0] == "track:3" and "track:1" in subject and "track:2" not in subject


def test_series_averages_seconds_and_marks_quality():
    timeline = []
    for t in range(0, 2000, 250):
        timeline.append({"t_ms": t, "faces": [face(1, SAD, predicted=t % 500 == 0, voice=SAD)],
                         "voice": {"label": "sad", "scores": SAD, "speaking": t < 1000}})
    timeline.append({"t_ms": 2500, "faces": [], "voice": None})
    doc = build_series_from_document({"camera_id": "cam", "timeline": timeline})

    assert doc["t"] == [0.0, 1.0, 2.0] and doc["duration_s"] == 3.0 and doc["labels"][5] == "sad"
    assert doc["fused"][0] == pytest.approx(SAD) and doc["fused"][2] is None and doc["voice"][1] == pytest.approx(SAD)
    assert doc["face_present"] == [1.0, 1.0, 0.0] and doc["predicted"] == [0.5, 0.5, 0.0]
    assert doc["speech"] == [1.0, 0.0, 0.0] and doc["subject"]["tracks"] == [1]


def test_series_from_absolute_pts_and_identified_person():
    records = [{"pts_ms": 5000 + t, "faces": [face(7 if t < 500 else 8, HAPPY, face_id="u1")]} for t in range(0, 1000, 100)]
    doc = build_series(records)
    assert doc["t"] == [0.0] and doc["subject"]["people"] == ["face:u1"] and doc["subject"]["tracks"] == [7, 8]
    assert doc["speech"] == [0.0] and build_series([])["t"] == []


def test_pending_id_belongs_to_track_and_other_people_are_not_merged():
    frames = [(t, [face(1, SAD, face_id="pending-ab" if t < 500 else "u1")]) for t in range(0, 1000, 100)]
    frames += [(t, [face(2, HAPPY, face_id="u2")]) for t in range(1000, 1500, 100)]
    frames += [(t, [face(3, SAD)]) for t in range(1500, 1800, 100)]
    assert choose_subject(frames) == ["face:u1", "track:3"]
    doc = build_series([{"t_ms": t, "faces": faces} for t, faces in frames])
    assert doc["face_present"] == [1.0, 0.375] and doc["others"] == [0.0, 0.625] and doc["fused"][1] is not None
