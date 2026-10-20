"""
Результат анализа одного аудиоокна (топик audio-analytics, JSON, ключ — camera_id).

Окна идут с шагом hop_ms и перекрываются. Окно без речи тоже публикуется
(`is_speech=False`, `scores=None`): слиянию нужно знать, что голоса нет,
а не гадать, потерялось ли сообщение.
"""
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from src.emotion_labels import EMOTION_LABELS, LABEL_SET

RESULT_SCHEMA_VERSION = 1


@dataclass(kw_only=True)
class AudioAnalytics:
    schema_version:    int = RESULT_SCHEMA_VERSION
    camera_id:         str
    window_seq:        int                     # номер окна в сессии (с 0)
    pts_start_ms:      int                     # начало окна во времени потока
    pts_end_ms:        int                     # конец окна (не включительно)
    capture_wall_ts:   float                   # время захвата конца окна
    processed_wall_ts: float
    speech_prob:       float                   # доля речи в окне по VAD, 0..1
    is_speech:         bool
    # Сырые вероятности модели для этого окна; None — речи нет, модель не запускалась
    scores:            Optional[List[float]] = None
    label:             Optional[str] = None
    # Сглаженные по окнам сессии; держатся между фразами
    smoothed_scores:   Optional[List[float]] = None
    smoothed_label:    Optional[str] = None
    model:             str = ''
    label_set:         str = LABEL_SET
    emotion_labels:    List[str] = None        # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.emotion_labels is None:
            self.emotion_labels = list(EMOTION_LABELS)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False).encode('utf-8')

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AudioAnalytics':
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})
