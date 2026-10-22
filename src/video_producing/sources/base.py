"""Базовые типы источников медиа: кадры, аудиочанки, общий отсчёт времени сессии."""

import time
import numpy as np

from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from typing import Callable
from typing import Iterator
from typing import Optional
from typing import Tuple
from typing import Union


class VideoFrame:
    """
    Кадр источника в BGR.

    Изображение может считаться лениво через loader: перевод в BGR и уменьшение выполняются
    только для кадров, которые продюсер отправит (при 60 fps файла и 20 fps отправки — треть).
    original_size — (ширина, высота) кадра источника до уменьшения.
    """
    __slots__ = ("pts_ms", "capture_wall_ts", "_image", "_loader", "_original_size")

    def __init__(self, pts_ms: int, capture_wall_ts: float, image: Optional[np.ndarray] = None,
                 loader: Optional[Callable[[], np.ndarray]] = None,
                 original_size: Optional[Tuple[int, int]] = None):
        if image is None and loader is None:
            raise ValueError("VideoFrame needs image or loader")
        self.pts_ms          = pts_ms
        self.capture_wall_ts = capture_wall_ts
        self._image          = image
        self._loader         = loader
        self._original_size  = original_size

    @property
    def image(self) -> np.ndarray:
        if self._image is None:
            self._image  = self._loader()
            self._loader = None
        return self._image

    @property
    def original_size(self) -> Tuple[int, int]:
        if self._original_size is not None:
            return self._original_size
        h, w = self.image.shape[:2]
        return w, h

    def __repr__(self) -> str:
        return f"VideoFrame(pts_ms={self.pts_ms}, size={self._original_size}, loaded={self._image is not None})"


@dataclass
class AudioChunk:
    """Аудиочанк s16le interleaved; num_samples — на канал."""
    pts_ms:          int
    capture_wall_ts: float
    pcm:             bytes
    num_samples:     int


@dataclass
class SourceInfo:
    has_video:         bool
    has_audio:         bool
    is_live:           bool
    native_fps:        Optional[float] = None
    duration_sec:      Optional[float] = None
    total_frames:      Optional[int] = None
    audio_native_rate: Optional[int] = None


class SessionClock:
    """Общий отсчёт времени живой сессии: кадры камеры и чанки микрофона на одной шкале."""

    def __init__(self) -> None:
        self.t0      = time.monotonic()
        self.wall_t0 = time.time()

    def now_ms(self) -> int:
        return self.from_monotonic(time.monotonic())

    def from_monotonic(self, t: float) -> int:
        return int(round((t - self.t0) * 1000))

    def wall_ts(self, pts_ms: int) -> float:
        return self.wall_t0 + pts_ms / 1000.0


class MediaSource(ABC):
    """
    Источник медиа.

    read() — генератор VideoFrame и AudioChunk в порядке захвата; завершается на конце файла,
    при обрыве живого источника выбрасывает исключение. Прореживание и темп — на стороне продюсера.
    """
    @abstractmethod
    def open(self, want_video: bool, want_audio: bool) -> SourceInfo: ...
    @abstractmethod
    def read(self) -> Iterator[Union[VideoFrame, AudioChunk]]: ...
    @abstractmethod
    def close(self) -> None: ...

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False
