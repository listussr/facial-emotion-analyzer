"""Метрики реального времени: возраст кадра, отбрасывание устаревших кадров, сквозная задержка."""

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

    Время сравнивается между машинами, поэтому в распределённой установке нужен NTP.
    """

    def __init__(self, max_age_ms: Optional[int]):
        self.max_age_s = max_age_ms / 1000.0 if max_age_ms and max_age_ms > 0 else None
        self.dropped   = 0

    def is_stale(self, payload: Dict, now: float) -> bool:
        if self.max_age_s is None:
            return False
        ts = capture_ts(payload)
        if ts is None:
            return False
        if now - ts > self.max_age_s:
            self.dropped += 1
            return True
        return False


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
