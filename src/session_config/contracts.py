"""Конфиг обработки сессии в топике session-config: секции настроек видео, голоса и слияния."""

import time
import msgpack

from dataclasses import dataclass
from dataclasses import field
from dataclasses import fields
from dataclasses import replace
from typing import Any
from typing import ClassVar
from typing import Dict
from typing import Optional
from typing import Type
from typing import TypeVar

SCHEMA_VERSION = 1

S = TypeVar("S", bound="_Section")


@dataclass(kw_only=True)
class _Section:
    """
    Базовая секция настроек одного сервиса.

    Поле со значением None — не задано, сервис использует своё значение по умолчанию.
    """

    def to_dict(self) -> Dict[str, Any]:
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if getattr(self, f.name) is not None
        }

    @classmethod
    def from_dict(cls: Type[S], data: Optional[Dict[str, Any]]) -> Optional[S]:
        """
        Секция из словаря, неизвестные ключи игнорируются.

        Args:
            data (Optional[Dict[str, Any]]): Словарь секции.

        Returns:
            Optional[S]: Секция или None, если она не задана или пуста.
        """
        if not data or not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def merged_over(self: S, defaults: S) -> S:
        """
        Значения по умолчанию, поверх которых наложены заданные поля секции.

        Args:
            defaults (S): Значения по умолчанию сервиса.

        Returns:
            S: Итоговые настройки.
        """
        return replace(defaults, **self.to_dict())

    def updated(self: S, **changes: Any) -> S:
        """
        Копия секции с изменёнными полями.

        Raises:
            ValueError: Неизвестное имя поля.

        Returns:
            S: Новая секция.
        """
        known = {f.name for f in fields(self)}
        for k in changes:
            if k not in known:
                raise ValueError(f"field {k} not in {type(self).__name__} fields: {sorted(known)}")
        return replace(self, **changes)


@dataclass(kw_only=True)
class VisionSettings(_Section):
    """Настройки видеопайплайна для сессии."""
    tracker:             Optional[str] = None
    model:               Optional[str] = None
    device:              Optional[str] = None
    detection_frequency: Optional[int] = None
    clip_frames:         Optional[int] = None
    emotion_hop_ms:      Optional[int] = None
    identification:      Optional[bool] = None


@dataclass(kw_only=True)
class AudioSettings(_Section):
    """Настройки аудиоконсьюмера для сессии."""
    model:         Optional[str] = None
    device:        Optional[str] = None
    vad_threshold: Optional[float] = None
    window_ms:     Optional[int] = None
    hop_ms:        Optional[int] = None


@dataclass(kw_only=True)
class FusionSettings(_Section):
    """Настройки слияния модальностей для сессии."""
    method:           Optional[str] = None
    voice_max_age_ms: Optional[int] = None
    min_overlap_ms:   Optional[int] = None


@dataclass(kw_only=True)
class SessionConfig:
    """
    Конфиг обработки одной сессии, ключ в топике — camera_id.

    Ревизия растёт при каждом изменении, более старая никогда не перетирает более новую.
    """
    _sections: ClassVar[Dict[str, Type[_Section]]] = {
        "vision": VisionSettings,
        "audio":  AudioSettings,
        "fusion": FusionSettings,
    }
    _scalar_fields: ClassVar[tuple] = ("schema_version", "camera_id", "revision", "updated_at")

    schema_version: int = SCHEMA_VERSION
    camera_id:      str
    revision:       int
    updated_at:     float = field(default_factory=time.time)
    vision:         Optional[VisionSettings] = None
    audio:          Optional[AudioSettings] = None
    fusion:         Optional[FusionSettings] = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {name: getattr(self, name) for name in self._scalar_fields}
        for name in self._sections:
            section = getattr(self, name)
            if section is not None:
                data[name] = section.to_dict()
        return data

    def to_msgpack(self) -> bytes:
        return msgpack.packb(self.to_dict(), use_bin_type=True)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionConfig":
        """
        Конфиг из словаря, неизвестные поля и секции игнорируются.

        Args:
            data (Dict[str, Any]): Словарь конфига.

        Raises:
            ValueError: Не словарь или нет camera_id / revision.

        Returns:
            SessionConfig: Конфиг сессии.
        """
        if not isinstance(data, dict):
            raise ValueError(f"SessionConfig must be a dict, got {type(data).__name__}")
        if "camera_id" not in data or "revision" not in data:
            raise ValueError("No `camera_id` or `revision` in dict")

        kwargs: Dict[str, Any] = {k: data[k] for k in cls._scalar_fields if k in data}
        for name, section_cls in cls._sections.items():
            kwargs[name] = section_cls.from_dict(data.get(name))
        return cls(**kwargs)

    @classmethod
    def from_msgpack(cls, raw: bytes) -> "SessionConfig":
        return cls.from_dict(msgpack.unpackb(raw, raw=False))

    def section(self, name: str) -> Optional[_Section]:
        if name not in self._sections:
            raise KeyError(f"unknown section {name!r}, expected one of {sorted(self._sections)}")
        return getattr(self, name)

    def is_newer_than(self, other: Optional["SessionConfig"]) -> bool:
        return other is None or self.revision > other.revision

    def with_section(self, name: str, section: Optional[_Section], revision: int) -> "SessionConfig":
        """
        Копия конфига с заменённой секцией, новой ревизией и текущим updated_at.

        Args:
            name     (str)               : Имя секции.
            section  (Optional[_Section]): Новая секция или None.
            revision (int)               : Новая ревизия.

        Raises:
            KeyError : Неизвестная секция.
            TypeError: Секция не того класса.

        Returns:
            SessionConfig: Новый конфиг.
        """
        section_cls = self._sections.get(name)
        if section_cls is None:
            raise KeyError(f"unknown section {name!r}, expected one of {sorted(self._sections)}")
        if section is not None and not isinstance(section, section_cls):
            raise TypeError(f"section {name!r} must be {section_cls.__name__}, got {type(section).__name__}")
        return replace(self, **{name: section}, revision=revision, updated_at=time.time())


def next_revision(current: Optional[SessionConfig]) -> int:
    """
    Следующая ревизия: max(current.revision + 1, time.time_ns()).

    Args:
        current (Optional[SessionConfig]): Текущий конфиг.

    Returns:
        int: Ревизия.
    """
    now = time.time_ns()
    if current is None:
        return now
    return max(current.revision + 1, now)
