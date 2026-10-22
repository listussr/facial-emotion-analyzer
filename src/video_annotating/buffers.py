"""Буферы синхронизации кадров и аннотаций по frame_id."""

import logging
import threading
import time

from collections import OrderedDict
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple


class TTLBuffer(object):
    """Буфер элементов с временем жизни ttl секунд в порядке вставки."""

    def __init__(self, ttl: int, *, buffer_name: str = ""):
        self.buffer      = OrderedDict()
        self.ttl         = ttl
        self.buffer_name = buffer_name

    def cleanup(self):
        now  = time.time()
        keys = []
        for key, (ts, _) in self.buffer.items():
            if now - ts > self.ttl:
                keys.append(key)
            else:
                break

        for key in keys:
            del self.buffer[key]

    def insert(self, key: Any, value: Any):
        now              = time.time()
        self.buffer[key] = (now, value)
        self.buffer.move_to_end(key)

    def delete(self, key: Any):
        item = self.buffer.pop(key, None)
        if item is None:
            return None
        _, value = item
        return value

    def get(self, key: Any):
        item = self.buffer.get(key)
        if item is None:
            return None
        return item[1]

    def __contains__(self, key: Any):
        return key in self.buffer


class SyncBuffer(object):
    """Сопоставление кадров и аннотаций с одинаковым frame_id; пара выдаётся, как только есть обе половины."""

    def __init__(self, ttl_frames: int, ttl_annotations: int):
        self._frames_buffer     = TTLBuffer(ttl_frames, buffer_name="frames")
        self._annotation_buffer = TTLBuffer(ttl_annotations, buffer_name="annotations")
        self._lock              = threading.Lock()

    def cleanup(self):
        with self._lock:
            self._annotation_buffer.cleanup()
            self._frames_buffer.cleanup()

    def add_annotation(self, frame_id: Any, annotation: Dict) -> Optional[Tuple[Dict, Dict]]:
        """
        Добавление аннотации.

        Args:
            frame_id   (Any) : Идентификатор кадра.
            annotation (Dict): Аннотация кадра.

        Returns:
            Optional[Tuple[Dict, Dict]]: Пара (кадр, аннотация), если кадр уже есть.
        """
        with self._lock:
            self._annotation_buffer.insert(frame_id, annotation)

            if frame_id not in self._frames_buffer:
                return None

            annotation = self._annotation_buffer.delete(frame_id)
            frame      = self._frames_buffer.delete(frame_id)

            if annotation is None or frame is None:
                return None

            return frame, annotation

    def add_frame(self, frame_id: Any, frame: Dict) -> Optional[Tuple[Dict, Dict]]:
        """
        Добавление кадра.

        Args:
            frame_id (Any) : Идентификатор кадра.
            frame    (Dict): Кадр.

        Returns:
            Optional[Tuple[Dict, Dict]]: Пара (кадр, аннотация), если аннотация уже есть.
        """
        with self._lock:
            self._frames_buffer.insert(frame_id, frame)

            if frame_id not in self._annotation_buffer:
                return None

            annotation = self._annotation_buffer.delete(frame_id)
            frame      = self._frames_buffer.delete(frame_id)

            if annotation is None or frame is None:
                return None

            return frame, annotation
