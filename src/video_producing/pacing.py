import time
from typing import Optional


class RealtimePacer:
    def __init__(self, enabled: bool):
        """
        Выдерживает темп реального времени по PTS: событие с `pts_ms`
        отпускается не раньше `t0 + pts_ms`. Нужен для файлов — живые
        источники и так идут в реальном времени.

        Args:
            enabled (bool): Если False, `wait_until` ничего не делает.
        """
        self.enabled = enabled
        self._t0: Optional[float] = None

    def start(self) -> None:
        self._t0 = time.monotonic()

    def wait_until(self, pts_ms: int, stop_flag=None) -> None:
        """
        Спит до `t0 + pts_ms / 1000`.

        Args:
            pts_ms (int): Время события в потоке.
            stop_flag (Callable[[], bool], optional): Прервать ожидание, если вернул True.
        """
        if not self.enabled:
            return
        if self._t0 is None:
            self.start()
        deadline = self._t0 + pts_ms / 1000.0
        while True:
            delay = deadline - time.monotonic()
            if delay <= 0:
                return
            if stop_flag is not None and stop_flag():
                return
            time.sleep(min(delay, 0.1))


class FrameDecimator:
    def __init__(self, target_fps: float):
        """
        Прореживание кадров по PTS до целевого FPS.

        Кадр отправляется, если его `pts_ms` дошёл до очередного слота.
        При отставании больше чем на два интервала слоты не наверстываются
        пачкой — расписание перескакивает на текущий кадр.

        Args:
            target_fps (float): Целевой FPS.
        """
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
