"""Метрики реального времени: возраст кадра, отбрасывание устаревших кадров, сквозная задержка."""

import logging
import time

from typing import Dict
from typing import List
from typing import Optional


def capture_ts(payload: Dict) -> Optional[float]:
    """
    Время захвата кадра: capture_wall_ts в schema v2, timestamp в v1.

    Args:
        payload (Dict): Сообщение продюсера.

    Returns:
        Optional[float]: Время захвата или None.
    """
    ts = payload.get("capture_wall_ts")
    if ts is None:
        ts = payload.get("timestamp")
    try:
        return float(ts) if ts is not None else None
    except (TypeError, ValueError):
        return None


class StalenessFilter:
    """
    Отбрасывание кадров старше max_age_ms по времени захвата: лучше пропустить кадр, чем копить задержку.

    Время сравнивается между машинами, поэтому в распределённой установке нужен NTP. Признаки расхождения часов
    пишутся в лог не чаще раза в warn_interval_s: кадры «из будущего» дальше skew_tolerance_ms (часы источника
    спешат) и длинная серия только устаревших кадров (отставание обработки или часы источника отстают).
    """

    def __init__(self, max_age_ms: Optional[int], skew_tolerance_ms: int = 500, stale_run_warn: int = 100,
                 warn_interval_s: float = 60.0, name: str = "frames"):
        self.max_age_s         = max_age_ms / 1000.0 if max_age_ms and max_age_ms > 0 else None
        self.skew_tolerance_s  = skew_tolerance_ms / 1000.0
        self.stale_run_warn    = stale_run_warn
        self.warn_interval_s   = warn_interval_s
        self.name              = name
        self.dropped           = 0
        self.max_ahead_s       = 0.0
        self._stale_run        = 0
        self._last_warn        = float("-inf")

    def is_stale(self, payload: Dict, now: float) -> bool:
        ts = capture_ts(payload)
        if ts is None:
            return False
        age = now - ts
        if -age > self.skew_tolerance_s:
            self.max_ahead_s = max(self.max_ahead_s, -age)
            self._warn(f"{self.name} captured {-age * 1000:.0f} ms in the future: clocks of the nodes differ, "
                       f"sync them (NTP)")
        if self.max_age_s is None or age <= self.max_age_s:
            self._stale_run = 0
            return False
        self.dropped    += 1
        self._stale_run += 1
        if self._stale_run >= self.stale_run_warn:
            self._warn(f"{self._stale_run} {self.name} in a row older than {self.max_age_s * 1000:.0f} ms "
                       f"(last {age * 1000:.0f} ms): processing lags behind or the source clock is behind (NTP)")
        return True

    def _warn(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_warn >= self.warn_interval_s:
            self._last_warn = now
            logging.getLogger("staleness").warning(message)


class LatencyWindow:
    """Окно задержек в секундах для периодических логов."""

    def __init__(self):
        self._values: List[float] = []

    def add(self, seconds: float) -> None:
        self._values.append(seconds)

    def __len__(self) -> int:
        return len(self._values)

    def summary_ms(self) -> Dict[str, float]:
        """
        Перцентили окна.

        Returns:
            Dict[str, float]: p50, p95 и max в мс; для пустого окна — нули.
        """
        if not self._values:
            return {"p50": 0.0, "p95": 0.0, "max": 0.0}
        v = sorted(self._values)

        def q(p: float) -> float:
            return v[min(len(v) - 1, int(p * len(v)))] * 1000.0

        return {"p50": q(0.50), "p95": q(0.95), "max": v[-1] * 1000.0}

    def reset(self) -> None:
        self._values.clear()
