"""Энергетический детектор звука: отличает тишину от звука, но не речь от музыки."""

import numpy as np


class EnergyVAD:
    """
    Детектор по уровню сигнала в кадрах по 20 мс.

    Интерфейс speech_prob(samples) позволяет заменить его модельным VAD без изменений в пайплайне.
    """

    def __init__(self, sample_rate: int = 16000, frame_ms: int = 20,
                 threshold_dbfs: float = -45.0):
        self.sample_rate    = sample_rate
        self.frame          = max(1, sample_rate * frame_ms // 1000)
        self.threshold_dbfs = threshold_dbfs

    def frame_levels_dbfs(self, samples: np.ndarray) -> np.ndarray:
        n = samples.size // self.frame
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        frames = samples[: n * self.frame].reshape(n, self.frame)
        rms    = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
        return (20 * np.log10(np.maximum(rms, 1e-10))).astype(np.float32)

    def speech_prob(self, samples: np.ndarray) -> float:
        """
        Доля кадров окна громче порога.

        Args:
            samples (np.ndarray): Моно-сигнал в [-1, 1].

        Returns:
            float: 0 — тишина, 1 — звук во всём окне.
        """
        levels = self.frame_levels_dbfs(samples)
        if levels.size == 0:
            return 0.0
        return float(np.mean(levels > self.threshold_dbfs))
