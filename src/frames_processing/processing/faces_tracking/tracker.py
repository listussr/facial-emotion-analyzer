"""Трекеры лиц DeepSORT и ByteTrack с единым интерфейсом и прогнозом между детекциями."""

import logging
import numpy as np
import supervision as sv

from abc import ABC
from abc import abstractmethod
from typing import Dict
from typing import List

from .tracked_face import TrackedFace
from .tracking_utils import represent_detections
from .tracking_utils import initialize_deepsort
from .tracking_utils import initialize_bytetrack


class _Tracker(ABC):
    """Базовый интерфейс трекера: реализации возвращают TrackedFace, пайплайн не зависит от алгоритма."""

    @abstractmethod
    def update(self, detections: List, frame: np.ndarray) -> List[TrackedFace]:
        """
        Обновление треков по детекциям кадра.

        Args:
            detections (List)      : Детекции [x, y, w, h, conf].
            frame      (np.ndarray): Текущий кадр.

        Returns:
            List[TrackedFace]: Треки, сопоставленные с детекциями кадра.
        """
        pass

    @abstractmethod
    def predict(self) -> List[TrackedFace]:
        """
        Шаг без детекций, когда детектор работает не на каждом кадре.

        Returns:
            List[TrackedFace]: Треки последней детекции с предсказанной рамкой; time_since_update — кадров с неё.
        """
        pass


class DeepSORTTracker(_Tracker):
    """Трекер deep-sort-realtime с Re-ID эмбеддингами для идентификатора; прогноз — фильтром Калмана."""

    def __init__(self, settings: Dict):
        self._tracker         = initialize_deepsort(settings)
        self._since_detection = 0

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
        ds_detections         = represent_detections(detections)
        raw_tracks            = self._tracker.update_tracks(ds_detections, frame=frame)
        self._since_detection = 0
        return [
            self._wrap(t) for t in raw_tracks
            if t.is_confirmed() and t.time_since_update == 0
        ]

    def predict(self) -> List[TrackedFace]:
        self._tracker.tracker.predict()
        self._since_detection += 1
        return [
            self._wrap(t) for t in self._tracker.tracker.tracks
            if t.is_confirmed() and t.time_since_update <= self._since_detection
        ]


class ByteTracker(_Tracker):
    """
    Трекер supervision.ByteTrack без Re-ID эмбеддингов.

    supervision не даёт шага предсказания, поэтому между детекциями рамка экстраполируется линейно
    по двум последним детекциям трека, не дальше _MAX_EXTRAPOLATION кадров.
    """
    _MAX_EXTRAPOLATION = 3

    def __init__(self, settings: Dict):
        self._tracker = initialize_bytetrack(settings)
        self._last_tracks: List[TrackedFace] = []
        self._frame = 0
        self._history: Dict[int, List] = {}

    def update(self, detections: List, frame: np.ndarray) -> List[TrackedFace]:
        self._frame += 1
        if detections:
            xyxy = np.array(
                [[x, y, x + w, y + h] for x, y, w, h, _ in detections],
                dtype=np.float32,
            )
            conf = np.array([c for *_, c in detections], dtype=np.float32)
            sv_detections = sv.Detections(xyxy=xyxy, confidence=conf,
                                          class_id=np.zeros(len(detections), dtype=int))
        else:
            sv_detections = sv.Detections.empty()
        sv_tracks = self._tracker.update_with_detections(sv_detections)

        self._last_tracks = [
            TrackedFace(track_id=int(tid), xyxy=np.asarray(box, dtype=np.float32))
            for box, tid in zip(sv_tracks.xyxy, sv_tracks.tracker_id)
            if tid is not None
        ]
        alive = set()
        for t in self._last_tracks:
            hist = self._history.setdefault(t.track_id, [])
            hist.append((self._frame, t._xyxy.copy()))
            del hist[:-2]
            alive.add(t.track_id)
        for tid in [k for k in self._history if k not in alive]:
            del self._history[tid]
        return self._last_tracks

    def predict(self) -> List[TrackedFace]:
        self._frame += 1
        out = []
        for t in self._last_tracks:
            hist                 = self._history.get(t.track_id, [])
            last_frame, last_box = hist[-1] if hist else (self._frame - 1, t._xyxy)
            box                  = last_box
            if len(hist) == 2:
                (f0, b0), (f1, b1) = hist
                if f1 > f0:
                    steps = min(self._frame - f1, self._MAX_EXTRAPOLATION)
                    box   = b1 + (b1 - b0) / (f1 - f0) * steps
            out.append(TrackedFace(track_id=t.track_id, xyxy=np.asarray(box, dtype=np.float32),
                                   hits=t.hits, time_since_update=self._frame - last_frame))
        return out


_TRACKER_REGISTRY = {
    "deepsort": DeepSORTTracker,
    "deep_sort": DeepSORTTracker,
    "bytetrack": ByteTracker,
    "byte_track": ByteTracker,
    "byte": ByteTracker,
}


def get_tracker(settings: Dict) -> _Tracker:
    """
    Фабрика трекеров.

    Args:
        settings (Dict): Поле type — "deepsort" или "bytetrack", остальное передаётся трекеру.

    Raises:
        ValueError: Неизвестный тип трекера.

    Returns:
        _Tracker: Трекер.
    """
    tracker_type = str(settings.get("type", "deepsort")).lower()
    cls          = _TRACKER_REGISTRY.get(tracker_type)
    if cls is None:
        raise ValueError(
            f"Unknown tracker type '{tracker_type}'. "
            f"Available: {sorted(set(_TRACKER_REGISTRY))}"
        )
    logging.info(f"Initializing tracker: {cls.__name__}")
    return cls(settings)
