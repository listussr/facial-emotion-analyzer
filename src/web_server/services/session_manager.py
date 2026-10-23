"""Сессии веб-сервера: конфиги в session-config, команды сервису приёма, статусы из session-status."""

from __future__ import annotations

import logging
import threading
import time
import uuid

from datetime import datetime
from datetime import timezone
from typing import Callable
from typing import Dict
from typing import List
from typing import Optional

from src.db_managing.schema import upsert_recording_sql
from src.ingest import SessionCommand
from src.ingest import SessionSpec
from src.ingest import SessionStatus
from src.ingest import SessionStatusStore
from src.service_health import ServiceHealth
from src.service_health import ServiceHealthStore
from src.service_health import not_ready_reasons
from src.service_health import summarize
from src.session_config import AudioSettings
from src.session_config import FusionSettings
from src.session_config import KeyedPublisher
from src.session_config import SessionConfig as ProcessingConfig
from src.session_config import SessionConfigPublisher
from src.session_config import VisionSettings
from src.session_config import next_revision
from ..config import settings
from ..schemas import SessionConfig
from ..schemas import SessionInfo
from ..schemas import SessionKind
from .db import get_cursor


def build_processing_config(camera_id: str, config: SessionConfig) -> ProcessingConfig:
    """
    Конфиг обработки для session-config из настроек API.

    Args:
        camera_id (str)          : Идентификатор сессии.
        config    (SessionConfig): Настройки из API.

    Returns:
        ProcessingConfig: Секции vision, audio и fusion для сервисов обработки.
    """
    fusion = FusionSettings(method=config.fusion_method) if config.fusion_method else None
    return ProcessingConfig(
        camera_id=camera_id,
        revision=next_revision(None),
        vision=VisionSettings(tracker=config.tracker, model=config.model, device=config.device,
                              identification=config.identification, clip_frames=config.clip_frames),
        audio=AudioSettings(model=config.voice_model, device=config.device),
        fusion=fusion,
    )


class StartError(RuntimeError):
    """Сервис приёма не смог запустить сессию или не ответил."""


def session_info(status: SessionStatus, now: Optional[float] = None, stale_after_s: float = 10.0) -> SessionInfo:
    """
    Описание сессии для API по статусу от сервиса приёма.

    Идущая сессия без обновлений дольше stale_after_s считается потерянной: узел приёма упал или недоступен.

    Args:
        status        (SessionStatus)  : Последний статус.
        now           (float, optional): Текущее время. Defaults to None.
        stale_after_s (float, optional): Порог устаревания статуса, с. Defaults to 10.0.

    Returns:
        SessionInfo: Сессия в формате API.
    """
    now   = time.time() if now is None else now
    spec  = SessionSpec.from_dict(status.spec)
    stats = status.stats or {}
    kind  = "upload" if spec.kind == "upload" else "camera"
    state = status.state
    if status.active and now - status.updated_at > stale_after_s:
        state = "error"
    if state == "starting":
        state = "running" if kind == "upload" else "live"

    progress     = float(stats.get("progress") or 0.0)
    audio        = float((stats.get("audio") or {}).get("seconds_sent") or 0.0)
    duration     = stats.get("video_duration_sec")
    frames_sent  = int(status.frames_sent or 0)
    if kind == "upload" and state == "done" and progress == 0.0 and (frames_sent > 0 or audio > 0):
        progress = 1.0

    return SessionInfo(
        id=status.camera_id,
        kind=kind,
        name=spec.name,
        source=spec.filename or spec.source or spec.kind,
        status=state,
        config=SessionConfig(**spec.config) if spec.config else SessionConfig(),
        frame_rate=spec.frame_rate,
        started_at=datetime.fromtimestamp(spec.created_at, tz=timezone.utc),
        frames_sent=frames_sent,
        errors=int(status.errors or 0),
        fps=float(stats.get("current_fps") or 0.0),
        progress=progress,
        position_sec=float(stats.get("position_sec") or 0.0),
        filename=spec.filename,
        duration_sec=float(duration) if duration is not None else None,
        file_size=spec.file_size,
        modalities=list(stats.get("modalities") or []),
        degraded=bool(stats.get("degraded")),
        audio_seconds_sent=audio,
        error=status.error,
    )


class SessionManager:
    """
    Сессии всех узлов приёма.

    Продюсеры работают в сервисе приёма (ingest.py): веб-сервер публикует конфиг обработки, затем команду
    start и ждёт первого статуса. Список сессий — копия compacted-топика session-status, поэтому он
    переживает перезапуск веб-сервера. Завершённые сессии убираются из топика через keep_finished_s.
    """

    def __init__(self, status_store: Optional[SessionStatusStore] = None,
                 commands: Optional[KeyedPublisher] = None, configs: Optional[SessionConfigPublisher] = None,
                 status_publisher: Optional[KeyedPublisher] = None, keep_finished_s: float = 3600.0,
                 start_timeout_s: float = 25.0, register_recording: Optional[Callable] = None,
                 health_store: Optional[ServiceHealthStore] = None, require_ready: Optional[bool] = None) -> None:
        self._store            = status_store
        self._commands         = commands
        self._configs          = configs
        self._status_publisher = status_publisher
        self.keep_finished_s   = keep_finished_s
        self.start_timeout_s   = start_timeout_s
        self._register         = register_recording or _register_recording
        self._health           = health_store
        self.require_ready     = settings.REQUIRE_READY if require_ready is None else require_ready
        self._lock             = threading.Lock()
        self._log              = logging.getLogger("SessionManager")
        self._last_cleanup     = 0.0

    def start(self) -> None:
        """Подключение к Kafka при старте приложения; хранилище статусов дочитывается в фоне."""
        with self._lock:
            if self._store is None:
                self._store = SessionStatusStore(settings.KAFKA_BOOTSTRAP, topic=settings.TOPIC_SESSION_STATUS)
                try:
                    self._store.start(ready_timeout=10.0)
                except Exception as e:
                    self._log.warning(f"{e}; session list will fill in later")
            if self._commands is None:
                self._commands = KeyedPublisher(settings.KAFKA_BOOTSTRAP, settings.TOPIC_SESSION_COMMANDS)
            if self._configs is None:
                self._configs = SessionConfigPublisher(settings.KAFKA_BOOTSTRAP, topic=settings.TOPIC_SESSION_CONFIG)
            if self._status_publisher is None:
                self._status_publisher = KeyedPublisher(settings.KAFKA_BOOTSTRAP, settings.TOPIC_SESSION_STATUS)
            if self._health is None:
                self._health = ServiceHealthStore(settings.KAFKA_BOOTSTRAP, topic=settings.TOPIC_SERVICE_HEALTH)
                try:
                    self._health.start(ready_timeout=10.0)
                except Exception as e:
                    self._log.warning(f"{e}; service readiness is not checked until it is loaded")

    def close(self) -> None:
        for client in (self._commands, self._configs, self._status_publisher):
            if client is not None:
                client.close()
        for store in (self._store, self._health):
            if store is not None:
                store.stop()

    def services(self) -> Optional[Dict[str, ServiceHealth]]:
        """Состояние сервисов по service-health; None — топик ещё не прочитан."""
        if self._health is None or not self._health.ready:
            return None
        return summarize(self._health.snapshot().values())

    def check_ready(self, modalities) -> None:
        """
        Готовы ли сервисы, нужные сессии с такими модальностями.

        Raises:
            StartError: Нужный сервис не запущен или ещё загружает модели.
        """
        health = self.services() if self.require_ready else None
        if health is None:
            return
        reasons = not_ready_reasons(health, modalities)
        if reasons:
            raise StartError("Анализ пока нельзя запустить — " + "; ".join(reasons)
                             + ". Модели загружаются до пары минут после запуска.")

    def create_camera(self, name: str, source: str, frame_rate: int, config: SessionConfig) -> SessionInfo:
        spec = SessionSpec(camera_id=f"cam_{uuid.uuid4().hex[:6]}", kind="camera", name=name, source=str(source),
                           frame_rate=frame_rate, modalities=list(config.modalities), config=config.model_dump(),
                           **self._producer_params())
        return self._start(spec, config, wait_states=("live", "error", "offline"))

    def create_browser(self, name: str, frame_rate: int, config: SessionConfig) -> SessionInfo:
        """
        Сессия с камерой и микрофоном браузера; медиа идёт по WebSocket /api/capture/{id} сервиса приёма.

        Args:
            name       (str)          : Имя сессии.
            frame_rate (int)          : Частота кадров продюсера.
            config     (SessionConfig): Параметры обработки; modalities — что отдаёт браузер.

        Returns:
            SessionInfo: Созданная сессия.
        """
        spec = SessionSpec(camera_id=f"browser_{uuid.uuid4().hex[:6]}", kind="browser", name=name,
                           frame_rate=frame_rate, modalities=list(config.modalities), config=config.model_dump(),
                           **self._producer_params())
        return self._start(spec, config, wait_states=("live", "error", "offline"))

    def create_upload(self, name: str, upload_id: str, upload_key: str, frame_rate: int, config: SessionConfig,
                      filename: Optional[str] = None, file_size: Optional[int] = None) -> SessionInfo:
        spec = SessionSpec(camera_id=f"upload_{uuid.uuid4().hex[:6]}", kind="upload", name=name, source=upload_key,
                           frame_rate=frame_rate, modalities=list(config.modalities), upload_id=upload_id,
                           upload_key=upload_key, filename=filename, file_size=file_size,
                           config=config.model_dump(), **self._producer_params())
        return self._start(spec, config, wait_states=("running", "done", "error"))

    @staticmethod
    def _producer_params() -> Dict[str, int]:
        return {"quality": settings.DEFAULT_QUALITY, "max_width": settings.DEFAULT_MAX_WIDTH,
                "max_height": settings.DEFAULT_MAX_HEIGHT}

    def _start(self, spec: SessionSpec, config: SessionConfig, wait_states: tuple) -> SessionInfo:
        """
        Конфиг до первого кадра, команда start и ожидание ответа сервиса приёма.

        Raises:
            StartError: Сервис приёма вернул ошибку или не ответил за start_timeout_s.
        """
        self.start()
        self.check_ready(spec.modalities)
        self._configs.publish(build_processing_config(spec.camera_id, config))
        self._commands.send(spec.camera_id, SessionCommand(action="start", camera_id=spec.camera_id,
                                                           spec=spec.to_dict()).to_msgpack())
        status = self._store.wait_for(spec.camera_id, self.start_timeout_s,
                                      predicate=lambda s: s.state in wait_states)
        if status is None or status.state == "error" or status.state not in wait_states:
            self._delete_config(spec.camera_id)
            if status is None:
                self.stop(spec.camera_id)
                raise StartError("Сервис приёма медиа не ответил — запущен ли ingest?")
            raise StartError(status.error or f"source failed ({status.state})")
        self._register(spec.camera_id, "upload" if spec.kind == "upload" else "camera", spec.name)
        self._log.info(f"Session started: {spec.camera_id} ({spec.kind}, {spec.name})")
        return session_info(status)

    def _delete_config(self, camera_id: str) -> None:
        try:
            self._configs.delete(camera_id)
        except Exception as e:
            self._log.warning(f"Failed to delete session config {camera_id}: {e}")

    def _statuses(self) -> List[SessionStatus]:
        if self._store is None:
            return []
        return list(self._store.snapshot().values())

    def list(self, kind: Optional[SessionKind] = None) -> List[SessionInfo]:
        now = time.time()
        self._cleanup(now)
        out = [session_info(s, now) for s in self._statuses()
               if s.active or now - (s.ended_at or s.updated_at) <= self.keep_finished_s]
        if kind is not None:
            out = [s for s in out if s.kind == kind]
        return sorted(out, key=lambda s: s.started_at, reverse=True)

    def get(self, session_id: str) -> Optional[SessionInfo]:
        status = self._store.get(session_id) if self._store is not None else None
        return session_info(status) if status is not None else None

    def stop(self, session_id: str) -> bool:
        """
        Команда stop узлу приёма; сессия остаётся в списке с итоговым статусом.

        Args:
            session_id (str): Идентификатор сессии.

        Returns:
            bool: Сессия известна.
        """
        self.start()
        status = self._store.get(session_id)
        self._commands.send(session_id, SessionCommand(action="stop", camera_id=session_id).to_msgpack())
        return status is not None

    def stop_all(self, kind: Optional[SessionKind] = None) -> List[str]:
        ids = [s.id for s in self.list(kind) if s.status in ("live", "running")]
        for session_id in ids:
            self.stop(session_id)
        return ids

    def running_upload_session(self, upload_id: str) -> Optional[str]:
        """
        Сессия, которая сейчас обрабатывает загрузку.

        Args:
            upload_id (str): Идентификатор загрузки.

        Returns:
            Optional[str]: Идентификатор сессии или None.
        """
        now = time.time()
        for status in self._statuses():
            spec = status.spec or {}
            if spec.get("upload_id") == upload_id and session_info(status, now).status == "running":
                return status.camera_id
        return None

    def _cleanup(self, now: float, every_s: float = 60.0) -> None:
        """Удаление из session-status давно завершённых и потерянных сессий."""
        if now - self._last_cleanup < every_s or self._status_publisher is None:
            return
        self._last_cleanup = now
        for status in self._statuses():
            finished = not status.active and now - (status.ended_at or status.updated_at) > self.keep_finished_s
            lost     = status.active and now - status.updated_at > self.keep_finished_s
            if finished or lost:
                try:
                    self._status_publisher.send_async(status.camera_id, None)
                except Exception as e:
                    self._log.warning(f"Cannot remove status of {status.camera_id}: {e}")


def _register_recording(camera_id: str, kind: str, name: str) -> None:
    """Имя и тип сессии для списка записей; статус записи ведёт сервис архивации."""
    try:
        with get_cursor() as cur:
            cur.execute(upsert_recording_sql(("name", "kind", "started_at")),
                        (camera_id, name, kind, datetime.now(timezone.utc)))
    except Exception as e:
        logging.getLogger("SessionManager").warning(f"Recording entry for {camera_id} not saved: {e}")


session_manager = SessionManager()
