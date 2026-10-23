"""Камера и микрофон браузера: кадры JPEG и PCM приходят по WebSocket веб-сервера."""

import logging
import queue
import threading
import time
import cv2
import numpy as np

from typing import Dict
from typing import Iterator
from typing import Optional
from typing import Union

from ..audio_chunker import AudioChunker
from .base import AudioChunk
from .base import MediaSource
from .base import SourceInfo
from .base import VideoFrame

BROWSER_PREFIX = "browser:"

_END       = object()
_registry: Dict[str, "BrowserSource"] = {}
_lock      = threading.Lock()


def _decode_jpeg(data: bytes) -> np.ndarray:
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Bad JPEG frame from browser")
    return image


class BrowserSource(MediaSource):
    """
    Источник, который наполняет веб-сервер: push_video и push_audio вызываются из обработчика WebSocket.

    Время берётся из браузера (pts от начала захвата на его часах), к часам сервера привязывается
    по первому сообщению. Кадры при переполнении очереди выбрасываются, звук — нет, пока очередь
    не забита совсем. Повторное открытие после закрытия невозможно: сессия браузера одноразовая.
    """

    def __init__(self, camera_id: str, has_video: bool, has_audio: bool, sample_rate: int, channels: int,
                 samples_per_chunk: int, connect_timeout_s: float = 30.0, queue_size: int = 256):
        self.camera_id         = camera_id
        self.has_video         = has_video
        self.has_audio         = has_audio
        self.sample_rate       = sample_rate
        self.channels          = channels
        self.connect_timeout_s = connect_timeout_s

        self._chunker = AudioChunker(sample_rate, channels, samples_per_chunk)
        self._queue: "queue.Queue" = queue.Queue(maxsize=queue_size)
        self._opened  = False
        self._closed  = False
        self._wall_t0: Optional[float] = None
        self._pts_t0: Optional[int] = None
        self._last_video_pts = -1
        self.dropped_video   = 0
        self.dropped_audio   = 0

    def _wall_ts(self, pts_ms: int) -> float:
        if self._wall_t0 is None:
            self._wall_t0, self._pts_t0 = time.time(), pts_ms
        return self._wall_t0 + (pts_ms - self._pts_t0) / 1000.0

    def push_video(self, pts_ms: int, jpeg: bytes) -> bool:
        if self._closed or not self.has_video or pts_ms <= self._last_video_pts:
            return False
        self._last_video_pts = pts_ms
        frame = VideoFrame(pts_ms=pts_ms, capture_wall_ts=self._wall_ts(pts_ms), loader=lambda: _decode_jpeg(jpeg))
        try:
            self._queue.put_nowait(frame)
            return True
        except queue.Full:
            self.dropped_video += 1
            return False

    def push_audio(self, pts_ms: int, pcm: bytes) -> int:
        if self._closed or not self.has_audio:
            return 0
        chunks = self._chunker.push(pcm, pts_ms, self._wall_ts(pts_ms))
        for chunk in chunks:
            try:
                self._queue.put(chunk, timeout=0.05)
            except queue.Full:
                self.dropped_audio += 1
        return len(chunks)

    def finish(self) -> None:
        """Браузер отключился: читатель дочитает очередь и завершится."""
        if self._closed:
            return
        self._closed = True
        try:
            self._queue.put_nowait(_END)
        except queue.Full:
            pass

    def open(self, want_video: bool = True, want_audio: bool = True) -> SourceInfo:
        if self._opened:
            raise RuntimeError(f"Browser source {self.camera_id} cannot be reopened")
        self._opened = True
        logging.info(f"BrowserSource {self.camera_id}: video={self.has_video} audio={self.has_audio}")
        return SourceInfo(has_video=self.has_video and want_video, has_audio=self.has_audio and want_audio,
                          is_live=True, audio_native_rate=self.sample_rate if self.has_audio else None)

    def read(self) -> Iterator[Union[VideoFrame, AudioChunk]]:
        deadline = time.monotonic() + self.connect_timeout_s
        started  = False
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._closed:
                    return
                if not started and time.monotonic() > deadline:
                    raise RuntimeError(f"Browser did not send media within {self.connect_timeout_s:.0f} s")
                continue
            if item is _END:
                return
            started = True
            yield item

    def close(self) -> None:
        self.finish()


def register(source: BrowserSource) -> str:
    with _lock:
        _registry[source.camera_id] = source
    return BROWSER_PREFIX + source.camera_id


def get(camera_id: str) -> Optional[BrowserSource]:
    with _lock:
        return _registry.get(camera_id)


def unregister(camera_id: str) -> Optional[BrowserSource]:
    with _lock:
        return _registry.pop(camera_id, None)
