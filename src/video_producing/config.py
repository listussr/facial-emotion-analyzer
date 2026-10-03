from dataclasses import dataclass
from typing import Literal, Optional, Tuple, Union

Modality = Literal['video', 'audio']

_KNOWN_MODALITIES = ('video', 'audio')


@dataclass(kw_only=True)
class CameraConfig:
    """
    Конфигурация продюсера медиапотока.

    Args:
        camera_id: Уникальный идентификатор камеры / сессии
        source: Источник (URL, путь к файлу или индекс камеры)
        kafka_servers: Список Kafka брокеров
        partition: Явная партиция. None — Kafka выбирает партицию по ключу `camera_id` (так все потоки одной камеры попадают в партиции с одинаковым номером)
        frame_rate: Желаемый FPS видео
        quality: Качество JPEG сжатия (1-100)
        max_message_size: Максимальный размер сообщения
        total_partitions: Легаси: если задан вместе с `partition`, берётся `partition % total_partitions`
        reconnect_timeout: Таймаут переподключения при обрыве
        max_width: Максимальная ширина кадра
        max_height: Максимальная высота кадра
        stop_on_end: Остановить продюсер по окончании источника (файлы)

        modalities: Запрошенные модальности: ('video',), ('audio',) или ('video', 'audio')
        video_topic: Топик Kafka для кадров
        audio_topic: Топик Kafka для аудиочанков
        audio_sample_rate: Частота дискретизации аудио после ресемплинга, Гц
        audio_channels: Количество аудиоканалов (1 или 2)
        audio_chunk_ms: Длительность одного аудиочанка, мс
        audio_device: Имя или индекс микрофона для живого захвата
        realtime_pacing: Отдавать файлы в темпе реального времени (по PTS)
        strict_modalities: Не запускать сессию, если источник не даёт хотя бы одну
            из запрошенных модальностей (иначе — продолжать с доступными)

        events_topic: Топик Kafka для событий сессии (stream_start / stream_end)
        kafka_media_acks: `acks` для медиа ('1' — свежесть важнее гарантий доставки)
        kafka_media_linger_ms: Сколько мс копить батч медиасообщений
        kafka_media_timeout_ms: Через сколько мс недоставленное медиасообщение
            выбрасывается (после сбоя брокера не приходит волна старых кадров)
        kafka_media_queue_kbytes: Лимит локальной очереди медиасообщений, КБ

        topic_name: Легаси алиас `video_topic`.
    """
    camera_id:         str
    source:            Union[str, int]
    kafka_servers:     str
    partition:         Optional[int] = None
    frame_rate:        int = 10
    quality:           int = 80
    max_message_size:  int = 10485760
    total_partitions:  Optional[int] = None
    reconnect_timeout: int = 5
    max_width:         int = 1280
    max_height:        int = 720
    stop_on_end:       bool = False

    modalities:        Tuple[Modality, ...] = ('video',)
    video_topic:       str = 'raw-video-frames'
    audio_topic:       str = 'raw-audio-chunks'
    audio_sample_rate: int = 16000
    audio_channels:    int = 1
    audio_chunk_ms:    int = 100
    audio_device:      Optional[Union[str, int]] = None
    realtime_pacing:   bool = True
    strict_modalities: bool = False

    events_topic:             str = 'session-events'
    kafka_media_acks:         str = '1'
    kafka_media_linger_ms:    int = 5
    kafka_media_timeout_ms:   int = 2000
    kafka_media_queue_kbytes: int = 65536

    # легаси поле для обратной совместимости
    topic_name: Optional[str] = None

    def __post_init__(self) -> None:
        if self.topic_name:
            self.video_topic = self.topic_name
        self.topic_name = self.video_topic

        self.modalities = tuple(self.modalities)
        if not self.modalities:
            raise ValueError("modalities must not be empty")
        unknown = set(self.modalities) - set(_KNOWN_MODALITIES)
        if unknown:
            raise ValueError(f"Unknown modalities: {sorted(unknown)}")
        if len(set(self.modalities)) != len(self.modalities):
            raise ValueError(f"Duplicate modalities: {self.modalities}")

        if self.partition is not None and self.partition < 0:
            raise ValueError("partition must be non-negative")
        if self.total_partitions is not None and self.total_partitions <= 0:
            raise ValueError("total_partitions must be positive")
        if self.kafka_media_acks not in ('0', '1', 'all', '-1'):
            raise ValueError("kafka_media_acks must be one of '0', '1', 'all', '-1'")
        if self.kafka_media_timeout_ms <= self.kafka_media_linger_ms:
            raise ValueError("kafka_media_timeout_ms must be greater than kafka_media_linger_ms")

        if self.frame_rate <= 0:
            raise ValueError("frame_rate must be positive")

        if self.audio_channels not in (1, 2):
            raise ValueError("audio_channels must be 1 or 2")
        if self.audio_sample_rate <= 0:
            raise ValueError("audio_sample_rate must be positive")
        if not 10 <= self.audio_chunk_ms <= 1000:
            raise ValueError("audio_chunk_ms must be in [10, 1000]")
        if (self.audio_chunk_ms * self.audio_sample_rate) % 1000 != 0:
            raise ValueError(
                "audio_chunk_ms * audio_sample_rate must be divisible by 1000 "
                "(integer number of samples per chunk)"
            )

    @property
    def has_video(self) -> bool:
        return 'video' in self.modalities

    @property
    def has_audio(self) -> bool:
        return 'audio' in self.modalities

    @property
    def audio_samples_per_chunk(self) -> int:
        return self.audio_chunk_ms * self.audio_sample_rate // 1000

    @property
    def effective_partition(self) -> Optional[int]:
        """Партиция для `produce()`; None — выбор по ключу."""
        if self.partition is None:
            return None
        if self.total_partitions:
            return self.partition % self.total_partitions
        return self.partition
