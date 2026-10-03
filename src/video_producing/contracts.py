from dataclasses import asdict, dataclass, fields
from typing import Any, ClassVar, Dict, Literal, Optional, Tuple, Type, TypeVar

import msgpack

SCHEMA_VERSION = 2

T = TypeVar('T', bound='_Contract')


class _Contract:
    """
    Общая (де)сериализация контрактов.

    Сообщение - плоский msgpack-словарь. При чтении неизвестные поля
    игнорируются, чтобы новые поля не ломали старых консьюмеров.
    """
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
        known = {f.name for f in fields(cls)}
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
    """Общие поля любого медиасообщения."""
    _tuple_fields: ClassVar[Tuple[str, ...]] = ('modalities',)

    schema_version:        int = SCHEMA_VERSION
    camera_id:             str
    stream:                Literal['video', 'audio']
    seq:                   int      # свой счётчик для каждого потока
    pts_ms:                int      # время внутри медиапотока от начала сессии
    modalities:            Tuple[str, ...]
    capture_wall_ts:       float    # время захвата
    session_start_wall_ts: float


@dataclass(kw_only=True)
class VideoMessage(MediaEnvelope):
    """Кадр видеопотока (топик `video_topic`)."""
    stream:           Literal['video'] = 'video'
    frame_id:         str               # legacy-ключ склейки в аннотаторе
    timestamp:        float             # legacy, равен capture_wall_ts
    frame_data:       bytes             # JPEG
    format:           str = 'jpeg'
    quality:          int
    frame_rate:       int
    processed_width:  int
    processed_height: int
    original_width:   int
    original_height:  int


@dataclass(kw_only=True)
class AudioMessage(MediaEnvelope):
    """Аудиочанк (топик `audio_topic`)."""
    stream:        Literal['audio'] = 'audio'
    sample_rate:   int
    channels:      int
    sample_format: str = 's16le'
    num_samples:   int                  # на канал
    audio_data:    bytes                # interleaved PCM
    duration_ms:   int = 0              # вычисляется из num_samples / sample_rate

    def __post_init__(self) -> None:
        self.duration_ms = self.num_samples * 1000 // self.sample_rate


@dataclass(kw_only=True)
class SessionEvent(_Contract):
    """
    Событие жизненного цикла сессии (топик `events_topic`).

    - `stream_start` — один раз, перед первым медиасообщением: итоговые
      модальности и параметры потоков.
    - `stream_end` — один раз, после последнего медиасообщения: причина
      завершения, последние `pts_ms` и сколько сообщений отправлено
      (консьюмер может сверить с полученным).
    """
    _tuple_fields: ClassVar[Tuple[str, ...]] = ('modalities', 'requested_modalities')

    schema_version:        int = SCHEMA_VERSION
    camera_id:             str
    event:                 Literal['stream_start', 'stream_end']
    wall_ts:               float
    session_start_wall_ts: float
    requested_modalities:  Tuple[str, ...]
    modalities:            Tuple[str, ...]
    degraded:              bool = False
    is_live:               bool = False
    topics:                Dict[str, str]                  # {'video': ..., 'audio': ...}
    video:                 Optional[Dict[str, Any]] = None  # параметры видеопотока
    audio:                 Optional[Dict[str, Any]] = None  # параметры аудиопотока

    # только для stream_end
    reason:      Optional[Literal['eof', 'stopped', 'error']] = None
    last_pts_ms: Optional[Dict[str, Optional[int]]] = None
    sent:        Optional[Dict[str, int]] = None
