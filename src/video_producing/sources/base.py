import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
import numpy as np
from typing import Iterator, Optional, Union

@dataclass
class VideoFrame:
    pts_ms:          int
    capture_wall_ts: float
    image:           np.ndarray            # BGR, как ожидает остальная система

@dataclass
class AudioChunk:
    pts_ms:          int
    capture_wall_ts: float
    pcm:             bytes                 # s16le interleaved
    num_samples:     int                   # на канал

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
    """
    Общий отсчёт времени сессии для живых источников.

    `pts_ms` всех потоков сессии считается от одного `t0`, поэтому кадры
    камеры и чанки микрофона лежат на одной шкале времени.
    """
    def __init__(self) -> None:
        self.t0 = time.monotonic()
        self.wall_t0 = time.time()

    def now_ms(self) -> int:
        return self.from_monotonic(time.monotonic())

    def from_monotonic(self, t: float) -> int:
        return int(round((t - self.t0) * 1000))

    def wall_ts(self, pts_ms: int) -> float:
        return self.wall_t0 + pts_ms / 1000.0


class MediaSource(ABC):
    """
    Источник медиа. `read()` — генератор событий `VideoFrame | AudioChunk`
    в порядке времени захвата. Генератор завершается на конце файла и
    выбрасывает исключение при обрыве живого источника.

    Источник не прореживает кадры и не выдерживает темп — это делает продюсер.
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
