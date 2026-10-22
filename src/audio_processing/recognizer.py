"""
Распознавание эмоций по голосу.

Выход на окно — {"logits", "probs", "emb"} или None, если речи нет. Модели: crema-wavlm[-int8] (WavLM-Base+
на CREMA-D с предобработкой как при обучении), stub без файлов, onnx:<путь> или имя из VOICE_MODELS —
произвольная ONNX-модель: вход [N, T] моно 16 кГц, выход логиты [N, 6] и необязательный emb.
"""

import logging
import os
import numpy as np

from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import Dict
from typing import List
from typing import Optional

from src.emotion_labels import NUM_EMOTIONS

STUB_MODEL = "stub"
EMB_DIM    = 256

CREMA_VOICE_MODELS = {
    "crema-wavlm":      "fp32",
    "crema-wavlm-int8": "int8",
}
DEFAULT_VOICE_MODEL = "crema-wavlm-int8"

VOICE_MODELS: Dict[str, Path] = {}

VoiceOutput = Dict[str, Optional[np.ndarray]]


def _softmax(logits: np.ndarray) -> np.ndarray:
    z = np.asarray(logits, np.float64)
    e = np.exp(z - z.max(axis=-1, keepdims=True))
    return (e / e.sum(axis=-1, keepdims=True)).astype(np.float32)


def _output(logits: np.ndarray, emb: Optional[np.ndarray] = None) -> VoiceOutput:
    logits = np.asarray(logits, np.float32).reshape(-1)
    return {
        "logits": logits,
        "probs": _softmax(logits),
        "emb": None if emb is None else np.asarray(emb, np.float32).reshape(-1),
    }


class _VoiceRecognizer(ABC):
    name: str = ""

    @abstractmethod
    def predict(self, windows: List[np.ndarray]) -> List[Optional[VoiceOutput]]:
        """
        Эмоции окон.

        Args:
            windows (List[np.ndarray]): Окна float32 в [-1, 1].

        Returns:
            List[Optional[VoiceOutput]]: Выход на каждое окно; None — речи нет.
        """


class _StubVoiceRecognizer(_VoiceRecognizer):
    """Заглушка: случайная проекция энергии в частотных полосах; к реальным эмоциям отношения не имеет."""
    name   = STUB_MODEL
    _BANDS = 8

    def __init__(self, sample_rate: int = 16000, seed: int = 0):
        rng               = np.random.default_rng(seed)
        self._w           = rng.normal(0.0, 1.5, size=(self._BANDS + 1, NUM_EMOTIONS)).astype(np.float32)
        self._we          = rng.normal(0.0, 1.0, size=(self._BANDS + 1, EMB_DIM)).astype(np.float32)
        self._sample_rate = sample_rate
        logging.warning(
            f"VoiceEmotionRecognizer: using STUB model ({NUM_EMOTIONS} classes) — predictions are not real"
        )

    def _features(self, x: np.ndarray) -> np.ndarray:
        spec      = np.abs(np.fft.rfft(x * np.hanning(x.size))) ** 2
        edges     = np.geomspace(1, spec.size, self._BANDS + 1).astype(int)
        bands     = np.array([spec[a:max(b, a + 1)].sum() for a, b in zip(edges[:-1], edges[1:])])
        log_bands = np.log10(bands + 1e-9)
        log_bands = (log_bands - log_bands.mean()) / (log_bands.std() + 1e-6)
        return np.concatenate([log_bands, [1.0]]).astype(np.float32)

    def predict(self, windows: List[np.ndarray]) -> List[Optional[VoiceOutput]]:
        out = []
        for w in windows:
            f = self._features(w)
            out.append(_output(f @ self._w, np.tanh(f @ self._we)))
        return out


class _CremaVoiceRecognizer(_VoiceRecognizer):
    """
    WavLM-Base+ на CREMA-D через ONNX Runtime CPU.

    Предобработка prepare_audio как при обучении: обрезка тишины, нормировка по пику, не длиннее 4 с;
    меньше 0.3 с речи — выход None. Прогрев при создании компилирует функции librosa заранее.
    """

    def __init__(self, model: str = DEFAULT_VOICE_MODEL, num_threads: Optional[int] = None,
                 sample_rate: int = 16000):
        from src.emotion_kit.backends import AudioONNX
        from src.emotion_kit.preprocess import prepare_audio
        self.name         = model
        self._sample_rate = sample_rate
        self._prepare     = prepare_audio
        self._audio       = AudioONNX(CREMA_VOICE_MODELS[model], threads=num_threads)
        warm              = (0.1 * np.sin(np.arange(sample_rate) / sample_rate * 2 * np.pi * 220)).astype(np.float32)
        self.predict([warm])
        logging.info(f"Voice model '{model}' loaded (threads={num_threads})")

    def predict(self, windows: List[np.ndarray]) -> List[Optional[VoiceOutput]]:
        out: List[Optional[VoiceOutput]] = []
        for w in windows:
            wave = self._prepare(w, self._sample_rate)
            if wave is None:
                out.append(None)
                continue
            r = self._audio.predict(wave)
            out.append(_output(r["logits"], r["emb"]))
        return out


class _OnnxVoiceRecognizer(_VoiceRecognizer):
    """Произвольная ONNX-модель: волна [N, T] -> логиты [N, NUM_EMOTIONS] и emb, если есть."""

    def __init__(self, path: Path, device: str = "cpu", num_threads: Optional[int] = None):
        import onnxruntime as ort

        if not os.path.exists(path):
            raise FileNotFoundError(f"voice model not found: {path}")
        opts                          = ort.SessionOptions()
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if num_threads:
            opts.intra_op_num_threads = num_threads
        providers = ["CPUExecutionProvider"]
        if device == "cuda" and "CUDAExecutionProvider" in ort.get_available_providers():
            providers = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        self._session = ort.InferenceSession(str(path), sess_options=opts, providers=providers)
        self._input   = self._session.get_inputs()[0].name
        outputs       = [o.name for o in self._session.get_outputs()]
        self._outputs = [outputs[0]] + (["emb"] if "emb" in outputs[1:] else [])
        self.name     = Path(path).stem

        out_shape = self._session.get_outputs()[0].shape
        n         = out_shape[-1] if out_shape and isinstance(out_shape[-1], int) else None
        if n is not None and n != NUM_EMOTIONS:
            raise ValueError(f"voice model {path} outputs {n} classes, the system uses {NUM_EMOTIONS}")
        logging.info(f"Voice model {self.name} loaded, providers={self._session.get_providers()}")

    def predict(self, windows: List[np.ndarray]) -> List[Optional[VoiceOutput]]:
        if not windows:
            return []
        batch  = np.stack(windows).astype(np.float32, copy=False)
        res    = self._session.run(self._outputs, {self._input: batch})
        logits = res[0]
        if logits.shape[-1] != NUM_EMOTIONS:
            raise ValueError(f"voice model outputs {logits.shape[-1]} classes, expected {NUM_EMOTIONS}")
        embs = res[1] if len(res) > 1 else [None] * len(logits)
        return [_output(lg, e) for lg, e in zip(logits, embs)]


def available_voice_models() -> List[str]:
    return [STUB_MODEL, *CREMA_VOICE_MODELS, *VOICE_MODELS]


class VoiceEmotionRecognizer:
    """Фабрика распознавателей: stub, crema-wavlm[-int8], имя из VOICE_MODELS или onnx:<путь>."""

    def __new__(cls, model: str = DEFAULT_VOICE_MODEL, device: str = "cpu",
                num_threads: Optional[int] = None, sample_rate: int = 16000) -> _VoiceRecognizer:
        if model == STUB_MODEL:
            return _StubVoiceRecognizer(sample_rate=sample_rate)
        if model in CREMA_VOICE_MODELS:
            if device != "cpu":
                raise ValueError(f"voice model {model!r} supports only device='cpu', got {device!r}")
            return _CremaVoiceRecognizer(model, num_threads, sample_rate)
        if model.startswith("onnx:"):
            return _OnnxVoiceRecognizer(Path(model[len("onnx:"):]), device, num_threads)
        if model in VOICE_MODELS:
            return _OnnxVoiceRecognizer(VOICE_MODELS[model], device, num_threads)
        raise ValueError(f"unknown voice model {model!r}; available: {available_voice_models()}")
