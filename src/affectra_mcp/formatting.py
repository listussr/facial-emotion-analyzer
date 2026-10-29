"""Компактный текстовый формат ответов ручек: шапка-пояснение и таблица чисел вместо JSON-объектов."""

import math

from typing import Any
from typing import Iterable
from typing import List
from typing import Optional
from typing import Sequence

NICE_STEPS = (1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600)

SHORT_RU = {
    "anger":   "гнев",
    "disgust": "отвращ",
    "fear":    "страх",
    "happy":   "радость",
    "neutral": "нейтр",
    "sad":     "грусть",
}


def mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def pick_step(span_s: float, base_s: float, requested: Optional[float], max_points: int) -> float:
    """
    Шаг ряда: не меньше запрошенного и такой, чтобы точек было не больше max_points; из «круглых» значений.

    Args:
        span_s     (float)           : Длина интервала, с.
        base_s     (float)           : Шаг исходного ряда, с.
        requested  (Optional[float]) : Запрошенный шаг, с.
        max_points (int)             : Предел числа точек.

    Returns:
        float: Шаг, с.
    """
    need = max(base_s, requested or base_s, span_s / max(1, max_points))
    for step in NICE_STEPS:
        if step >= need:
            return float(step)
    return float(math.ceil(need / 60) * 60)


def fmt(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def table(header: Sequence[str], rows: Iterable[Sequence[Any]], digits: int = 2) -> str:
    lines: List[str] = [",".join(header)]
    lines += [",".join(fmt(v, digits) for v in row) for row in rows]
    return "\n".join(lines)


def shares(values: dict, top: int = 3) -> str:
    """«грусть 62%, нейтр 30%» по долям эмоций."""
    items = sorted(values.items(), key=lambda kv: -kv[1])[:top]
    return ", ".join(f"{SHORT_RU.get(k, k)} {v:.0%}" for k, v in items if v > 0) or "нет данных"
