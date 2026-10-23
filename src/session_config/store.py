"""Локальные копии compacted-топиков: конфиги сессий (session-config) и общий механизм чтения с начала."""

import logging
import threading
import uuid

from dataclasses import dataclass
from dataclasses import replace
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from .contracts import SessionConfig
from .topic import DEFAULT_TOPIC

ChangeCallback = Callable[[str, Optional[Any], Optional[Any]], None]

_log = logging.getLogger("session_config.store")


class StoreNotReadyError(RuntimeError):
    """Не удалось дочитать топик до конца за ready_timeout."""


@dataclass
class StoreStats:
    """Счётчики хранилища для логов и отладки."""
    configs:         int = 0
    applied:         int = 0
    stale_ignored:   int = 0
    tombstones:      int = 0
    decode_errors:   int = 0
    callback_errors: int = 0


class CompactedTopicStore:
    """
    Словарь «ключ -> значение» по compacted-топику.

    Все партиции назначаются вручную с начала; готовность — дочитан конец, бывший на момент старта.
    Наследники задают _decode и _is_newer. Колбэк on_change(key, old, new) вызывается из фонового потока,
    new=None — запись удалена tombstone.
    """

    kind = "record"

    def __init__(self, bootstrap_servers: str, topic: str, on_change: Optional[ChangeCallback] = None,
                 poll_timeout: float = 0.5):
        self._bootstrap    = bootstrap_servers
        self._topic        = topic
        self._on_change    = on_change
        self._poll_timeout = poll_timeout

        self._configs: Dict[str, Any] = {}
        self._lock  = threading.Lock()
        self._cond  = threading.Condition(self._lock)
        self._stats = StoreStats()

        self._ready = threading.Event()
        self._stop  = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._consumer = None
        self._targets: Dict[int, int] = {}
        self._done: set = set()

    def start(self, ready_timeout: float = 10.0) -> None:
        """
        Запуск фонового потока с ожиданием готовности.

        Args:
            ready_timeout (float, optional): Время ожидания готовности, с. Defaults to 10.0.

        Raises:
            StoreNotReadyError: Не готово за ready_timeout; поток продолжает работу.
        """
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"store-{self._topic}", daemon=True)
        self._thread.start()
        if not self._ready.wait(ready_timeout):
            raise StoreNotReadyError(f"topic {self._topic!r} not loaded in {ready_timeout}s")

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)

    def set_on_change(self, on_change: Optional[ChangeCallback]) -> None:
        self._on_change = on_change

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    def __enter__(self) -> "CompactedTopicStore":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.stop()
        return False

    def get(self, camera_id: str) -> Optional[Any]:
        with self._lock:
            return self._configs.get(camera_id)

    def wait_for(self, camera_id: str, timeout: float,
                 predicate: Optional[Callable[[Any], bool]] = None) -> Optional[Any]:
        """
        Запись с ожиданием её появления: первый кадр может прийти раньше конфига.

        Args:
            camera_id (str)               : Ключ записи.
            timeout   (float)             : Максимальное ожидание, с.
            predicate (Callable, optional): Ждать, пока запись не станет ему удовлетворять. Defaults to None.

        Returns:
            Optional[Any]: Запись или None.
        """
        def ready() -> bool:
            value = self._configs.get(camera_id)
            return value is not None and (predicate is None or predicate(value))

        with self._cond:
            self._cond.wait_for(ready, timeout)
            return self._configs.get(camera_id)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._configs)

    def stats(self) -> StoreStats:
        with self._lock:
            return replace(self._stats, configs=len(self._configs))

    def _run(self) -> None:
        from confluent_kafka import Consumer, KafkaError

        self._consumer = Consumer({
            "bootstrap.servers": self._bootstrap,
            "group.id": f"store-{self._topic}-{uuid.uuid4()}",
            "enable.auto.commit": False,
            "enable.partition.eof": True,
            "reconnect.backoff.max.ms": 1000,
        })
        try:
            while not self._stop.is_set():
                try:
                    self._targets = self._assign_all_partitions()
                    break
                except Exception as e:
                    _log.warning(f"Cannot assign {self._topic!r} partitions: {e}; retrying")
                    self._stop.wait(1.0)
            self._check_ready()

            while not self._stop.is_set():
                msg = self._consumer.poll(self._poll_timeout)
                if msg is None:
                    continue
                if msg.error():
                    if msg.error().code() == KafkaError._PARTITION_EOF:
                        self._update_readiness(msg.partition(), msg.offset() - 1)
                    else:
                        _log.warning(f"Kafka error in {self._topic!r} store: {msg.error()}")
                    continue
                self._apply(msg.key(), msg.value())
                self._update_readiness(msg.partition(), msg.offset())
        finally:
            try:
                self._consumer.close()
            except Exception:
                _log.exception(f"{self._topic!r} store consumer close failed")

    def _assign_all_partitions(self) -> Dict[int, int]:
        """
        Назначение всех партиций топика с начала.

        Запрашивается список всех топиков: запрос конкретного создал бы его без compaction.

        Returns:
            Dict[int, int]: Партиция -> high watermark на момент старта.
        """
        from confluent_kafka import OFFSET_BEGINNING, TopicPartition

        metadata = self._consumer.list_topics(timeout=5.0).topics.get(self._topic)
        if metadata is None or metadata.error is not None or not metadata.partitions:
            raise RuntimeError(f"topic {self._topic!r} not found")

        targets: Dict[int, int] = {}
        for p in metadata.partitions:
            low, high = self._consumer.get_watermark_offsets(
                TopicPartition(self._topic, p), timeout=5.0, cached=False
            )
            targets[p] = high
            if high <= low:
                self._done.add(p)

        self._consumer.assign([TopicPartition(self._topic, p, OFFSET_BEGINNING) for p in targets])
        return targets

    def _apply(self, key: Optional[bytes], value: Optional[bytes]) -> None:
        """
        Применение одного сообщения: tombstone удаляет запись, более новая заменяет её.

        Args:
            key   (Optional[bytes]): Ключ, camera_id.
            value (Optional[bytes]): Запись в msgpack или None.
        """
        if not key:
            with self._lock:
                self._stats.decode_errors += 1
            _log.warning(f"{self._topic!r} message without key ignored")
            return
        camera_id = key.decode("utf-8", errors="replace")

        if value is None:
            with self._lock:
                old = self._configs.pop(camera_id, None)
                self._stats.tombstones += 1
            if old is not None:
                self._notify(camera_id, old, None)
            return

        try:
            new = self._decode(value)
        except Exception as e:
            with self._lock:
                self._stats.decode_errors += 1
            _log.warning(f"Broken {self.kind} for {camera_id!r}: {e}")
            return

        with self._lock:
            old = self._configs.get(camera_id)
            if not self._is_newer(new, old):
                self._stats.stale_ignored += 1
                return
            self._configs[camera_id] = new
            self._stats.applied += 1
        self._notify(camera_id, old, new)

    def _update_readiness(self, partition: int, offset: int) -> None:
        if self._ready.is_set():
            return
        target = self._targets.get(partition)
        if target is not None and offset + 1 >= target:
            self._done.add(partition)
        self._check_ready()

    def _check_ready(self) -> None:
        if self._targets and self._done >= set(self._targets):
            if not self._ready.is_set():
                _log.info(f"{self.kind.capitalize()} store ready: {len(self.snapshot())} records")
            self._ready.set()

    def _decode(self, value: bytes) -> Any:
        raise NotImplementedError

    def _is_newer(self, new: Any, old: Optional[Any]) -> bool:
        return True

    def _notify(self, camera_id: str, old: Optional[Any], new: Optional[Any]) -> None:
        with self._cond:
            self._cond.notify_all()
        if self._on_change is None:
            return
        try:
            self._on_change(camera_id, old, new)
        except Exception:
            with self._lock:
                self._stats.callback_errors += 1
            _log.exception(f"on_change failed for {camera_id!r}")


class SessionConfigStore(CompactedTopicStore):
    """Хранилище конфигов сессий: более старая ревизия никогда не заменяет более новую."""

    kind = "session config"

    def __init__(self, bootstrap_servers: str, topic: str = DEFAULT_TOPIC,
                 on_change: Optional[ChangeCallback] = None, poll_timeout: float = 0.5):
        super().__init__(bootstrap_servers, topic, on_change=on_change, poll_timeout=poll_timeout)

    def _decode(self, value: bytes) -> SessionConfig:
        return SessionConfig.from_msgpack(value)

    def _is_newer(self, new: SessionConfig, old: Optional[SessionConfig]) -> bool:
        return new.is_newer_than(old)
