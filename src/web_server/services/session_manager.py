from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import cv2

from src.video_producing import CameraProducer, CameraConfig
from ..config import settings
from ..schemas import SessionConfig, SessionInfo, SessionKind


def _probe_source(source: Union[str, int]) -> None:
    """
    Открывает источник через cv2.VideoCapture, проверяет, что он отдаёт хотя бы
    один кадр, и закрывает. Пробрасывает исключение, если источник недоступен —
    тогда мы откажем в создании сессии заранее, не оставляя «зависшую» камеру.
    """
    src: Union[str, int] = source
    if isinstance(src, str) and src.isdigit():
        src = int(src)
    cap = cv2.VideoCapture(src)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Не удалось открыть источник: {source!r}")
        ok, _ = cap.read()
        if not ok:
            raise RuntimeError(
                f"Источник открылся, но не отдаёт кадры: {source!r}. "
                f"Возможно, камера занята или не подключена."
            )
    finally:
        cap.release()


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
        thread_alive = bool(self.producer.thread and self.producer.thread.is_alive())
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
        return SessionInfo(
            id=self.id,
            kind=self.kind,
            name=self.name,
            source=str(self.source),
            status=status,
            config=self.config,
            frame_rate=self.frame_rate,
            started_at=self.started_at,
            filename=self.filename,
            file_size=self.file_size,
        )


class SessionManager:
    """Потокобезопасный реестр сессий."""

    def __init__(self) -> None:
        self._sessions: Dict[str, _Session] = {}
        self._lock = threading.Lock()
        self._log = logging.getLogger("SessionManager")

    def create_camera(
        self,
        name: str,
        source: Union[str, int],
        frame_rate: int,
        config: SessionConfig,
    ) -> SessionInfo:
        camera_id = f"cam_{uuid.uuid4().hex[:6]}"
        return self._spawn(camera_id, "camera", name, source, frame_rate, config)

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
        info = self._spawn(camera_id, "upload", name, str(upload_path), frame_rate, config)
        with self._lock:
            sess = self._sessions[camera_id]
            sess.filename = upload_path.name
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
    ) -> SessionInfo:
        _probe_source(source)

        cam_cfg = CameraConfig(
            camera_id=camera_id,
            source=source,
            kafka_servers=settings.KAFKA_BOOTSTRAP,
            topic_name=settings.TOPIC_RAW,
            partition=0,
            total_partitions=1,
            frame_rate=frame_rate,
            quality=settings.DEFAULT_QUALITY,
            max_width=settings.DEFAULT_MAX_WIDTH,
            max_height=settings.DEFAULT_MAX_HEIGHT,
            max_message_size=5_000_000,
            reconnect_timeout=5,
        )
        producer = CameraProducer(cam_cfg)
        producer.start()

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
        return sess.info()

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


    def stop(self, session_id: str) -> bool:
        with self._lock:
            sess = self._sessions.pop(session_id, None)
        if sess is None:
            return False
        try:
            sess.producer.stop()
        except Exception as e:
            self._log.warning(f"Error stopping {session_id}: {e}")
        self._log.info(f"Session stopped: {session_id}")
        return True

    def stop_all(self) -> None:
        with self._lock:
            ids = list(self._sessions.keys())
        for sid in ids:
            self.stop(sid)


session_manager = SessionManager()
