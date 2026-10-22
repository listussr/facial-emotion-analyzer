"""Сглаживание покадровых эмоций старого распознавателя."""

import logging
import numpy as np

from typing import Callable
from typing import Dict
from typing import Literal

from src.emotion_labels import EMOTION_LABELS
from src.emotion_labels import NUM_EMOTIONS
from src.emotion_labels import label_of


def idx_to_label(idx: int) -> str:
    """
    Метка класса по индексу с ошибкой в логе вне диапазона.

    Args:
        idx (int): Индекс класса.

    Returns:
        str: Метка или пустая строка.
    """
    label = label_of(int(idx))
    if not label:
        logging.error(f"Incorrect emotion index. Required [0..{NUM_EMOTIONS - 1}] but got {idx}")
    return label


def exponential_smoothing(cached: Dict, prediction: np.ndarray, frequency: int, ema_coef: float = 0.2):
    """
    EMA вероятностей трека.

    Args:
        cached     (Dict)           : Состояние трека.
        prediction (np.ndarray)     : Вероятности классификатора.
        frequency  (int)            : Частота вызова классификатора, кадров.
        ema_coef   (float, optional): Коэффициент сглаживания. Defaults to 0.2.
    """
    prediction = prediction / (prediction.sum() + 1e-6)

    if cached.get("emotion_probs") is None:
        cached["emotion_probs"] = prediction.copy()
        cached["emotion_label"] = idx_to_label(np.argmax(prediction))
        return

    ema_sparse_coef         = ema_coef ** frequency
    cached["emotion_probs"] = ema_sparse_coef * cached["emotion_probs"] + (1 - ema_sparse_coef) * prediction
    cached["emotion_label"] = idx_to_label(np.argmax(cached["emotion_probs"]))


def voting(cached: Dict, prediction: np.ndarray, frequency: int, ema_coef: float = 0.2):
    """
    EMA со счётчиком: новая метка принимается, только если встретилась дважды.

    Args:
        cached     (Dict)           : Состояние трека.
        prediction (np.ndarray)     : Вероятности классификатора.
        frequency  (int)            : Частота вызова классификатора, кадров.
        ema_coef   (float, optional): Коэффициент сглаживания. Defaults to 0.2.
    """
    prediction = prediction / (prediction.sum() + 1e-6)

    if cached.get("emotion_probs") is None:
        cached["emotion_probs"] = prediction.copy()
        cached["emotion_label"] = idx_to_label(np.argmax(prediction))
        return

    ema_sparse_coef         = ema_coef ** frequency
    cached["emotion_probs"] = ema_sparse_coef * cached["emotion_probs"] + (1 - ema_sparse_coef) * prediction
    ema_top                 = np.argmax(cached["emotion_probs"])

    top = np.argmax(prediction)

    cached["votes"][top] += 1

    if cached["votes"][top] >= 2:
        cached["emotion_label"] = idx_to_label(ema_top)
        cached["votes"].fill(0)


def hysteresis_exp_smoothing(cached: Dict, prediction: np.ndarray, frequency: int, ema_coef: float = 0.2):
    """
    EMA с гистерезисом: метка меняется после двух уверенных предсказаний подряд.

    Args:
        cached     (Dict)           : Состояние трека.
        prediction (np.ndarray)     : Вероятности классификатора.
        frequency  (int)            : Частота вызова классификатора, кадров.
        ema_coef   (float, optional): Коэффициент сглаживания. Defaults to 0.2.
    """
    num_labels = len(EMOTION_LABELS)
    if (
        prediction.ndim != 1
        or prediction.size != num_labels
        or cached.get("emotion_probs") is None
        or cached["emotion_probs"].size != num_labels
    ):
        cached["emotion_probs"]          = prediction[:num_labels].copy()
        cached["emotion_label"]          = idx_to_label(int(np.argmax(cached["emotion_probs"])))
        cached["emotion_switch_counter"] = 0
        return

    if cached.get("emotion_probs") is None:
        cached["emotion_probs"]          = prediction.copy()
        cached["emotion_label"]          = idx_to_label(np.argmax(prediction))
        cached["emotion_switch_counter"] = 0
        return

    ema_sparse_coef         = ema_coef ** frequency
    cached["emotion_probs"] = ema_sparse_coef * cached["emotion_probs"] + (1 - ema_sparse_coef) * prediction

    new_label = idx_to_label(np.argmax(cached["emotion_probs"]))

    if new_label != cached["emotion_label"] and prediction.max() > 0.6:
        cached["emotion_switch_counter"] += 1
    else:
        cached["emotion_switch_counter"] = max(0, cached["emotion_switch_counter"] - 1)

    if cached["emotion_switch_counter"] >= 2:
        cached["emotion_label"]          = new_label
        cached["emotion_switch_counter"] = 0


def get_emotion_smoothing_strategy(strategy: Literal["ema", "ema_voting", "ema_hysteresis"]) -> Callable:
    """
    Функция сглаживания по имени стратегии.

    Args:
        strategy (Literal): "ema", "ema_voting" или "ema_hysteresis".

    Returns:
        Callable: Функция сглаживания; по умолчанию гистерезис.
    """
    return {
        "ema": exponential_smoothing,
        "ema_voting": voting,
        "ema_hysteresis": hysteresis_exp_smoothing,
    }.get(strategy, hysteresis_exp_smoothing)
