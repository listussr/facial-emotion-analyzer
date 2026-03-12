from abc import ABC, abstractmethod
import cv2
import torch
from torchvision import transforms
from PIL import Image
import numpy as np
import onnxruntime as ort

from typing import Literal
import logging

_data_transforms = transforms.Compose([
    transforms.Resize(224),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
])

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)

_models_pathes = {
    'cuda=resnet-18': r'src\frames_processing\processing\emotion_recognition\models\resnet_18.pth',
    'cpu=resnet-18': r'src\frames_processing\processing\emotion_recognition\models\resnet_18.onnx',
    # TODO - добавить модели
    'cuda=resnet-50': r'src\frames_processing\processing\emotion_recognition\models\resnet_18.pth',
    'cpu=resnet-50': r'src\frames_processing\processing\emotion_recognition\models\resnet_18.onnx',
    'cuda=convnext': r'src\frames_processing\processing\emotion_recognition\models\resnet_18.pth',
    'cpu=convnext': r'src\frames_processing\processing\emotion_recognition\models\resnet_18.onnx',
}

class _Recognizer(ABC):
    """
    Интерфейс для распознавателя эмоций.
    ---

    Методы:
    - predict() - предсказание эмоции по заданному изображению.
    """
    @abstractmethod
    def predict(self, image: np.ndarray) -> np.ndarray:
        """
        Предсказание эмоции по переданному изображению лица.
        ---

        Args:
            image (np.ndarray): Изображение лица в 3-канальном формате.

        Returns:
            np.ndarray: Вектор вероятностей эмоций.
        """
        pass
    
class _RecognizerCuda(_Recognizer):
    """
    Распознаватель с использованием GPU от NVIDIA.
    ---
    """
    def __init__(self, model_path: str):
        """
        Распознавание эмоций людей по изображениям.
        ---

        Args:
            model_path (str, optional): Путь к модели.
        """
        self._set_device()
        self._set_model(model_path)
        
    def _set_device(self) -> None:
        """
        Установка девайса для инференса модели.
        ---
        """
        if not torch.cuda.is_available():
            raise RuntimeError("CUDA is not available. Use device='cpu'.")
        self._device = torch.device("cuda:0")

    def _set_model(self, path: str):
        """
        Инициализация модели для предсказания.
        ---

        Args:
            path (str): Путь к модели.
        """
        self.model = torch.load(path, map_location=self._device, weights_only=False)
        self.model.to(self._device)
        self.model.eval()

    def predict(self, image: np.ndarray) -> np.ndarray:
        """
        Предсказание эмоции по изображению лица.
        ---

        Args:
            image (np.ndarray): Изображение.

        Returns:
            np.ndarray: Массив вероятностей классов.
        """
        pil_image = Image.fromarray(image.astype('uint8'))
        tensor = _data_transforms(pil_image).unsqueeze(0).to(self._device)
        with torch.no_grad():
            output = self.model(tensor)
            output = torch.softmax(output, dim=1)
        return output.cpu().numpy()

class _RecognizerCPU(_Recognizer):
    """
    Распознаватель эмоций с использованием CPU.
    ---
    """
    def __init__(self, model_path: str):
        sess_options = ort.SessionOptions()
        sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        sess_options.intra_op_num_threads = 2
        self._session = ort.InferenceSession(
            model_path,
            sess_options=sess_options,
            providers=['CPUExecutionProvider']
        )
        self._input_name = self._session.get_inputs()[0].name
        logging.info(f"EmotionRecognizer loaded model to CPU")
    
    def predict(self, image: np.ndarray) -> np.ndarray:
        """
        Предсказание эмоции по изображению лица.

        Args:
            image (np.ndarray): Изображение лица в BGR (OpenCV).

        Returns:
            np.ndarray: Массив вероятностей классов.
        """
        img = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        img = cv2.resize(img, (224, 224), interpolation=cv2.INTER_LINEAR)
        img = img.astype(np.float32) / 255.0
        img = (img - _MEAN) / _STD
        img = img.transpose(2, 0, 1)[np.newaxis, :]

        logits = self._session.run(None, {self._input_name: img})[0]

        e = np.exp(logits - logits.max(axis=1, keepdims=True))
        probs = e / e.sum(axis=1, keepdims=True)
        return probs


class EmotionRecognizer(object):
    """
    Фабрика для распознавателей эмоций.
    ---
    Возвращает объект распознавателя с моделью на CPU или GPU.
    """
    _located_models = {
        'cuda': _RecognizerCuda,
        'cpu': _RecognizerCPU,
    }
    def __new__(cls, model: Literal['resnet-18', 'convnext', 'resnet-50'] = 'resnet-18', device: Literal['cpu', 'cuda'] = 'cpu') -> _Recognizer:
        """
        Распознаватель эмоций.
        ---

        Методы:

        - predict() - предсказание эмоции по 3-канальному изображению.

        Args:
            model (Literal['resnet-18', 'resnet-50', 'convnext'], optional): Название модели. Defaults to 'resnet-18'.
            device (Literal['cpu', 'cuda'], optional): Локализация распознавателя. Defaults to 'cpu'.

        Returns:
            _Recognizer: Локализованный объект распознавателя с соответствующей моделью.
        """
        return EmotionRecognizer._located_models[device](_models_pathes[f"{device}={model}"])
