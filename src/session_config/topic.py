"""Создание топика session-config с уплотнением и проверка настроек существующего."""

import logging

from dataclasses import dataclass
from dataclasses import field
from typing import Dict
from typing import List
from typing import Optional

DEFAULT_TOPIC = "session-config"


@dataclass(kw_only=True)
class TopicSettings:
    """Параметры топика конфигов сессий."""
    name:                      str = DEFAULT_TOPIC
    partitions:                int = 1
    replication_factor:        int = 1
    segment_ms:                int = 3_600_000
    delete_retention_ms:       int = 86_400_000
    min_cleanable_dirty_ratio: float = 0.1
    extra_configs:             Dict[str, str] = field(default_factory=dict)

    def to_topic_configs(self) -> Dict[str, str]:
        return {
            "cleanup.policy": "compact",
            "segment.ms": str(self.segment_ms),
            "delete.retention.ms": str(self.delete_retention_ms),
            "min.cleanable.dirty.ratio": str(self.min_cleanable_dirty_ratio),
            **{k: str(v) for k, v in self.extra_configs.items()},
        }


def _admin(bootstrap_servers: str):
    from confluent_kafka.admin import AdminClient
    return AdminClient({"bootstrap.servers": bootstrap_servers})


def ensure_topic(bootstrap_servers: str, settings: Optional[TopicSettings] = None,
                 timeout: float = 10.0) -> bool:
    """
    Создание топика, если его нет; у существующего только сверяются настройки.

    Args:
        bootstrap_servers (str)                    : Адрес Kafka.
        settings          (TopicSettings, optional): Параметры топика. Defaults to None.
        timeout           (float, optional)        : Таймаут операций, с. Defaults to 10.0.

    Returns:
        bool: True — топик создан, False — уже существовал.
    """
    from confluent_kafka import KafkaError, KafkaException
    from confluent_kafka.admin import NewTopic

    settings = settings or TopicSettings()
    admin    = _admin(bootstrap_servers)

    if settings.name not in admin.list_topics(timeout=timeout).topics:
        new_topic = NewTopic(
            settings.name,
            num_partitions=settings.partitions,
            replication_factor=settings.replication_factor,
            config=settings.to_topic_configs(),
        )
        future = admin.create_topics([new_topic], operation_timeout=timeout)[settings.name]
        try:
            future.result(timeout=timeout)
            logging.info(f"Created topic {settings.name}")
            return True
        except KafkaException as e:
            if e.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise

    diffs = check_topic(bootstrap_servers, settings, timeout)
    if diffs:
        logging.warning(f"Topic {settings.name} exists with different settings: {diffs}")
    return False


def check_topic(bootstrap_servers: str, settings: Optional[TopicSettings] = None,
                timeout: float = 10.0) -> List[str]:
    """
    Сравнение фактических настроек топика с ожидаемыми.

    Запрашивается список всех топиков: запрос метаданных конкретного топика создал бы его при auto.create.

    Args:
        bootstrap_servers (str)                    : Адрес Kafka.
        settings          (TopicSettings, optional): Ожидаемые параметры. Defaults to None.
        timeout           (float, optional)        : Таймаут операций, с. Defaults to 10.0.

    Raises:
        RuntimeError: Топика нет.

    Returns:
        List[str]: Расхождения; пустой список — всё совпадает.
    """
    from confluent_kafka.admin import ConfigResource

    settings = settings or TopicSettings()
    admin    = _admin(bootstrap_servers)

    metadata = admin.list_topics(timeout=timeout).topics.get(settings.name)
    if metadata is None or metadata.error is not None:
        raise RuntimeError(f"Topic {settings.name} does not exist")

    diffs: List[str] = []
    if len(metadata.partitions) != settings.partitions:
        diffs.append(f"partitions: expected {settings.partitions}, got {len(metadata.partitions)}")

    resource = ConfigResource("topic", settings.name)
    actual   = admin.describe_configs([resource])[resource].result(timeout=timeout)
    for key, expected in settings.to_topic_configs().items():
        entry = actual.get(key)
        got   = entry.value if entry is not None else None
        if not _same(expected, got):
            diffs.append(f"{key}: expected {expected}, got {got}")
    return diffs


def _same(expected: str, got: Optional[str]) -> bool:
    if got is None:
        return False
    if expected == got:
        return True
    try:
        return float(expected) == float(got)
    except ValueError:
        return False
