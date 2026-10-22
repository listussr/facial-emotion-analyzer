"""Быстрые эвристики «похоже ли изображение на лицо» по градиентам."""

import cv2
import numpy as np


def fast_face_filter(gray: np.ndarray, width: int, height: int) -> bool:
    """
    Грубая проверка лица: слишком ровные градиенты по вертикали и горизонтали — не лицо.

    Args:
        gray   (np.ndarray): Кроп в оттенках серого.
        width  (int)       : Ширина рамки.
        height (int)       : Высота рамки.

    Returns:
        bool: Похоже ли на лицо.
    """
    if max(width, height) > 64:
        gray = cv2.resize(gray, (64, 64), cv2.INTER_AREA)

    upper = gray[: gray.shape[0] // 2]
    if np.mean(np.abs(upper[:, 1:] - upper[:, :-1])) < 10:
        return False

    if np.mean(np.abs(gray[1:] - gray[:-1])) < 6:
        return False

    return True


def pass_face_filters(gray: np.ndarray, width: int, height: int) -> bool:
    """
    Проверка лица голосованием 2 из 3: горизонтальные градиенты верха, контраст центра против краёв,
    вертикальные градиенты.

    Args:
        gray   (np.ndarray): Кроп в оттенках серого.
        width  (int)       : Ширина рамки.
        height (int)       : Высота рамки.

    Returns:
        bool: Похоже ли на лицо.
    """
    if max(width, height) > 96:
        gray = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)

    votes = 0

    upper = gray[: gray.shape[0] // 2]
    if np.mean(np.abs(upper[:, 1:] - upper[:, :-1])) > 12:
        votes += 1

    center = gray[:, gray.shape[1]//3 : 2*gray.shape[1]//3]
    sides  = np.hstack([gray[:, :gray.shape[1] // 3], gray[:, 2 * gray.shape[1] // 3:]])
    if np.mean(np.abs(center - center.mean())) > 0.9 * np.mean(np.abs(sides - sides.mean())):
        votes += 1

    if np.mean(np.abs(gray[1:] - gray[:-1])) > 8:
        votes += 1

    return votes >= 2
