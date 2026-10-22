"""Единый набор классов эмоций CREMA-D для всех модальностей: i-й выход моделей соответствует EMOTION_LABELS[i]."""

from typing import Tuple


EMOTION_LABELS: Tuple[str, ...] = ("anger", "disgust", "fear", "happy", "neutral", "sad")
NUM_EMOTIONS: int               = len(EMOTION_LABELS)
LABEL_SET: str                  = "crema-d-6"


def label_of(idx: int) -> str:
    """
    Метка класса по индексу.

    Args:
        idx (int): Индекс класса.

    Returns:
        str: Метка или пустая строка вне диапазона.
    """
    if 0 <= idx < NUM_EMOTIONS:
        return EMOTION_LABELS[idx]
    return ""
