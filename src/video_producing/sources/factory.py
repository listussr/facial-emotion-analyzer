from typing import TYPE_CHECKING, Optional, Union

from .base import MediaSource

from .live_source import LiveSource
from .file_source import FileSource

if TYPE_CHECKING:
    from ..config import CameraConfig

MIC_ONLY_SOURCE = 'mic'


def _as_camera_index(source: Union[str, int]) -> Optional[int]:
    if isinstance(source, int):
        return source
    if isinstance(source, str) and source.isdigit():
        return int(source)
    return None


def open_source(config: "CameraConfig") -> MediaSource:
    """
    Выбор источника по конфигу.

    - целое число / строка из цифр -> `LiveSource` (вебкамера и/или микрофон);
    - 'mic' -> `LiveSource` только с микрофоном;
    - иначе (путь к файлу, rtsp://, http:// …) -> `FileSource`.

    `av` (PyAV) загружается вместе с пакетом. `sounddevice` — лениво, внутри
    `MicSource`: PortAudio нужен только для живого захвата микрофона.
    """
    source = config.source
    camera_index = _as_camera_index(source)

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
    )
