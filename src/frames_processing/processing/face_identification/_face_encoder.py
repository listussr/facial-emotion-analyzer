"""Эмбеддинг лица FaceNet (VGGFace2)."""

import logging
import time
import numpy as np
import torch

from facenet_pytorch import InceptionResnetV1


class FaceEncoder:
    """Преобразование лица в эмбеддинг FaceNet; torch в один поток, чтобы не мешать видеопайплайну."""

    def __init__(self, warmup: bool = True):
        torch.set_num_threads(1)
        torch.set_num_interop_threads(1)

        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model  = InceptionResnetV1(pretrained="vggface2").eval().to(self.device)
        logging.info(f"Face embedder: using {self.device.upper()}")

        if warmup:
            self._warmup()

    def _warmup(self) -> None:
        t0    = time.time()
        dummy = np.zeros((160, 160, 3), dtype=np.float32)
        with torch.no_grad():
            for _ in range(2):
                self.get_embedding(dummy)
        logging.info(f"Face embedder warmup done in {time.time() - t0:.2f}s")

    def get_embedding(self, aligned_face: np.ndarray) -> np.ndarray:
        """
        Эмбеддинг лица.

        Args:
            aligned_face (np.ndarray): Изображение лица.

        Returns:
            np.ndarray: Эмбеддинг.
        """
        if aligned_face.dtype != np.float32:
            aligned_face = np.ascontiguousarray(aligned_face, dtype=np.float32)

        img_tensor = torch.from_numpy(aligned_face).permute(2, 0, 1).unsqueeze(0)
        img_tensor = (img_tensor - 127.5) / 128.0
        img_tensor = img_tensor.to(self.device)

        with torch.no_grad():
            embedding = self.model(img_tensor)

        return embedding.cpu().numpy().ravel()
