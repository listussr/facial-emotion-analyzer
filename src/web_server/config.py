import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings:
    KAFKA_BOOTSTRAP: str = os.getenv("AFFECTRA_KAFKA", "localhost:9092")

    TOPIC_RAW: str = os.getenv("AFFECTRA_TOPIC_RAW", "raw-video-frames")
    TOPIC_ANALYTICS: str = os.getenv("AFFECTRA_TOPIC_ANALYTICS", "vision-analytics")
    TOPIC_ANNOTATED: str = os.getenv("AFFECTRA_TOPIC_ANNOTATED", "annotated-video")

    UPLOADS_DIR: Path = REPO_ROOT / "data" / "uploads"

    DEFAULT_FRAME_RATE: int = 60
    DEFAULT_QUALITY: int = 60
    DEFAULT_MAX_WIDTH: int = 854
    DEFAULT_MAX_HEIGHT: int = 480

    MAX_UPLOAD_SIZE_MB: int = 2048

    CORS_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()
settings.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
