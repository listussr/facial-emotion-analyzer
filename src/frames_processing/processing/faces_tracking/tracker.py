from abc import ABC, abstractmethod
from typing import Dict, List
import logging

import numpy as np
import supervision as sv

from .tracked_face import TrackedFace
from .tracking_utils import (
    represent_detections,
    initialize_deepsort,
    initialize_bytetrack,
)


class _Tracker(ABC):
    """
    Базовый интерфейс трекера лиц. Все конкретные реализации возвращают
    список `TrackedFace`, что позволяет пайплайну быть независимым от
    конкретного алгоритма.
    """

    @abstractmethod
    def update(self, detections: List, frame: np.ndarray) -> List[TrackedFace]:
        """
        Обновление треков по новым детекциям.

        Args:
            detections (List): Список [x, y, w, h, conf] от FaceDetector.
            frame (np.ndarray): Текущий кадр.

        Returns:
            List[TrackedFace]: Подтверждённые треки текущего кадра.
        """
        pass

    @abstractmethod
    def predict(self) -> List[TrackedFace]:
        """
        Шаг трекинга без новых детекций (только предсказание).
        """
        pass


class DeepSORTTracker(_Tracker):
    """
    Трекер на базе deep-sort-realtime. Поддерживает Re-ID эмбеддинги
    (`last_feature`), что используется идентификатором лиц.
    """

    def __init__(self, settings: Dict):
        self._tracker = initialize_deepsort(settings)

    @staticmethod
    def _wrap(t) -> TrackedFace:
        return TrackedFace(
            track_id=int(t.track_id),
            xyxy=np.array(t.to_ltrb(), dtype=np.float32),
            hits=getattr(t, "hits", 0),
            time_since_update=getattr(t, "time_since_update", 0),
            last_feature=getattr(t, "last_feature", None),
        )

    def update(self, detections: List, frame: np.ndarray) -> List[TrackedFace]:
        ds_detections = represent_detections(detections)
        raw_tracks = self._tracker.update_tracks(ds_detections, frame=frame)
        return [
            self._wrap(t) for t in raw_tracks
            if t.is_confirmed() and t.time_since_update == 0
        ]

    def predict(self) -> List[TrackedFace]:
        self._tracker.tracker.predict()
        return [
            self._wrap(t) for t in self._tracker.tracker.tracks
            if t.is_confirmed() and t.time_since_update == 0
        ]


class ByteTracker(_Tracker):
    """
    Трекер на базе supervision.ByteTrack. Не предоставляет Re-ID
    эмбеддингов: `last_feature` всегда None, идентификация в этом
    режиме опирается только на изображение лица.
    """

    def __init__(self, settings: Dict):
        self._tracker = initialize_bytetrack(settings)
        self._last_tracks: List[TrackedFace] = []

    def update(self, detections: List, frame: np.ndarray) -> List[TrackedFace]:
        if not detections:
            return self.predict()

        xyxy = np.array(
            [[x, y, x + w, y + h] for x, y, w, h, _ in detections],
            dtype=np.float32,
        )
        conf = np.array([c for *_, c in detections], dtype=np.float32)
        class_id = np.zeros(len(detections), dtype=int)

        sv_detections = sv.Detections(xyxy=xyxy, confidence=conf, class_id=class_id)
        sv_tracks = self._tracker.update_with_detections(sv_detections)

        self._last_tracks = [
            TrackedFace(track_id=int(tid), xyxy=np.asarray(box, dtype=np.float32))
            for box, tid in zip(sv_tracks.xyxy, sv_tracks.tracker_id)
            if tid is not None
        ]
        return self._last_tracks

    def predict(self) -> List[TrackedFace]:
        for t in self._last_tracks:
            t.time_since_update += 1
        return self._last_tracks


_TRACKER_REGISTRY = {
    "deepsort": DeepSORTTracker,
    "deep_sort": DeepSORTTracker,
    "bytetrack": ByteTracker,
    "byte_track": ByteTracker,
    "byte": ByteTracker,
}


def get_tracker(settings: Dict) -> _Tracker:
    """
    Фабрика трекеров. Тип трекера выбирается полем `type` в настройках,
    остальные ключи передаются конкретной реализации.

    Args:
        settings (Dict): Настройки. Ожидается поле `type`
            ("deepsort" | "bytetrack"). По умолчанию "deepsort".

    Returns:
        _Tracker: Инициализированный трекер.
    """
    tracker_type = str(settings.get("type", "deepsort")).lower()
    cls = _TRACKER_REGISTRY.get(tracker_type)
    if cls is None:
        raise ValueError(
            f"Unknown tracker type '{tracker_type}'. "
            f"Available: {sorted(set(_TRACKER_REGISTRY))}"
        )
    logging.info(f"Initializing tracker: {cls.__name__}")
    return cls(settings)
