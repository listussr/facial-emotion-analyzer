"""Детекторы речи: энергетический (тишина или звук) и Silero VAD (речь или нет)."""

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


class SileroVAD:
    """
    Модельный детектор речи Silero VAD (ONNX, MIT): отличает речь от музыки, шума и тонов.

    Окно режется на кадры по 512 сэмплов (32 мс при 16 кГц), к каждому добавляются 64 сэмпла
    контекста, состояние RNN переносится между кадрами окна. Окна обрабатываются независимо.
    """

    FRAME   = 512
    CONTEXT = 64

    def __init__(self, model_path: str, sample_rate: int = 16000, frame_threshold: float = 0.5,
                 num_threads: int = 1):
        import onnxruntime as ort

        if sample_rate != 16000:
            raise ValueError("SileroVAD expects 16 kHz audio")
        opts                      = ort.SessionOptions()
        opts.intra_op_num_threads = num_threads
        opts.inter_op_num_threads = 1
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._session        = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        self.sample_rate     = sample_rate
        self.frame_threshold = frame_threshold
        self._sr             = np.array(sample_rate, dtype=np.int64)

    def frame_probs(self, samples: np.ndarray) -> np.ndarray:
        """
        Вероятность речи в каждом кадре 32 мс.

        Args:
            samples (np.ndarray): Моно-сигнал 16 кГц в [-1, 1].

        Returns:
            np.ndarray: Вероятности кадров; неполный последний кадр отбрасывается.
        """
        n = samples.size // self.FRAME
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        x       = samples[: n * self.FRAME].astype(np.float32).reshape(n, self.FRAME)
        state   = np.zeros((2, 1, 128), dtype=np.float32)
        context = np.zeros((1, self.CONTEXT), dtype=np.float32)
        probs   = np.empty(n, dtype=np.float32)
        for i in range(n):
            frame      = np.concatenate([context, x[i:i + 1]], axis=1)
            out, state = self._session.run(None, {"input": frame, "state": state, "sr": self._sr})
            probs[i]   = out[0, 0]
            context    = frame[:, -self.CONTEXT:]
        return probs

    def speech_prob(self, samples: np.ndarray) -> float:
        """
        Доля кадров окна с речью.

        Args:
            samples (np.ndarray): Моно-сигнал 16 кГц в [-1, 1].

        Returns:
            float: 0 — речи нет, 1 — речь во всём окне.
        """
        probs = self.frame_probs(samples)
        if probs.size == 0:
            return 0.0
        return float(np.mean(probs >= self.frame_threshold))
