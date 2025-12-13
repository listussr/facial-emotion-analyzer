# sync_buffer.py
from collections import OrderedDict
import time
from typing import Optional, Tuple, Any
import numpy as np

class SyncBuffer:
    def __init__(self, ttl_seconds: float = 3.0, max_size: int = 200):
        """
        Буфер для синхронизации кадров и аналитики по timestamp.

        Args:
            ttl_seconds: Время жизни записи в секундах.
            max_size: Максимальное количество записей в буфере.
        """
        self.ttl = ttl_seconds
        self.max_size = max_size
        self.frames = OrderedDict()
        self.analytics = OrderedDict()

    def _cleanup(self, buffer_dict: OrderedDict):
        """Удаляет устаревшие записи (старше TTL)."""
        now = time.time()
        keys_to_remove = []
        for ts, (_, insert_time) in buffer_dict.items():
            if now - insert_time > self.ttl:
                keys_to_remove.append(ts)
            else:
                break
        for ts in keys_to_remove:
            del buffer_dict[ts]

    def add_frame(self, timestamp_ns: int, frame: np.ndarray) -> Optional[Tuple[np.ndarray, dict]]:
        """
        Добавляет кадр и пытается найти пару в буфере аналитики.

        Returns:
            (frame, analytics) если пара найдена, иначе None.
        """
        self._cleanup(self.frames)
        if len(self.frames) >= self.max_size:
            self.frames.popitem(last=False)
        self.frames[timestamp_ns] = (frame, time.time())

        if timestamp_ns in self.analytics:
            analytics, _ = self.analytics.pop(timestamp_ns)
            del self.frames[timestamp_ns]
            return frame, analytics
        return None

    def add_analytics(self, timestamp_ns: int, analytics: dict) -> Optional[Tuple[np.ndarray, dict]]:
        """
        Добавляет аналитику и пытается найти пару в буфере кадров.

        Returns:
            (frame, analytics) если пара найдена, иначе None.
        """
        self._cleanup(self.analytics)
        if len(self.analytics) >= self.max_size:
            self.analytics.popitem(last=False)
        self.analytics[timestamp_ns] = (analytics, time.time())

        if timestamp_ns in self.frames:
            frame, _ = self.frames.pop(timestamp_ns)
            del self.analytics[timestamp_ns]
            return frame, analytics
        return None
