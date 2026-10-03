import logging
import queue
import threading
from typing import Iterator, List, Optional, Union

from .base import AudioChunk, MediaSource, SessionClock, SourceInfo, VideoFrame

_END = object()


class LiveSource(MediaSource):
    def __init__(self, camera_index: Optional[int], mic_device: Optional[Union[str, int]],
                 sample_rate: int, channels: int, samples_per_chunk: int,
                 queue_size: int = 64):
        """
        Живой захват: вебкамера и/или микрофон с общими часами сессии.

        Каждый источник читается в своём потоке, события сливаются в одну
        очередь. При переполнении выбрасываются видеокадры, аудио — нет
        (пропуск звука заметнее и ломает окна аудиомодели).

        Args:
            camera_index (int, optional): Индекс камеры; None — без видео.
            mic_device (str | int | None): Устройство микрофона (None — по умолчанию).
            sample_rate (int): Частота дискретизации аудио, Гц.
            channels (int): Количество аудиоканалов.
            samples_per_chunk (int): Сэмплов (на канал) в одном аудиочанке.
            queue_size (int): Размер общей очереди событий.
        """
        self._camera_index = camera_index
        self._mic_device = mic_device
        self._sample_rate = sample_rate
        self._channels = channels
        self._samples_per_chunk = samples_per_chunk

        self._clock: Optional[SessionClock] = None
        self._sources: List[MediaSource] = []
        self._threads: List[threading.Thread] = []
        self._queue: "queue.Queue" = queue.Queue(maxsize=queue_size)
        self._stop = threading.Event()
        self._error: Optional[BaseException] = None
        self._dropped_video = 0

    @property
    def dropped_video(self) -> int:
        return self._dropped_video

    def open(self, want_video: bool, want_audio: bool) -> SourceInfo:
        self._clock = SessionClock()
        info = SourceInfo(has_video=False, has_audio=False, is_live=True)
        try:
            if want_video and self._camera_index is not None:
                from .webcam_source import WebcamSource
                cam = WebcamSource(self._camera_index, clock=self._clock)
                cam_info = cam.open()
                self._sources.append(cam)
                info.has_video = True
                info.native_fps = cam_info.native_fps
            if want_audio:
                from .mic_source import MicSource
                mic = MicSource(self._mic_device, self._sample_rate, self._channels,
                                self._samples_per_chunk, clock=self._clock)
                mic.open()
                self._sources.append(mic)
                info.has_audio = True
                info.audio_native_rate = self._sample_rate
        except Exception:
            self.close()
            raise
        return info

    def _pump(self, source: MediaSource) -> None:
        try:
            for item in source.read():
                if self._stop.is_set():
                    break
                if isinstance(item, VideoFrame):
                    try:
                        self._queue.put_nowait(item)
                    except queue.Full:
                        self._dropped_video += 1
                else:
                    while not self._stop.is_set():
                        try:
                            self._queue.put(item, timeout=0.2)
                            break
                        except queue.Full:
                            continue
        except BaseException as e:
            if not self._stop.is_set():
                logging.error(f"LiveSource worker {type(source).__name__} failed: {e}")
                self._error = e
        finally:
            while True:
                try:
                    self._queue.put(_END, timeout=0.2)
                    break
                except queue.Full:
                    # Читатель ушёл — освобождаем место под маркер конца
                    try:
                        self._queue.get_nowait()
                    except queue.Empty:
                        pass

    def read(self) -> Iterator[Union[VideoFrame, AudioChunk]]:
        if not self._sources:
            return
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._pump, args=(s,), daemon=True,
                             name=f"live-{type(s).__name__}")
            for s in self._sources
        ]
        for t in self._threads:
            t.start()

        finished = 0
        while finished < len(self._threads):
            item = self._queue.get()
            if item is _END:
                finished += 1
                # Обрыв одного источника — обрыв всей живой сессии:
                # продюсер переподключится целиком, с теми же часами нельзя.
                if self._error is not None:
                    raise RuntimeError(f"Live source failed: {self._error}") from self._error
                if not self._stop.is_set():
                    self._stop.set()
                    for s in self._sources:
                        s.close()
                continue
            yield item

    def close(self) -> None:
        self._stop.set()
        for s in self._sources:
            try:
                s.close()
            except Exception:
                logging.exception("LiveSource: failed to close source")
        for t in self._threads:
            t.join(timeout=2.0)
        self._sources = []
        self._threads = []
