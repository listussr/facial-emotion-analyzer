"""Архивация сессий: итоговое видео с аннотациями и аналитика в S3, статус записей в БД."""

import os

from pathlib import Path

from src.cpu_threads import limit_opencv_threads
from src.cpu_threads import lower_priority
from src.logging_config import setup_logger
from src.service_health import heartbeat_from_env
from src.session_archive import ArchiveService
from src.session_archive import S3Settings
from src.session_archive import S3Storage

setup_logger("archiver")
limit_opencv_threads()
lower_priority()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def make_db_factory():
    """Статусы записей в session_recordings; ARCHIVE_DB_ENABLED=false — не писать."""
    if not _env_bool("ARCHIVE_DB_ENABLED", True):
        return None
    from src.db_managing import FacesDBHandler

    def factory():
        h = FacesDBHandler(
            host=os.getenv("DB_HOST", "localhost"), port=int(os.getenv("DB_PORT", "5433")),
            database=os.getenv("DB_NAME", "emotions"), user=os.getenv("DB_USER", "app_user"),
            password=os.getenv("DB_PASSWORD", "basic_app_password"),
        )
        h.connect()
        return h

    return factory


if __name__ == "__main__":
    kafka_settings = {
        "bootstrap_servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        "video_topic":       os.getenv("TOPIC_RAW", "raw-video-frames"),
        "audio_topic":       os.getenv("TOPIC_AUDIO_RAW", "raw-audio-chunks"),
        "analytics_topic":   os.getenv("TOPIC_FUSED", "fused-analytics"),
        "events_topic":      os.getenv("TOPIC_SESSION_EVENTS", "session-events"),
        "group_id":          os.getenv("ARCHIVE_GROUP_ID", "archiver-v1"),
    }
    heartbeat = heartbeat_from_env("archiver")
    service   = ArchiveService(
        spool_root=Path(os.getenv("ARCHIVE_SPOOL_DIR", "data/archive_spool")),
        storage=S3Storage(S3Settings.from_env()),
        db_factory=make_db_factory(),
        kafka_settings=kafka_settings,
        analytics_grace_s=float(os.getenv("ARCHIVE_ANALYTICS_GRACE_S", "15")),
        idle_timeout_s=float(os.getenv("ARCHIVE_IDLE_TIMEOUT_S", "120")),
        max_duration_s=float(os.getenv("ARCHIVE_MAX_DURATION_S", "1800")),
        render_threads=int(os.getenv("ARCHIVE_RENDER_THREADS", "2")),
    )
    heartbeat.ready()
    try:
        service.process()
    finally:
        heartbeat.stop()
