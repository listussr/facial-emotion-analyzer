"""
Трекеры при детекции не на каждом кадре: между детекциями — предсказанные рамки.
"""

import numpy as np
import pytest

from src.frames_processing.processing.faces_tracking.tracker import ByteTracker

FRAME = np.zeros((480, 640, 3), np.uint8)
BT = {"track_activation_threshold": 0.25, "lost_track_buffer": 30, "minimum_matching_threshold": 0.8,
      "frame_rate": 20, "minimum_consecutive_frames": 1}


def det(x, y=100, w=80, h=100, conf=0.9):
    return [x, y, w, h, conf]


def test_bytetrack_extrapolates_between_detections():
    t = ByteTracker(BT)
    for frame_no in range(0, 8, 2):
        [tr] = t.update([det(100 + 10 * frame_no)], FRAME)
        assert tr.time_since_update == 0
        [pred] = t.predict()
        assert pred.track_id == tr.track_id and pred.time_since_update == 1
    x1 = pred.to_ltrb()[0]
    assert x1 == pytest.approx(170, abs=3)


def test_bytetrack_extrapolation_is_capped():
    t = ByteTracker(BT)
    t.update([det(100)], FRAME)
    t.update([det(120)], FRAME)
    for _ in range(10):
        [pred] = t.predict()
    assert pred.time_since_update == 10
    assert pred.to_ltrb()[0] == pytest.approx(120 + 20 * ByteTracker._MAX_EXTRAPOLATION, abs=3)


def test_bytetrack_empty_detection_hides_tracks():
    t = ByteTracker(BT)
    t.update([det(100)], FRAME)
    t.update([det(102)], FRAME)
    assert t.update([], FRAME) == []
    assert t.predict() == []
    [tr] = t.update([det(104)], FRAME)
    assert tr.time_since_update == 0


def test_deepsort_predict_keeps_tracks_from_last_detection():
    from src.frames_processing.processing.faces_tracking.tracker import DeepSORTTracker
    try:
        t = DeepSORTTracker({"max_age": 30, "n_init": 1, "nn_budget": 10, "max_cosine_distance": 0.2,
                             "embedder_model_name": "mobilenet"})
    except Exception as e:
        pytest.skip(f"DeepSORT unavailable: {e}")
    rng   = np.random.default_rng(0)
    frame = rng.integers(0, 255, size=(480, 640, 3), dtype=np.uint8)
    for i in range(3):
        tracks = t.update([det(100 + 4 * i)], frame)
    assert tracks
    pred = t.predict()
    assert [p.track_id for p in pred] == [tracks[0].track_id]
    assert pred[0].time_since_update == 1
    assert pred[0].to_ltrb()[0] > tracks[0].to_ltrb()[0]
