"""Модели эмоций лица по окну кадров: CREMA-D Swin-T и заглушка с тем же контрактом."""

import logging
import cv2
import numpy as np

from abc import ABC
from abc import abstractmethod
from typing import Dict
from typing import Optional

from src.emotion_labels import NUM_EMOTIONS

STUB_MODEL = "stub"
EMB_DIM    = 256

CREMA_MODELS = {
    "crema-swin":      "fp32",
    "crema-swin-int8": "int8",
}
DEFAULT_CLIP_MODEL = "crema-swin-int8"


def _softmax(z: np.ndarray) -> np.ndarray:
    z = np.asarray(z, np.float64)
    e = np.exp(z - z.max())
    return (e / e.sum()).astype(np.float32)


class ClipEmotionModel(ABC):
    """
    Модель эмоций по окну.

    Кадровый энкодер переводит кроп в признак, временная голова по признакам окна выдаёт
    {"logits": (6,), "probs": (6,), "emb": (256,)}. Кадры кодируются по мере поступления,
    голова запускается раз в шаг окна.
    """
    name: str = ""
    feat_dim: int = 0

    @abstractmethod
    def encode(self, faces_rgb: np.ndarray) -> np.ndarray:
        """
        Признаки кропов лица.

        Args:
            faces_rgb (np.ndarray): Кропы (N, 224, 224, 3) RGB uint8.

        Returns:
            np.ndarray: Признаки (N, feat_dim).
        """

    @abstractmethod
    def predict(self, feats: np.ndarray, times: np.ndarray) -> Dict[str, np.ndarray]:
        """
        Эмоция окна.

        Args:
            feats (np.ndarray): Признаки кадров окна (T, feat_dim).
            times (np.ndarray): Времена кадров на шкале обучения (T,).

        Returns:
            Dict[str, np.ndarray]: logits, probs и emb.
        """


class CremaClipModel(ClipEmotionModel):
    """FER-Swin-T по кадрам и временной трансформер CREMA-D на ONNX Runtime CPU."""
    feat_dim = 768

    def __init__(self, model: str = DEFAULT_CLIP_MODEL, num_threads: Optional[int] = None):
        from src.emotion_kit.backends import VideoONNX
        from src.emotion_kit.preprocess import faces_to_input
        self.name      = model
        self._to_input = faces_to_input
        self._video    = VideoONNX(CREMA_MODELS[model], threads=num_threads)
        logging.info(f"Clip emotion model '{model}' loaded (threads={num_threads})")

    def encode(self, faces_rgb: np.ndarray) -> np.ndarray:
        return self._video.encode_frames(self._to_input(faces_rgb))

    def predict(self, feats: np.ndarray, times: np.ndarray) -> Dict[str, np.ndarray]:
        return self._video.predict_features(feats, times)


class StubClipModel(ClipEmotionModel):
    """
    Заглушка с контрактом CREMA-модели для тестов и запуска без файлов моделей.

    Признак кадра — яркостная статистика кропа, голова — фиксированная случайная проекция;
    к реальным эмоциям отношения не имеет.
    """
    name     = STUB_MODEL
    feat_dim = 8

    def __init__(self, seed: int = 0):
        rng      = np.random.default_rng(seed)
        self._w  = rng.normal(0.0, 3.0, size=(self.feat_dim, NUM_EMOTIONS)).astype(np.float32)
        self._b  = rng.normal(0.0, 0.5, size=NUM_EMOTIONS).astype(np.float32)
        self._we = rng.normal(0.0, 1.0, size=(self.feat_dim, EMB_DIM)).astype(np.float32)
        logging.warning(
            f"Clip emotion model: using STUB ({NUM_EMOTIONS} classes) — predictions are not real"
        )

    def encode(self, faces_rgb: np.ndarray) -> np.ndarray:
        out = []
        for face in faces_rgb:
            small = cv2.resize(face, (16, 16), interpolation=cv2.INTER_AREA).reshape(-1, 3) / 255.0
            out.append(np.concatenate([small.mean(0), small.std(0), [1.0, 0.0]]))
        return np.asarray(out, np.float32).reshape(-1, self.feat_dim)

    def predict(self, feats: np.ndarray, times: np.ndarray) -> Dict[str, np.ndarray]:
        f      = np.asarray(feats, np.float32).mean(axis=0)
        logits = (f @ self._w + self._b).astype(np.float32)
        emb    = np.tanh(f @ self._we).astype(np.float32)
        return {"logits": logits, "probs": _softmax(logits), "emb": emb}


def available_clip_models():
    return [STUB_MODEL, *CREMA_MODELS]


def load_clip_model(model: str = DEFAULT_CLIP_MODEL, device: str = "cpu",
                    num_threads: Optional[int] = None) -> ClipEmotionModel:
    """
    Фабрика моделей эмоций по окну.

    Args:
        model       (str, optional): "stub", "crema-swin" или "crema-swin-int8". Defaults to "crema-swin-int8".
        device      (str, optional): Только "cpu". Defaults to "cpu".
        num_threads (int, optional): Потоки ONNX Runtime. Defaults to None.

    Raises:
        ValueError: Неизвестная модель или устройство.

    Returns:
        ClipEmotionModel: Модель.
    """
    if model == STUB_MODEL:
        return StubClipModel()
    if model not in CREMA_MODELS:
        raise ValueError(f"Unknown clip emotion model '{model}'. Available: {available_clip_models()}")
    if device != "cpu":
        raise ValueError(f"Clip emotion model '{model}' supports only device='cpu', got '{device}'")
    return CremaClipModel(model, num_threads=num_threads)
