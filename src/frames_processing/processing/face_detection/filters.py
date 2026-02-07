import numpy as np
import cv2

def fast_face_filter(gray: np.ndarray, width: int, height: int) -> bool:
    """
    #### Фильтр для определения, есть ли на ЧБ изображении лицо.
    
    Грубая аппроксимация применения оператора Собеля и вычисления среднего значения <br>
    градиентов по вертикали и по горизонтали.

    Если градиенты слишком ровные, то изображение не может быть лицом.

    :param gray: Исследуемое изображение в ЧБ. 
    :type gray: np.ndarray
    :param width: Ширина изображения.
    :type width: int
    :param height: Высота изображения.
    :type height: int
    :return: Флаг наличия изображения.
    :rtype: bool
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
    #### Более продвинутый метод определения наличия лица на изображении.

    Используются 3 условия. Для наличия лица достаточно выполнения 2/3.

    1) В среднем горизонтальные градиенты в верхней части изображения больше порогового числа.
    2) Первый центральные момент центра изображения больше 0.9 * первый центральный момент краёв изображения.
    3) В среднем вертикальные градиенты изображения больше порогового значения.
    
    :param gray: Исследуемое изображение в ЧБ. 
    :type gray: np.ndarray
    :param width: Ширина изображения.
    :type width: int
    :param height: Высота изображения.
    :type height: int
    :return: Флаг наличия изображения.
    :rtype: bool
    """
    if max(width, height) > 96:
        gray = cv2.resize(gray, (96, 96), interpolation=cv2.INTER_AREA)

    votes = 0

    upper = gray[: gray.shape[0] // 2]
    if np.mean(np.abs(upper[:, 1:] - upper[:, :-1])) > 12:
        votes += 1

    center = gray[:, gray.shape[1]//3 : 2*gray.shape[1]//3]
    sides = np.hstack([gray[:, :gray.shape[1] // 3], gray[:, 2 * gray.shape[1] // 3:]])
    if np.mean(np.abs(center - center.mean())) > 0.9 * np.mean(np.abs(sides - sides.mean())):
        votes += 1

    if np.mean(np.abs(gray[1:] - gray[:-1])) > 8:
        votes += 1

    return votes >= 2
