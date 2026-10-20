import time
from dataclasses import dataclass, field, fields, replace
from typing import Any, ClassVar, Dict, Optional, Type, TypeVar

import msgpack

SCHEMA_VERSION = 1

S = TypeVar('S', bound='_Section')


@dataclass(kw_only=True)
class _Section:
    """
    Базовая секция настроек одного сервиса.

    Поле со значением None - «не задано, использовать значение по умолчанию сервиса».
    """

    def to_dict(self) -> Dict[str, Any]:
        """Словарь только с заданными (не None) полями — для компактной записи в Kafka."""
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }

    @classmethod
    def from_dict(cls: Type[S], data: Optional[Dict[str, Any]]) -> Optional[S]:
        """
        Секция из словаря. Неизвестные ключи игнорируются.
        None или не-словарь → None (секция не задана).

        Пустой словарь тоже → None: секция без заданных полей и отсутствующая
        секция означают одно и то же — «всё по умолчанию».
        """
        if not data or not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def merged_over(self: S, defaults: S) -> S:
        """
        Итоговые настройки: копия `defaults`, поверх которой наложены
        заданные (не None) поля этой секции.
        """
        return replace(defaults, **self.to_dict())

    def updated(self: S, **changes: Any) -> S:
        """
        Копия секции с изменёнными полями (для CLI `set` / `unset`).
        Неизвестное имя поля → ValueError.
        """
        known = {f.name for f in fields(self)}
        for k in changes:
            if k not in known:
                raise ValueError(f'field {k} not in {type(self).__name__} fields: {sorted(known)}')
        return replace(self, **changes)


@dataclass(kw_only=True)
class VisionSettings(_Section):
    """Настройки видеопайплайна для сессии."""
    tracker:             Optional[str] = None   # 'bytetrack' | 'deepsort'
    model:               Optional[str] = None   # 'resnet-18', 'convnext', ...
    device:              Optional[str] = None   # 'cpu' | 'cuda'
    detection_frequency: Optional[int] = None   # детекция раз в N кадров
    emotion_frequency:   Optional[int] = None   # эмоции раз в N кадров
    identification:      Optional[bool] = None  # False — без FaceNet и записи эмбеддингов


@dataclass(kw_only=True)
class AudioSettings(_Section):
    """Настройки аудиоконсьюмера для сессии."""
    model:         Optional[str] = None
    device:        Optional[str] = None
    vad_threshold: Optional[float] = None       # порог вероятности речи
    window_ms:     Optional[int] = None         # длина окна для модели эмоций
    hop_ms:        Optional[int] = None         # шаг окна


@dataclass(kw_only=True)
class FusionSettings(_Section):
    """Настройки слияния модальностей для сессии."""
    face_weight:      Optional[float] = None
    voice_weight:     Optional[float] = None
    voice_max_age_ms: Optional[int] = None      # насколько старая голосовая оценка ещё учитывается


@dataclass(kw_only=True)
class SessionConfig:
    """
    Конфиг обработки одной сессии (значение в топике `session-config`, ключ — camera_id).

    `revision` растёт при каждом изменении: более старая ревизия никогда
    не перетирает более новую (повторная доставка, гонка двух писателей).
    """
    _sections: ClassVar[Dict[str, Type[_Section]]] = {
        'vision': VisionSettings,
        'audio':  AudioSettings,
        'fusion': FusionSettings,
    }
    _scalar_fields: ClassVar[tuple] = ('schema_version', 'camera_id', 'revision', 'updated_at')

    schema_version: int = SCHEMA_VERSION
    camera_id:      str
    revision:       int
    updated_at:     float = field(default_factory=time.time)
    vision:         Optional[VisionSettings] = None
    audio:          Optional[AudioSettings] = None
    fusion:         Optional[FusionSettings] = None

    def to_dict(self) -> Dict[str, Any]:
        """Плоский словарь для msgpack; незаданные секции не пишутся."""
        data: Dict[str, Any] = {name: getattr(self, name) for name in self._scalar_fields}
        for name in self._sections:
            section = getattr(self, name)
            if section is not None:
                data[name] = section.to_dict()
        return data

    def to_msgpack(self) -> bytes:
        """Сериализация для записи в Kafka."""
        return msgpack.packb(self.to_dict(), use_bin_type=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SessionConfig':
        """
        Конфиг из словаря. Неизвестные поля и секции игнорируются,
        секции разбираются своими классами. Нет camera_id или revision → ValueError.
        """
        if not isinstance(data, dict):
            raise ValueError(f'SessionConfig must be a dict, got {type(data).__name__}')
        if 'camera_id' not in data or 'revision' not in data:
            raise ValueError('No `camera_id` or `revision` in dict')

        kwargs: Dict[str, Any] = {k: data[k] for k in cls._scalar_fields if k in data}
        for name, section_cls in cls._sections.items():
            kwargs[name] = section_cls.from_dict(data.get(name))
        return cls(**kwargs)

    @classmethod
    def from_msgpack(cls, raw: bytes) -> 'SessionConfig':
        """Десериализация значения из Kafka."""
        return cls.from_dict(msgpack.unpackb(raw, raw=False))

    def section(self, name: str) -> Optional[_Section]:
        """Секция по имени ('vision' | 'audio' | 'fusion'); неизвестное имя → KeyError."""
        if name not in self._sections:
            raise KeyError(f'unknown section {name!r}, expected one of {sorted(self._sections)}')
        return getattr(self, name)

    def is_newer_than(self, other: Optional['SessionConfig']) -> bool:
        """True, если этот конфиг должен заменить `other` (other is None или ревизия больше)."""
        return other is None or self.revision > other.revision

    def with_section(self, name: str, section: Optional[_Section], revision: int) -> 'SessionConfig':
        """
        Копия конфига с заменённой секцией, новой ревизией и updated_at=now.
        Используется CLI для частичного обновления.
        """
        section_cls = self._sections.get(name)
        if section_cls is None:
            raise KeyError(f'unknown section {name!r}, expected one of {sorted(self._sections)}')
        if section is not None and not isinstance(section, section_cls):
            raise TypeError(f'section {name!r} must be {section_cls.__name__}, got {type(section).__name__}')
        return replace(self, **{name: section}, revision=revision, updated_at=time.time())


def next_revision(current: Optional[SessionConfig]) -> int:
    """
    Следующая ревизия: max(current.revision + 1, time.time_ns()).
    Монотонна для одного писателя и почти всегда — для нескольких.
    """
    now = time.time_ns()
    if current is None:
        return now
    return max(current.revision + 1, now)
