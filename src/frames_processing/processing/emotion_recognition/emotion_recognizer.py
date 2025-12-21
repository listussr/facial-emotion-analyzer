import torch
from torchvision import transforms
from PIL import Image
import numpy as np

import logging

class EmotionRecognizer(object):
    data_transforms = transforms.Compose([
        transforms.Resize(224),
        transforms.ToTensor(),
        transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
    ])

    def __init__(self, model_path: str = r"src\frames_processor\models\resnet_18.pth"):
        """
        Распознавание эмоций людей по изображениям.
        ---

        Args:
            model_path (str, optional): Путь к модели.
        """
        self._set_device()
        self._set_model(model_path)
        self.emotion_labels = ('anger', 'contempt', 'disgust', 'fear', 'happy', 'neutral', 'sad', 'surprise')

    def _set_device(self) -> None:
        """
        Установка девайса для инференса модели.
        ---
        """
        self._device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

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
        tensor = self.data_transforms(pil_image).unsqueeze(0).to(self._device)
        with torch.no_grad():
            output = self.model.forward(tensor)
        return output.cpu().numpy()

    def idx_to_label(self, idx: int) -> str:
        """
        Возврат названия эмоции по её индексу.
        ---

        Args:
            idx (int): Индекс эмоции.

        Returns:
            str: Название эмоции.
        """
        if idx < 0 or idx >= len(self.emotion_labels):
            logging.error(f"Incorrect emotion index. Required [0..7] but got {idx}")
            return ""
        return self.emotion_labels[idx]
