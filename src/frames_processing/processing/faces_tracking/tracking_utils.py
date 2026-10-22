"""Создание трекеров и преобразование рамок между форматами."""

import numpy as np
import supervision as sv

from typing import Dict
from typing import List
from typing import Tuple

from deep_sort_realtime.deepsort_tracker import DeepSort


def initialize_deepsort(deep_sort_settings: Dict) -> DeepSort:
    """
    Создание DeepSort с основными параметрами.

    Args:
        deep_sort_settings (Dict): max_age, n_init, nn_budget, max_cosine_distance, эмбеддер.

    Returns:
        DeepSort: Трекер.
    """
    max_age             = deep_sort_settings.get("max_age", 70)
    n_init              = deep_sort_settings.get("n_init", 5)
    nn_budget           = deep_sort_settings.get("nn_budget", 100)
    max_cosine_distance = deep_sort_settings.get("max_cosine_distance", 0.3)
    embedder_gpu        = deep_sort_settings.get("embedder_gpu", False)
    embedder_model_name = deep_sort_settings.get("embedder_model_name", "mobilenet")
    return DeepSort(
        max_age=max_age,
        n_init=n_init,
        max_cosine_distance=max_cosine_distance,
        nn_budget=nn_budget,
        embedder=embedder_model_name,
        embedder_gpu=embedder_gpu,
    )


def initialize_bytetrack(bytetrack_settings: Dict) -> sv.ByteTrack:
    return sv.ByteTrack(
            track_activation_threshold=bytetrack_settings.get("track_activation_threshold", 0.25),
            lost_track_buffer=bytetrack_settings.get("lost_track_buffer", 30),
            minimum_matching_threshold=bytetrack_settings.get("minimum_matching_threshold", 0.8),
            frame_rate=bytetrack_settings.get("frame_rate", 20),
            minimum_consecutive_frames=bytetrack_settings.get("minimum_consecutive_frames", 3),
        )


def represent_ltrb(track) -> Tuple:
    """
    Рамка трека в целых (x, y, w, h).

    Args:
        track: Трек с методом to_ltrb().

    Returns:
        Tuple: (x, y, w, h) или None для вырожденной рамки.
    """
    ltrb = track.to_ltrb()
    if ltrb is None:
        return None

    if len(ltrb) < 4:
        return None

    x1, y1, x2, y2 = ltrb[:4]

    if not np.isfinite([x1, y1, x2, y2]).all():
        return None

    w = x2 - x1
    h = y2 - y1

    if w <= 0 or h <= 0:
        return None

    return int(x1), int(y1), int(w), int(h)


def represent_detections(detections: List[Tuple]) -> List[List]:
    """
    Детекции в формате DeepSort.

    Args:
        detections (List[Tuple]): Детекции (x, y, w, h, conf).

    Returns:
        List[List]: Элементы [[x, y, w, h], conf, "face"].
    """
    repr_detections = []

    for det in detections:
        if det is None:
            continue

        if not isinstance(det, (tuple, list)):
            continue

        if len(det) < 5:
            continue

        x, y, w, h, conf = det[:5]

        if w <= 0 or h <= 0:
            continue

        if conf <= 0.3:
            continue

        repr_detections.append([[int(x), int(y), int(w), int(h)], float(conf), "face"])
    return repr_detections
