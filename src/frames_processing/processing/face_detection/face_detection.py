import numpy as np
from typing import List, Tuple
import mediapipe as mp
import cv2
import logging

mp_face_detection = mp.solutions.face_detection
mp_drawing = mp.solutions.drawing_utils

class FaceDetector(object):
    def __init__(self, min_detection_confidence: float = 0.5):
        """
        Инициализация детектора лиц MediaPipe.
        
        Args:
            min_detection_confidence (float): порог уверенности.
        """
        self.face_detection = mp_face_detection.FaceDetection(
            min_detection_confidence=min_detection_confidence,
            model_selection=0
        )
        self._next_id = 0

    def detect(self, image: np.ndarray) -> List[Tuple[float, float, float, float, float]]:
        """
        Детекция лиц на изображении.

        Args:
            image (np.ndarray): Изображение в формате BGR или RGB (H, W, 3).

        Returns:
            List[Tuple[str, np.ndarray]]: Список пар (face_id, bounding_box),
                где bounding_box — массив [x_min, y_min, x_max, y_max] в пикселях.
        """
        if image is None or image.size == 0:
            return []

        if image.shape[2] == 3:
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        else:
            rgb_image = image

        results = self.face_detection.process(rgb_image)
        detections = []

        if results.detections:
            h, w = image.shape[:2]
            for detection in results.detections:
                bbox = detection.location_data.relative_bounding_box
                conf = detection.score[0]
                x_min = int(bbox.xmin * w)
                y_min = int(bbox.ymin * h)
                x_max = x_min + int(bbox.width * w)
                y_max = y_min + int(bbox.height * h)

                x_min = max(0, x_min)
                y_min = max(0, y_min)
                x_max = min(w - 1, x_max)
                y_max = min(h - 1, y_max)

                if x_max <= x_min or y_max <= y_min:
                    continue

                detections.append([x_min, y_min, x_max, y_max, conf])

        return detections

    def __del__(self):
        if hasattr(self, 'face_detection') and self.face_detection:
            self.face_detection.close()
