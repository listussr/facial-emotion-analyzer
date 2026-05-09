from datetime import datetime
from typing import Literal, Optional, Union

from pydantic import BaseModel, Field


TrackerName = Literal["deepsort", "bytetrack"]
EmotionModel = Literal[
    "resnet-18", "resnet-18-int8",
    "resnet-50", "resnet-50-int8",
    "convnext", "convnext-int8",
]
Device = Literal["cpu", "cuda"]
SessionKind = Literal["camera", "upload"]


class SessionConfig(BaseModel):
    """Параметры обработки сессии. Сейчас сохраняются как метаданные —
    реальный пайплайн (`consumer.py`) использует свои настройки."""
    tracker: TrackerName = "deepsort"
    model: EmotionModel = "resnet-18"
    device: Device = "cpu"


class CreateCameraRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    source: Union[str, int]
    frame_rate: int = 20
    config: SessionConfig = SessionConfig()


class CreateUploadRequest(BaseModel):
    """Уже загруженный файл превращаем в сессию."""
    upload_id: str
    name: str = Field(..., min_length=1, max_length=80)
    frame_rate: int = 20
    config: SessionConfig = SessionConfig()


class SessionInfo(BaseModel):
    id: str
    kind: SessionKind
    name: str
    source: str
    status: Literal["live", "running", "paused", "done", "error", "offline"]
    config: SessionConfig
    frame_rate: int
    started_at: datetime
    frames_sent: int = 0
    errors: int = 0
    fps: float = 0.0
    progress: float = 0.0
    position_sec: float = 0.0
    filename: Optional[str] = None
    duration_sec: Optional[float] = None
    file_size: Optional[int] = None


class UploadInfo(BaseModel):
    upload_id: str
    filename: str
    size: int
    saved_path: str
    uploaded_at: Optional[float] = None


class HealthInfo(BaseModel):
    status: str
    kafka: bool
    sessions: int
    uploads_dir: str
