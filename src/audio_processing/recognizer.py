"""
Распознавание эмоций по голосу.

Пока обученной модели нет, по умолчанию работает ЗАГЛУШКА (`stub`).
Реальная модель подключается через ONNX:
  - вход: float32 [N, T] — моно волна 16 кГц в [-1, 1] (T — длина окна);
  - выход: [N, NUM_EMOTIONS] — логиты в порядке src/emotion_labels.EMOTION_LABELS.
Зарегистрировать модель: добавить имя → путь в VOICE_MODELS или передать
`model='onnx:<путь>'`.
"""
import logging
import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from src.emotion_labels import NUM_EMOTIONS

STUB_MODEL = 'stub'

_MODELS_DIR = Path(__file__).resolve().parent / 'models'

# Имя модели → путь к .onnx. Заполнить, когда появится модель на CREMA-D.
VOICE_MODELS: Dict[str, Path] = {}


def _softmax(logits: np.ndarray) -> np.ndarray:
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


class _VoiceRecognizer(ABC):
    name: str = ''

    @abstractmethod
    def predict(self, windows: List[np.ndarray]) -> np.ndarray:
        """Окна float32 [-1, 1] → вероятности формы (N, NUM_EMOTIONS)."""


class _StubVoiceRecognizer(_VoiceRecognizer):
    """
    ЗАГЛУШКА: фиксированная случайная проекция энергии в частотных полосах → softmax.

    Похожий звук даёт похожий выход, поэтому сглаживание и слияние ведут себя
    правдоподобно. К реальным эмоциям выход отношения НЕ имеет.
    """
    name = STUB_MODEL
    _BANDS = 8

    def __init__(self, sample_rate: int = 16000, seed: int = 0):
        rng = np.random.default_rng(seed)
        self._w = rng.normal(0.0, 1.5, size=(self._BANDS + 1, NUM_EMOTIONS)).astype(np.float32)
        self._sample_rate = sample_rate
        logging.warning(
            f"VoiceEmotionRecognizer: using STUB model ({NUM_EMOTIONS} classes) — predictions are not real"
        )

    def _features(self, x: np.ndarray) -> np.ndarray:
        spec = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
        edges = np.geomspace(1, spec.size, self._BANDS + 1).astype(int)
        bands = np.array([spec[a:max(b, a + 1)].sum() for a, b in zip(edges[:-1], edges[1:])])
        log_bands = np.log10(bands + 1e-9)
        log_bands = (log_bands - log_bands.mean()) / (log_bands.std() + 1e-6)
        return np.concatenate([log_bands, [1.0]]).astype(np.float32)

    def predict(self, windows: List[np.ndarray]) -> np.ndarray:
        if not windows:
            return np.zeros((0, NUM_EMOTIONS), dtype=np.float32)
        feats = np.stack([self._features(w) for w in windows])
        return _softmax(feats @ self._w)


class _OnnxVoiceRecognizer(_VoiceRecognizer):
    """ONNX-модель: волна [N, T] → логиты [N, NUM_EMOTIONS]."""

    def __init__(self, path: Path, device: str = 'cpu', num_threads: Optional[int] = None):
        import onnxruntime as ort

        if not os.path.exists(path):
            raise FileNotFoundError(f"voice model not found: {path}")
        opts = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if num_threads:
            opts.intra_op_num_threads = num_threads
        providers = ['CPUExecutionProvider']
        if device == 'cuda' and 'CUDAExecutionProvider' in ort.get_available_providers():
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
        self._session = ort.InferenceSession(str(path), sess_options=opts, providers=providers)
        self._input = self._session.get_inputs()[0].name
        self.name = Path(path).stem

        out_shape = self._session.get_outputs()[0].shape
        n = out_shape[-1] if out_shape and isinstance(out_shape[-1], int) else None
        if n is not None and n != NUM_EMOTIONS:
            raise ValueError(f"voice model {path} outputs {n} classes, the system uses {NUM_EMOTIONS}")
        logging.info(f"Voice model {self.name} loaded, providers={self._session.get_providers()}")

    def predict(self, windows: List[np.ndarray]) -> np.ndarray:
        if not windows:
            return np.zeros((0, NUM_EMOTIONS), dtype=np.float32)
        batch = np.stack(windows).astype(np.float32, copy=False)
        logits = self._session.run(None, {self._input: batch})[0]
        if logits.shape[-1] != NUM_EMOTIONS:
            raise ValueError(f"voice model outputs {logits.shape[-1]} classes, expected {NUM_EMOTIONS}")
        return _softmax(logits)


class VoiceEmotionRecognizer:
    """Фабрика: 'stub' | имя из VOICE_MODELS | 'onnx:<путь>'."""

    def __new__(cls, model: str = STUB_MODEL, device: str = 'cpu',
                num_threads: Optional[int] = None, sample_rate: int = 16000) -> _VoiceRecognizer:
        if model == STUB_MODEL:
            return _StubVoiceRecognizer(sample_rate=sample_rate)
        if model.startswith('onnx:'):
            return _OnnxVoiceRecognizer(Path(model[len('onnx:'):]), device, num_threads)
        if model in VOICE_MODELS:
            return _OnnxVoiceRecognizer(VOICE_MODELS[model], device, num_threads)
        raise ValueError(f"unknown voice model {model!r}; available: {[STUB_MODEL, *VOICE_MODELS]}")
