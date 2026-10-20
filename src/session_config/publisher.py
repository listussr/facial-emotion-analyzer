from typing import Any, Dict, Optional

from confluent_kafka import Producer

from .contracts import SessionConfig
from .topic import DEFAULT_TOPIC


class PublishError(RuntimeError):
    """Брокер не подтвердил запись за отведённое время."""


class SessionConfigPublisher:
    def __init__(self, bootstrap_servers: str, topic: str = DEFAULT_TOPIC):
        """
        Создаёт Kafka-продюсер: acks=all, идемпотентность, ключ — camera_id,
        тот же партиционер, что у медиа (murmur2_random).
        """
        self.topic = topic
        self.producer = Producer({
            'bootstrap.servers': bootstrap_servers,
            'acks': 'all',
            'enable.idempotence': True,
            'partitioner': 'murmur2_random',
            'linger.ms': 0,
            'reconnect.backoff.max.ms': 1000,
        })
        self._closed = False

    def publish(self, config: SessionConfig, timeout: float = 5.0) -> None:
        """
        Записать конфиг и дождаться подтверждения.

        produce → flush(timeout) → проверить результат колбэка доставки.
        Не подтверждено / ошибка доставки → PublishError.
        """
        self._produce_sync(config.camera_id, config.to_msgpack(), timeout)

    def delete(self, camera_id: str, timeout: float = 5.0) -> None:
        """
        Удалить конфиг сессии: tombstone (ключ camera_id, значение None).
        Читатели убирают запись сразу, Kafka — при уплотнении.
        Не подтверждено → PublishError.
        """
        self._produce_sync(camera_id, None, timeout)

    def close(self, timeout: float = 5.0) -> None:
        """Дослать очередь и освободить клиента."""
        if self._closed:
            return
        self._closed = True
        self.producer.flush(timeout)

    def __enter__(self) -> 'SessionConfigPublisher':
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def _produce_sync(self, camera_id: str, value: Optional[bytes], timeout: float) -> None:
        """Общая часть publish/delete: отправка, ожидание и разбор результата доставки."""
        result: Dict[str, Any] = {'done': False, 'error': None}

        def on_delivery(err, msg) -> None:
            result['done'] = True
            result['error'] = err

        self.producer.produce(self.topic, key=camera_id.encode('utf-8'), value=value,
                              on_delivery=on_delivery)
        remaining = self.producer.flush(timeout)
        what = 'tombstone' if value is None else 'config'
        if remaining > 0 or not result['done']:
            raise PublishError(f"{what} for {camera_id!r} was not confirmed in {timeout}s")
        if result['error'] is not None:
            raise PublishError(f"{what} for {camera_id!r} failed: {result['error']}")
