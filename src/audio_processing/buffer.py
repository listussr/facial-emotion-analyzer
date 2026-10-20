"""
Буфер аудио одной камеры: склейка чанков продюсера, контроль непрерывности,
нарезка перекрывающихся окон.
"""
import logging
from dataclasses import dataclass
from typing import List, Optional

import numpy as np

_log = logging.getLogger("audio.buffer")


@dataclass
class AudioWindow:
    """Окно для модели: моно float32 в [-1, 1] с привязкой ко времени потока."""
    camera_id:       str
    pts_start_ms:    int
    pts_end_ms:      int
    capture_wall_ts: float      # время захвата последнего сэмпла окна
    samples:         np.ndarray


class CameraAudioBuffer:
    def __init__(self, camera_id: str, sample_rate: int, window_ms: int, hop_ms: int,
                 gap_tolerance_ms: int = 150):
        """
        Args:
            camera_id: Камера / сессия.
            sample_rate: Частота дискретизации чанков, Гц.
            window_ms: Длина окна для модели, мс.
            hop_ms: Шаг между началами окон, мс (≤ window_ms — окна перекрываются).
            gap_tolerance_ms: Допустимое расхождение pts соседних чанков; больше —
                разрыв потока: буфер сбрасывается, окна не склеивают звук через дыру.
        """
        if window_ms <= 0 or hop_ms <= 0:
            raise ValueError("window_ms and hop_ms must be positive")
        if hop_ms > window_ms:
            raise ValueError("hop_ms must not exceed window_ms")
        self.camera_id = camera_id
        self.sample_rate = sample_rate
        self.window_ms = window_ms
        self.hop_ms = hop_ms
        self.window_samples = sample_rate * window_ms // 1000
        self.hop_samples = sample_rate * hop_ms // 1000
        self.gap_tolerance_ms = gap_tolerance_ms

        self.resets = 0
        self.seq_gaps = 0
        self._reset_state()

    def _reset_state(self) -> None:
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_start_pts_ms: Optional[float] = None   # pts первого сэмпла в буфере
        self._last_wall_ts: float = 0.0
        self._expected_pts_ms: Optional[float] = None
        self._last_seq: Optional[int] = None

    @staticmethod
    def pcm_to_mono_float(pcm: bytes, channels: int) -> np.ndarray:
        """s16le interleaved → моно float32 в [-1, 1]."""
        x = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
        if channels > 1:
            x = x[: len(x) - len(x) % channels].reshape(-1, channels).mean(axis=1)
        return x

    def push(self, pcm: bytes, pts_ms: int, capture_wall_ts: float, seq: Optional[int] = None,
             channels: int = 1, sample_rate: Optional[int] = None) -> List[AudioWindow]:
        """
        Добавить чанк, вернуть окна, которые стали полными.

        Разрыв (пропуск seq или скачок pts больше gap_tolerance_ms) сбрасывает
        буфер: начатое окно с дырой внутри модели не отдаётся.
        """
        if sample_rate is not None and sample_rate != self.sample_rate:
            raise ValueError(f"sample rate changed: {self.sample_rate} -> {sample_rate}")
        samples = self.pcm_to_mono_float(pcm, channels)
        if samples.size == 0:
            return []

        gap = False
        if seq is not None and self._last_seq is not None and seq != self._last_seq + 1:
            self.seq_gaps += 1
            gap = True
        if self._expected_pts_ms is not None and abs(pts_ms - self._expected_pts_ms) > self.gap_tolerance_ms:
            gap = True
        if gap and self._buf_start_pts_ms is not None:
            _log.info(f"[{self.camera_id}] audio discontinuity at pts {pts_ms} ms "
                      f"(expected {self._expected_pts_ms}); window buffer reset")
            self.resets += 1
            self._reset_state()

        if self._buf_start_pts_ms is None:
            self._buf_start_pts_ms = float(pts_ms)
        self._buf = np.concatenate([self._buf, samples])
        self._last_seq = seq
        self._last_wall_ts = capture_wall_ts + samples.size / self.sample_rate
        self._expected_pts_ms = pts_ms + samples.size * 1000 / self.sample_rate

        windows: List[AudioWindow] = []
        while self._buf.size >= self.window_samples:
            start = self._buf_start_pts_ms
            end = start + self.window_ms
            # время захвата конца окна: от конца буфера назад на «хвост» после окна
            tail_s = (self._buf.size - self.window_samples) / self.sample_rate
            windows.append(AudioWindow(
                camera_id=self.camera_id,
                pts_start_ms=int(round(start)),
                pts_end_ms=int(round(end)),
                capture_wall_ts=self._last_wall_ts - tail_s,
                samples=self._buf[: self.window_samples].copy(),
            ))
            self._buf = self._buf[self.hop_samples:]
            self._buf_start_pts_ms = start + self.hop_ms
        return windows

    @property
    def buffered_ms(self) -> float:
        return self._buf.size * 1000 / self.sample_rate
