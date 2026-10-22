"""Конфигурация продюсера медиапотока камеры или файла."""

from dataclasses import dataclass
from typing import Literal
from typing import Optional
from typing import Tuple
from typing import Union

Modality = Literal["video", "audio"]

_KNOWN_MODALITIES = ("video", "audio")


@dataclass(kw_only=True)
class CameraConfig:
    """
    Конфигурация продюсера медиапотока.

    Партиция по умолчанию выбирается Kafka по ключу camera_id, поэтому все потоки камеры попадают
    в партиции с одним номером. Медиа отправляются с acks=1 и коротким таймаутом доставки:
    свежесть важнее гарантий. topic_name — легаси-алиас video_topic.
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

    modalities:        Tuple[Modality, ...] = ("video",)
    video_topic:       str = "raw-video-frames"
    audio_topic:       str = "raw-audio-chunks"
    audio_sample_rate: int = 16000
    audio_channels:    int = 1
    audio_chunk_ms:    int = 100
    audio_device:      Optional[Union[str, int]] = None
    realtime_pacing:   bool = True
    strict_modalities: bool = False

    events_topic:             str = "session-events"
    kafka_media_acks:         str = "1"
    kafka_media_linger_ms:    int = 5
    kafka_media_timeout_ms:   int = 2000
    kafka_media_queue_kbytes: int = 65536

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
        if self.kafka_media_acks not in ("0", "1", "all", "-1"):
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
        return "video" in self.modalities

    @property
    def has_audio(self) -> bool:
        return "audio" in self.modalities

    @property
    def audio_samples_per_chunk(self) -> int:
        return self.audio_chunk_ms * self.audio_sample_rate // 1000

    @property
    def effective_partition(self) -> Optional[int]:
        if self.partition is None:
            return None
        if self.total_partitions:
            return self.partition % self.total_partitions
        return self.partition
