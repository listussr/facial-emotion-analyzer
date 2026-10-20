"""
Метрики реального времени видеопайплайна: возраст кадра, отбрасывание
устаревших кадров, сквозная задержка.
"""
from typing import Dict, List, Optional


def capture_ts(payload: Dict) -> Optional[float]:
    """
    Время захвата кадра (wall time).

    schema v2 — `capture_wall_ts`; v1 — `timestamp` (время отправки
    продюсером, ближайшее доступное приближение).
    """
    ts = payload.get('capture_wall_ts')
    if ts is None:
        ts = payload.get('timestamp')
    try:
        return float(ts) if ts is not None else None
    except (TypeError, ValueError):
        return None


class StalenessFilter:
    def __init__(self, max_age_ms: Optional[int]):
        """
        Отбрасывание кадров старше `max_age_ms` по времени захвата.

        В реальном времени лучше пропустить устаревший кадр, чем копить
        задержку: если обработка отстаёт, отставание не растёт без предела.

        Args:
            max_age_ms: Порог возраста, мс; None или <= 0 — не отбрасывать.

        Время сравнивается по wall time разных машин — на распределённой
        установке нужна синхронизация часов (NTP).
        """
        self.max_age_s = max_age_ms / 1000.0 if max_age_ms and max_age_ms > 0 else None
        self.dropped = 0

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
    def __init__(self):
        """Окно значений задержки (с) для периодических логов: p50 / p95 / max."""
        self._values: List[float] = []

    def add(self, seconds: float) -> None:
        self._values.append(seconds)

    def __len__(self) -> int:
        return len(self._values)

    def summary_ms(self) -> Dict[str, float]:
        """{'p50', 'p95', 'max'} в мс; пустое окно → нули."""
        if not self._values:
            return {'p50': 0.0, 'p95': 0.0, 'max': 0.0}
        v = sorted(self._values)

        def q(p: float) -> float:
            return v[min(len(v) - 1, int(p * len(v)))] * 1000.0

        return {'p50': q(0.50), 'p95': q(0.95), 'max': v[-1] * 1000.0}

    def reset(self) -> None:
        self._values.clear()
