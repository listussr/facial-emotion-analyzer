"""
Единый набор классов эмоций для всех модальностей (вариант Б — классы CREMA-D).

Порядок важен: i-й выход любой модели (лицо, голос) и i-й элемент
`emotion_scores` во всех топиках и в БД соответствуют EMOTION_LABELS[i].
Модели, обученные на CREMA-D, должны выдавать классы именно в этом порядке.
"""
from typing import Tuple

# Порядок как в кодах CREMA-D: ANG, DIS, FEA, HAP, NEU, SAD
EMOTION_LABELS: Tuple[str, ...] = ('anger', 'disgust', 'fear', 'happy', 'neutral', 'sad')
NUM_EMOTIONS: int = len(EMOTION_LABELS)

# Версия набора классов — пишется в результаты, чтобы потребители могли
# отличить данные старого 8-классового набора от нового.
LABEL_SET: str = 'crema-d-6'


def label_of(idx: int) -> str:
    """Метка класса по индексу; вне диапазона → ''."""
    if 0 <= idx < NUM_EMOTIONS:
        return EMOTION_LABELS[idx]
    return ''
