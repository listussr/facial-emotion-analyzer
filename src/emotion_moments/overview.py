"""Обзор сессии по секундному ряду: длительность, качество данных, распределение эмоций по модальностям."""

import numpy as np

from typing import Any
from typing import Dict
from typing import List

from .series import EmotionSeries

QUALITY_THRESHOLDS = {"low_face": 0.6, "many_predicted": 0.75, "others_in_frame": 0.2}


def quality_flags(s: EmotionSeries) -> List[str]:
    """
    Флаги качества данных сессии.

    Args:
        s (EmotionSeries): Ряд.

    Returns:
        List[str]: low_face — лицо в кадре меньше 60% времени; many_predicted — больше 75% рамок предсказал
        трекер (штатно около половины: детектор работает раз в 2 кадра); others_in_frame — в кадре часто есть
        другие люди; no_voice — нет ни одного окна голоса.
    """
    flags = []
    if len(s) and float(np.mean(s.face_present)) < QUALITY_THRESHOLDS["low_face"]:
        flags.append("low_face")
    with_face = s.face_present > 0
    if np.any(with_face) and float(np.mean(s.predicted[with_face])) > QUALITY_THRESHOLDS["many_predicted"]:
        flags.append("many_predicted")
    if len(s) and float(np.mean(s.others)) > QUALITY_THRESHOLDS["others_in_frame"]:
        flags.append("others_in_frame")
    if not np.any(s.valid("voice")):
        flags.append("no_voice")
    return flags


def session_overview(s: EmotionSeries) -> Dict[str, Any]:
    """
    Обзор сессии для агента и отчёта.

    Args:
        s (EmotionSeries): Ряд.

    Returns:
        Dict[str, Any]: duration_s; face_present и speech — доли времени; по модальностям fused / face / voice —
        средние вероятности и доли секунд, где каждая эмоция ведущая; флаги качества.
    """
    whole = slice(0, len(s))
    out: Dict[str, Any] = {
        "duration_s":   round(s.duration_s, 1),
        "face_present": round(float(np.mean(s.face_present)), 3) if len(s) else 0.0,
        "speech":       round(float(np.mean(s.speech)), 3) if len(s) else 0.0,
        "modalities":   {},
        "quality":      quality_flags(s),
    }
    for modality in ("fused", "face", "voice"):
        mean = s.mean_probs(whole, modality)
        if mean is None:
            continue
        out["modalities"][modality] = {
            "seconds":  int(np.sum(s.valid(modality))),
            "mean":     {label: round(float(p), 3) for label, p in zip(s.labels, mean)},
            "dominant": s.dominant_shares(whole, modality),
        }
    return out
