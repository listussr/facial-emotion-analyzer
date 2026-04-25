from typing import Dict, List, Tuple
import numpy as np
from deep_sort_realtime.deepsort_tracker import DeepSort
import supervision as sv

def initialize_deepsort(deep_sort_settings: Dict) -> DeepSort:
    """
    #### Инициализация DeepSort только с важными параметрами.
    
    :param deep_sort_settings: Параметры DeepSort в словаре.
    :type deep_sort_settings: Dict
    :return: Инициализированный класс.
    :rtype: DeepSort
    """
    max_age = deep_sort_settings.get("max_age", 70)
    n_init = deep_sort_settings.get("n_init", 5)
    nn_budget = deep_sort_settings.get("nn_budget", 100)
    max_cosine_distance = deep_sort_settings.get("max_cosine_distance", 0.3)
    embedder_gpu = deep_sort_settings.get("embedder_gpu", False)
    embedder_model_name = deep_sort_settings.get("embedder_model_name", 'mobilenet')
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
    #### Вырез координат лица из трекера.
    
    :param track: Трек лица.
    :return: Кортеж (координата x, координата y, ширина, высота)
    :rtype: Tuple
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
    Docstring для represent_detections
    
    :param detections: Список кортежей с детекциями лиц с кадра - [(x_left, y_left, width, height, conf),].
    :type detections: List[Tuple]
    :return: Список в форме, подходящей к передаче в DeepSort - [[x_left, y_left, width, height], conf, 'face'], ].
    :rtype: List[List]
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

        repr_detections.append([[int(x), int(y), int(w), int(h)], float(conf), 'face'])
    return repr_detections
