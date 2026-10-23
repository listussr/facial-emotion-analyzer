"""Проверка источника перед созданием сессии: открывается ли и какие в нём модальности."""

from dataclasses import dataclass
from typing import Optional
from typing import Union

MIC_SOURCE = "mic"


@dataclass
class MediaProbe:
    """Модальности источника; None — неизвестно без захвата, например микрофон вебкамеры."""
    has_video: Optional[bool]
    has_audio: Optional[bool]
    duration_sec: Optional[float] = None


def _probe_webcam(index: int) -> MediaProbe:
    import cv2
    cap = cv2.VideoCapture(index)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Не удалось открыть камеру {index}")
        ok, _ = cap.read()
        if not ok:
            raise RuntimeError(f"Камера {index} открылась, но не отдаёт кадры. "
                               f"Возможно, она занята или не подключена.")
    finally:
        cap.release()
    return MediaProbe(has_video=True, has_audio=None)


def _probe_file(path: str, timeout_s: float) -> MediaProbe:
    import av
    try:
        container = av.open(path, timeout=(timeout_s, timeout_s))
    except Exception as e:
        raise RuntimeError(f"Не удалось открыть источник {path!r}: {e}") from e
    try:
        has_video = bool(container.streams.video)
        has_audio = bool(container.streams.audio)
        duration  = container.duration / av.time_base if container.duration else None
    finally:
        container.close()
    if not has_video and not has_audio:
        raise RuntimeError(f"В источнике {path!r} нет ни видео, ни звука")
    return MediaProbe(has_video=has_video, has_audio=has_audio, duration_sec=duration)


def probe_source(source: Union[str, int], timeout_s: float = 10.0) -> MediaProbe:
    """
    Модальности источника: файлы и сетевые потоки через PyAV, вебкамера через OpenCV.

    Args:
        source    (Union[str, int]) : Путь, URL, индекс камеры или "mic".
        timeout_s (float, optional) : Таймаут открытия, с. Defaults to 10.0.

    Raises:
        RuntimeError: Источник недоступен или пуст.

    Returns:
        MediaProbe: Модальности и длительность.
    """
    if source == MIC_SOURCE:
        return MediaProbe(has_video=False, has_audio=True)
    if isinstance(source, int) or (isinstance(source, str) and source.isdigit()):
        return _probe_webcam(int(source))
    return _probe_file(str(source), timeout_s)


def check_modalities(probe: MediaProbe, requested) -> None:
    """
    Проверка, что источник может дать хотя бы одну запрошенную модальность.

    Raises:
        ValueError: Ни одной запрошенной модальности точно нет.
    """
    available = {"video": probe.has_video, "audio": probe.has_audio}
    if all(available[m] is False for m in requested):
        have = [m for m, v in available.items() if v] or ["ничего"]
        raise ValueError(f"Источник не содержит запрошенных модальностей {list(requested)}; "
                         f"в нём есть: {have}")
