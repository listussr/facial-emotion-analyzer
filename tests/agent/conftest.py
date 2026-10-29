"""Общие данные тестов агента: синтетическая сессия с устойчивой грустью и гневом."""

import numpy as np
import pytest

from src.emotion_labels import EMOTION_LABELS


def _dist(main: int, p: float, rng) -> list:
    v = np.full(6, (1 - p) / 5)
    v[main] = p
    v = np.clip(v + rng.normal(0, 0.02, 6), 0.001, None)
    return [round(float(x), 4) for x in v / v.sum()]


def make_series_doc() -> dict:
    rng   = np.random.default_rng(3)
    fused = [_dist(4, 0.6, rng) for _ in range(60)] + [_dist(5, 0.8, rng) for _ in range(40)] \
        + [_dist(4, 0.6, rng) for _ in range(40)] + [_dist(0, 0.8, rng) for _ in range(20)] \
        + [_dist(4, 0.6, rng) for _ in range(40)]
    n = len(fused)
    return {"camera_id": "upload_ab12cd", "labels": list(EMOTION_LABELS), "step_s": 1.0, "t": list(range(n)),
            "fused": fused, "face": fused, "voice": [None] * n, "speech": [0.3] * n, "face_present": [1.0] * n,
            "predicted": [0.5] * n, "others": [0.0] * n, "subject": {"people": ["face:u1"], "tracks": [1]}}


@pytest.fixture
def series_doc() -> dict:
    return make_series_doc()
