import numpy as np
from typing import Tuple, Optional


class TrackedFace:
    """
    Единый интерфейс для трекируемого лица.

    Хранит координаты в формате `xyxy` (left, top, right, bottom),
    что соответствует выводу `to_ltrb()` у трекеров deep-sort-realtime
    и совместимо с `represent_ltrb` из пайплайна.
    """
    __slots__ = ('track_id', '_xyxy', 'hits', 'time_since_update', 'last_feature')

    def __init__(self, track_id: int, xyxy: np.ndarray, hits: int = 3,
                 time_since_update: int = 0, last_feature: Optional[np.ndarray] = None):
        """
        Args:
            track_id (int): Идентификатор трека.
            xyxy (np.ndarray): Границы трека на изображении [x1, y1, x2, y2].
            hits (int, optional): Число подтверждений трека.
            time_since_update (int, optional): Кадров без обновления.
            last_feature (np.ndarray, optional): Re-ID эмбеддинг (DeepSORT).
        """
        self.track_id = track_id
        self._xyxy = xyxy
        self.hits = hits
        self.time_since_update = time_since_update
        self.last_feature = last_feature

    def is_confirmed(self) -> bool:
        return True

    def to_ltrb(self) -> Optional[Tuple[float, float, float, float]]:
        """
        Координаты трека в формате (left, top, right, bottom).
        """
        if self._xyxy is None:
            return None
        x1, y1, x2, y2 = self._xyxy
        if not np.isfinite([x1, y1, x2, y2]).all():
            return None
        if (x2 - x1) <= 0 or (y2 - y1) <= 0:
            return None
        return float(x1), float(y1), float(x2), float(y2)

    def to_xywh(self) -> Optional[Tuple[int, int, int, int]]:
        """
        Координаты в формате (x, y, w, h).
        """
        ltrb = self.to_ltrb()
        if ltrb is None:
            return None
        x1, y1, x2, y2 = ltrb
        return int(x1), int(y1), int(x2 - x1), int(y2 - y1)
