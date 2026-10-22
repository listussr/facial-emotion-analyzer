"""Результат анализа аудиоокна в топике audio-analytics."""

import json

from dataclasses import asdict
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import LABEL_SET

RESULT_SCHEMA_VERSION = 2


@dataclass(kw_only=True)
class AudioAnalytics:
    """
    Результат одного аудиоокна.

    Окно без речи тоже публикуется с scores=None: слиянию нужно знать, что голоса нет.
    logits и emb — вход алгоритмов слияния; smoothed_* держатся между фразами, label None — «не определено».
    """
    schema_version:    int = RESULT_SCHEMA_VERSION
    camera_id:         str
    window_seq:        int
    pts_start_ms:      int
    pts_end_ms:        int
    capture_wall_ts:   float
    processed_wall_ts: float
    speech_prob:       float
    is_speech:         bool
    scores:            Optional[List[float]] = None
    label:             Optional[str] = None
    logits:            Optional[List[float]] = None
    emb:               Optional[List[float]] = None
    smoothed_scores:   Optional[List[float]] = None
    smoothed_label:    Optional[str] = None
    model:             str = ""
    label_set:         str = LABEL_SET
    emotion_labels:    List[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.emotion_labels is None:
            self.emotion_labels = list(EMOTION_LABELS)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict(), ensure_ascii=False).encode("utf-8")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AudioAnalytics":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})
