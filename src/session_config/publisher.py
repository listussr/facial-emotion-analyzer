"""Запись конфигов сессий в топик session-config с подтверждением доставки."""

from typing import Any
from typing import Dict
from typing import Optional

from confluent_kafka import Producer

from .contracts import SessionConfig
from .topic import DEFAULT_TOPIC


class PublishError(RuntimeError):
    """Брокер не подтвердил запись за отведённое время."""


class SessionConfigPublisher:
    """Продюсер конфигов: acks=all, идемпотентность, ключ — camera_id, партиционер как у медиа."""

    def __init__(self, bootstrap_servers: str, topic: str = DEFAULT_TOPIC):
        self.topic    = topic
        self.producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "acks": "all",
            "enable.idempotence": True,
            "partitioner": "murmur2_random",
            "linger.ms": 0,
            "reconnect.backoff.max.ms": 1000,
        })
        self._closed = False

    def publish(self, config: SessionConfig, timeout: float = 5.0) -> None:
        """
        Запись конфига с ожиданием подтверждения.

        Args:
            config  (SessionConfig)  : Конфиг сессии.
            timeout (float, optional): Время ожидания подтверждения, с. Defaults to 5.0.

        Raises:
            PublishError: Запись не подтверждена.
        """
        self._produce_sync(config.camera_id, config.to_msgpack(), timeout)

    def delete(self, camera_id: str, timeout: float = 5.0) -> None:
        """
        Удаление конфига сессии записью tombstone.

        Args:
            camera_id (str)            : Идентификатор сессии.
            timeout   (float, optional): Время ожидания подтверждения, с. Defaults to 5.0.

        Raises:
            PublishError: Запись не подтверждена.
        """
        self._produce_sync(camera_id, None, timeout)

    def close(self, timeout: float = 5.0) -> None:
        if self._closed:
            return
        self._closed = True
        self.producer.flush(timeout)

    def __enter__(self) -> "SessionConfigPublisher":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def _produce_sync(self, camera_id: str, value: Optional[bytes], timeout: float) -> None:
        result: Dict[str, Any] = {"done": False, "error": None}

        def on_delivery(err, msg) -> None:
            result["done"]  = True
            result["error"] = err

        self.producer.produce(self.topic, key=camera_id.encode("utf-8"), value=value,
                              on_delivery=on_delivery)
        remaining = self.producer.flush(timeout)
        what      = "tombstone" if value is None else "config"
        if remaining > 0 or not result["done"]:
            raise PublishError(f"{what} for {camera_id!r} was not confirmed in {timeout}s")
        if result["error"] is not None:
            raise PublishError(f"{what} for {camera_id!r} failed: {result['error']}")
