from abc import ABC, abstractmethod
from typing import List, Literal, Optional, Sequence, Union
import logging
import os

import cv2
import numpy as np
import torch
from torchvision import transforms
from PIL import Image
import onnxruntime as ort

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _build_transforms(input_size: int):
    """Стандартный ImageNet-препроцессор torchvision с заданным размером входа."""
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
    """Размер входа модели для CUDA-варианта; INT8 суффикс игнорируем."""
    base = model.replace("-int8", "")
    return _CUDA_INPUT_SIZE.get(base, 224)

_MODELS_DIR = r'src\frames_processing\processing\emotion_recognition\models'

_models_pathes = {
    'cuda=resnet-18':           rf'{_MODELS_DIR}\resnet_18.pth',
    'cpu=resnet-18':            rf'{_MODELS_DIR}\resnet_18.onnx',
    'cpu=resnet-18-int8':       rf'{_MODELS_DIR}\resnet_18.int8.onnx',

    'cuda=resnet-50':           rf'{_MODELS_DIR}\resnet_50.pth',
    'cpu=resnet-50':            rf'{_MODELS_DIR}\resnet_50.onnx',
    'cpu=resnet-50-int8':       rf'{_MODELS_DIR}\resnet_50.int8.onnx',

    'cuda=convnext':            rf'{_MODELS_DIR}\convnext_basic.pth',
    'cpu=convnext':             rf'{_MODELS_DIR}\convnext_basic.onnx',
    'cpu=convnext-int8':        rf'{_MODELS_DIR}\convnext_basic.int8.onnx',

    'cuda=convnext-gelu':       rf'{_MODELS_DIR}\convnext_gelu_head.pth',
    'cpu=convnext-gelu':        rf'{_MODELS_DIR}\convnext_gelu_head.onnx',

    'cuda=efficientnet-b3':     rf'{_MODELS_DIR}\efficientnet_b3.pth',
    'cpu=efficientnet-b3':      rf'{_MODELS_DIR}\efficientnet_b3.onnx',
    'cpu=efficientnet-b3-int8': rf'{_MODELS_DIR}\efficientnet_b3.int8.onnx',

    'cuda=swin-tiny':           rf'{_MODELS_DIR}\swin_tiny.pth',
    'cpu=swin-tiny':            rf'{_MODELS_DIR}\swin_tiny.onnx',
    'cpu=swin-tiny-int8':       rf'{_MODELS_DIR}\swin_tiny.int8.onnx',
}


def _is_quantized_model(model_path: str) -> bool:
    """
    Эвристика: считаем модель квантизованной по имени файла.
    INT8 модели в этом проекте всегда содержат '.int8.' в названии.
    """
    return ".int8." in os.path.basename(model_path).lower()


def _build_cpu_session(model_path: str, num_threads: Optional[int] = None) -> ort.InferenceSession:
    """
    Создаёт ONNXRuntime-сессию для CPU c оптимальными настройками.

    Args:
        model_path (str): Путь к .onnx файлу.
        num_threads (int, optional): Явное число потоков. По умолчанию
            берётся max(1, os.cpu_count() - 1).

    Returns:
        ort.InferenceSession: Готовая сессия.
    """
    sess_options = ort.SessionOptions()
    sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

    if num_threads is None:
        num_threads = max(1, (os.cpu_count() or 2) - 1)
    sess_options.intra_op_num_threads = num_threads
    sess_options.inter_op_num_threads = 1
    sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

    available = set(ort.get_available_providers())
    if _is_quantized_model(model_path):
        providers = ['CPUExecutionProvider']
    else:
        preferred = ['XnnpackExecutionProvider', 'CPUExecutionProvider']
        providers = [p for p in preferred if p in available] or ['CPUExecutionProvider']

    session = ort.InferenceSession(model_path, sess_options=sess_options, providers=providers)
    logging.info(
        f"ONNX session: model='{os.path.basename(model_path)}', "
        f"threads={num_threads}, providers={session.get_providers()}"
    )
    return session


ImageOrBatch = Union[np.ndarray, Sequence[np.ndarray]]


def _is_batch(images: ImageOrBatch) -> bool:
    """
    True если на вход подан батч (список изображений).
    """
    if isinstance(images, np.ndarray):
        return images.ndim == 4
    return True


class _Recognizer(ABC):
    """
    Интерфейс для распознавателя эмоций.
    ---

    Методы:
    - predict() - предсказание эмоции по одному изображению или батчу.
    """
    @abstractmethod
    def predict(self, images: ImageOrBatch) -> np.ndarray:
        """
        Предсказание эмоций.

        Args:
            images (np.ndarray | Sequence[np.ndarray]): Одно изображение
                лица (H,W,3) или последовательность изображений (батч).

        Returns:
            np.ndarray: Матрица вероятностей эмоций формы (N, num_classes).
                Для одиночного входа N == 1.
        """
        pass


class _RecognizerCuda(_Recognizer):
    """
    Распознаватель с использованием GPU от NVIDIA.
    """
    def __init__(self, model_path: str, input_size: int = 224):
        # Импорт кастомных классов (EfficientNet-B3 и т.п.) — регистрирует их
        # в `__main__` для unpickle через torch.load.
        from . import _custom_models  # noqa: F401

        self._set_device()
        self._set_model(model_path)
        self._input_size = input_size
        self._transform = _build_transforms(input_size)
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
        pil_image = Image.fromarray(image.astype('uint8'))
        return self._transform(pil_image)

    def predict(self, images: ImageOrBatch) -> np.ndarray:
        """
        Предсказание эмоций для одного изображения или батча.
        Возвращает матрицу формы (N, num_classes).
        """
        batch_input = _is_batch(images)
        if batch_input:
            tensors = [self._preprocess_one(img) for img in images]
            tensor = torch.stack(tensors, dim=0).to(self._device)
        else:
            tensor = self._preprocess_one(images).unsqueeze(0).to(self._device)

        with torch.inference_mode():
            output = self.model(tensor)
            output = torch.softmax(output, dim=1)
        return output.cpu().numpy()


class _RecognizerCPU(_Recognizer):
    """
    Распознаватель эмоций с использованием CPU (ONNXRuntime).

    Поддерживает FP32 и INT8 модели прозрачно — формат определяется
    самим .onnx файлом, путь к которому выбирается в `EmotionRecognizer`
    по имени модели (например, 'resnet-18' vs 'resnet-18-int8').
    """
    def __init__(self, model_path: str, num_threads: Optional[int] = None):
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"ONNX model not found: {model_path}. "
                f"For -int8 модели — сгенерируйте их скриптом "
                f"`python scripts/quantize_emotion_model.py`."
            )
        self._session = _build_cpu_session(model_path, num_threads=num_threads)
        self._input_name = self._session.get_inputs()[0].name

        shape = self._session.get_inputs()[0].shape
        def _as_int(v, default):
            return v if isinstance(v, int) and v > 0 else default
        if len(shape) == 4:
            self._input_h = _as_int(shape[2], 224)
            self._input_w = _as_int(shape[3], 224)
        else:
            self._input_h = self._input_w = 224
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
        """
        Предсказание эмоций для одного изображения или батча.
        Возвращает матрицу формы (N, num_classes).
        """
        batch_input = _is_batch(images)
        if batch_input:
            arrays = [self._preprocess_one(img) for img in images]
            batch = np.stack(arrays, axis=0).astype(np.float32, copy=False)
        else:
            batch = self._preprocess_one(images)[np.newaxis, :].astype(np.float32, copy=False)

        logits = self._session.run(None, {self._input_name: batch})[0]

        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        return probs


_ModelName = Literal[
    'resnet-18',         'resnet-18-int8',
    'resnet-50',         'resnet-50-int8',
    'convnext',          'convnext-int8',
    'convnext-gelu',
    'efficientnet-b3',   'efficientnet-b3-int8',
    'swin-tiny',         'swin-tiny-int8',
]


class EmotionRecognizer(object):
    """
    Фабрика для распознавателей эмоций.

    """
    _located_models = {
        'cuda': _RecognizerCuda,
        'cpu': _RecognizerCPU,
    }

    def __new__(cls, model: _ModelName = 'resnet-18',
                device: Literal['cpu', 'cuda'] = 'cpu',
                num_threads: Optional[int] = None) -> _Recognizer:
        """
        Args:
            model (str): Название модели. Суффикс `-int8` выбирает
                квантизованный вариант (только для device='cpu').
            device (str): 'cpu' или 'cuda'.
            num_threads (int, optional): Количество CPU-потоков для ORT.
                По умолчанию = max(1, os.cpu_count() - 1). Игнорируется для CUDA.
        """
        key = f"{device}={model}"
        if key not in _models_pathes:
            available = sorted(k.split('=', 1)[1] for k in _models_pathes if k.startswith(f"{device}="))
            raise ValueError(
                f"Unknown model '{model}' for device '{device}'. Available: {available}"
            )
        if device == 'cpu':
            return _RecognizerCPU(_models_pathes[key], num_threads=num_threads)
        return _RecognizerCuda(
            _models_pathes[key],
            input_size=_resolve_input_size_for_cuda(model),
        )
