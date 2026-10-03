import logging
import sys
import time
from typing import Iterator, Optional

import cv2

from .base import MediaSource, SessionClock, SourceInfo, VideoFrame


class WebcamSource(MediaSource):
    def __init__(self, index: int, clock: Optional[SessionClock] = None):
        """
        Вебкамера через OpenCV.

        На Windows форсим DirectShow — дефолтный MSMF-бэкенд OpenCV на
        11-м поколении Intel часто долго инициализируется или не открывает
        камеру вовсе.

        Args:
            index (int): Индекс камеры.
            clock (SessionClock, optional): Общие часы сессии. Если не задан —
                создаётся свой.
        """
        self._index = index
        self._clock = clock or SessionClock()
        self._cap: Optional[cv2.VideoCapture] = None
        self._closed = False

    def open(self, want_video: bool = True, want_audio: bool = False) -> SourceInfo:
        if sys.platform == "win32":
            self._cap = cv2.VideoCapture(self._index, cv2.CAP_DSHOW)
        else:
            self._cap = cv2.VideoCapture(self._index)

        if not self._cap.isOpened():
            self._cap.release()
            self._cap = None
            raise RuntimeError(f"Failed to open webcam {self._index}")

        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 2)
        fps = float(self._cap.get(cv2.CAP_PROP_FPS) or 0) or None
        self._closed = False
        logging.info(f"WebcamSource opened camera {self._index} (fps={fps})")
        return SourceInfo(has_video=True, has_audio=False, is_live=True, native_fps=fps)

    def read(self) -> Iterator[VideoFrame]:
        if self._cap is None:
            raise RuntimeError("WebcamSource.read() called before open()")
        last_pts = -1
        while not self._closed:
            cap = self._cap
            if cap is None:
                return
            ret, image = cap.read()
            t = time.monotonic()
            if not ret:
                if self._closed:
                    return
                raise RuntimeError(f"Webcam {self._index} stopped delivering frames")
            pts_ms = self._clock.from_monotonic(t)
            if pts_ms <= last_pts:
                pts_ms = last_pts + 1
            last_pts = pts_ms
            yield VideoFrame(pts_ms=pts_ms, capture_wall_ts=self._clock.wall_ts(pts_ms), image=image)

    def close(self) -> None:
        self._closed = True
        cap, self._cap = self._cap, None
        if cap is not None:
            cap.release()
