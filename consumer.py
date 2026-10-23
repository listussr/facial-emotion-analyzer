"""Видеоконсьюмер: raw-video-frames -> лица и эмоции -> vision-analytics; настройки из переменных окружения."""

import logging
import os

from src.frames_processing.processing_pipline import ProcessingPipeline
from src.cpu_threads import limit_opencv_threads
from src.logging_config import setup_logger
from src.service_health import heartbeat_from_env
from src.session_config import SessionConfigStore
from src.session_config import StoreNotReadyError

setup_logger("processing")
limit_opencv_threads()


def _env_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def start_session_store(bootstrap: str):
    """Хранилище конфигов сессий; если топик не готов, работа идёт на значениях по умолчанию."""
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
        "input_topic":  os.getenv("TOPIC_RAW",      "raw-video-frames"),
        "output_topic": os.getenv("TOPIC_ANALYTICS", "vision-analytics"),
        "events_topic": os.getenv("TOPIC_SESSION_EVENTS", "session-events") or None,
        "group_id":     os.getenv("PIPELINE_GROUP_ID", "emotion-pipeline-v1"),
    }

    detector_settings = {
        "backend": os.getenv("DETECTOR_BACKEND", "yunet"),
        "min_detection_confidence": float(os.getenv("DETECTOR_MIN_CONF", "0.6")),
    }
    if detector_settings["backend"] == "yunet":
        detector_settings["max_side"] = int(os.getenv("DETECTOR_MAX_SIDE", "480"))

    analyzer_settings = {
        "model":  os.getenv("EMOTION_MODEL_DEFAULT", "crema-swin-int8"),
        "device": os.getenv("EMOTION_DEVICE_DEFAULT", "cpu"),
        "num_threads": int(os.getenv("EMOTION_NUM_THREADS", "2")),
    }

    identifier_settings = {
        "host":     os.getenv("DB_HOST",     "localhost"),
        "port":     int(os.getenv("DB_PORT", "5433")),
        "database": os.getenv("DB_NAME",     "emotions"),
        "user":     os.getenv("DB_USER",     "app_user"),
        "password": os.getenv("DB_PASSWORD", "basic_app_password"),
        "threshold": float(os.getenv("IDENTIFIER_THRESHOLD", "0.4")),
    } if _env_bool("IDENTIFICATION_ENABLED", True) else None

    tracker_settings = {
        "type": os.getenv("TRACKER_DEFAULT", "bytetrack"),
        "max_age": 30,
        "n_init": 1,
        "nn_budget": 100,
        "max_cosine_distance": 0.2,
        "embedder_model_name": "mobilenet",
        "track_activation_threshold": 0.25,
        "lost_track_buffer": 30,
        "minimum_matching_threshold": 0.8,
        "frame_rate": 20,
        "minimum_consecutive_frames": 3,
    }

    heartbeat     = heartbeat_from_env("consumer")
    max_age       = int(os.getenv("MAX_FRAME_AGE_MS", "1000"))
    session_store = start_session_store(bootstrap)

    pipeline = ProcessingPipeline(
        kafka_settings,
        detector_settings,
        analyzer_settings,
        tracker_settings,
        identifier_settings,
        detection_frequency=int(os.getenv("DETECTION_FREQUENCY", "2")),
        clip_frames=int(os.getenv("CLIP_FRAMES", "8")),
        emotion_hop_ms=int(os.getenv("EMOTION_HOP_MS", "1000")),
        clip_window_ms=int(os.getenv("CLIP_WINDOW_MS", "3000")),
        session_store=session_store,
        max_frame_age_ms=max_age if max_age > 0 else None,
    )
    heartbeat.ready(model=analyzer_settings["model"], detector=detector_settings["backend"])
    try:
        pipeline.process()
    except KeyboardInterrupt:
        logging.info("Consumer interrupted by user")
    finally:
        heartbeat.stop()
        if session_store is not None:
            session_store.stop()
