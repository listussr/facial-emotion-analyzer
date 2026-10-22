"""Единый интерфейс трекируемого лица для разных трекеров."""

import numpy as np

from typing import Optional
from typing import Tuple


class TrackedFace:
    """Трекируемое лицо; рамка хранится в xyxy, как to_ltrb() у deep-sort-realtime."""
    __slots__ = ("track_id", "_xyxy", "hits", "time_since_update", "last_feature")

    def __init__(self, track_id: int, xyxy: np.ndarray, hits: int = 3,
                 time_since_update: int = 0, last_feature: Optional[np.ndarray] = None):
        self.track_id          = track_id
        self._xyxy             = xyxy
        self.hits              = hits
        self.time_since_update = time_since_update
        self.last_feature      = last_feature

    def is_confirmed(self) -> bool:
        return True

    def to_ltrb(self) -> Optional[Tuple[float, float, float, float]]:
        if self._xyxy is None:
            return None
        x1, y1, x2, y2 = self._xyxy
        if not np.isfinite([x1, y1, x2, y2]).all():
            return None
        if (x2 - x1) <= 0 or (y2 - y1) <= 0:
            return None
        return float(x1), float(y1), float(x2), float(y2)

    def to_xywh(self) -> Optional[Tuple[int, int, int, int]]:
        ltrb = self.to_ltrb()
        if ltrb is None:
            return None
        x1, y1, x2, y2 = ltrb
        return int(x1), int(y1), int(x2 - x1), int(y2 - y1)
