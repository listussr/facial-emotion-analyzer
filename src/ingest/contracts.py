"""Команды запуска и остановки сессий (session-commands) и их статусы (session-status), msgpack."""

import time
import msgpack

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from typing import Any
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Type
from typing import TypeVar

SCHEMA_VERSION = 1

SessionKind = Literal["camera", "upload", "browser"]
SessionState = Literal["starting", "live", "running", "done", "offline", "error"]

ACTIVE_STATES = ("starting", "live", "running")

T = TypeVar("T", bound="_Message")


class _Message:
    """Плоский msgpack-словарь; при чтении неизвестные поля игнорируются."""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_msgpack(self) -> bytes:
        return msgpack.packb(self.to_dict(), use_bin_type=True)

    @classmethod
    def from_dict(cls: Type[T], data: Dict[str, Any]) -> T:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @classmethod
    def from_msgpack(cls: Type[T], raw: bytes) -> T:
        return cls.from_dict(msgpack.unpackb(raw, raw=False))


@dataclass(kw_only=True)
class SessionSpec(_Message):
    """
    Что запустить: источник, частота и параметры продюсера.

    source — адрес потока или номер камеры; для загрузки — ключ объекта в хранилище (upload_key),
    для браузера медиа придёт по WebSocket сервиса приёма. config — настройки API, только для показа.
    """
    camera_id:  str
    kind:       SessionKind
    name:       str
    source:     str = ""
    frame_rate: int = 20
    modalities: List[str] = field(default_factory=lambda: ["video", "audio"])
    quality:    int = 60
    max_width:  int = 854
    max_height: int = 480
    upload_id:  Optional[str] = None
    upload_key: Optional[str] = None
    filename:   Optional[str] = None
    file_size:  Optional[int] = None
    config:     Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)


@dataclass(kw_only=True)
class SessionCommand(_Message):
    """Команда сервису приёма; stop не требует spec."""
    schema_version: int = SCHEMA_VERSION
    action:         Literal["start", "stop"]
    camera_id:      str
    issued_at:      float = field(default_factory=time.time)
    spec:           Optional[Dict[str, Any]] = None


@dataclass(kw_only=True)
class SessionStatus(_Message):
    """
    Состояние сессии от сервиса приёма; публикуется раз в секунду, пока сессия идёт.

    stats — статистика продюсера: fps, progress, position_sec, video_duration_sec, modalities, degraded, audio.
    ingest_address — host:port узла-владельца для других узлов приёма (пересылка камеры браузера).
    """
    schema_version: int = SCHEMA_VERSION
    camera_id:      str
    state:          SessionState
    spec:           Dict[str, Any]
    stats:          Dict[str, Any] = field(default_factory=dict)
    frames_sent:    int = 0
    errors:         int = 0
    error:          Optional[str] = None
    ingest_id:      str = ""
    ingest_address: str = ""
    updated_at:     float = field(default_factory=time.time)
    ended_at:       Optional[float] = None

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES
