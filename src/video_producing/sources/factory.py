"""Выбор источника медиа по конфигу продюсера."""

from typing import Optional
from typing import TYPE_CHECKING
from typing import Union

from . import browser_source
from .base import MediaSource
from .live_source import LiveSource
from .file_source import FileSource

if TYPE_CHECKING:
    from ..config import CameraConfig

MIC_ONLY_SOURCE = "mic"


def _as_camera_index(source: Union[str, int]) -> Optional[int]:
    if isinstance(source, int):
        return source
    if isinstance(source, str) and source.isdigit():
        return int(source)
    return None


def open_source(config: "CameraConfig") -> MediaSource:
    """
    Выбор источника по конфигу.

    Индекс камеры — LiveSource с вебкамерой, "mic" — LiveSource только с микрофоном,
    "browser:<id>" — зарегистрированный BrowserSource, остальное (файл, rtsp://, http://) — FileSource.

    Args:
        config (CameraConfig): Конфиг продюсера.

    Raises:
        RuntimeError: Источник браузера не зарегистрирован.

    Returns:
        MediaSource: Неоткрытый источник.
    """
    source       = config.source
    camera_index = _as_camera_index(source)

    if isinstance(source, str) and source.startswith(browser_source.BROWSER_PREFIX):
        found = browser_source.get(source[len(browser_source.BROWSER_PREFIX):])
        if found is None:
            raise RuntimeError(f"Browser source {source!r} is not registered")
        return found

    if camera_index is not None or source == MIC_ONLY_SOURCE:
        return LiveSource(
            camera_index=camera_index,
            mic_device=config.audio_device,
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
            samples_per_chunk=config.audio_samples_per_chunk,
        )

    return FileSource(
        path=str(source),
        sample_rate=config.audio_sample_rate,
        channels=config.audio_channels,
        samples_per_chunk=config.audio_samples_per_chunk,
        max_size=(config.max_width, config.max_height),
    )
