"""Секундный ряд эмоций субъекта (series.json) в виде массивов numpy."""

import numpy as np

from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence
from typing import Tuple

from src.emotion_labels import EMOTION_LABELS

LABELS_RU = {
    "anger":   "гнев",
    "disgust": "отвращение",
    "fear":    "страх",
    "happy":   "радость",
    "neutral": "нейтральное",
    "sad":     "грусть",
}


def _matrix(rows: Sequence[Optional[Sequence[float]]], k: int) -> np.ndarray:
    out = np.full((len(rows), k), np.nan)
    for i, row in enumerate(rows):
        if row is not None and len(row) == k:
            out[i] = row
    return out


def jsd(p: np.ndarray, q: np.ndarray) -> float:
    """
    Расстояние Дженсена — Шеннона между распределениями по основанию 2: 0 — совпадают, 1 — не пересекаются.

    Args:
        p (np.ndarray): Распределение.
        q (np.ndarray): Распределение.

    Returns:
        float: Значение в [0, 1].
    """
    p = np.clip(np.asarray(p, np.float64), 0, None)
    q = np.clip(np.asarray(q, np.float64), 0, None)
    p, q = p / max(p.sum(), 1e-12), q / max(q.sum(), 1e-12)
    m = (p + q) / 2

    def kl(a: np.ndarray) -> float:
        mask = a > 0
        return float(np.sum(a[mask] * np.log2(a[mask] / m[mask])))

    return 0.5 * kl(p) + 0.5 * kl(q)


@dataclass
class EmotionSeries:
    """
    Ряд субъекта: строки — секунды, NaN — нет данных (лица нет в кадре или нет речи).

    fused, face, voice — вероятности эмоций (N × K); face_present, predicted, others, speech — доли кадров секунды.
    """
    camera_id:    str
    labels:       Tuple[str, ...]
    step_s:       float
    t:            np.ndarray
    fused:        np.ndarray
    face:         np.ndarray
    voice:        np.ndarray
    face_present: np.ndarray
    predicted:    np.ndarray
    others:       np.ndarray
    speech:       np.ndarray
    subject:      Dict[str, Any]

    @classmethod
    def from_document(cls, doc: Dict[str, Any]) -> "EmotionSeries":
        """
        Ряд из документа series.json.

        Args:
            doc (Dict[str, Any]): Документ series.json.

        Returns:
            EmotionSeries: Ряд.
        """
        labels = tuple(doc.get("labels") or EMOTION_LABELS)
        k      = len(labels)
        n      = len(doc.get("t") or [])

        def vec(name: str) -> np.ndarray:
            values = doc.get(name) or [0.0] * n
            return np.asarray(values, np.float64)

        return cls(
            camera_id=doc.get("camera_id", ""),
            labels=labels,
            step_s=float(doc.get("step_s") or 1.0),
            t=vec("t"),
            fused=_matrix(doc.get("fused") or [None] * n, k),
            face=_matrix(doc.get("face") or [None] * n, k),
            voice=_matrix(doc.get("voice") or [None] * n, k),
            face_present=vec("face_present"),
            predicted=vec("predicted"),
            others=vec("others"),
            speech=vec("speech"),
            subject=doc.get("subject") or {},
        )

    def __len__(self) -> int:
        return len(self.t)

    @property
    def duration_s(self) -> float:
        return len(self) * self.step_s

    def index(self, label: str) -> int:
        return self.labels.index(label)

    def valid(self, modality: str = "fused") -> np.ndarray:
        return ~np.isnan(getattr(self, modality)[:, 0])

    def window(self, start_s: float, end_s: float) -> slice:
        """Срез строк для интервала [start_s, end_s)."""
        i = max(0, int(np.floor(start_s / self.step_s)))
        j = min(len(self), int(np.ceil(end_s / self.step_s)))
        return slice(i, max(i, j))

    def mean_probs(self, sl: slice, modality: str = "fused") -> Optional[np.ndarray]:
        rows = getattr(self, modality)[sl]
        rows = rows[~np.isnan(rows[:, 0])]
        return rows.mean(axis=0) if len(rows) else None

    def dominant_shares(self, sl: slice, modality: str = "fused") -> Dict[str, float]:
        """Доля секунд интервала с данными, в которых каждая эмоция ведущая."""
        rows = getattr(self, modality)[sl]
        rows = rows[~np.isnan(rows[:, 0])]
        if not len(rows):
            return {}
        idx = rows.argmax(axis=1)
        return {self.labels[i]: round(float(np.mean(idx == i)), 3) for i in range(len(self.labels)) if np.any(idx == i)}

    def label_ru(self, label: str) -> str:
        return LABELS_RU.get(label, label)

    def labels_ru(self) -> List[str]:
        return [self.label_ru(x) for x in self.labels]
