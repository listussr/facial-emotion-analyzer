"""Покадровый распознаватель эмоций старых моделей: CUDA (PyTorch), CPU (ONNX) и заглушка."""

import logging
import os
import cv2
import numpy as np
import onnxruntime as ort
import torch

from abc import ABC
from abc import abstractmethod
from typing import List
from typing import Literal
from typing import Optional
from typing import Sequence
from typing import Union

from PIL import Image
from torchvision import transforms

from src.emotion_labels import NUM_EMOTIONS

STUB_MODEL = "stub"

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _build_transforms(input_size: int):
    return transforms.Compose([
        transforms.Resize((input_size, input_size)),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
    ])


_CUDA_INPUT_SIZE: dict = {
    "resnet-18":          224,
    "resnet-50":          224,
    "convnext":           224,
    "convnext-gelu":      224,
    "swin-tiny":          224,
    "efficientnet-b3":    300,
}


def _resolve_input_size_for_cuda(model: str) -> int:
    base = model.replace("-int8", "")
    return _CUDA_INPUT_SIZE.get(base, 224)


_MODELS_DIR = r"src\frames_processing\processing\emotion_recognition\models"

_models_pathes = {
    "cuda=resnet-18":           rf"{_MODELS_DIR}\resnet_18.pth",
    "cpu=resnet-18":            rf"{_MODELS_DIR}\resnet_18.onnx",
    "cpu=resnet-18-int8":       rf"{_MODELS_DIR}\resnet_18.int8.onnx",

    "cuda=resnet-50":           rf"{_MODELS_DIR}\resnet_50.pth",
    "cpu=resnet-50":            rf"{_MODELS_DIR}\resnet_50.onnx",
    "cpu=resnet-50-int8":       rf"{_MODELS_DIR}\resnet_50.int8.onnx",

    "cuda=convnext":            rf"{_MODELS_DIR}\convnext_basic.pth",
    "cpu=convnext":             rf"{_MODELS_DIR}\convnext_basic.onnx",
    "cpu=convnext-int8":        rf"{_MODELS_DIR}\convnext_basic.int8.onnx",

    "cuda=convnext-gelu":       rf"{_MODELS_DIR}\convnext_gelu_head.pth",
    "cpu=convnext-gelu":        rf"{_MODELS_DIR}\convnext_gelu_head.onnx",

    "cuda=efficientnet-b3":     rf"{_MODELS_DIR}\efficientnet_b3.pth",
    "cpu=efficientnet-b3":      rf"{_MODELS_DIR}\efficientnet_b3.onnx",
    "cpu=efficientnet-b3-int8": rf"{_MODELS_DIR}\efficientnet_b3.int8.onnx",

    "cuda=swin-tiny":           rf"{_MODELS_DIR}\swin_tiny.pth",
    "cpu=swin-tiny":            rf"{_MODELS_DIR}\swin_tiny.onnx",
    "cpu=swin-tiny-int8":       rf"{_MODELS_DIR}\swin_tiny.int8.onnx",
}


def _is_quantized_model(model_path: str) -> bool:
    return ".int8." in os.path.basename(model_path).lower()


def _build_cpu_session(model_path: str, num_threads: Optional[int] = None) -> ort.InferenceSession:
    """
    Сессия ONNX Runtime для CPU.

    Args:
        model_path  (str)          : Путь к .onnx.
        num_threads (int, optional): Потоки; по умолчанию число ядер минус один. Defaults to None.

    Returns:
        ort.InferenceSession: Сессия.
    """
    sess_options                          = ort.SessionOptions()
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    if num_threads is None:
        num_threads = max(1, (os.cpu_count() or 2) - 1)
    sess_options.intra_op_num_threads = num_threads
    sess_options.inter_op_num_threads = 1
    sess_options.execution_mode       = ort.ExecutionMode.ORT_SEQUENTIAL

    available = set(ort.get_available_providers())
    if _is_quantized_model(model_path):
        providers = ["CPUExecutionProvider"]
    else:
        preferred = ["XnnpackExecutionProvider", "CPUExecutionProvider"]
        providers = [p for p in preferred if p in available] or ["CPUExecutionProvider"]

    session = ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
    logging.info(
        f"ONNX session: model='{os.path.basename(model_path)}', "
        f"threads={num_threads}, providers={session.get_providers()}"
    )
    return session


ImageOrBatch = Union[np.ndarray, Sequence[np.ndarray]]


def _is_batch(images: ImageOrBatch) -> bool:
    if isinstance(images, np.ndarray):
        return images.ndim == 4
    return True


class _Recognizer(ABC):
    """Интерфейс покадрового распознавателя эмоций."""

    @abstractmethod
    def predict(self, images: ImageOrBatch) -> np.ndarray:
        """
        Предсказание эмоций.

        Args:
            images (ImageOrBatch): Кроп лица (H, W, 3) или батч кропов.

        Returns:
            np.ndarray: Вероятности формы (N, num_classes).
        """
        pass


class _RecognizerCuda(_Recognizer):
    """Распознаватель на GPU NVIDIA; кастомные архитектуры регистрируются для torch.load."""

    def __init__(self, model_path: str, input_size: int = 224):
        from . import _custom_models  # noqa: F401

        self._set_device()
        self._set_model(model_path)
        self._input_size = input_size
        self._transform  = _build_transforms(input_size)
        logging.info(
            f"EmotionRecognizer loaded model to CUDA, input_size={input_size}"
        )

    def _set_device(self) -> None:
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available. Use device='cpu'.")
        self._device = torch.device("cuda:0")

    def _set_model(self, path: str):
        self.model = torch.load(path, map_location=self._device, weights_only=False)
        self.model.to(self._device)
        self.model.eval()

    def _preprocess_one(self, image: np.ndarray) -> torch.Tensor:
        pil_image = Image.fromarray(image.astype("uint8"))
        return self._transform(pil_image)

    def predict(self, images: ImageOrBatch) -> np.ndarray:
        batch_input = _is_batch(images)
        if batch_input:
            tensors = [self._preprocess_one(img) for img in images]
            tensor  = torch.stack(tensors, dim=0).to(self._device)
        else:
            tensor = self._preprocess_one(images).unsqueeze(0).to(self._device)

        with torch.inference_mode():
            output = self.model(tensor)
            output = torch.softmax(output, dim=1)
        probs = output.cpu().numpy()
        if probs.shape[-1] != NUM_EMOTIONS:
            raise ValueError(
                f"CUDA model outputs {probs.shape[-1]} classes, but the system uses "
                f"{NUM_EMOTIONS} (see src/emotion_labels.py)"
            )
        return probs


class _RecognizerCPU(_Recognizer):
    """Распознаватель на CPU через ONNX Runtime; fp32 и int8 различаются только файлом модели."""

    def __init__(self, model_path: str, num_threads: Optional[int] = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"ONNX model not found: {model_path}. "
                f"For -int8 модели — сгенерируйте их скриптом "
                f"`python scripts/quantize_emotion_model.py`."
            )
        self._session    = _build_cpu_session(model_path, num_threads=num_threads)
        self._input_name = self._session.get_inputs()[0].name

        shape = self._session.get_inputs()[0].shape
        def _as_int(v, default):
            return v if isinstance(v, int) and v > 0 else default
        if len(shape) == 4:
            self._input_h = _as_int(shape[2], 224)
            self._input_w = _as_int(shape[3], 224)
        else:
            self._input_h = self._input_w = 224
        out_shape   = self._session.get_outputs()[0].shape
        num_classes = out_shape[-1] if out_shape and isinstance(out_shape[-1], int) else None
        if num_classes is not None and num_classes != NUM_EMOTIONS:
            raise ValueError(
                f"Model {os.path.basename(model_path)} outputs {num_classes} classes, "
                f"but the system uses {NUM_EMOTIONS} (see src/emotion_labels.py). "
                f"Use model='{STUB_MODEL}' until a {NUM_EMOTIONS}-class model is available."
            )
        logging.info(
            f"ONNX session ready: input={self._input_h}x{self._input_w}"
        )

    def _preprocess_one(self, image: np.ndarray) -> np.ndarray:
        img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (self._input_w, self._input_h), interpolation=cv2.INTER_LINEAR)
        img = img.astype(np.float32) / 255.0
        img = (img - _MEAN) / _STD
        return img.transpose(2, 0, 1)

    def predict(self, images: ImageOrBatch) -> np.ndarray:
        batch_input = _is_batch(images)
        if batch_input:
            arrays = [self._preprocess_one(img) for img in images]
            batch  = np.stack(arrays, axis=0).astype(np.float32, copy=False)
        else:
            batch = self._preprocess_one(images)[np.newaxis, :].astype(np.float32, copy=False)

        logits = self._session.run(None, {self._input_name: batch})[0]

        e     = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        return probs


class _RecognizerStub(_Recognizer):
    """
    Заглушка распознавателя на NUM_EMOTIONS классов.

    Детерминированная случайная проекция яркостных признаков кропа; к реальным эмоциям отношения не имеет.
    """
    _FEATURES = 8

    def __init__(self, seed: int = 0):
        rng     = np.random.default_rng(seed)
        self._w = rng.normal(0.0, 3.0, size=(self._FEATURES, NUM_EMOTIONS)).astype(np.float32)
        self._b = rng.normal(0.0, 0.5, size=NUM_EMOTIONS).astype(np.float32)
        logging.warning(
            f"EmotionRecognizer: using STUB model ({NUM_EMOTIONS} classes) — predictions are not real"
        )

    def _features(self, image: np.ndarray) -> np.ndarray:
        small  = cv2.resize(image, (16, 16), interpolation=cv2.INTER_AREA).astype(np.float32) / 255.0
        h, w   = image.shape[:2]
        mean   = small.reshape(-1, 3).mean(axis=0)
        std    = small.reshape(-1, 3).std(axis=0)
        aspect = np.float32(w / h if h else 1.0)
        return np.concatenate([mean, std, [aspect, 1.0]]).astype(np.float32)

    def predict(self, images: ImageOrBatch) -> np.ndarray:
        batch  = images if _is_batch(images) else [images]
        feats  = np.stack([self._features(img) for img in batch])
        logits = feats @ self._w + self._b
        e      = np.exp(logits - logits.max(axis=1, keepdims=True))
        return e / e.sum(axis=1, keepdims=True)


_ModelName = Literal[
    "stub",
    "resnet-18",         "resnet-18-int8",
    "resnet-50",         "resnet-50-int8",
    "convnext",          "convnext-int8",
    "convnext-gelu",
    "efficientnet-b3",   "efficientnet-b3-int8",
    "swin-tiny",         "swin-tiny-int8",
]


class EmotionRecognizer(object):
    """Фабрика покадровых распознавателей; суффикс -int8 выбирает квантизованную модель для CPU."""
    _located_models = {
        "cuda": _RecognizerCuda,
        "cpu": _RecognizerCPU,
    }

    def __new__(cls, model: _ModelName = "resnet-18",
                device: Literal["cpu", "cuda"] = "cpu",
                num_threads: Optional[int] = None) -> _Recognizer:
        if model == STUB_MODEL:
            return _RecognizerStub()
        key = f"{device}={model}"
        if key not in _models_pathes:
            available = sorted(k.split("=", 1)[1] for k in _models_pathes if k.startswith(f"{device}="))
            raise ValueError(
                f"Unknown model '{model}' for device '{device}'. Available: {available}"
            )
        if device == "cpu":
            return _RecognizerCPU(_models_pathes[key], num_threads=num_threads)
        return _RecognizerCuda(
            _models_pathes[key],
            input_size=_resolve_input_size_for_cuda(model),
        )
