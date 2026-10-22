"""Темп реального времени, прореживание кадров и порядок выпуска событий файла."""

import heapq
import time

from typing import Optional


class RealtimePacer:
    """
    Выпуск событий файла в темпе реального времени по PTS.

    При отставании больше max_lag_ms график сдвигается на величину отставания: воспроизведение
    продолжается с места паузы, а не догоняет её пачкой событий, которые консьюмеры сочли бы устаревшими.
    """

    def __init__(self, enabled: bool, max_lag_ms: int = 300):
        self.enabled   = enabled
        self.max_lag_s = max_lag_ms / 1000.0
        self._t0: Optional[float] = None
        self.shift_ms = 0
        self.resyncs  = 0

    def start(self) -> None:
        self._t0 = time.monotonic()

    def wait_until(self, pts_ms: int, stop_flag=None) -> None:
        """
        Ожидание момента t0 + pts_ms.

        Args:
            pts_ms    (int)               : Время события в потоке.
            stop_flag (Callable, optional): Прервать ожидание, если вернул True. Defaults to None.
        """
        if not self.enabled:
            return
        if self._t0 is None:
            self.start()
        deadline = self._t0 + pts_ms / 1000.0
        lag      = time.monotonic() - deadline
        if lag > self.max_lag_s:
            self._t0 += lag
            self.shift_ms += int(round(lag * 1000))
            self.resyncs += 1
            return
        while True:
            delay = deadline - time.monotonic()
            if delay <= 0:
                return
            if stop_flag is not None and stop_flag():
                return
            time.sleep(min(delay, 0.1))


class FrameDecimator:
    """
    Прореживание кадров по PTS до целевого FPS.

    При отставании больше двух интервалов расписание перескакивает на текущий кадр.
    """

    def __init__(self, target_fps: float):
        self._interval_ms = 1000.0 / target_fps
        self._next_due_ms: Optional[float] = None

    def accept(self, pts_ms: int) -> bool:
        if self._next_due_ms is None:
            self._next_due_ms = pts_ms + self._interval_ms
            return True
        if pts_ms + 0.5 < self._next_due_ms:
            return False
        if pts_ms - self._next_due_ms > 2 * self._interval_ms:
            self._next_due_ms = pts_ms + self._interval_ms
        else:
            self._next_due_ms += self._interval_ms
        return True


class ReleaseOrderer:
    """
    Переупорядочивание событий файла по времени выпуска.

    Кадр выпускается в свой pts, аудиочанк — в pts своего конца. Событие выпускается, как только
    прочитано событие с началом не раньше его времени выпуска, иначе кадры ждали бы чанк.
    """

    def __init__(self):
        self._heap    = []
        self._counter = 0

    def push(self, release_ms: int, start_ms: int, item) -> list:
        """
        Добавление события.

        Args:
            release_ms (int): Время выпуска.
            start_ms   (int): Время начала события.
            item            : Событие.

        Returns:
            list: Готовые к выпуску пары (release_ms, item) по порядку.
        """
        heapq.heappush(self._heap, (release_ms, self._counter, item))
        self._counter += 1
        ready = []
        while self._heap and self._heap[0][0] <= start_ms:
            release, _, it = heapq.heappop(self._heap)
            ready.append((release, it))
        return ready

    def drain(self) -> list:
        out = []
        while self._heap:
            release, _, it = heapq.heappop(self._heap)
            out.append((release, it))
        return out

    def __len__(self) -> int:
        return len(self._heap)
