"""Схемы API веб-сервера."""

from datetime import datetime
from typing import List
from typing import Literal
from typing import Optional
from typing import Union

from pydantic import BaseModel
from pydantic import Field


TrackerName = Literal["deepsort", "bytetrack"]
Device      = Literal["cpu", "cuda"]
SessionKind = Literal["camera", "upload"]
Modality    = Literal["video", "audio"]
FusionMethod = Literal["stack_hidden", "stack_logits", "weighted_logits", "weighted_logits_robust",
                       "confidence_weighted", "mean_probs", "audio_only", "video_only"]


class SessionConfig(BaseModel):
    """
    Параметры обработки сессии; публикуются в session-config до запуска продюсера.

    Неизвестную модель сервисы заменяют своей по умолчанию. clip_frames и fusion_method со значением None —
    значения по умолчанию видеоконсьюмера и сервиса слияния.
    """
    tracker: TrackerName = "bytetrack"
    model: str = Field(default="crema-swin-int8", min_length=1, max_length=64)
    device: Device = "cpu"
    modalities: List[Modality] = Field(default_factory=lambda: ["video", "audio"], min_length=1)
    voice_model: str = Field(default="crema-wavlm-int8", min_length=1, max_length=64)
    identification: bool = True
    clip_frames: Optional[Literal[8, 16]] = None
    fusion_method: Optional[FusionMethod] = None


class CreateCameraRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    source: Union[str, int]
    frame_rate: int = 20
    config: SessionConfig = SessionConfig()


class CreateUploadRequest(BaseModel):
    """Сессия по уже загруженному файлу."""
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
    modalities: List[Modality] = []
    degraded: bool = False
    audio_seconds_sent: float = 0.0


class UploadInfo(BaseModel):
    upload_id: str
    filename: str
    size: int
    saved_path: str
    uploaded_at: Optional[float] = None
    has_video: Optional[bool] = None
    has_audio: Optional[bool] = None
    duration_sec: Optional[float] = None


class HealthInfo(BaseModel):
    status: str
    kafka: bool
    sessions: int
    uploads_dir: str
