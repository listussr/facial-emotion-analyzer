"""Сервис приёма медиа: запуск и остановка продюсеров сессий по командам, статусы сессий в Kafka."""

import logging
import shutil
import threading
import time
import uuid

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Callable
from typing import Dict
from typing import Optional

from src.session_config import KeyedPublisher
from src.session_config import SessionConfigPublisher
from src.video_producing import CameraConfig
from src.video_producing import CameraProducer
from src.video_producing.probe import check_modalities
from src.video_producing.probe import probe_source
from src.video_producing.sources import browser_source
from .contracts import ACTIVE_STATES
from .contracts import SessionCommand
from .contracts import SessionSpec
from .contracts import SessionStatus

_log = logging.getLogger("ingest")


@dataclass
class _Run:
    spec:       SessionSpec
    producer:   Optional[CameraProducer] = None
    state:      str = "starting"
    error:      Optional[str] = None
    local_file: Optional[Path] = None
    stopping:   bool = False
    ended_at:   Optional[float] = None


class IngestService:
    """
    Продюсеры сессий одного узла приёма.

    Команды читаются из session-commands (группа узлов приёма делит их по camera_id), подготовка источника —
    скачивание загрузки из хранилища или проверка потока — идёт в пуле потоков, чтобы не задерживать другие
    команды. Статус каждой сессии публикуется в session-status раз в status_interval_s и при смене состояния.
    После конца сессии удаляются её конфиг и локальная копия файла.
    """

    def __init__(self, kafka: Dict[str, str], storage=None, cache_dir: Path = Path("data/ingest_cache"),
                 status_publisher: Optional[KeyedPublisher] = None,
                 config_publisher: Optional[SessionConfigPublisher] = None,
                 producer_factory: Callable[[CameraConfig], Any] = CameraProducer,
                 status_interval_s: float = 1.0, max_command_age_s: float = 60.0, workers: int = 4,
                 consume: bool = True, advertised_address: str = ""):
        self.kafka             = kafka
        self.storage           = storage
        self.cache_dir         = Path(cache_dir)
        self.producer_factory  = producer_factory
        self.status_interval_s = status_interval_s
        self.max_command_age_s = max_command_age_s
        self.ingest_id         = f"ingest-{uuid.uuid4().hex[:6]}"
        self.address           = advertised_address
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self._status = status_publisher or KeyedPublisher(kafka["bootstrap_servers"], kafka["status_topic"])
        self._config = config_publisher or SessionConfigPublisher(kafka["bootstrap_servers"], kafka["config_topic"])
        self._runs: Dict[str, _Run] = {}
        self._lock      = threading.Lock()
        self._executor  = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="ingest-start")
        self._stop      = threading.Event()
        self._loop_done = threading.Event()
        self._consumer  = None
        if consume:
            from confluent_kafka import Consumer

            self._consumer = Consumer({
                "bootstrap.servers": kafka["bootstrap_servers"],
                "group.id": kafka.get("group_id", "ingest-v1"),
                "auto.offset.reset": "latest",
                "enable.auto.commit": True,
                "partition.assignment.strategy": "range",
            })
            self._consumer.subscribe([kafka["commands_topic"]])

    def handle_command(self, command: SessionCommand, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if now - command.issued_at > self.max_command_age_s:
            _log.warning(f"[{command.camera_id}] {command.action} command is {now - command.issued_at:.0f} s old; ignored")
            return
        if command.action == "start" and command.spec:
            self.start(SessionSpec.from_dict(command.spec))
        elif command.action == "stop":
            self.stop(command.camera_id)

    def start(self, spec: SessionSpec) -> None:
        """
        Запуск сессии: статус starting сразу, подготовка источника и продюсер — в пуле потоков.

        Args:
            spec (SessionSpec): Описание сессии.
        """
        with self._lock:
            if spec.camera_id in self._runs:
                _log.warning(f"[{spec.camera_id}] already running on this node; start ignored")
                return
            run = self._runs[spec.camera_id] = _Run(spec=spec)
        if spec.kind == "browser":
            self._register_browser(spec)
        self._publish(run)
        self._executor.submit(self._launch, run)

    def _register_browser(self, spec: SessionSpec) -> None:
        defaults = CameraConfig(camera_id=spec.camera_id, source="", kafka_servers="")
        browser_source.register(browser_source.BrowserSource(
            spec.camera_id, has_video="video" in spec.modalities, has_audio="audio" in spec.modalities,
            sample_rate=defaults.audio_sample_rate, channels=defaults.audio_channels,
            samples_per_chunk=defaults.audio_samples_per_chunk,
        ))

    def _launch(self, run: _Run) -> None:
        spec = run.spec
        try:
            source = self._prepare_source(run)
            if run.stopping:
                if run.local_file is not None:
                    run.local_file.unlink(missing_ok=True)
                return
            config = CameraConfig(
                camera_id=spec.camera_id,
                source=source,
                kafka_servers=self.kafka["bootstrap_servers"],
                video_topic=self.kafka.get("video_topic", "raw-video-frames"),
                audio_topic=self.kafka.get("audio_topic", "raw-audio-chunks"),
                events_topic=self.kafka.get("events_topic", "session-events"),
                modalities=tuple(spec.modalities),
                frame_rate=spec.frame_rate,
                quality=spec.quality,
                max_width=spec.max_width,
                max_height=spec.max_height,
                max_message_size=5_000_000,
                reconnect_timeout=5,
                stop_on_end=(spec.kind == "upload"),
            )
            producer = self.producer_factory(config)
            producer.start()
            run.producer = producer
            _log.info(f"[{spec.camera_id}] {spec.kind} session started: {spec.name}")
        except Exception as e:
            _log.error(f"[{spec.camera_id}] cannot start: {e}")
            run.state, run.error, run.ended_at = "error", str(e)[:500], time.time()
        self._publish(run)

    def _prepare_source(self, run: _Run) -> str:
        """
        Источник продюсера: локальная копия загрузки, проверенный поток или зарегистрированный браузер.

        Raises:
            ValueError: Источник не открывается или в нём нет запрошенных дорожек.
        """
        spec = run.spec
        if spec.kind == "browser":
            return browser_source.BROWSER_PREFIX + spec.camera_id
        if spec.kind == "upload":
            if self.storage is None or not spec.upload_key:
                raise ValueError("upload storage is not configured")
            path = self.cache_dir / f"{spec.camera_id}{Path(spec.upload_key).suffix}"
            self.storage.download_file(spec.upload_key, path)
            run.local_file = path
            return str(path)
        source = int(spec.source) if spec.source.isdigit() else spec.source
        check_modalities(probe_source(source), spec.modalities)
        return spec.source

    def stop(self, camera_id: str) -> bool:
        """
        Остановка сессии: источник браузера закрывается сразу, продюсер останавливается в фоне.

        Args:
            camera_id (str): Идентификатор сессии.

        Returns:
            bool: Сессия была на этом узле.
        """
        with self._lock:
            run = self._runs.get(camera_id)
        if run is None or run.stopping:
            return False
        run.stopping = True
        browser = browser_source.unregister(camera_id)
        if browser is not None:
            browser.finish()
        self._executor.submit(self._stop_producer, run)
        return True

    def _stop_producer(self, run: _Run) -> None:
        if run.producer is not None:
            try:
                run.producer.stop()
            except Exception:
                _log.exception(f"[{run.spec.camera_id}] producer stop failed")
        if run.state in ACTIVE_STATES:
            run.state = "done" if run.spec.kind == "upload" else "offline"
        run.ended_at = run.ended_at or time.time()

    def _state_of(self, run: _Run) -> str:
        producer = run.producer
        if run.state == "error" or run.ended_at is not None:
            return run.state
        if producer is None:
            return "starting"
        alive = bool(producer.is_running) and bool(producer.thread and producer.thread.is_alive())
        if alive:
            return "live" if run.spec.kind != "upload" else "running"
        if producer.is_running:
            run.error = run.error or "producer thread died"
            return "error"
        if run.spec.kind == "upload":
            return "done" if getattr(producer, "end_reason", "eof") != "error" else "error"
        return "error" if getattr(producer, "end_reason", None) == "error" else "offline"

    def _status_of(self, run: _Run) -> SessionStatus:
        producer = run.producer
        stats: Dict[str, Any] = {}
        if producer is not None:
            try:
                stats = producer.get_stats() or {}
            except Exception:
                stats = {}
        return SessionStatus(
            camera_id=run.spec.camera_id,
            state=self._state_of(run),
            spec=run.spec.to_dict(),
            stats=stats,
            frames_sent=int(getattr(producer, "frame_count", 0) or 0),
            errors=int(getattr(producer, "error_count", 0) or 0),
            error=run.error,
            ingest_id=self.ingest_id,
            ingest_address=self.address,
            ended_at=run.ended_at,
        )

    def _publish(self, run: _Run) -> SessionStatus:
        status = self._status_of(run)
        try:
            self._status.send_async(run.spec.camera_id, status.to_msgpack())
        except Exception:
            _log.exception(f"[{run.spec.camera_id}] status publish failed")
        return status

    def tick(self) -> None:
        """Статусы идущих сессий; завершённые публикуются последний раз и освобождаются."""
        with self._lock:
            runs = list(self._runs.values())
        for run in runs:
            if self._state_of(run) not in ACTIVE_STATES and run.ended_at is None:
                run.state, run.ended_at = self._state_of(run), time.time()
            status = self._publish(run)
            if not status.active:
                self._finish(run)

    def _finish(self, run: _Run) -> None:
        camera_id = run.spec.camera_id
        with self._lock:
            self._runs.pop(camera_id, None)
        browser_source.unregister(camera_id)
        if run.local_file is not None:
            run.local_file.unlink(missing_ok=True)
        try:
            self._config.delete(camera_id)
        except Exception as e:
            _log.warning(f"[{camera_id}] session config not deleted: {e}")
        _log.info(f"[{camera_id}] session finished: {self._state_of(run)}")

    def process(self) -> None:
        """Цикл команд и статусов."""
        if self._consumer is None:
            raise RuntimeError("IngestService created without Kafka consumer")
        _log.info(f"Ingest {self.ingest_id} started, cache={self.cache_dir}")
        last_tick = 0.0
        try:
            while not self._stop.is_set():
                msg = self._consumer.poll(0.2)
                if msg is not None and not msg.error() and msg.value():
                    try:
                        self.handle_command(SessionCommand.from_msgpack(msg.value()))
                    except Exception:
                        _log.exception("Bad session command")
                elif msg is not None and msg.error():
                    _log.warning(f"Kafka error: {msg.error()}")
                now = time.time()
                if now - last_tick >= self.status_interval_s:
                    last_tick = now
                    self.tick()
        finally:
            self._consumer.close()
            self._loop_done.set()

    def close(self) -> None:
        """Остановка узла: все его сессии останавливаются и получают итоговый статус."""
        self._stop.set()
        with self._lock:
            ids = list(self._runs)
        for camera_id in ids:
            self.stop(camera_id)
        self._executor.shutdown(wait=True)
        self.tick()
        if self._consumer is not None:
            self._loop_done.wait(5.0)
        self._status.close()
        self._config.close()
        shutil.rmtree(self.cache_dir, ignore_errors=True)

    def has_session(self, camera_id: str) -> bool:
        with self._lock:
            return camera_id in self._runs
