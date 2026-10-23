"""Сервис слияния: vision-analytics и audio-analytics -> fused-analytics, слитые таймсерии в БД."""

import logging
import os

from src.fusion.recorder import FusionRecorder
from src.fusion.service import FusionService
from src.logging_config import setup_logger
from src.service_health import heartbeat_from_env
from src.session_config import FusionSettings
from src.session_config import SessionConfigStore
from src.session_config import StoreNotReadyError

setup_logger("fusion")


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def make_recorder():
    """Запись слитых таймсерий в БД; FUSION_DB_ENABLED=false — не писать."""
    if not _env_bool("FUSION_DB_ENABLED", True):
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

    return FusionRecorder(factory, track_timeout_s=float(os.getenv("FUSION_TRACK_TIMEOUT_S", "3")))


def start_session_store(bootstrap: str):
    topic = os.getenv("TOPIC_SESSION_CONFIG", "session-config")
    if not topic:
        return None
    store = SessionConfigStore(bootstrap, topic=topic)
    try:
        store.start(ready_timeout=float(os.getenv("SESSION_CONFIG_READY_TIMEOUT", "10")))
    except StoreNotReadyError as e:
        logging.warning(f"{e}; continuing with default settings until it is loaded")
    return store


if __name__ == "__main__":
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka_settings = {
        "bootstrap_servers": bootstrap,
        "vision_topic": os.getenv("TOPIC_ANALYTICS", "vision-analytics"),
        "audio_topic":  os.getenv("TOPIC_AUDIO_ANALYTICS", "audio-analytics"),
        "events_topic": os.getenv("TOPIC_SESSION_EVENTS", "session-events") or None,
        "output_topic": os.getenv("TOPIC_FUSED", "fused-analytics"),
        "group_id":     os.getenv("FUSION_GROUP_ID", "fusion-v1"),
    }
    defaults = FusionSettings(
        method=os.getenv("FUSION_METHOD", "stack_hidden"),
        voice_max_age_ms=int(os.getenv("FUSION_VOICE_MAX_AGE_MS", "3000")),
        min_overlap_ms=int(os.getenv("FUSION_MIN_OVERLAP_MS", "1000")),
    )
    heartbeat     = heartbeat_from_env("fusion")
    max_age       = int(os.getenv("FUSION_MAX_FRAME_AGE_MS", "2000"))
    session_store = start_session_store(bootstrap)

    service = FusionService(
        kafka_settings, defaults,
        session_store=session_store,
        max_frame_age_ms=max_age if max_age > 0 else None,
        recorder=make_recorder(),
    )
    heartbeat.ready(method=defaults.method)
    try:
        service.process()
    finally:
        heartbeat.stop()
        if session_store is not None:
            session_store.stop()
