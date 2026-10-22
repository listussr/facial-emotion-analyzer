"""Контракты сообщений продюсера: кадры, аудиочанки и события сессии (msgpack, schema v2)."""

import msgpack

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import fields
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import Literal
from typing import Optional
from typing import Tuple
from typing import Type
from typing import TypeVar

SCHEMA_VERSION = 2

T = TypeVar("T", bound="_Contract")


class _Contract:
    """Плоский msgpack-словарь; при чтении неизвестные поля игнорируются ради совместимости."""
    _tuple_fields: ClassVar[Tuple[str, ...]] = ()

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        for name in self._tuple_fields:
            if data.get(name) is not None:
                data[name] = list(data[name])
        return data

    def to_msgpack(self) -> bytes:
        return msgpack.packb(self.to_dict(), use_bin_type=True)

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        known  = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        for name in cls._tuple_fields:
            if kwargs.get(name) is not None:
                kwargs[name] = tuple(kwargs[name])
        return cls(**kwargs)

    @classmethod
    def from_msgpack(cls: Type[T], raw: bytes) -> T:
        return cls.from_dict(msgpack.unpackb(raw, raw=False))


@dataclass(kw_only=True)
class MediaEnvelope(_Contract):
    """Общие поля медиасообщения: seq — счётчик потока, pts_ms — время от начала сессии."""
    _tuple_fields: ClassVar[Tuple[str, ...]] = ("modalities",)

    schema_version:        int = SCHEMA_VERSION
    camera_id:             str
    stream:                Literal["video", "audio"]
    seq:                   int
    pts_ms:                int
    modalities:            Tuple[str, ...]
    capture_wall_ts:       float
    session_start_wall_ts: float


@dataclass(kw_only=True)
class VideoMessage(MediaEnvelope):
    """Кадр видеопотока в JPEG; frame_id и timestamp — легаси-поля аннотатора."""
    stream:           Literal["video"] = "video"
    frame_id:         str
    timestamp:        float
    frame_data:       bytes
    format:           str = "jpeg"
    quality:          int
    frame_rate:       int
    processed_width:  int
    processed_height: int
    original_width:   int
    original_height:  int


@dataclass(kw_only=True)
class AudioMessage(MediaEnvelope):
    """Аудиочанк interleaved PCM; num_samples — на канал."""
    stream:        Literal["audio"] = "audio"
    sample_rate:   int
    channels:      int
    sample_format: str = "s16le"
    num_samples:   int
    audio_data:    bytes
    duration_ms:   int = 0

    def __post_init__(self) -> None:
        self.duration_ms = self.num_samples * 1000 // self.sample_rate


@dataclass(kw_only=True)
class SessionEvent(_Contract):
    """
    Событие жизненного цикла сессии.

    stream_start — перед первым медиасообщением, stream_end — после последнего, с причиной и счётчиками.
    """
    _tuple_fields: ClassVar[Tuple[str, ...]] = ("modalities", "requested_modalities")

    schema_version:        int = SCHEMA_VERSION
    camera_id:             str
    event:                 Literal["stream_start", "stream_end"]
    wall_ts:               float
    session_start_wall_ts: float
    requested_modalities:  Tuple[str, ...]
    modalities:            Tuple[str, ...]
    degraded:              bool = False
    is_live:               bool = False
    topics:                Dict[str, str]
    video:                 Optional[Dict[str, Any]] = None
    audio:                 Optional[Dict[str, Any]] = None

    reason:      Optional[Literal["eof", "stopped", "error"]] = None
    last_pts_ms: Optional[Dict[str, Optional[int]]] = None
    sent:        Optional[Dict[str, int]] = None
