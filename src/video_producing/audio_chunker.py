import logging
from typing import List, Optional

from .sources.base import AudioChunk


class AudioChunker:
    def __init__(self, sample_rate: int, channels: int, samples_per_chunk: int):
        """
        Нарезка PCM (s16le interleaved) на чанки фиксированной длины.

        Время чанка считается от базы по счётчику сэмплов, а не по времени
        прихода данных — так не накапливается дрейф. Если входной `pts_ms`
        расходится с ожидаемым больше чем на длину чанка (разрыв в потоке),
        накопленный остаток отдаётся неполным чанком и база сбрасывается.

        Args:
            sample_rate (int): Частота дискретизации, Гц.
            channels (int): Количество каналов.
            samples_per_chunk (int): Сэмплов (на канал) в одном чанке.
        """
        self._sample_rate = sample_rate
        self._bytes_per_sample = 2 * channels
        self._samples_per_chunk = samples_per_chunk
        self._chunk_bytes = samples_per_chunk * self._bytes_per_sample
        self._chunk_ms = samples_per_chunk * 1000 / sample_rate
        self.reset()

    def reset(self) -> None:
        self._buf = bytearray()
        self._base_pts_ms: Optional[float] = None
        self._base_wall_ts: Optional[float] = None
        self._emitted_samples = 0

    def _samples_to_ms(self, samples: int) -> float:
        return samples * 1000 / self._sample_rate

    def _expected_pts_ms(self) -> float:
        buffered = len(self._buf) // self._bytes_per_sample
        return self._base_pts_ms + self._samples_to_ms(self._emitted_samples + buffered)

    def _make_chunk(self, pcm: bytes) -> AudioChunk:
        num_samples = len(pcm) // self._bytes_per_sample
        offset_ms = self._samples_to_ms(self._emitted_samples)
        chunk = AudioChunk(
            pts_ms=int(round(self._base_pts_ms + offset_ms)),
            capture_wall_ts=self._base_wall_ts + offset_ms / 1000.0,
            pcm=pcm,
            num_samples=num_samples,
        )
        self._emitted_samples += num_samples
        return chunk

    def push(self, pcm: bytes, pts_ms: Optional[float], capture_wall_ts: float) -> List[AudioChunk]:
        """
        Добавить PCM и получить готовые чанки.

        Args:
            pcm (bytes): Данные s16le interleaved.
            pts_ms (Optional[float]): Время первого сэмпла `pcm`. None — данные
                непрерывно продолжают предыдущие.
            capture_wall_ts (float): Время захвата первого сэмпла `pcm`.

        Returns:
            List[AudioChunk]: Готовые чанки (возможно, пустой список).
        """
        out: List[AudioChunk] = []

        if self._base_pts_ms is None:
            self._base_pts_ms = float(pts_ms) if pts_ms is not None else 0.0
            self._base_wall_ts = capture_wall_ts
        elif pts_ms is not None:
            expected = self._expected_pts_ms()
            if abs(pts_ms - expected) > self._chunk_ms:
                logging.warning(
                    f"Audio discontinuity: got pts {pts_ms:.1f} ms, expected {expected:.1f} ms; "
                    f"rebasing chunker"
                )
                tail = self.flush()
                if tail is not None:
                    out.append(tail)
                self.reset()
                self._base_pts_ms = float(pts_ms)
                self._base_wall_ts = capture_wall_ts

        self._buf.extend(pcm)
        while len(self._buf) >= self._chunk_bytes:
            data = bytes(self._buf[:self._chunk_bytes])
            del self._buf[:self._chunk_bytes]
            out.append(self._make_chunk(data))
        return out

    def flush(self) -> Optional[AudioChunk]:
        """Отдать остаток буфера неполным чанком (или None, если буфер пуст)."""
        usable = len(self._buf) - len(self._buf) % self._bytes_per_sample
        if usable == 0 or self._base_pts_ms is None:
            self._buf.clear()
            return None
        data = bytes(self._buf[:usable])
        self._buf.clear()
        return self._make_chunk(data)
