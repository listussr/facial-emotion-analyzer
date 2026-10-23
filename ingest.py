"""Сервис приёма медиа: продюсеры сессий по командам из Kafka и WebSocket камеры браузера."""

import logging
import os
import socket
import uvicorn

from pathlib import Path

from src.cpu_threads import limit_opencv_threads
from src.ingest import IngestService
from src.ingest.app import create_app
from src.ingest.status_store import SessionStatusStore
from src.logging_config import setup_logger
from src.object_storage import S3Settings
from src.object_storage import S3Storage
from src.service_health import heartbeat_from_env
from src.session_config import StoreNotReadyError

setup_logger("ingest")
limit_opencv_threads()


def advertised_address(port: int) -> str:
    """Адрес узла для других узлов приёма: INGEST_ADVERTISED_ADDRESS или IP хоста и порт."""
    address = os.getenv("INGEST_ADVERTISED_ADDRESS")
    if address:
        return address
    try:
        return f"{socket.gethostbyname(socket.gethostname())}:{port}"
    except OSError:
        return f"{socket.gethostname()}:{port}"


if __name__ == "__main__":
    port      = int(os.getenv("INGEST_PORT", "8001"))
    heartbeat = heartbeat_from_env("ingest")
    kafka     = {
        "bootstrap_servers": os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092"),
        "commands_topic":    os.getenv("TOPIC_SESSION_COMMANDS", "session-commands"),
        "status_topic":      os.getenv("TOPIC_SESSION_STATUS", "session-status"),
        "config_topic":      os.getenv("TOPIC_SESSION_CONFIG", "session-config"),
        "video_topic":       os.getenv("TOPIC_RAW", "raw-video-frames"),
        "audio_topic":       os.getenv("TOPIC_AUDIO_RAW", "raw-audio-chunks"),
        "events_topic":      os.getenv("TOPIC_SESSION_EVENTS", "session-events"),
        "group_id":          os.getenv("INGEST_GROUP_ID", "ingest-v1"),
    }
    service = IngestService(
        kafka,
        storage=S3Storage(S3Settings.from_env()),
        cache_dir=Path(os.getenv("INGEST_CACHE_DIR", "data/ingest_cache")),
        advertised_address=advertised_address(port),
    )
    statuses = SessionStatusStore(kafka["bootstrap_servers"], topic=kafka["status_topic"])
    try:
        statuses.start(ready_timeout=15)
    except StoreNotReadyError as e:
        logging.getLogger("ingest").warning(f"{e}; relaying browser sessions once loaded")
    heartbeat.ready(address=service.address)
    try:
        uvicorn.run(create_app(service, status_store=statuses), host="0.0.0.0", port=port, log_level="info")
    finally:
        heartbeat.stop()
