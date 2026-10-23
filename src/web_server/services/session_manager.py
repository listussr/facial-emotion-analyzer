"""Реестр сессий веб-сервера: продюсеры и конфиги обработки в session-config."""

from __future__ import annotations

import logging
import threading
import uuid

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from src.db_managing.schema import upsert_recording_sql
from src.session_config import AudioSettings
from src.session_config import FusionSettings
from src.session_config import SessionConfig as ProcessingConfig
from src.session_config import SessionConfigPublisher
from src.session_config import VisionSettings
from src.session_config import next_revision
from src.video_producing import CameraConfig
from src.video_producing import CameraProducer
from src.video_producing.sources import browser_source
from ..config import settings
from ..schemas import SessionConfig
from ..schemas import SessionInfo
from ..schemas import SessionKind
from .db import get_cursor
from .media_probe import MediaProbe
from .media_probe import check_modalities
from .media_probe import probe_source


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


@dataclass
class _Session:
    id: str
    kind: SessionKind
    name: str
    source: str
    config: SessionConfig
    frame_rate: int
    producer: CameraProducer
    started_at: datetime = field(default_factory=datetime.utcnow)
    filename: Optional[str] = None
    file_size: Optional[int] = None

    def info(self) -> SessionInfo:
        thread_alive   = bool(self.producer.thread and self.producer.thread.is_alive())
        producer_alive = bool(self.producer.is_running) and thread_alive

        if self.kind == "camera":
            if producer_alive:
                status = "live"
            elif self.producer.is_running and not thread_alive:
                status = "error"
            else:
                status = "offline"
        else:
            if producer_alive:
                status = "running"
            elif self.producer.is_running and not thread_alive:
                status = "error"
            else:
                status = "done"

        frames_sent  = int(getattr(self.producer, "frame_count", 0) or 0)
        errors       = int(getattr(self.producer, "error_count", 0) or 0)
        fps          = 0.0
        progress     = 0.0
        position_sec = 0.0
        duration_sec = None
        modalities: List[str] = []
        degraded      = False
        audio_seconds = 0.0
        try:
            stats_fn = getattr(self.producer, "get_stats", None)
            if callable(stats_fn):
                stats         = stats_fn() or {}
                fps           = float(stats.get("current_fps") or 0.0)
                progress      = float(stats.get("progress") or 0.0)
                position_sec  = float(stats.get("position_sec") or 0.0)
                vd            = stats.get("video_duration_sec")
                duration_sec  = float(vd) if vd is not None else None
                modalities    = list(stats.get("modalities") or [])
                degraded      = bool(stats.get("degraded"))
                audio_seconds = float((stats.get("audio") or {}).get("seconds_sent") or 0.0)
        except Exception:
            pass

        if self.kind == "upload" and status == "done" and progress == 0.0 and (frames_sent > 0 or audio_seconds > 0):
            progress = 1.0

        return SessionInfo(
            id=self.id,
            kind=self.kind,
            name=self.name,
            source=str(self.source),
            status=status,
            config=self.config,
            frame_rate=self.frame_rate,
            started_at=self.started_at,
            frames_sent=frames_sent,
            errors=errors,
            fps=fps,
            progress=progress,
            position_sec=position_sec,
            filename=self.filename,
            duration_sec=duration_sec,
            file_size=self.file_size,
            modalities=modalities,
            degraded=degraded,
            audio_seconds_sent=audio_seconds,
        )


class SessionManager:
    """Потокобезопасный реестр сессий."""

    def __init__(self) -> None:
        self._sessions: Dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._log  = logging.getLogger("SessionManager")
        self._publisher: Optional[SessionConfigPublisher] = None
        self._publisher_lock = threading.Lock()

    def _config_publisher(self) -> SessionConfigPublisher:
        with self._publisher_lock:
            if self._publisher is None:
                self._publisher = SessionConfigPublisher(
                    settings.KAFKA_BOOTSTRAP, topic=settings.TOPIC_SESSION_CONFIG
                )
            return self._publisher

    def create_camera(
        self,
        name: str,
        source: Union[str, int],
        frame_rate: int,
        config: SessionConfig,
    ) -> SessionInfo:
        camera_id = f"cam_{uuid.uuid4().hex[:6]}"
        return self._spawn(camera_id, "camera", name, source, frame_rate, config)

    def create_browser(self, name: str, frame_rate: int, config: SessionConfig) -> SessionInfo:
        """
        Сессия с камерой и микрофоном браузера; медиа приходит по WebSocket /api/capture/{id}.

        Args:
            name       (str)          : Имя сессии.
            frame_rate (int)          : Частота кадров продюсера.
            config     (SessionConfig): Параметры обработки; modalities — что отдаёт браузер.

        Returns:
            SessionInfo: Созданная сессия.
        """
        camera_id = f"browser_{uuid.uuid4().hex[:6]}"
        defaults  = CameraConfig(camera_id=camera_id, source="", kafka_servers="")
        source    = browser_source.BrowserSource(
            camera_id, has_video="video" in config.modalities, has_audio="audio" in config.modalities,
            sample_rate=defaults.audio_sample_rate, channels=defaults.audio_channels,
            samples_per_chunk=defaults.audio_samples_per_chunk,
        )
        uri   = browser_source.register(source)
        probe = MediaProbe(has_video=source.has_video, has_audio=source.has_audio)
        try:
            return self._spawn(camera_id, "camera", name, uri, frame_rate, config, probe=probe)
        except Exception:
            browser_source.unregister(camera_id)
            raise

    def create_upload(
        self,
        name: str,
        upload_path: Path,
        frame_rate: int,
        config: SessionConfig,
    ) -> SessionInfo:
        if not upload_path.exists():
            raise FileNotFoundError(f"Uploaded file not found: {upload_path}")
        camera_id = f"upload_{uuid.uuid4().hex[:6]}"
        info      = self._spawn(camera_id, "upload", name, str(upload_path), frame_rate, config)
        with self._lock:
            sess           = self._sessions[camera_id]
            sess.filename  = upload_path.name
            sess.file_size = upload_path.stat().st_size
        return sess.info()

    def _spawn(
        self,
        camera_id: str,
        kind: SessionKind,
        name: str,
        source: Union[str, int],
        frame_rate: int,
        config: SessionConfig,
        probe: Optional[MediaProbe] = None,
    ) -> SessionInfo:
        """
        Запуск сессии: проверка модальностей источника, публикация конфига, старт продюсера.

        Конфиг публикуется до первого кадра: консьюмеры читают настройки из session-config.
        """
        probe = probe or probe_source(source)
        check_modalities(probe, config.modalities)

        self._config_publisher().publish(build_processing_config(camera_id, config))

        cam_cfg = CameraConfig(
            camera_id=camera_id,
            source=source,
            kafka_servers=settings.KAFKA_BOOTSTRAP,
            video_topic=settings.TOPIC_RAW,
            audio_topic=settings.TOPIC_AUDIO_RAW,
            events_topic=settings.TOPIC_SESSION_EVENTS,
            modalities=tuple(config.modalities),
            frame_rate=frame_rate,
            quality=settings.DEFAULT_QUALITY,
            max_width=settings.DEFAULT_MAX_WIDTH,
            max_height=settings.DEFAULT_MAX_HEIGHT,
            max_message_size=5_000_000,
            reconnect_timeout=5,
            stop_on_end=(kind == "upload"),
        )
        try:
            producer = CameraProducer(cam_cfg)
            producer.start()
        except Exception:
            self._delete_config(camera_id)
            raise
        self._log.info(
            f"Spawned producer for {camera_id}: source={source!r}, frame_rate={frame_rate}, "
            f"kind={kind}, modalities={config.modalities} "
            f"(source has video={probe.has_video}, audio={probe.has_audio})"
        )

        sess = _Session(
            id=camera_id,
            kind=kind,
            name=name,
            source=str(source),
            config=config,
            frame_rate=frame_rate,
            producer=producer,
        )
        with self._lock:
            self._sessions[camera_id] = sess
        self._log.info(f"Session started: {camera_id} ({kind}, {name})")
        self._register_recording(camera_id, kind, name)
        return sess.info()

    def _register_recording(self, camera_id: str, kind: SessionKind, name: str) -> None:
        """Имя и тип сессии для списка записей; статус записи ведёт сервис архивации."""
        try:
            with get_cursor() as cur:
                cur.execute(upsert_recording_sql(("name", "kind", "started_at")),
                            (camera_id, name, kind, datetime.now(timezone.utc)))
        except Exception as e:
            self._log.warning(f"Recording entry for {camera_id} not saved: {e}")

    def list(self, kind: Optional[SessionKind] = None) -> List[SessionInfo]:
        with self._lock:
            sessions = list(self._sessions.values())
        if kind is not None:
            sessions = [s for s in sessions if s.kind == kind]
        return [s.info() for s in sessions]

    def get(self, session_id: str) -> Optional[SessionInfo]:
        with self._lock:
            sess = self._sessions.get(session_id)
        return sess.info() if sess else None

    def running_session_for(self, path: Path) -> Optional[str]:
        """
        Сессия, которая сейчас читает файл.

        Args:
            path (Path): Путь к загруженному файлу.

        Returns:
            Optional[str]: Идентификатор сессии или None.
        """
        target = str(path.resolve())
        with self._lock:
            sessions = list(self._sessions.values())
        for sess in sessions:
            if sess.kind != "upload" or str(Path(sess.source).resolve()) != target:
                continue
            if sess.info().status == "running":
                return sess.id
        return None

    def detach(self, session_id: str) -> Optional[CameraProducer]:
        """Сессия снимается из реестра без остановки продюсера: её делает вызывающий код в фоне."""
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if sess is None:
            return None
        self._log.info(f"Session detached: {session_id}")
        return sess.producer

    def _delete_config(self, session_id: str) -> None:
        try:
            self._config_publisher().delete(session_id)
        except Exception as e:
            self._log.warning(f"Failed to delete session config {session_id}: {e}")

    def shutdown_producer(self, producer: CameraProducer, log_id: str = "") -> None:
        """
        Остановка продюсера и удаление конфига сессии.

        Конфиг удаляется после stream_end, чтобы консьюмеры дообработали последние кадры с настройками сессии.
        """
        browser = browser_source.unregister(log_id) if log_id else None
        if browser is not None:
            browser.finish()
        try:
            producer.stop()
            self._log.info(f"Producer stopped: {log_id}")
        except Exception as e:
            self._log.warning(f"Error stopping producer {log_id}: {e}")
        if log_id:
            self._delete_config(log_id)

    def stop(self, session_id: str) -> bool:
        producer = self.detach(session_id)
        if producer is None:
            return False
        self.shutdown_producer(producer, session_id)
        return True

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            self.stop(sid)
        with self._publisher_lock:
            if self._publisher is not None:
                self._publisher.close()
                self._publisher = None


session_manager = SessionManager()
