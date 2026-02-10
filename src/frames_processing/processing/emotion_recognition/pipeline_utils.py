from typing import Dict, Literal, Callable
import numpy as np
import logging

EMOTION_LABELS = ('anger', 'contempt', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprise')

def idx_to_label(idx: int) -> str:
    """
    #### Перевод индекса эмоции в метку класса.
    
    :param idx: Индекс эмоции.
    :type idx: int
    :return: Текстовая метка класса.
    :rtype: str
    """
    if idx < 0 or idx >= len(EMOTION_LABELS):
        logging.error(f"Incorrect emotion index. Required [0..7] but got {idx}")
        return ""
    return EMOTION_LABELS[idx]


def exponential_smoothing(cached: Dict, prediction: np.ndarray, frequency: int, ema_coef: float = 0.2):
    """
    #### Экспоненциальное сглаживание эмоций для борьбы со скачками эмоций.
    
    :param cached: Кэшированные данные по треку.
    :type cached: Dict
    :param prediction: Предсказание классификатора.
    :type prediction: np.ndarray
    :param frequency: Частота сглаживания вызова классификатора (для более гладкой картины).
    :type frequency: int
    :param ema_coef: Коэффицент сглажтивания.
    :type ema_coef: float
    """
    prediction = prediction / (prediction.sum() + 1e-6)

    if cached.get("emotion_probs") is None:
        cached["emotion_probs"] = prediction.copy()
        cached["emotion_label"] = idx_to_label(np.argmax(prediction))
        return

    ema_sparse_coef = ema_coef ** frequency
    cached["emotion_probs"] = ema_sparse_coef * cached["emotion_probs"] + (1 - ema_sparse_coef) * prediction
    cached["emotion_label"] = idx_to_label(np.argmax(cached["emotion_probs"]))

def voting(cached: Dict, prediction: np.ndarray, frequency: int, ema_coef: float = 0.2):
    """
    #### Экспоненциальное сглаживание эмоций + счётчик проявления эмоций.
    
    Если новая эмоция появляется меньше заданного числа раз, то остаётся старая эмоция.

    :param cached: Кэшированные данные по треку.
    :type cached: Dict
    :param prediction: Предсказание классификатора.
    :type prediction: np.ndarray
    :param frequency: Частота сглаживания вызова классификатора (для более гладкой картины).
    :type frequency: int
    :param ema_coef: Коэффицент сглажтивания.
    :type ema_coef: float
    """
    prediction = prediction / (prediction.sum() + 1e-6)

    if cached.get("emotion_probs") is None:
        cached["emotion_probs"] = prediction.copy()
        cached["emotion_label"] = idx_to_label(np.argmax(prediction))
        return

    ema_sparse_coef = ema_coef ** frequency
    cached["emotion_probs"] = ema_sparse_coef * cached["emotion_probs"] + (1 - ema_sparse_coef) * prediction
    ema_top = np.argmax(cached["emotion_probs"])

    top = np.argmax(prediction)

    cached["votes"][top] += 1

    if cached["votes"][top] >= 2:
        cached["emotion_label"] = idx_to_label(ema_top)
        cached["votes"].fill(0)

def hysteresis_exp_smoothing(cached: Dict, prediction: np.ndarray, frequency: int, ema_coef: float = 0.2):
    """
    #### Экспоненциальное сглаживание эмоций + улучшенный счётчик проявления эмоций.
    
    :param cached: Кэшированные данные по треку.
    :type cached: Dict
    :param prediction: Предсказание классификатора.
    :type prediction: np.ndarray
    :param frequency: Частота сглаживания вызова классификатора (для более гладкой картины).
    :type frequency: int
    :param ema_coef: Коэффицент сглажтивания.
    :type ema_coef: float
    """
    num_labels = len(EMOTION_LABELS)
    if (
        prediction.ndim != 1
        or prediction.size != num_labels
        or cached.get("emotion_probs") is None
        or cached["emotion_probs"].size != num_labels
    ):
        cached["emotion_probs"] = prediction[:num_labels].copy()
        cached["emotion_label"] = idx_to_label(int(np.argmax(cached["emotion_probs"])))
        cached["emotion_switch_counter"] = 0
        return

    if cached.get("emotion_probs") is None:
        cached["emotion_probs"] = prediction.copy()
        cached["emotion_label"] = idx_to_label(np.argmax(prediction))
        cached["emotion_switch_counter"] = 0
        return

    ema_sparse_coef = ema_coef ** frequency
    cached["emotion_probs"] = ema_sparse_coef * cached["emotion_probs"] + (1 - ema_sparse_coef) * prediction

    new_label = idx_to_label(np.argmax(cached["emotion_probs"]))

    if new_label != cached["emotion_label"] and prediction.max() > 0.6:
        cached["emotion_switch_counter"] += 1
    else:
        cached["emotion_switch_counter"] = max(0, cached["emotion_switch_counter"] - 1)

    if cached["emotion_switch_counter"] >= 2:
        cached["emotion_label"] = new_label
        cached["emotion_switch_counter"] = 0

def get_emotion_smoothing_strategy(strategy: Literal['ema', 'ema_voting', 'ema_hysteresis']) -> Callable:
    """
    #### Фабрика функций сглаживания эмоционального ряда.
    
    :param strategy: Название стратегии сглаживания эмоций.
    :type strategy: Literal['ema', 'ema_voting', 'ema_hysteresis']
    :return: Функция сглаживания эмоций.
    :rtype: Callable[..., Any]
    """
    return {
        'ema': exponential_smoothing,
        'ema_voting': voting,
        'ema_hysteresis': hysteresis_exp_smoothing,
    }.get(strategy, hysteresis_exp_smoothing)
