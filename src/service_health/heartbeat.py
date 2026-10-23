"""Сигналы живости сервисов в compacted-топике service-health и файл готовности для healthcheck Docker."""

import logging
import os
import socket
import threading
import time
import msgpack

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from pathlib import Path
from typing import Any
from typing import Dict
from typing import Literal
from typing import Optional

from src.session_config import KeyedPublisher

ServiceState = Literal["starting", "ready"]

DEFAULT_TOPIC = "service-health"

_log = logging.getLogger("service_health")


@dataclass(kw_only=True)
class ServiceBeat:
    """Сигнал экземпляра сервиса: starting — загружает модели, ready — обрабатывает данные."""
    service:    str
    instance:   str
    state:      ServiceState
    host:       str = ""
    started_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    info:       Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.service}@{self.instance}"

    def to_msgpack(self) -> bytes:
        return msgpack.packb(asdict(self), use_bin_type=True)

    @classmethod
    def from_msgpack(cls, raw: bytes) -> "ServiceBeat":
        data  = msgpack.unpackb(raw, raw=False)
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


class Heartbeat:
    """
    Сигнал живости одного экземпляра сервиса.

    Фоновый поток публикует ServiceBeat раз в interval_s; после ready() — с состоянием ready и обновляет
    health_file (его возраст проверяет healthcheck Docker). При остановке запись удаляется tombstone.
    Ошибки Kafka не роняют сервис: сигнал просто пропускается.
    """

    def __init__(self, bootstrap_servers: str, service: str, topic: str = DEFAULT_TOPIC, interval_s: float = 5.0,
                 health_file: Optional[Path] = None, publisher: Optional[KeyedPublisher] = None,
                 instance: Optional[str] = None):
        self.interval_s  = interval_s
        self.health_file = Path(health_file) if health_file else None
        self.beat        = ServiceBeat(service=service, instance=instance or f"{socket.gethostname()}-{os.getpid()}",
                                       state="starting", host=socket.gethostname())
        self._bootstrap  = bootstrap_servers
        self._topic      = topic
        self._publisher  = publisher
        self._stop       = threading.Event()
        self._lock       = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> "Heartbeat":
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="heartbeat", daemon=True)
            self._thread.start()
        return self

    def ready(self, **info: Any) -> None:
        """Сервис загрузил модели и обрабатывает данные."""
        with self._lock:
            self.beat.state = "ready"
            self.beat.info.update(info)
        self.send()
        _log.info(f"{self.beat.service} ready")

    def send(self) -> None:
        with self._lock:
            self.beat.updated_at = time.time()
            key, value = self.beat.key, self.beat.to_msgpack()
            ready = self.beat.state == "ready"
        try:
            self._client().send_async(key, value)
        except Exception as e:
            _log.warning(f"heartbeat not sent: {e}")
        if ready and self.health_file is not None:
            try:
                self.health_file.touch()
            except OSError as e:
                _log.warning(f"health file not updated: {e}")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(self.interval_s + 1)
        try:
            self._client().send(self.beat.key, None, timeout=3.0)
            self._client().close()
        except Exception as e:
            _log.warning(f"heartbeat tombstone not sent: {e}")
        if self.health_file is not None:
            self.health_file.unlink(missing_ok=True)

    def _client(self) -> KeyedPublisher:
        if self._publisher is None:
            self._publisher = KeyedPublisher(self._bootstrap, self._topic)
        return self._publisher

    def _run(self) -> None:
        while not self._stop.is_set():
            self.send()
            self._stop.wait(self.interval_s)


def heartbeat_from_env(service: str) -> Heartbeat:
    """
    Сигнал живости по переменным окружения и сразу запущенный.

    KAFKA_BOOTSTRAP_SERVERS, TOPIC_SERVICE_HEALTH (service-health), HEARTBEAT_INTERVAL_S (5),
    HEALTH_FILE (/tmp/affectra-ready в Docker; не задан — без файла).

    Args:
        service (str): Имя сервиса: ingest, consumer, audio-consumer, fusion, visualizer, archiver.

    Returns:
        Heartbeat: Запущенный сигнал в состоянии starting.
    """
    return Heartbeat(
        os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        service,
        topic=os.getenv("TOPIC_SERVICE_HEALTH", DEFAULT_TOPIC),
        interval_s=float(os.getenv("HEARTBEAT_INTERVAL_S", "5")),
        health_file=os.getenv("HEALTH_FILE") or None,
    ).start()
