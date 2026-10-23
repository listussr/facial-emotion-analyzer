"""Аудиоконсьюмер: raw-audio-chunks -> эмоции по голосу -> audio-analytics; работает с пониженным приоритетом."""

import logging
import os

from src.audio_processing.pipeline import AudioPipeline
from src.cpu_threads import lower_priority
from src.logging_config import setup_logger
from src.service_health import heartbeat_from_env
from src.session_config import AudioSettings
from src.session_config import SessionConfigStore
from src.session_config import StoreNotReadyError

setup_logger("audio")
lower_priority()


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


def make_speech_vad():
    """Silero VAD поверх энергетического; VAD_BACKEND=energy или нет файла модели — только энергетический."""
    if os.getenv("VAD_BACKEND", "silero").strip().lower() != "silero":
        return None
    path = os.getenv("VAD_MODEL_PATH", "models/vad/silero_vad.onnx")
    if not os.path.exists(path):
        logging.warning(f"Silero VAD model not found at {path}; using energy VAD only")
        return None
    from src.audio_processing.vad import SileroVAD

    logging.info(f"Speech VAD: Silero ({path})")
    return SileroVAD(path, frame_threshold=float(os.getenv("VAD_FRAME_THRESHOLD", "0.5")))


if __name__ == "__main__":
    bootstrap = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka_settings = {
        "bootstrap_servers": bootstrap,
        "input_topic":  os.getenv("TOPIC_AUDIO_RAW", "raw-audio-chunks"),
        "output_topic": os.getenv("TOPIC_AUDIO_ANALYTICS", "audio-analytics"),
        "events_topic": os.getenv("TOPIC_SESSION_EVENTS", "session-events") or None,
        "group_id":     os.getenv("AUDIO_GROUP_ID", "audio-pipeline-v1"),
    }
    defaults = AudioSettings(
        model=os.getenv("VOICE_MODEL_DEFAULT", "crema-wavlm-int8"),
        device=os.getenv("VOICE_DEVICE_DEFAULT", "cpu"),
        vad_threshold=float(os.getenv("VAD_THRESHOLD", "0.3")),
        window_ms=int(os.getenv("AUDIO_WINDOW_MS", "3000")),
        hop_ms=int(os.getenv("AUDIO_HOP_MS", "1000")),
    )
    heartbeat     = heartbeat_from_env("audio-consumer")
    max_age       = int(os.getenv("MAX_CHUNK_AGE_MS", "2000"))
    session_store = start_session_store(bootstrap)

    pipeline = AudioPipeline(
        kafka_settings, defaults,
        session_store=session_store,
        max_chunk_age_ms=max_age if max_age > 0 else None,
        num_threads=int(os.getenv("VOICE_NUM_THREADS", "2")),
        vad_threshold_dbfs=float(os.getenv("VAD_THRESHOLD_DBFS", "-45")),
        speech_vad=make_speech_vad(),
    )
    heartbeat.ready(model=defaults.model, vad=os.getenv("VAD_BACKEND", "silero"))
    try:
        pipeline.process()
    finally:
        heartbeat.stop()
        if session_store is not None:
            session_store.stop()
