"""Настройки веб-сервера из переменных окружения AFFECTRA_*."""

import os

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings:
    KAFKA_BOOTSTRAP: str = os.getenv("AFFECTRA_KAFKA", "localhost:9092")

    TOPIC_RAW: str = os.getenv("AFFECTRA_TOPIC_RAW", "raw-video-frames")
    TOPIC_AUDIO_RAW: str = os.getenv("AFFECTRA_TOPIC_AUDIO_RAW", "raw-audio-chunks")
    TOPIC_SESSION_EVENTS: str = os.getenv("AFFECTRA_TOPIC_SESSION_EVENTS", "session-events")
    TOPIC_SESSION_CONFIG: str = os.getenv("AFFECTRA_TOPIC_SESSION_CONFIG", "session-config")
    TOPIC_SESSION_COMMANDS: str = os.getenv("AFFECTRA_TOPIC_SESSION_COMMANDS", "session-commands")
    TOPIC_SESSION_STATUS: str = os.getenv("AFFECTRA_TOPIC_SESSION_STATUS", "session-status")
    TOPIC_ANALYTICS: str = os.getenv("AFFECTRA_TOPIC_ANALYTICS", "fused-analytics")
    TOPIC_ANNOTATED: str = os.getenv("AFFECTRA_TOPIC_ANNOTATED", "annotated-video")
    TOPIC_SERVICE_HEALTH: str = os.getenv("AFFECTRA_TOPIC_SERVICE_HEALTH", "service-health")

    # Не запускать сессию, пока нужные ей сервисы не загрузили модели
    REQUIRE_READY: bool = os.getenv("AFFECTRA_REQUIRE_READY", "true").strip().lower() in ("1", "true", "yes", "on")

    UPLOADS_DIR: Path = REPO_ROOT / "data" / "uploads"

    DEFAULT_FRAME_RATE: int = 60
    DEFAULT_QUALITY: int = 60
    DEFAULT_MAX_WIDTH: int = 854
    DEFAULT_MAX_HEIGHT: int = 480

    MAX_UPLOAD_SIZE_MB: int = 2048

    # Автоочистка: записи и загрузки старше N дней удаляются; 0 — хранить всегда
    RECORDINGS_RETENTION_DAYS: float = float(os.getenv("AFFECTRA_RECORDINGS_RETENTION_DAYS", "0"))
    UPLOADS_RETENTION_DAYS: float = float(os.getenv("AFFECTRA_UPLOADS_RETENTION_DAYS", "0"))

    CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]

    DB_HOST: str = os.getenv("AFFECTRA_DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("AFFECTRA_DB_PORT", "5433"))
    DB_NAME: str = os.getenv("AFFECTRA_DB_NAME", "emotions")
    DB_USER: str = os.getenv("AFFECTRA_DB_USER", "app_user")
    DB_PASSWORD: str = os.getenv("AFFECTRA_DB_PASSWORD", "basic_app_password")
    # Пароль роли affectra_reader (только чтение, для агента); пусто — роль без входа
    DB_READER_PASSWORD: str = os.getenv("AFFECTRA_DB_READER_PASSWORD", "")


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
