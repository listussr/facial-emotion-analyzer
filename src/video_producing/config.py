from dataclasses import dataclass
from typing import Union

@dataclass
class CameraConfig:
    """
   
    Args:
        camera_id: Уникальный идентификатор камеры
        source: Источник видео (URL, путь к файлу или индекс камеры)
        kafka_servers: Список Kafka брокеров
        topic_name: Название топика Kafka
        partition: Целевая партиция
        frame_rate: Желаемый FPS
        quality: Качество JPEG сжатия (1-100)
        max_message_size: Максимальный размер сообщения
        total_partitions: Общее количество партиций в топике
        reconnect_timeout: Таймаут переподключения при обрыве
        max_width: Максимальная ширина кадра
        max_height: Максимальная высота кадра
    """
    camera_id: str
    source: Union[str, int]
    kafka_servers: str
    topic_name: str
    partition: int
    frame_rate: int = 10
    quality: int = 80
    max_message_size: int = 10485760
    total_partitions: int = 5
    reconnect_timeout: int = 5
    max_width: int = 1280
    max_height: int = 720
    # Если True — продюсер завершается, как только источник перестал отдавать
    # кадры (конец файла). Если False — пытается переподключиться (поведение
    # для IP-камер). Для загруженных пользователем видео ставим True.
    stop_on_end: bool = False
