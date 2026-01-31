import torch
from facenet_pytorch import InceptionResnetV1
import numpy as np

import logging

class FaceEncoder:
    def __init__(self):
        """
        Преобразователь 
        """
        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.model = InceptionResnetV1(
            pretrained='vggface2'
        ).eval().to(self.device)
        logging.info(f"Face embedder: using {self.device.upper()}")

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
            aligned_face = aligned_face.astype(np.float32)

        img_tensor = torch.from_numpy(aligned_face).permute(2, 0, 1).unsqueeze(0)
        img_tensor = (img_tensor - 127.5) / 128.0
        img_tensor = img_tensor.to(self.device)

        with torch.no_grad():
            embedding = self.model(img_tensor)

        return embedding.cpu().numpy().flatten()
