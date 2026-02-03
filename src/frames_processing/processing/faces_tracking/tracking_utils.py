from typing import Dict, List, Tuple
from deep_sort_realtime.deepsort_tracker import DeepSort

def initialize_deepsort(deep_sort_settings: Dict) -> DeepSort:
    """
    #### Инициализация DeepSort только с важными параметрами.
    
    :param deep_sort_settings: Параметры DeepSort в словаре.
    :type deep_sort_settings: Dict
    :return: Инициализированный класс.
    :rtype: DeepSort
    """
    max_age = deep_sort_settings.get("max_age", 70)
    n_init = deep_sort_settings.get("n_init", 5)
    nn_budget = deep_sort_settings.get("nn_budget", 100)
    max_cosine_distance = deep_sort_settings.get("max_cosine_distance", 0.3)
    embedder_gpu = deep_sort_settings.get("embedder_gpu", False)
    embedder_model_name = deep_sort_settings.get("embedder_model_name", 'mobilenet')
    return DeepSort(
        max_age=max_age,
        n_init=n_init,
        max_cosine_distance=max_cosine_distance,
        nn_budget=nn_budget,
        embedder=embedder_model_name,
        embedder_gpu=embedder_gpu,
    )

def represent_ltrb(track) -> Tuple:
    """
    #### Вырез координат лица из трекера.
    
    :param track: Трек лица.
    :return: Кортеж (координата x, координата y, ширина, высота)
    :rtype: Tuple
    """
    ltrb = track.to_ltrb()
    return int(ltrb[0]), int(ltrb[1]), int(ltrb[2] - ltrb[0]), int(ltrb[3] - ltrb[1])

def represent_detections(detections: List[Tuple]) -> List[List]:
    """
    Docstring для represent_detections
    
    :param detections: Список кортежей с детекциями лиц с кадра - [(x_left, y_left, width, height, conf),].
    :type detections: List[Tuple]
    :return: Список в форме, подходящей к передаче в DeepSort - [[x_left, y_left, width, height], conf, 'face'], ].
    :rtype: List[List]
    """
    repr_detections = []
    for (x, y, w, h, conf) in detections:
        repr_detections.append([[x, y, w, h], conf, 'face'])
    return repr_detections
