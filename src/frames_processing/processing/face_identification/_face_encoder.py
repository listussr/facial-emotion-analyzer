import time
import logging

import numpy as np
import torch
from facenet_pytorch import InceptionResnetV1


class FaceEncoder:
    def __init__(self, warmup: bool = True):
        """
        Преобразователь лица в эмбеддинг (FaceNet / VGGFace2).

        Args:
            warmup (bool): Флаг прогрева модели для более стабильной работы.
        """
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)

        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.model = InceptionResnetV1(pretrained='vggface2').eval().to(self.device)
        logging.info(f"Face embedder: using {self.device.upper()}")

        if warmup:
            self._warmup()

    def _warmup(self) -> None:
        """Пара прогревочных прогонов модели — после этого `get_embedding`
        стабильно укладывается в свои миллисекунды."""
        t0 = time.time()
        dummy = np.zeros((160, 160, 3), dtype=np.float32)
        with torch.no_grad():
            for _ in range(2):
                self.get_embedding(dummy)
        logging.info(f"Face embedder warmup done in {time.time() - t0:.2f}s")

    def get_embedding(self, aligned_face: np.ndarray) -> np.ndarray:
        """
        Преобразование лица в набор эмбеддингов
        ---

        Args:
            aligned_face (np.ndarray): Выровненное лицо.

        Returns:
            np.ndarray: Набор эмбеддингов.
        """
        if aligned_face.dtype != np.float32:
            aligned_face = np.ascontiguousarray(aligned_face, dtype=np.float32)

        img_tensor = torch.from_numpy(aligned_face).permute(2, 0, 1).unsqueeze(0)
        img_tensor = (img_tensor - 127.5) / 128.0
        img_tensor = img_tensor.to(self.device)

        with torch.no_grad():
            embedding = self.model(img_tensor)

        return embedding.cpu().numpy().ravel()
