"""Захват звука с микрофона через sounddevice."""

import logging
import queue
import time

from typing import Dict
from typing import Iterator
from typing import List
from typing import Optional
from typing import Union

from ..audio_chunker import AudioChunker
from .base import AudioChunk
from .base import MediaSource
from .base import SessionClock
from .base import SourceInfo


class MicSource(MediaSource):
    """
    Микрофон через sounddevice.

    Колбэк PortAudio только кладёт блок в очередь, нарезка на чанки — в потоке читателя.
    Время блока — момент оцифровки, пересчитанный на часы сессии.
    """

    def __init__(self, device: Optional[Union[str, int]], sample_rate: int, channels: int,
                 samples_per_chunk: int, clock: Optional[SessionClock] = None, block_ms: int = 20):
        import sounddevice
        self._sd = sounddevice

        self._device      = device
        self._sample_rate = sample_rate
        self._channels    = channels
        self._block       = max(1, sample_rate * block_ms // 1000)
        self._clock       = clock or SessionClock()
        self._chunker     = AudioChunker(sample_rate, channels, samples_per_chunk)

        self._queue: "queue.Queue" = queue.Queue(maxsize=500)
        self._stream = None
        self._closed = False
        self._clock_offset: float = 0.0

    def open(self, want_video: bool = False, want_audio: bool = True) -> SourceInfo:
        self._stream = self._sd.InputStream(
            device=self._device,
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            blocksize=self._block,
            callback=self._callback,
        )
        self._stream.start()
        self._clock_offset = self._stream.time - time.monotonic()
        self._closed       = False
        logging.info(
            f"MicSource opened device={self._device!r} rate={self._sample_rate} "
            f"channels={self._channels}"
        )
        return SourceInfo(
            has_video=False, has_audio=True, is_live=True,
            audio_native_rate=self._sample_rate,
        )

    def _callback(self, indata, frames, time_info, status) -> None:
        """
        Блок PortAudio в очередь.

        Время АЦП берётся, только если драйвер отдаёт осмысленное текущее время: MME на Windows
        даёт мусор, тогда время блока — момент колбэка минус его длительность.
        """
        if status:
            logging.warning(f"Microphone status: {status}")
        adc     = getattr(time_info, "inputBufferAdcTime", 0.0) or 0.0
        current = getattr(time_info, "currentTime", 0.0) or 0.0
        if current > 0 and 0 <= current - adc < 1.0:
            t = adc - self._clock_offset
        else:
            t = time.monotonic() - frames / self._sample_rate
        try:
            self._queue.put_nowait((bytes(indata), t))
        except queue.Full:
            logging.warning("Microphone queue overflow, dropping block")

    def read(self) -> Iterator[AudioChunk]:
        if self._stream is None:
            raise RuntimeError("MicSource.read() called before open()")
        while not self._closed:
            try:
                pcm, t = self._queue.get(timeout=0.5)
            except queue.Empty:
                if self._stream is not None and not self._stream.active and not self._closed:
                    raise RuntimeError("Microphone stream stopped")
                continue
            pts_ms = self._clock.from_monotonic(t)
            yield from self._chunker.push(pcm, pts_ms, self._clock.wall_ts(pts_ms))
        tail = self._chunker.flush()
        if tail is not None:
            yield tail

    def close(self) -> None:
        self._closed         = True
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                logging.exception("MicSource close failed")


def list_input_devices() -> List[Dict]:
    """
    Устройства ввода для выбора audio_device.

    Returns:
        List[Dict]: Индекс, имя, число каналов и частота по умолчанию.
    """
    import sounddevice as sd
    return [
        {"index": i, "name": d["name"], "channels": d["max_input_channels"],
         "default_samplerate": d["default_samplerate"]}
        for i, d in enumerate(sd.query_devices())
        if d["max_input_channels"] > 0
    ]
